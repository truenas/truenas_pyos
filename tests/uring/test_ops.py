# SPDX-License-Identifier: LGPL-3.0-or-later
"""
The six operations, oracled against ordinary blocking I/O.

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
def test_open_returns_process_fd(tmp_path):
    """The open's completion result is a regular process file descriptor,
    usable outside the ring like any os.open result."""
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        assert isinstance(fh, int)
        assert fh >= 0
        assert os.fstat(fh).st_size == 4    # a real fd: plain syscalls work
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
def test_open_honors_o_cloexec(tmp_path):
    """A plain openat2 accepts O_CLOEXEC and the returned fd carries it."""
    import fcntl
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_RDONLY | os.O_CLOEXEC))
        assert fcntl.fcntl(fh, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        drive_one(r, r.prep_close(fh))


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
        assert drive_one(r, r.prep_preadv2(fh, [buf], 0)) == 3
        assert bytes(buf) == b'abc'
        drive_one(r, r.prep_close(fh))


# ── handle repr ──────────────────────────────────────────────────────────────

@requires_io_uring
def test_ring_op_repr_names_its_op_type(tmp_path):
    """UringOp's repr names its op type and stays stable across submission."""
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        op = r.prep_openat2(dirfd, b'f', os.O_RDONLY)
        assert repr(op) == '<truenas_os.UringOp openat2>'
        fd = drive_one(r, op)
        assert repr(op) == '<truenas_os.UringOp openat2>'   # unchanged after submit
        # every op type maps to its own name (prepared, then dropped)
        buf = bytearray(4)
        assert repr(r.prep_preadv2(fd, [buf], 0)) == '<truenas_os.UringOp preadv2>'
        assert repr(r.prep_pwritev2(fd, [b'zzzz'], 0)) == '<truenas_os.UringOp pwritev2>'
        assert repr(r.prep_fsync(fd)) == '<truenas_os.UringOp fsync>'
        assert repr(r.prep_statx(dirfd, b'f')) == '<truenas_os.UringOp statx>'
        assert repr(r.prep_close(fd)) == '<truenas_os.UringOp close>'
        drive_one(r, r.prep_close(fd))


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
    """Every read/write takes an explicit offset; no file position moves."""
    (tmp_path / 'f').write_bytes(b'0123456789')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(3)
        assert drive_one(r, r.prep_preadv2(fh, [buf], 4)) == 3
        assert bytes(buf) == b'456'
        # Re-reading the same offset yields the same bytes: no position moved.
        assert drive_one(r, r.prep_preadv2(fh, [buf], 4)) == 3
        assert bytes(buf) == b'456'
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_write_at_offset(tmp_path):
    (tmp_path / 'f').write_bytes(b'AAAAAAAAAA')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_WRONLY))
        assert drive_one(r, r.prep_pwritev2(fh, [b'ZZ'], 4)) == 2
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'f').read_bytes() == b'AAAAZZAAAA'


@requires_io_uring
def test_default_offset_is_zero(tmp_path):
    (tmp_path / 'f').write_bytes(b'0123456789')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(4)
        assert drive_one(r, r.prep_preadv2(fh, [buf])) == 4    # offset omitted
        assert bytes(buf) == b'0123'
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_read_past_eof_returns_zero(tmp_path):
    (tmp_path / 'f').write_bytes(b'short')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(16)
        assert drive_one(r, r.prep_preadv2(fh, [buf], 1000)) == 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_read_accepts_memoryview(tmp_path):
    (tmp_path / 'f').write_bytes(b'abcdef')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(6)
        n = drive_one(r, r.prep_preadv2(fh, [memoryview(buf)], 0))
        drive_one(r, r.prep_close(fh))
    assert bytes(buf[:n]) == b'abcdef'


@requires_io_uring
def test_read_rejects_readonly_buffer(tmp_path):
    """bytes is not writable, so it cannot be a read destination."""
    (tmp_path / 'f').write_bytes(b'abc')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        with pytest.raises((TypeError, BufferError)):
            r.prep_preadv2(fh, [b'immutable'], 0)
        assert r.inflight == 0
        drive_one(r, r.prep_close(fh))


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
        nw, nr = drive(r, [r.prep_pwritev2(fh, [payload], 0),
                           r.prep_preadv2(fh, [buf], 0)], linked=True)
        assert nw == len(payload)
        assert nr == len(payload)
        assert bytes(buf) == payload
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'f').read_bytes() == payload


