# SPDX-License-Identifier: LGPL-3.0-or-later
"""
The four core operations, oracled against ordinary blocking I/O.

Every path resolves against an O_PATH anchor dirfd; open is confined in-kernel
by RESOLVE_BENEATH. There is no AT_FDCWD surface and no absolute-path surface.
"""

import errno
import os

import pytest

import truenas_os
from .conftest import (
    _drain,
    drive,
    drive_one,
    drive_raw,
    read_file,
    requires_io_uring,
    ring_env,
    write_file,
)


# ── open ─────────────────────────────────────────────────────────────────────

@requires_io_uring
def test_open_returns_int_slot_not_fd(tmp_path):
    """An explicit-index install materialises no process file descriptor.

    Its handle is a bare registered-file-table slot index (an int), not a fd.
    """
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        assert isinstance(fh, int)
        assert fh >= 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_open_missing_file(tmp_path):
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(FileNotFoundError):
            drive_one(r, r.prep_openat2(dirfd, b'nope', os.O_RDONLY))


@requires_io_uring
def test_open_multi_component_path_allowed(tmp_path):
    """openat2 may resolve multiple components; RESOLVE_BENEATH confines it."""
    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'f').write_bytes(b'nested')
    with ring_env(tmp_path) as (r, dirfd):
        assert read_file(r, dirfd, b'sub/f') == b'nested'


@requires_io_uring
def test_resolve_beneath_blocks_escape(tmp_path):
    sub = tmp_path / 'sub'
    sub.mkdir()
    with ring_env(sub) as (r, dirfd):
        with pytest.raises(OSError) as exc:
            drive_one(r, r.prep_openat2(dirfd, b'../escaped', os.O_RDONLY))
        # RESOLVE_BENEATH reports an attempted escape as EXDEV.
        assert exc.value.errno == errno.EXDEV


@requires_io_uring
def test_open_rejects_o_cloexec(tmp_path):
    """O_CLOEXEC is invalid with a fixed-file install; reject it up front."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(ValueError, match='O_CLOEXEC'):
            r.prep_openat2(dirfd, b'f', os.O_RDONLY | os.O_CLOEXEC)
        assert r.inflight == 0


@requires_io_uring
def test_open_creat_creates_file(tmp_path):
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'new',
                                         os.O_CREAT | os.O_WRONLY, 0o600))
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'new').exists()


@requires_io_uring
def test_default_flags_are_rdonly(tmp_path):
    """prep_openat2 defaults flags to O_RDONLY when omitted."""
    (tmp_path / 'f').write_bytes(b'abc')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f'))
        buf = bytearray(3)
        assert drive_one(r, r.prep_pread(fh, buf, 0)) == 3
        assert bytes(buf) == b'abc'
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_failed_open_returns_its_file_slot(tmp_path):
    """A failed open installed nothing, so its reserved slot must come back."""
    with ring_env(tmp_path, files=4) as (r, dirfd):
        for _ in range(50):             # far more than the table holds
            with pytest.raises(FileNotFoundError):
                drive_one(r, r.prep_openat2(dirfd, b'missing', os.O_RDONLY))
        # The table must still be usable.
        fh = drive_one(r, r.prep_openat2(dirfd, b'ok',
                                         os.O_CREAT | os.O_WRONLY, 0o644))
        drive_one(r, r.prep_close(fh))


# ── fixed-fd install ─────────────────────────────────────────────────────────

@requires_io_uring
def test_fixed_fd_install_returns_usable_regular_fd(tmp_path):
    """prep_fixed_fd_install mints a real process fd from a registered slot: it
    reads with ordinary syscalls and is CLOEXEC by default."""
    import fcntl
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path) as (r, dirfd):
        slot = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        fd = drive_one(r, r.prep_fixed_fd_install(slot))
        assert isinstance(fd, int) and fd >= 0
        try:
            assert os.read(fd, 5) == b'hello'          # a real, usable fd
            assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        finally:
            os.close(fd)
        # The slot is untouched -- it still needs its own close.
        drive_one(r, r.prep_close(slot))


@requires_io_uring
def test_fixed_fd_install_cloexec_false(tmp_path):
    import fcntl
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        slot = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        fd = drive_one(r, r.prep_fixed_fd_install(slot, False))
        try:
            assert not (fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
        finally:
            os.close(fd)
        drive_one(r, r.prep_close(slot))


@requires_io_uring
def test_fixed_fd_install_on_empty_slot_fails(tmp_path):
    """Installing a never-opened slot is a bare-slot op: the kernel reports an
    error, like any fixed-file op on an empty slot."""
    with ring_env(tmp_path, files=4) as (r, _dirfd):
        with pytest.raises(OSError):
            drive_one(r, r.prep_fixed_fd_install(0))


# ── handle repr ──────────────────────────────────────────────────────────────

@requires_io_uring
def test_ring_op_repr_names_its_op_type(tmp_path):
    """UringOp's repr names its op type and stays stable across submission."""
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        op = r.prep_openat2(dirfd, b'f', os.O_RDONLY)
        assert repr(op) == '<truenas_os.UringOp openat2>'
        slot = drive_one(r, op)
        assert repr(op) == '<truenas_os.UringOp openat2>'   # unchanged after submit
        # every op type maps to its own name (prepared, then dropped)
        buf = bytearray(4)
        assert repr(r.prep_pread(slot, buf, 0)) == '<truenas_os.UringOp pread>'
        assert repr(r.prep_pwrite(slot, b'zzzz', 0)) == '<truenas_os.UringOp pwrite>'
        assert repr(r.prep_statx(dirfd, b'f')) == '<truenas_os.UringOp statx>'
        assert repr(r.prep_fixed_fd_install(slot)) == \
            '<truenas_os.UringOp fixed_fd_install>'
        assert repr(r.prep_close(slot)) == '<truenas_os.UringOp close>'
        drive_one(r, r.prep_close(slot))


