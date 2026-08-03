"""
Type-level tests for truenas_os.Uring.

Each test uses assert_type() to pin the static type mypy must infer. The tests
are also valid pytest tests that execute at runtime.

Deliberately self-contained: tests/uring/conftest.py is untyped, and pulling it
in here would drag it into the strict mypy run. The availability gating mirrors
that conftest -- EPERM/ENOSYS/EACCES skip, EINVAL is a ring bug and never skips,
and TRUENAS_PYOS_REQUIRE_IO_URING turns skips into hard failures.

Files are ordinary process fds (an open completes with a new fd); a dirfd is an
O_PATH directory fd the caller opens (os.open); prep_* return an opaque UringOp
handle.
"""
import errno
import os
import select
from pathlib import Path
from typing import Any, assert_type

import pytest

import truenas_os
from truenas_os import Uring, UringOp

_UNAVAILABLE = (errno.EPERM, errno.ENOSYS, errno.EACCES)

_REQUIRE_ENV = 'TRUENAS_PYOS_REQUIRE_IO_URING'


def _probe_io_uring() -> str | None:
    try:
        r = Uring(entries=8)
    except OSError as exc:
        if exc.errno in _UNAVAILABLE:
            return f'io_uring unavailable: {exc}'
        raise
    r.close()
    return None


_UNAVAILABLE_REASON = _probe_io_uring()

if _UNAVAILABLE_REASON is not None and os.environ.get(_REQUIRE_ENV):
    raise RuntimeError(f'{_REQUIRE_ENV} is set but {_UNAVAILABLE_REASON}')

requires_io_uring = pytest.mark.skipif(
    _UNAVAILABLE_REASON is not None,
    reason=_UNAVAILABLE_REASON or '',
)


def _drive_one(r: Uring, handle: object) -> Any:
    """Submit one prepared handle, drive to completion, return its result."""
    (ud,) = r.submit([handle])
    while True:
        select.select([r.ringfd()], [], [], 5.0)
        for (token, res, result) in r.reap():
            if token == ud:
                if res < 0:
                    raise OSError(-res, os.strerror(-res))
                if isinstance(result, BaseException):
                    raise result
                return result


@requires_io_uring
def test_ring_lifecycle_types() -> None:
    r = Uring(entries=8, cq_entries=0)
    try:
        assert_type(r, Uring)
        assert_type(r.ringfd(), int)
        assert_type(r.inflight, int)
        assert_type(r.closed, bool)
    finally:
        r.close()


@requires_io_uring
def test_prep_submit_reap_cancel_types(tmp_path: Path) -> None:
    r = Uring(entries=8)
    dirfd = os.open(os.fspath(tmp_path), os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        open_handle = r.prep_openat2(dirfd, 'f', os.O_CREAT | os.O_RDWR, 0o600)
        assert_type(open_handle, UringOp)
        uds = r.submit([open_handle], linked=False)
        assert_type(uds, tuple[int, ...])

        comps = r.reap()
        assert_type(comps, list[tuple[int, int, object]])

        fh = _drive_one(r, r.prep_openat2(dirfd, 'g', os.O_CREAT | os.O_RDWR, 0o600))
        assert isinstance(fh, int)

        wr = r.prep_pwritev2(fh, [b'payload'], 0)
        assert_type(wr, UringOp)
        rd = r.prep_preadv2(fh, [bytearray(16)], 0)
        assert_type(rd, UringOp)
        assert_type(r.prep_statx(dirfd, 'f'), UringOp)  # dropped unsubmitted
        # openat2 / fsync / statx accept keyword arguments (no positional-only `/`)
        assert_type(r.prep_openat2(dirfd, 'f', flags=os.O_RDONLY, mode=0), UringOp)
        assert_type(r.prep_statx(dirfd, 'f', mask=0, flags=0), UringOp)
        assert_type(r.prep_fsync(fh, fdatasync=True, offset=0, length=0), UringOp)
        _drive_one(r, r.prep_fsync(fh))
        # A linked write-then-read chain.
        assert_type(r.submit([wr, rd], linked=True), tuple[int, ...])

        # Drain everything.
        while r.inflight:
            select.select([r.ringfd()], [], [], 5.0)
            for comp in r.reap():
                assert_type(comp, tuple[int, int, object])
                assert_type(comp[0], int)
                assert_type(comp[1], int)
                assert_type(comp[2], object)

        cl = r.prep_close(fh)
        assert_type(cl, UringOp)
        _drive_one(r, cl)

        assert_type(r.cancel(uds[0]), None)
    finally:
        r.close()
        os.close(dirfd)


@requires_io_uring
def test_default_arguments_type_check(tmp_path: Path) -> None:
    # Optional prep_* arguments have defaults, so the shorter forms type-check.
    (tmp_path / 'f').write_bytes(b'abcd')
    r = Uring(entries=8)
    dirfd = os.open(os.fspath(tmp_path), os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        fh = _drive_one(r, r.prep_openat2(dirfd, 'f'))   # flags/mode/resolve default
        assert isinstance(fh, int)
        buf = bytearray(4)
        assert_type(r.prep_preadv2(fh, [buf]), UringOp)  # offset defaults to 0
        _drive_one(r, r.prep_preadv2(fh, [buf]))
        _drive_one(r, r.prep_close(fh))
    finally:
        r.close()
        os.close(dirfd)


def test_resolve_flag_lives_on_truenas_os() -> None:
    # The Uring types expose no constants of their own; prep_openat2's resolve
    # values come from truenas_os (RESOLVE_BENEATH is the default).
    _ = truenas_os.RESOLVE_BENEATH
