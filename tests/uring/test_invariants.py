# SPDX-License-Identifier: LGPL-3.0-or-later
"""
The disciplines that keep the kernel from touching freed or moved memory.

These are the tests that matter most. A buffer handed to a READ or WRITE is
kernel-visible from submission until its completion reaps -- possibly long
after the awaiting task was cancelled -- so the op-table entry owns it, not
the caller. Everything here exists to stop that ownership rule eroding.
"""

import asyncio
import os

import pytest

from truenas_os import uring

from .conftest import asyncio_test, reactor_env, requires_io_uring


# ── buffer pinning ───────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_buffer_cannot_be_resized_while_in_flight(tmp_path):
    """Holding a Py_buffer is what prevents a realloc under the kernel.

    bytearray's bf_getbuffer bumps ob_exports, so resize raises BufferError
    while an operation is outstanding.
    """
    (tmp_path / 'f').write_bytes(b'x' * 64)
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(64)
        fut = r.pread(pers, fh, buf, 0)
        with pytest.raises(BufferError):
            buf.extend(b'y' * 64)
        await fut
        await r.close_file(fh)


@requires_io_uring
@asyncio_test
async def test_buffer_resizable_again_after_completion(tmp_path):
    """The pin must be released when the completion reaps, not leaked."""
    (tmp_path / 'f').write_bytes(b'x' * 8)
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(8)
        await r.pread(pers, fh, buf, 0)
        buf.extend(b'y')                # must not raise
        await r.close_file(fh)


@requires_io_uring
@asyncio_test
async def test_buffer_stays_pinned_after_cancellation(tmp_path):
    """Cancellation orphans the entry; it does not release the buffer early.

    The kernel may still write into it, so the pin outlives the waiter and is
    dropped only when the CQE arrives.
    """
    (tmp_path / 'f').write_bytes(b'x' * 4096)
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(4096)
        task = asyncio.ensure_future(r.pread(pers, fh, buf, 0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Let the completion reap, then the pin must be gone.
        for _ in range(100):
            if r.inflight == 0:
                break
            await asyncio.sleep(0.01)
        assert r.inflight == 0, 'orphaned op never reaped'
        buf.extend(b'z')                # pin released
        await r.close_file(fh)


# ── close-last discipline ────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_close_refused_while_ops_in_flight(tmp_path):
    """A slot freed under a live operation would be reused beneath it."""
    (tmp_path / 'f').write_bytes(b'x' * 4096)
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(4096)
        fut = r.pread(pers, fh, buf, 0)
        with pytest.raises(BlockingIOError, match='last operation'):
            await r.close_file(fh)
        await fut
        await r.close_file(fh)          # now permitted


# ── slot recycling ───────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_op_slots_recycle_without_confusion(tmp_path):
    """Generations make a completion for a recycled slot inert.

    Churn far more operations than the table holds, through a table small
    enough that every slot is reused many times.
    """
    payload = b'payload'
    async with reactor_env(tmp_path, entries=8, files=4) as (r, pers, anchor):
        for i in range(300):
            name = b'f%d' % (i % 3)
            fh = await r.open(pers, anchor, name,
                              flags=os.O_CREAT | os.O_RDWR | os.O_TRUNC,
                              mode=0o644)
            body = payload + b'%d' % i
            assert await r.pwrite(pers, fh, body, 0) == len(body)
            buf = bytearray(64)
            n = await r.pread(pers, fh, buf, 0)
            assert bytes(buf[:n]) == body, 'slot %d returned wrong data' % i
            await r.close_file(fh)
        assert r.inflight == 0


@requires_io_uring
@asyncio_test
async def test_stale_fixedfile_rejected(tmp_path):
    """A FixedFile whose slot was recycled must not address the new file."""
    (tmp_path / 'a').write_bytes(b'aaa')
    (tmp_path / 'b').write_bytes(b'bbb')
    async with reactor_env(tmp_path, files=1) as (r, pers, anchor):
        first = await r.open(pers, anchor, b'a', flags=os.O_RDONLY)
        await r.close_file(first)

        # Same slot, different file.
        second = await r.open(pers, anchor, b'b', flags=os.O_RDONLY)
        assert second.slot == first.slot

        with pytest.raises(ValueError):
            await r.pread(pers, first, bytearray(4), 0)
        await r.close_file(second)


# ── resource exhaustion ──────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_file_table_exhaustion_is_reported(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    async with reactor_env(tmp_path, entries=64, files=2) as (r, pers, anchor):
        held = [await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
                for _ in range(2)]
        with pytest.raises(OSError, match='file table is full'):
            await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        for fh in held:
            await r.close_file(fh)


@requires_io_uring
@asyncio_test
async def test_op_table_exhaustion_is_reported(tmp_path):
    (tmp_path / 'f').write_bytes(b'x' * 1024)
    async with reactor_env(tmp_path, entries=4, files=8) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        bufs = [bytearray(1024) for _ in range(8)]
        futs = []
        with pytest.raises(BlockingIOError, match='operation table is full'):
            for buf in bufs:
                futs.append(r.pread(pers, fh, buf, 0))
        await asyncio.gather(*futs)
        await r.close_file(fh)


# ── concurrency ──────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_concurrent_operations_do_not_cross(tmp_path):
    """Fan out enough that completions interleave, then check every result."""
    count = 64
    for i in range(count):
        (tmp_path / ('f%d' % i)).write_bytes(b'%04d' % i * 8)

    async with reactor_env(tmp_path, entries=256, files=256) as (r, pers, anchor):
        handles = await asyncio.gather(*[
            r.open(pers, anchor, b'f%d' % i, flags=os.O_RDONLY)
            for i in range(count)
        ])
        assert len({fh.slot for fh in handles}) == count, 'slots collided'

        bufs = [bytearray(64) for _ in range(count)]
        lengths = await asyncio.gather(*[
            r.pread(pers, handles[i], bufs[i], 0) for i in range(count)
        ])
        for i in range(count):
            assert bytes(bufs[i][:lengths[i]]) == b'%04d' % i * 8, (
                'completion %d received another operation\'s data' % i
            )

        await asyncio.gather(*[r.close_file(fh) for fh in handles])
        assert r.inflight == 0


# ── teardown ─────────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_close_with_operations_in_flight(tmp_path):
    """Teardown cancels, drains, and only then releases the tables."""
    (tmp_path / 'f').write_bytes(b'x' * 8192)
    loop = asyncio.get_running_loop()
    r = uring.Reactor(entries=64, files=64)
    r.attach(loop)
    pers = r.register_self()
    anchor = uring.Anchor(str(tmp_path))

    fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
    bufs = [bytearray(8192) for _ in range(8)]
    for buf in bufs:
        r.pread(pers, fh, buf, 0)       # deliberately not awaited

    r.close()                           # must not hang, crash or leak
    assert r.closed is True
    anchor.close()


@requires_io_uring
@asyncio_test
async def test_reactor_does_not_leak_descriptors(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    before = len(os.listdir('/proc/self/fd'))
    for _ in range(10):
        async with reactor_env(tmp_path) as (r, pers, anchor):
            fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
            await r.close_file(fh)
    after = len(os.listdir('/proc/self/fd'))
    assert after <= before, 'leaked %d descriptors' % (after - before)
