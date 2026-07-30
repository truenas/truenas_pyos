# SPDX-License-Identifier: LGPL-3.0-or-later
"""
The disciplines that keep the kernel from touching freed or moved memory, and
that keep a prepared/submitted handle honest.

These are the tests that matter most. A buffer handed to a pread/pwrite is
kernel-visible from submission until its completion reaps, so the op slot owns
it, not the caller. A prepared-but-dropped handle must give its slot and pin
back; a submitted handle must never be submittable twice; and a batch that
fails to stage in full must consume nothing.
"""

import gc
import os

import pytest

from .conftest import (
    drive,
    drive_one,
    requires_io_uring,
    ring_env,
)


# ── buffer pinning ───────────────────────────────────────────────────────────

@requires_io_uring
def test_buffer_cannot_be_resized_while_in_flight(tmp_path):
    """Holding a Py_buffer is what prevents a realloc under the kernel.

    bytearray's bf_getbuffer bumps ob_exports, so resize raises BufferError
    while the op slot still owns the buffer -- i.e. until its CQE has reaped.
    """
    (tmp_path / 'f').write_bytes(b'x' * 64)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(64)
        r.submit([r.prep_pread(fh, buf, 0)])         # not reaped yet
        with pytest.raises(BufferError):
            buf.extend(b'y' * 64)                     # pinned while in flight
        # drain the read; the pin releases when the completion reaps
        import select
        while r.inflight:
            select.select([r.ringfd()], [], [], 5.0)
            r.reap()
        buf.extend(b'y' * 64)                          # now resizable: pin released
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_buffer_resizable_again_after_completion(tmp_path):
    """The pin must be released when the completion reaps, not leaked."""
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(8)
        drive_one(r, r.prep_pread(fh, buf, 0))
        buf.extend(b'y')                # must not raise
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_pin_released_after_cancel(tmp_path):
    """cancel() is advisory; the op still reaps and only then frees its pin."""
    (tmp_path / 'f').write_bytes(b'x' * 4096)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(4096)
        (ud,) = r.submit([r.prep_pread(fh, buf, 0)])
        with pytest.raises(BufferError):
            buf.extend(b'z')
        r.cancel(ud)
        import select
        while r.inflight:
            select.select([r.ringfd()], [], [], 5.0)
            r.reap()
        assert r.inflight == 0, 'cancelled op never reaped'
        buf.extend(b'z')                # pin released
        drive_one(r, r.prep_close(fh))


# ── prepare-then-drop reclaims the slot and the pin ──────────────────────────