# ── read / write ─────────────────────────────────────────────────────────────

@requires_io_uring
def test_write_then_read_round_trip(tmp_path):
    payload = b'hello io_uring\n'
    with ring_env(tmp_path) as (r, dirfd):
        assert write_file(r, dirfd, b'f', payload) == len(payload)
        assert (tmp_path / 'f').read_bytes() == payload
        assert read_file(r, dirfd, b'f') == payload


@requires_io_uring
def test_read_is_positional(tmp_path):
    """A registered file exposes no file position: every op takes an offset."""
    (tmp_path / 'f').write_bytes(b'0123456789')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(3)
        assert drive_one(r, r.prep_pread(fh, buf, 4)) == 3
        assert bytes(buf) == b'456'
        # Re-reading the same offset yields the same bytes: no position moved.
        assert drive_one(r, r.prep_pread(fh, buf, 4)) == 3
        assert bytes(buf) == b'456'
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_write_at_offset(tmp_path):
    (tmp_path / 'f').write_bytes(b'AAAAAAAAAA')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_WRONLY))
        assert drive_one(r, r.prep_pwrite(fh, b'ZZ', 4)) == 2
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'f').read_bytes() == b'AAAAZZAAAA'


@requires_io_uring
def test_default_offset_is_zero(tmp_path):
    (tmp_path / 'f').write_bytes(b'0123456789')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(4)
        assert drive_one(r, r.prep_pread(fh, buf)) == 4    # offset omitted
        assert bytes(buf) == b'0123'
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_read_past_eof_returns_zero(tmp_path):
    (tmp_path / 'f').write_bytes(b'short')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(16)
        assert drive_one(r, r.prep_pread(fh, buf, 1000)) == 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_read_accepts_memoryview(tmp_path):
    (tmp_path / 'f').write_bytes(b'abcdef')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(6)
        n = drive_one(r, r.prep_pread(fh, memoryview(buf), 0))
        drive_one(r, r.prep_close(fh))
    assert bytes(buf[:n]) == b'abcdef'


@requires_io_uring
def test_read_rejects_readonly_buffer(tmp_path):
    """bytes is not writable, so it cannot be a read destination."""
    (tmp_path / 'f').write_bytes(b'abc')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        with pytest.raises((TypeError, BufferError)):
            r.prep_pread(fh, b'immutable', 0)
        assert r.inflight == 0
        drive_one(r, r.prep_close(fh))


# ── close ────────────────────────────────────────────────────────────────────

@requires_io_uring
def test_close_frees_the_slot_for_reuse(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path, files=2) as (r, dirfd):
        for _ in range(20):             # ten times the table size
            fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
            drive_one(r, r.prep_close(fh))
        assert r.inflight == 0