@requires_io_uring
def test_linked_chain_cancels_rest_on_failure(tmp_path):
    """A failed link -ECANCELEDs the rest of the chain (inherent io_uring).

    Writing to an O_RDONLY fd fails with EBADF; the read linked behind it is
    never issued and completes with ECANCELED.
    """
    (tmp_path / 'f').write_bytes(b'0123456789')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        buf = bytearray(4)
        (write_res, _), (read_res, _) = drive_raw(
            r, [r.prep_pwritev2(fh, [b'zz'], 0), r.prep_preadv2(fh, [buf], 0)],
            linked=True)
        assert write_res == -errno.EBADF
        assert read_res == -errno.ECANCELED
        drive_one(r, r.prep_close(fh))


# ── fsync ────────────────────────────────────────────────────────────────────

@requires_io_uring
def test_fsync_completes_with_none(tmp_path):
    """A plain prep_fsync completes with res 0 and result None."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        assert drive_one(r, r.prep_pwritev2(fh, [b'x' * 4096], 0)) == 4096
        (res, result), = drive_raw(r, [r.prep_fsync(fh)])
        assert res == 0
        assert result is None
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_fsync_fdatasync_flag(tmp_path):
    """fdatasync=True (IORING_FSYNC_DATASYNC) completes successfully."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        assert drive_one(r, r.prep_pwritev2(fh, [b'y' * 512], 0)) == 512
        assert drive_one(r, r.prep_fsync(fh, fdatasync=True)) is None
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_fsync_byte_range(tmp_path):
    """A nonzero length syncs only [offset, offset+length]; completes res 0."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        assert drive_one(r, r.prep_pwritev2(fh, [b'z' * 8192], 0)) == 8192
        assert drive_one(r, r.prep_fsync(fh, offset=4096, length=4096)) is None
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_fsync_links_behind_write(tmp_path):
    """The durability idiom: a pwritev2 and its fsync batch together, ordered
    by IOSQE_IO_LINK, and both complete."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        nw, synced = drive(r, [r.prep_pwritev2(fh, [b'durable'], 0),
                               r.prep_fsync(fh, fdatasync=True)], linked=True)
        assert nw == 7
        assert synced is None
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_fsync_stale_fd_yields_ebadf(tmp_path):
    """fsync of a closed fd is the kernel's bare EBADF at completion."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        drive_one(r, r.prep_close(fh))
        (res, result), = drive_raw(r, [r.prep_fsync(fh)])
        assert res == -errno.EBADF
        assert result is None


@requires_io_uring
def test_fsync_length_over_uint32_rejected(tmp_path):
    """The SQE length field is 32-bit; a larger length would silently truncate
    to the wrong range, so prep_fsync rejects it up front."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        with pytest.raises(ValueError, match='too large'):
            r.prep_fsync(fh, length=2 ** 32)
        assert r.inflight == 0
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
    """The keyword-taking preps reject unknown, duplicate, and missing-required
    keywords."""
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(TypeError, match='unexpected keyword'):
            r.prep_openat2(dirfd, 'f', bogus=1)
        with pytest.raises(TypeError, match='multiple values'):
            r.prep_openat2(dirfd, 'f', dirfd=dirfd)
        with pytest.raises(TypeError, match='missing required'):
            r.prep_openat2(path='f')
        with pytest.raises(TypeError, match='unexpected keyword'):
            r.prep_statx(dirfd, 'f', nope=1)
        with pytest.raises(TypeError, match='unexpected keyword'):
            r.prep_fsync(0, nope=1)
        with pytest.raises(TypeError, match='missing required'):
            r.prep_fsync(fdatasync=True)
        with pytest.raises(TypeError, match='unexpected keyword'):
            r.prep_preadv2(0, [bytearray(1)], nope=1)
        with pytest.raises(TypeError, match="missing required argument 'buffers'"):
            r.prep_pwritev2(0, flags=0)