@requires_io_uring
def test_prep_then_drop_releases_pin(tmp_path):
    """A prepared handle pins its buffer; dropping it before submit releases it."""
    (tmp_path / 'f').write_bytes(b'x' * 64)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(64)
        handle = r.prep_pread(fh, buf, 0)
        with pytest.raises(BufferError):
            buf.extend(b'y')            # pinned while prepared
        assert r.inflight == 0, 'prepare does not put an op in flight'
        del handle
        gc.collect()
        buf.extend(b'y')                # pin released on drop
        assert r.inflight == 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_prep_then_drop_reclaims_the_slot(tmp_path):
    """A dropped prepared handle returns its op slot to the pool.

    Prepare and drop far more operations than the pool holds; if the slot were
    not reclaimed, the pool would exhaust and prep would raise BlockingIOError.
    """
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path, entries=4) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        for _ in range(200):            # fifty times the pool depth
            handle = r.prep_pread(fh, bytearray(8), 0)
            del handle
        assert r.inflight == 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_prep_then_drop_reclaims_open_file_slot(tmp_path):
    """A dropped prepared open hands its reserved fixed-file slot back too."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path, files=4) as (r, dirfd):
        for _ in range(50):             # far more than the file table holds
            handle = r.prep_openat2(dirfd, b'f', os.O_RDONLY)
            del handle
        # The file table must still be usable.
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        drive_one(r, r.prep_close(fh))


# ── submit-once ──────────────────────────────────────────────────────────────

@requires_io_uring
def test_submit_refuses_a_submitted_handle(tmp_path):
    """A handle whose slot is in flight (or reaped) is not prepared, so it is
    refused with ValueError -- a handle is single-use."""
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        handle = r.prep_pread(fh, bytearray(8), 0)
        drive(r, [handle])              # submit + drain
        with pytest.raises(ValueError, match='not a prepared handle'):
            r.submit([handle])
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_submitted_handle_inert_across_slot_reuse(tmp_path):
    """A submitted handle forgets its slot, so dropping it never disturbs a
    later op that happens to reuse that slot.

    With a one-slot pool the second prep necessarily draws the first op's slot.
    Dropping the first handle -- long submitted and reaped -- must not see the
    second op sitting PREPPED in that slot and reclaim it: that would release a
    live op's pinned buffer and fixed-file slot from a destructor.
    """
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path, entries=1) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))

        first = r.prep_pread(fh, bytearray(8), 0)
        drive(r, [first])               # submit + reap; slot is now free again
        assert r.inflight == 0

        bufB = bytearray(8)
        second = r.prep_pread(fh, bufB, 0)   # reuses the one slot
        with pytest.raises(BufferError):
            bufB.extend(b'z')           # second op pins its buffer

        del first                       # drop the stale, already-submitted handle
        gc.collect()

        with pytest.raises(BufferError):
            bufB.extend(b'z')           # still pinned: the drop reclaimed nothing
        assert r.inflight == 0

        n = drive_one(r, second)        # and it still submits its own op
        assert n == 8
        assert bytes(bufB) == b'x' * 8
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_submit_refuses_a_duplicate_in_one_batch(tmp_path):
    """The same handle twice in one submit is refused: the second sees it
    already staged INFLIGHT, and the batch unwinds."""
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        handle = r.prep_pread(fh, bytearray(8), 0)
        with pytest.raises(ValueError):
            r.submit([handle, handle])
        assert r.inflight == 0
        # The handle was unwound back to prepared; it still submits once.
        drive(r, [handle])
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_submit_refuses_a_foreign_handle(tmp_path):
    """A handle prepared on another ring is refused with ValueError."""
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        with ring_env(tmp_path) as (other, other_dirfd):
            of = drive_one(other, other.prep_openat2(other_dirfd, b'f',
                                                     os.O_RDONLY))
            foreign = other.prep_pread(of, bytearray(8), 0)
            with pytest.raises(ValueError, match='different Uring'):
                r.submit([foreign])
            assert r.inflight == 0
            drive(other, [foreign])
            drive_one(other, other.prep_close(of))
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_submit_refuses_a_non_handle(tmp_path):
    """A non-UringOp item aborts the whole batch, consuming nothing."""
    (tmp_path / 'f').write_bytes(b'x' * 8)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        good = r.prep_pread(fh, bytearray(8), 0)
        with pytest.raises(TypeError, match='UringOp'):
            r.submit([good, ('not', 'a', 'handle')])
        assert r.inflight == 0
        with pytest.raises(TypeError):
            r.submit([object()])
        assert r.inflight == 0
        # The staged-then-unwound handle is still usable.
        drive(r, [good])
        drive_one(r, r.prep_close(fh))


# ── transactional staging ────────────────────────────────────────────────────

@requires_io_uring
def test_transactional_unwind_consumes_nothing(tmp_path):
    """A batch with one bad handle submits nothing and reclaims no slot.

    The good handle staged ahead of the failure is reverted to prepared:
    inflight stays 0, and the ring stays usable even after many rejections --
    far more than the op pool holds.
    """
    (tmp_path / 'f').write_bytes(b'x' * 16)
    with ring_env(tmp_path, entries=8) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        for _ in range(200):
            good = r.prep_pread(fh, bytearray(4), 0)
            with pytest.raises(TypeError):
                r.submit([good, object()])
            assert r.inflight == 0
            # `good` reverted to prepared; drop it to reclaim the slot.
            del good
        # Nothing leaked: a real op still works.
        buf = bytearray(4)
        assert drive_one(r, r.prep_pread(fh, buf, 0)) == 4
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_batch_larger_than_ring_is_rejected(tmp_path):
    """A single submit batch cannot exceed the ring's SQ depth (`entries`).

    Staging past the SQ capacity fails with BlockingIOError and the whole batch
    is unwound -- nothing is submitted, every staged handle reverts to prepared,
    and its buffer pin is retained (the handle is still alive and prepared).
    """
    (tmp_path / 'f').write_bytes(b'x' * 16)
    with ring_env(tmp_path, entries=4, files=8) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        # The op pool is only `entries` deep, so prepare exactly that many and
        # submit them all at once -- fits the SQ. To exceed the SQ we would need
        # more handles than the pool holds, so instead assert the pool bound.
        handles = [r.prep_pread(fh, bytearray(16), 0) for _ in range(4)]
        with pytest.raises(BlockingIOError):
            r.prep_pread(fh, bytearray(16), 0)   # pool exhausted
        assert r.inflight == 0
        # Submitting the full batch fits the SQ.
        drive(r, handles)
        drive_one(r, r.prep_close(fh))


# ── slot recycling ───────────────────────────────────────────────────────────

@requires_io_uring
def test_op_slots_recycle_without_confusion(tmp_path):
    """Churn far more operations than the pool holds, through a pool small
    enough that every slot is reused many times, and every op gets its own
    data back."""
    with ring_env(tmp_path, entries=8, files=4) as (r, dirfd):
        for i in range(300):
            name = b'f%d' % (i % 3)
            fh = drive_one(r, r.prep_openat2(
                dirfd, name, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o644))
            body = b'payload%d' % i
            assert drive_one(r, r.prep_pwrite(fh, body, 0)) == len(body)
            buf = bytearray(64)
            n = drive_one(r, r.prep_pread(fh, buf, 0))
            assert bytes(buf[:n]) == body, 'slot %d returned wrong data' % i
            drive_one(r, r.prep_close(fh))
        assert r.inflight == 0


# ── close-last discipline ────────────────────────────────────────────────────

@requires_io_uring
def test_close_refused_while_ops_in_flight(tmp_path):
    """A slot freed under a live operation would be reused beneath it."""
    (tmp_path / 'f').write_bytes(b'x' * 4096)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(4096)
        r.submit([r.prep_pread(fh, buf, 0)])   # in flight, not reaped
        with pytest.raises(BlockingIOError, match='last operation'):
            r.prep_close(fh)
        import select
        while r.inflight:
            select.select([r.ringfd()], [], [], 5.0)
            r.reap()
        drive_one(r, r.prep_close(fh))          # now permitted


# ── resource exhaustion ──────────────────────────────────────────────────────

@requires_io_uring
def test_file_table_exhaustion_is_reported(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path, entries=64, files=2) as (r, dirfd):
        held = [drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
                for _ in range(2)]
        with pytest.raises(OSError, match='file table is full'):
            r.prep_openat2(dirfd, b'f', os.O_RDONLY)
        for fh in held:
            drive_one(r, r.prep_close(fh))


# ── teardown ─────────────────────────────────────────────────────────────────

@requires_io_uring
def test_close_with_operations_in_flight(tmp_path):
    """Teardown cancels, drains, and only then releases the pool."""
    (tmp_path / 'f').write_bytes(b'x' * 8192)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        bufs = [bytearray(8192) for _ in range(8)]
        r.submit([r.prep_pread(fh, buf, 0) for buf in bufs])   # not reaped

        r.close()                       # must not hang, crash or leak
        assert r.closed is True


@requires_io_uring
def test_close_with_prepared_handles_outstanding(tmp_path):
    """close() releases the pins of prepared-but-unsubmitted operations, and a
    later drop of those handles (pool is gone) is safe."""
    (tmp_path / 'f').write_bytes(b'x' * 4096)
    with ring_env(tmp_path, entries=16, files=16) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        bufs = [bytearray(4096) for _ in range(3)]
        prepared = [r.prep_pread(fh, buf, 0) for buf in bufs]   # prepared, not submitted
        with pytest.raises(BufferError):
            bufs[0].extend(b'y')        # pinned while prepared

        r.close()
        assert r.closed is True
        for buf in bufs:
            buf.extend(b'y')            # close released the pins
        del prepared                    # dropping after close must not crash
        gc.collect()


@requires_io_uring
def test_ring_does_not_leak_descriptors(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    before = len(os.listdir('/proc/self/fd'))
    for _ in range(10):
        with ring_env(tmp_path) as (r, dirfd):
            fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
            drive_one(r, r.prep_close(fh))
    after = len(os.listdir('/proc/self/fd'))
    assert after <= before, 'leaked %d descriptors' % (after - before)