@requires_io_uring
def test_operations_on_closed_slot_yield_ebadf(tmp_path):
    """A stale slot is a stale handle: the kernel reports EBADF, not ValueError."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        drive_one(r, r.prep_close(fh))
        with pytest.raises(OSError) as exc:
            drive_one(r, r.prep_pread(fh, bytearray(4), 0))
        assert exc.value.errno == errno.EBADF


# ── linked chains ────────────────────────────────────────────────────────────

@requires_io_uring
def test_linked_write_then_read_sees_the_write(tmp_path):
    """IOSQE_IO_LINK orders a chain: the read runs after the write completes.

    Submitting an unlinked write+read at the same offset would race; the link
    is what makes the read observe the write.
    """
    payload = b'linked-payload'
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o644))
        buf = bytearray(len(payload))
        nw, nr = drive(r, [r.prep_pwrite(fh, payload, 0),
                           r.prep_pread(fh, buf, 0)], linked=True)
        assert nw == len(payload)
        assert nr == len(payload)
        assert bytes(buf) == payload
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'f').read_bytes() == payload


@requires_io_uring
def test_linked_chain_cancels_rest_on_failure(tmp_path):
    """A failed link -ECANCELEDs the rest of the chain (inherent io_uring).

    Writing to an O_RDONLY slot fails with EBADF; the read linked behind it is
    never issued and completes with ECANCELED.
    """
    (tmp_path / 'f').write_bytes(b'0123456789')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(4)
        (write_res, _), (read_res, _) = drive_raw(
            r, [r.prep_pwrite(fh, b'zz', 0), r.prep_pread(fh, buf, 0)],
            linked=True)
        assert write_res == -errno.EBADF
        assert read_res == -errno.ECANCELED
        drive_one(r, r.prep_close(fh))


# ── statx ────────────────────────────────────────────────────────────────────

@requires_io_uring
def test_statx_path_matches_os_stat(tmp_path):
    """prep_statx of a path relative to the dirfd oracles against os.stat."""
    (tmp_path / 'f').write_bytes(b'0123456789')
    oracle = os.stat(tmp_path / 'f')
    with ring_env(tmp_path) as (r, dirfd):
        st = drive_one(r, r.prep_statx(dirfd, 'f'))
    assert type(st).__name__ == 'StatxResult'
    assert st.stx_size == oracle.st_size == 10
    assert st.stx_ino == oracle.st_ino
    assert st.stx_mode == oracle.st_mode


@requires_io_uring
def test_statx_empty_path_stats_the_dirfd(tmp_path):
    """AT_EMPTY_PATH with an empty path statx's the dirfd itself (a directory)."""
    with ring_env(tmp_path) as (r, dirfd):
        st = drive_one(r, r.prep_statx(dirfd, '', truenas_os.AT_EMPTY_PATH))
    assert (st.stx_mode & 0o170000) == 0o040000  # S_IFDIR


@requires_io_uring
def test_statx_explicit_flags_and_mask(tmp_path):
    """flags and mask are forwarded (AT_SYMLINK_NOFOLLOW + STATX_BASIC_STATS)."""
    (tmp_path / 'f').write_bytes(b'abc')
    with ring_env(tmp_path) as (r, dirfd):
        st = drive_one(r, r.prep_statx(dirfd, 'f',
                                       truenas_os.AT_SYMLINK_NOFOLLOW,
                                       truenas_os.STATX_BASIC_STATS))
    assert st.stx_size == 3


@requires_io_uring
def test_statx_missing_path_is_enoent(tmp_path):
    """statx of a name that does not exist completes with -ENOENT, no raise mid-drain."""
    with ring_env(tmp_path) as (r, dirfd):
        (res, result), = drive_raw(r, [r.prep_statx(dirfd, 'nope')])
    assert res == -errno.ENOENT
    assert result is None


# ── keyword arguments (openat2 / statx only) ──────────────────────────────────