@requires_io_uring
def test_positional_only_close_rejects_keywords(tmp_path):
    """prep_close is the one positional-only prep: a keyword raises TypeError."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_CREAT | os.O_RDWR, 0o600))
        with pytest.raises(TypeError, match='no keyword arguments'):
            r.prep_close(fh, extra=1)
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_over_long_path_yields_enametoolong(tmp_path):
    """Paths are not policed client-side (the op holds a reference to the
    caller's path bytes, not a bounded inline copy): a path over PATH_MAX
    reaches the kernel and completes with -ENAMETOOLONG, like any bad value."""
    with ring_env(tmp_path) as (r, dirfd):
        (res, result), = drive_raw(r, [r.prep_openat2(dirfd, b'a' * 5000)])
        assert res == -errno.ENAMETOOLONG
        assert result is None
        (res, result), = drive_raw(r, [r.prep_statx(dirfd, b'a' * 5000)])
        assert res == -errno.ENAMETOOLONG
        assert result is None


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
def test_double_close_yields_ebadf(tmp_path):
    """A second close of the same fd completes with -EBADF, exactly like a
    stale os.close -- there is no ring-side fd tracking to corrupt."""
    (tmp_path / 'f').write_bytes(b'A')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDONLY))
        (res1, _), = drive_raw(r, [r.prep_close(fh)])
        assert res1 == 0
        (res2, _), = drive_raw(r, [r.prep_close(fh)])   # stale fd: bare EBADF
        assert res2 == -errno.EBADF
        # the ring is unharmed: a fresh open + close still works
        fh2 = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDONLY))
        drive_one(r, r.prep_close(fh2))


@requires_io_uring
def test_close_links_behind_read_in_one_batch(tmp_path):
    """There is no close-last guard on a plain fd: a read and its close batch
    together, ordered by IOSQE_IO_LINK, and both complete."""
    (tmp_path / 'f').write_bytes(b'hello')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, 'f', os.O_RDONLY))
        buf = bytearray(5)
        nread, _ = drive(r, [r.prep_preadv2(fh, [buf], 0), r.prep_close(fh)],
                         linked=True)
        assert nread == 5
        assert bytes(buf) == b'hello'


@requires_io_uring
def test_prep_fd_parses_as_c_int(tmp_path):
    """The fd argument of close/preadv2/pwritev2/fsync parses as a C int up
    front: a non-int raises TypeError and an out-of-range int raises
    OverflowError, before any slot is consumed."""
    with ring_env(tmp_path) as (r, dirfd):
        for prep in (lambda fd: r.prep_close(fd),
                     lambda fd: r.prep_preadv2(fd, [bytearray(1)], 0),
                     lambda fd: r.prep_pwritev2(fd, [b'x'], 0),
                     lambda fd: r.prep_fsync(fd)):
            with pytest.raises(TypeError):
                prep('not an fd')
            with pytest.raises(OverflowError):
                prep(2 ** 31)
        assert r.inflight == 0


# ── vectored gather / scatter ────────────────────────────────────────────────

@requires_io_uring
def test_gather_write_scatter_read_round_trip(tmp_path):
    """One pwritev2 gathers multiple source buffers; one preadv2 scatters the
    bytes back across multiple destinations in order."""
    parts = [b'aa', b'bbbb', b'c' * 8]
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        assert drive_one(r, r.prep_pwritev2(fh, parts, 0)) == 14
        bufs = [bytearray(2), bytearray(4), bytearray(8)]
        assert drive_one(r, r.prep_preadv2(fh, bufs, 0)) == 14
        assert [bytes(b) for b in bufs] == parts
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'f').read_bytes() == b''.join(parts)


@requires_io_uring
def test_rw_flags_rwf_append(tmp_path):
    """flags is forwarded per-IO: RWF_APPEND writes at EOF despite offset 0."""
    (tmp_path / 'f').write_bytes(b'base')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDWR))
        assert drive_one(r, r.prep_pwritev2(fh, [b'-tail'], 0,
                                            os.RWF_APPEND)) == 5
        drive_one(r, r.prep_close(fh))
    assert (tmp_path / 'f').read_bytes() == b'base-tail'


@requires_io_uring
def test_rw_flags_unknown_bit_is_kernel_rejected(tmp_path):
    """An RWF_* bit the kernel does not support surfaces as the completion's
    -errno (EOPNOTSUPP), not a client-side error."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDWR))
        (res, result), = drive_raw(r, [r.prep_pwritev2(fh, [b'x'],
                                                       flags=1 << 30)])
        assert res == -errno.EOPNOTSUPP
        assert result is None
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_rw_buffer_count_capped(tmp_path):
    """A documented limitation: the iovec and Py_buffer arrays are inline in
    the op slot, so at most 8 buffers per operation -- 8 fit, 9 raise
    ValueError before anything is prepared."""
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f',
                                         os.O_CREAT | os.O_RDWR, 0o600))
        assert drive_one(r, r.prep_pwritev2(fh, [b'x'] * 8, 0)) == 8
        with pytest.raises(ValueError, match='too many buffers'):
            r.prep_pwritev2(fh, [b'x'] * 9, 0)
        assert r.inflight == 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_rw_empty_buffer_list_completes_zero(tmp_path):
    """Zero iovecs is Linux's traditional no-op: the op completes with res 0."""
    (tmp_path / 'f').write_bytes(b'x')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDWR))
        assert drive_one(r, r.prep_preadv2(fh, [], 0)) == 0
        assert drive_one(r, r.prep_pwritev2(fh, [], 0)) == 0
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_rw_non_sequence_buffers_rejected(tmp_path):
    """buffers must be a sequence; a non-sequence raises TypeError up front."""
    with ring_env(tmp_path) as (r, dirfd):
        with pytest.raises(TypeError, match='sequence'):
            r.prep_preadv2(0, 42)
        assert r.inflight == 0