@requires_io_uring
def test_openat2_accepts_keywords(tmp_path):
    """prep_openat2 takes dirfd/path/flags/mode/resolve by keyword."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f',
                                         flags=os.O_CREAT | os.O_RDWR, mode=0o600))
        assert isinstance(fh, int)
        drive_one(r, r.prep_close(fh))
        # resolve= alone, without positionally padding flags/mode
        fh = drive_one(r, r.prep_openat2(dirfd, 'f',
                                         resolve=truenas_os.RESOLVE_BENEATH))
        drive_one(r, r.prep_close(fh))
        # all-keyword
        fh = drive_one(r, r.prep_openat2(dirfd=dirfd, path='f'))
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_statx_accepts_keywords(tmp_path):
    """prep_statx takes dirfd/path/flags/mask by keyword."""
    (tmp_path / 'f').write_bytes(b'abc')
    with ring_env(tmp_path) as (r, dirfd):
        # mask= alone, without positionally padding flags
        st = drive_one(r, r.prep_statx(dirfd, 'f',
                                       mask=truenas_os.STATX_BASIC_STATS))
        assert st.stx_size == 3
        st = drive_one(r, r.prep_statx(dirfd=dirfd, path='f',
                                       flags=truenas_os.AT_SYMLINK_NOFOLLOW))
        assert st.stx_size == 3


@requires_io_uring
def test_prep_keyword_errors(tmp_path):
    """openat2/statx reject unknown, duplicate, and missing-required keywords."""
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(TypeError, match='unexpected keyword'):
            r.prep_openat2(dirfd, 'f', bogus=1)
        with pytest.raises(TypeError, match='multiple values'):
            r.prep_openat2(dirfd, 'f', dirfd=dirfd)
        with pytest.raises(TypeError, match='missing required'):
            r.prep_openat2(path='f')
        with pytest.raises(TypeError, match='unexpected keyword'):
            r.prep_statx(dirfd, 'f', nope=1)


@requires_io_uring
def test_positional_only_ops_reject_keywords(tmp_path):
    """close/pread/pwrite stay positional-only: a keyword raises TypeError."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_CREAT | os.O_RDWR, 0o600))
        with pytest.raises(TypeError, match='no keyword arguments'):
            r.prep_pread(fh, bytearray(4), offset=0)
        with pytest.raises(TypeError, match='no keyword arguments'):
            r.prep_pwrite(fh, b'x', offset=0)
        with pytest.raises(TypeError, match='no keyword arguments'):
            r.prep_close(fh, extra=1)
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_prep_rejects_path_at_or_over_path_max(tmp_path):
    """A path >= PATH_MAX is rejected client-side (the slot's inline path buffer
    is PATH_MAX); openat2 and statx both raise ValueError before submission."""
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(ValueError, match='too long'):
            r.prep_openat2(dirfd, b'a' * 4096)
        with pytest.raises(ValueError, match='too long'):
            r.prep_statx(dirfd, b'a' * 4096)


@requires_io_uring
def test_file_slot_over_uint32_rejected(tmp_path):
    """A file slot >= 2**32 must not truncate into an in-range slot: the range
    check runs at full width, so it's rejected, not treated as slot (v mod 2**32)."""
    with ring_env(tmp_path) as (r, _dirfd):
        with pytest.raises(ValueError, match='out of range'):
            r.prep_close(2 ** 32)          # would truncate to slot 0
        with pytest.raises(ValueError, match='out of range'):
            r.prep_pread(2 ** 32 + 3, bytearray(4), 0)


@requires_io_uring
def test_cancel_rejects_invalid_op_id(tmp_path):
    """cancel() validates only the op id's slot field (low 32 bits, 1..entries): 0
    (the sentinel), out-of-range, and would-truncate slot fields all raise. The
    generation (high 32 bits) is deliberately not range-checked -- a valid slot
    field with any generation is accepted (it simply no-ops if it matches no op)."""
    with ring_env(tmp_path, entries=8) as (r, dirfd):
        # slot field 0 / >entries / truncates-to-0 / >entries with a generation set
        for bad in (0, 9, 2 ** 40, (1 << 32) | 9):
            with pytest.raises(ValueError, match='invalid op id'):
                r.cancel(bad)
        # a valid slot field carrying a foreign generation is accepted, not rejected
        r.cancel((7 << 32) | 1)          # slot 0, generation 7: a no-op in the kernel
        # a real token from submit() is accepted (drained when the ring closes)
        (ud,) = r.submit([r.prep_openat2(dirfd, 'f', os.O_CREAT | os.O_RDWR, 0o600)])
        r.cancel(ud)


@requires_io_uring
def test_cancel_stale_token_does_not_abort_reused_slot(tmp_path):
    """The generation packed into the op id closes the cancel ABA: once an op reaps
    and its slot is reused by a new op, the old token no longer matches, so
    cancelling it must NOT abort the new op that took the slot."""
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path, entries=1) as (r, dirfd):
        # op A on slot 0; reap it so the single slot frees for reuse
        (tok_a,) = r.submit([r.prep_statx(dirfd, 'f')])
        _drain(r, [tok_a])
        # op B (entries=1) must reuse slot 0: same slot field, bumped generation
        (tok_b,) = r.submit([r.prep_statx(dirfd, 'f')])
        assert tok_b != tok_a                                 # generation differs
        assert (tok_b & 0xffffffff) == (tok_a & 0xffffffff)   # same slot field
        # cancelling the STALE token A must leave live op B untouched
        r.cancel(tok_a)
        (res, _result) = _drain(r, [tok_b])[tok_b]
        assert res >= 0, f"a stale cancel aborted the reused slot's op: res={res}"


@requires_io_uring
def test_double_close_does_not_corrupt_file_table(tmp_path):
    """Closing a slot that is not installed (a double close, or a never-opened slot)
    must NOT push it onto the file free list a second time. If it did, the list
    would become self-referential and two later opens would receive the same slot."""
    (tmp_path / 'f').write_bytes(b'A')
    (tmp_path / 'g').write_bytes(b'B')
    with ring_env(tmp_path, files=8) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDONLY))
        (res1, _), = drive_raw(r, [r.prep_close(fh)])   # normal close: installed->freed
        assert res1 == 0
        (res2, _), = drive_raw(r, [r.prep_close(fh)])   # stale close: EBADF, must not re-free
        assert res2 < 0
        # the free list is intact: two opens must get two DIFFERENT slots
        a = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDONLY))
        b = drive_one(r, r.prep_openat2(dirfd, 'g', os.O_RDONLY))
        assert a != b, f"file free list corrupted: both opens got slot {a}"


@requires_io_uring
def test_close_batched_with_same_slot_sibling_refused(tmp_path):
    """A close prepared in the same batch as another op on the same fixed-file slot
    is refused. The sibling charges the slot's live counter at prep time, so
    prep_close sees it (live > 0) even before the batch is submitted."""
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDWR))
        read = r.prep_pread(fh, bytearray(5), 0)   # sibling: prepared, not submitted
        with pytest.raises(BlockingIOError):
            r.prep_close(fh)                       # refused while `read` targets fh
        (res, _), = drive_raw(r, [read])           # the sibling is still submittable
        assert res == 5


@requires_io_uring
def test_op_batched_after_close_on_same_slot_refused(tmp_path):
    """The reverse of the above: once a close is prepared on a slot, a further op on
    the same slot is refused (close_pending), so a close and an unordered
    read/write can never be batched together in either prep order."""
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDWR))
        close = r.prep_close(fh)                   # close prepared first
        with pytest.raises(BlockingIOError):
            r.prep_pread(fh, bytearray(5), 0)      # refused while close is pending
        with pytest.raises(BlockingIOError):
            r.prep_fixed_fd_install(fh)            # install is refused too
        (res, _), = drive_raw(r, [close])          # the close is still submittable
        assert res == 0


@requires_io_uring
def test_rw_buffer_over_uint32_rejected(tmp_path):
    """A buffer larger than UINT_MAX would truncate io_uring's 32-bit length field
    (a 4 GiB buffer to 0), so prep_pread/prep_pwrite reject it up front rather than
    issue a wrong-sized transfer with no error."""
    import mmap
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_CREAT | os.O_RDWR, 0o600))
        try:
            big = mmap.mmap(-1, (1 << 32))   # 4 GiB, demand-zero virtual reservation
        except (OSError, OverflowError, MemoryError):
            pytest.skip("cannot reserve a >4 GiB mapping in this environment")
        try:
            with pytest.raises(ValueError, match='too large'):
                r.prep_pwrite(fh, big, 0)
            with pytest.raises(ValueError, match='too large'):
                r.prep_pread(fh, big, 0)
        finally:
            big.close()


# ── completion callbacks ─────────────────────────────────────────────────────

def _drive_idle(r, timeout=5.0):
    """Poll + reap until the ring is idle (inflight == 0); return the reap list.

    A callback'd op is consumed (never appears in reap()), so it cannot be waited
    for with _drain; drive by inflight instead. Callbacks fire inside reap(), so
    they have all run by the time this returns."""
    import select
    import time
    listed = []
    deadline = time.monotonic() + timeout
    while r.inflight:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('ring did not go idle')
        select.select([r.ringfd()], [], [], remaining)
        listed += r.reap()
    return listed