@requires_io_uring
def test_rw_bad_item_mid_list_unwinds_earlier_pins(tmp_path):
    """A non-writable item part-way through a preadv2 list fails the prep and
    releases the pins already taken -- the first buffer is resizable again and
    the ring is unharmed."""
    (tmp_path / 'f').write_bytes(b'abcd')
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        first = bytearray(2)
        with pytest.raises((TypeError, BufferError)):
            r.prep_preadv2(fh, [first, b'readonly', bytearray(2)], 0)
        first.extend(b'zz')             # not left pinned by the failed prep
        assert r.inflight == 0
        buf = bytearray(4)
        assert drive_one(r, r.prep_preadv2(fh, [buf], 0)) == 4
        drive_one(r, r.prep_close(fh))


@requires_io_uring
def test_rw_all_buffers_pinned_in_flight(tmp_path):
    """Every buffer of a vectored op is pinned until the completion reaps,
    not just the first."""
    (tmp_path / 'f').write_bytes(b'x' * 64)
    with ring_env(tmp_path) as (r, dirfd):
        fh = drive_one(r, r.prep_openat2(dirfd, b'f', os.O_RDONLY))
        bufs = [bytearray(16) for _ in range(3)]
        r.submit([r.prep_preadv2(fh, bufs, 0)])          # in flight, not reaped
        for b in bufs:
            with pytest.raises(BufferError):
                b.extend(b'y')
        import select
        while r.inflight:
            select.select([r.ringfd()], [], [], 5.0)
            r.reap()
        for b in bufs:
            b.extend(b'y')              # all pins released at reap
        drive_one(r, r.prep_close(fh))


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
            r.submit([r.prep_preadv2(fh, [bytearray(5)], callback=closer),
                      r.prep_preadv2(fh, [bytearray(5)], callback=closer)])
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
        # a positional-only prep (prep_close): private_data without a callback
        with pytest.raises(TypeError):
            r.prep_close(0, None, object())
        # callback=None -> not consumed; the op still appears in reap()
        (ud,) = r.submit([r.prep_statx(dirfd, b'f', callback=None)])
        assert any(t[0] == ud for t in _drive_idle(r))


@requires_io_uring
def test_callback_accepted_on_all_preps(tmp_path):
    """callback is accepted on all six preps -- by keyword on every
    keyword-taking prep and positionally on close -- and fires for each."""
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        seen = []

        def cb(*a):
            seen.append(a[0])                        # a[0] = completion tuple

        r.submit([r.prep_openat2(dirfd, b'f', os.O_RDWR, callback=cb)])
        _drive_idle(r)
        fd = seen[-1][2]                             # open result = the new fd
        r.submit([r.prep_statx(dirfd, b'f', callback=cb)])
        _drive_idle(r)
        r.submit([r.prep_preadv2(fd, [bytearray(4)], callback=cb)])
        _drive_idle(r)
        r.submit([r.prep_pwritev2(fd, [b'zzzz'], callback=cb)])
        _drive_idle(r)
        r.submit([r.prep_fsync(fd, callback=cb)])
        _drive_idle(r)
        r.submit([r.prep_close(fd, cb)])
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