@requires_io_uring
def test_callback_fires_and_consumes(tmp_path):
    """A callback'd op fires the callback with its completion tuple and is
    consumed -- its token never appears in reap()'s returned list."""
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path) as (r, dirfd):
        seen = []
        (ud,) = r.submit([r.prep_statx(dirfd, b'f',
                                       callback=lambda c: seen.append(c))])
        listed = _drive_idle(r)
        assert len(seen) == 1
        assert isinstance(seen[0], tuple) and len(seen[0]) == 3
        assert seen[0][0] == ud                      # the op's own completion
        assert all(t[0] != ud for t in listed)       # consumed: not returned


@requires_io_uring
def test_callback_private_data_identity(tmp_path):
    """private_data is handed back untouched (by identity) as the 2nd arg."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        sentinel = object()
        got = []
        r.submit([r.prep_statx(dirfd, b'f',
                               callback=lambda c, pd: got.append(pd),
                               private_data=sentinel)])
        _drive_idle(r)
        assert len(got) == 1 and got[0] is sentinel


@requires_io_uring
def test_callback_arity(tmp_path):
    """No private_data -> 1-arg call; private_data given -> 2-arg call."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        calls = []
        r.submit([r.prep_statx(dirfd, b'f',
                               callback=lambda *a: calls.append(a))])
        _drive_idle(r)
        assert len(calls[-1]) == 1
        r.submit([r.prep_statx(dirfd, b'f',
                               callback=lambda *a: calls.append(a),
                               private_data='pd')])
        _drive_idle(r)
        assert len(calls[-1]) == 2 and calls[-1][1] == 'pd'


@requires_io_uring
def test_callback_raise_is_unraisable_and_drain_continues(tmp_path):
    """A raising callback is reported unraisably and does not abort the drain;
    other ops in the same batch are still processed."""
    import sys
    for name in ('a', 'b', 'c'):
        (tmp_path / name).write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        def boom(c):
            raise RuntimeError('boom')
        captured = []
        old = sys.unraisablehook
        sys.unraisablehook = lambda a: captured.append(a.exc_type)
        try:
            r.submit([
                r.prep_statx(dirfd, b'a', callback=boom),
                r.prep_statx(dirfd, b'b', callback=boom),
                r.prep_statx(dirfd, b'c'),           # no callback -> returned
            ])
            listed = _drive_idle(r)
        finally:
            sys.unraisablehook = old
        assert captured.count(RuntimeError) == 2
        assert len(listed) == 1                      # only the non-callback op


@requires_io_uring
def test_callback_closing_ring_is_refused_and_siblings_fire(tmp_path):
    """A callback that calls close() on its own ring mid-reap is refused
    (RuntimeError, reported unraisably) rather than tearing the ring down under
    the drain -- so other completed ops in the same reap still fire, and the ring
    stays open for the caller to close after reap() returns."""
    import sys
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        seen = []
        captured = []

        def closer(c):
            seen.append(c)
            r.close()                                # refused inside a callback

        old = sys.unraisablehook
        sys.unraisablehook = lambda a: captured.append(a.exc_type)
        try:
            r.submit([r.prep_pread(fh, bytearray(5), 0, closer),
                      r.prep_pread(fh, bytearray(5), 0, closer)])
            _drive_idle(r)
        finally:
            sys.unraisablehook = old
        assert len(seen) == 2                        # both siblings' callbacks fired
        assert captured.count(RuntimeError) == 2     # each close() refused
        assert r.closed is False                     # ring intact
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_reap_from_callback_is_refused(tmp_path):
    """reap() is not re-entrant: calling it from a completion callback raises
    RuntimeError (reported unraisably), never draining the ring under itself."""
    import sys
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        captured = []

        def reaper(c):
            r.reap()                                 # refused inside a callback

        old = sys.unraisablehook
        sys.unraisablehook = lambda a: captured.append(a.exc_type)
        try:
            r.submit([r.prep_statx(dirfd, b'f', callback=reaper)])
            _drive_idle(r)
        finally:
            sys.unraisablehook = old
        assert captured.count(RuntimeError) == 1


@requires_io_uring
def test_callback_not_fired_on_dropped_prepped_handle(tmp_path):
    """A callback'd handle dropped before submit never fires."""
    import gc
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        seen = []
        h = r.prep_statx(dirfd, b'f', callback=lambda c: seen.append(c))
        del h
        gc.collect()
        drive_one(r, r.prep_statx(dirfd, b'f'))      # drive an unrelated op
        assert seen == []


@requires_io_uring
def test_callback_not_fired_at_close(tmp_path):
    """An in-flight callback'd op torn down by close() never fires."""
    (tmp_path / 'f').write_bytes(b'x')
    seen = []
    with ring_env(tmp_path) as (r, dirfd):
        r.submit([r.prep_statx(dirfd, b'f', callback=lambda c: seen.append(c))])
        r.close()                                    # drains without firing
    assert seen == []


@requires_io_uring
def test_callback_fires_after_handle_dropped(tmp_path):
    """The callback lives on the op slot, so it fires even if the submitted
    handle is dropped before reap."""
    import gc
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        seen = []
        h = r.prep_statx(dirfd, b'f', callback=lambda c: seen.append(c))
        r.submit([h])
        del h
        gc.collect()
        _drive_idle(r)
        assert len(seen) == 1


@requires_io_uring
def test_callback_fires_on_failure(tmp_path):
    """A failed op still fires its callback (res = -errno, result None)."""
    with ring_env(tmp_path) as (r, dirfd):
        seen = []
        r.submit([r.prep_statx(dirfd, b'nope',
                               callback=lambda c: seen.append(c))])
        _drive_idle(r)
        assert len(seen) == 1
        assert seen[0][1] == -errno.ENOENT and seen[0][2] is None


@requires_io_uring
def test_callback_validation(tmp_path):
    """private_data without a callback, or a non-callable callback, raise
    TypeError; callback=None is a no-op (the op is still returned by reap())."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(TypeError):
            r.prep_statx(dirfd, b'f', private_data=object())
        with pytest.raises(TypeError):
            r.prep_statx(dirfd, b'f', callback=42)
        # positional path (prep_pread): private_data without a callback
        with pytest.raises(TypeError):
            r.prep_pread(0, bytearray(1), 0, None, object())
        # callback=None -> not consumed; the op still appears in reap()
        (ud,) = r.submit([r.prep_statx(dirfd, b'f', callback=None)])
        assert any(t[0] == ud for t in _drive_idle(r))


@requires_io_uring
def test_callback_accepted_on_all_preps(tmp_path):
    """callback is accepted on all six preps -- by keyword on openat2/statx and
    positionally on close/pread/pwrite/fixed_fd_install -- and fires for each."""
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        seen = []

        def cb(*a):
            seen.append(a[0])                        # a[0] = completion tuple

        r.submit([r.prep_openat2(dirfd, b'f', os.O_RDWR, callback=cb)])
        _drive_idle(r)
        slot = seen[-1][2]                           # open result = file slot
        r.submit([r.prep_statx(dirfd, b'f', callback=cb)])
        _drive_idle(r)
        r.submit([r.prep_pread(slot, bytearray(4), 0, cb)])
        _drive_idle(r)
        r.submit([r.prep_pwrite(slot, b'zzzz', 0, cb)])
        _drive_idle(r)
        r.submit([r.prep_fixed_fd_install(slot, True, cb)])
        _drive_idle(r)
        os.close(seen[-1][2])                        # install result = a real fd
        r.submit([r.prep_close(slot, cb)])
        _drive_idle(r)
        assert len(seen) == 6
        assert all(isinstance(c, tuple) and len(c) == 3 for c in seen)


@requires_io_uring
def test_callback_refcounts_return_to_baseline(tmp_path):
    """The owned callback/private_data refs are released on every path -- fired
    at reap, and dropped before submit."""
    import gc
    import sys
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):

        def cb(*a):
            pass

        pd = object()
        gc.collect()
        base_cb = sys.getrefcount(cb)
        base_pd = sys.getrefcount(pd)
        for _ in range(100):
            r.submit([r.prep_statx(dirfd, b'f', callback=cb, private_data=pd)])
            _drive_idle(r)                           # fire + release
            dropped = r.prep_statx(dirfd, b'f', callback=cb, private_data=pd)
            del dropped                              # drop -> release
        gc.collect()
        assert sys.getrefcount(cb) == base_cb
        assert sys.getrefcount(pd) == base_pd
