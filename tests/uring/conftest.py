# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Fixtures and helpers for truenas_os.Uring tests.

The raw Ring has no event loop. These tests drive it directly: prepare a batch
of operations with prep_*(), submit() the handles, poll the ring fd, and reap()
the completions -- drive()/drive_one() below wrap that loop. reap() yields plain
(token, res, result) tuples; a result is unwrapped per the C layer's
convention: res < 0 raises OSError(-res), a captured exception instance is
re-raised, otherwise the built object is returned.

Environment gating follows the repo convention. io_uring can legitimately be
absent -- kernel.io_uring_disabled, or a seccomp policy that blocks
io_uring_setup, both common in containers -- so those cases skip. Setting
TRUENAS_PYOS_REQUIRE_IO_URING turns the skip into a hard failure, which is what
CI does so coverage is never silently lost.
"""

import contextlib
import os
import select
import time

import pytest

from truenas_os import Uring


# ── availability ─────────────────────────────────────────────────────────────

# EPERM/ENOSYS/EACCES mean io_uring is unavailable or blocked in this
# environment. EINVAL is deliberately NOT in this list: a rejected setup
# argument is a bug in the ring, not an environmental condition.
import errno

_UNAVAILABLE = (errno.EPERM, errno.ENOSYS, errno.EACCES)

_REQUIRE_ENV = 'TRUENAS_PYOS_REQUIRE_IO_URING'


def _probe_io_uring():
    """Return None when io_uring works, or a reason string when it does not."""
    try:
        r = Uring(entries=8, files=8)
    except OSError as exc:
        if exc.errno in _UNAVAILABLE:
            return 'io_uring unavailable: %s' % exc
        raise
    r.close()
    return None


_UNAVAILABLE_REASON = _probe_io_uring()

if _UNAVAILABLE_REASON is not None and os.environ.get(_REQUIRE_ENV):
    raise RuntimeError(
        '%s is set but %s' % (_REQUIRE_ENV, _UNAVAILABLE_REASON)
    )

requires_io_uring = pytest.mark.skipif(
    _UNAVAILABLE_REASON is not None,
    reason=_UNAVAILABLE_REASON or '',
)


# ── the driver ───────────────────────────────────────────────────────────────
#
# reap() drains the *whole* completion queue, so a caller waiting on a subset of
# tokens must not throw the rest away. So completions are pumped into a store and
# claimed by token.

def _drain(ring, tokens, timeout=5.0):
    """Poll + reap until every token in `tokens` has a completion.

    Returns dict token -> (res, result). Raises TimeoutError rather than
    hanging if a completion never arrives.
    """
    store = {}
    deadline = time.monotonic() + timeout
    while not all(tok in store for tok in tokens):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            missing = [tok for tok in tokens if tok not in store]
            raise TimeoutError('completions never arrived: %r' % (missing,))
        select.select([ring.ringfd()], [], [], remaining)
        for (tok, res, result) in ring.reap():
            store[tok] = (res, result)
    return store


def _unwrap(res, result):
    """res < 0 raises OSError(-res); a captured exception is re-raised."""
    if res < 0:
        raise OSError(-res, os.strerror(-res))
    if isinstance(result, BaseException):
        raise result
    return result


def drive_raw(ring, handles, linked=False, timeout=5.0):
    """Submit prepared handles; return [(res, result), ...] in order (no raise)."""
    tokens = ring.submit(handles, linked)
    store = _drain(ring, tokens, timeout)
    return [store[tok] for tok in tokens]


def drive(ring, handles, linked=False, timeout=5.0):
    """Submit prepared handles, drive to completion, return unwrapped results."""
    return [_unwrap(res, result)
            for (res, result) in drive_raw(ring, handles, linked, timeout)]


def drive_one(ring, handle, timeout=5.0):
    """Submit one prepared handle, drive to completion, return its result."""
    (result,) = drive(ring, [handle], timeout=timeout)
    return result


# ── ring helpers ─────────────────────────────────────────────────────────────

def open_dir(path):
    """An O_PATH anchor dirfd for `path`; the caller owns it (os.close).

    A dirfd is just a real directory fd the consumer opens; this is the one-line
    stdlib equivalent the tests use.
    """
    return os.open(os.fspath(path), os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)


@contextlib.contextmanager
def ring(entries=64, files=64, cq_entries=0):
    """A Ring, closed on exit."""
    r = Uring(entries=entries, files=files, cq_entries=cq_entries)
    try:
        yield r
    finally:
        r.close()


@contextlib.contextmanager
def ring_env(tmp_path, entries=64, files=64):
    """A Ring plus an O_PATH anchor dirfd on tmp_path.

    Yields (ring, dirfd). `dirfd` is an O_PATH directory fd from open_dir(),
    closed on exit.
    """
    with ring(entries=entries, files=files) as r:
        dirfd = open_dir(tmp_path)
        try:
            yield r, dirfd
        finally:
            os.close(dirfd)


def write_file(r, dirfd, name, data):
    """Create `name` under `dirfd` containing `data`, via the ring."""
    fh = drive_one(r, r.prep_openat2(dirfd, name,
                                     os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644))
    written = 0
    while written < len(data):
        n = drive_one(r, r.prep_pwrite(fh, data[written:], written))
        assert n > 0, 'short write made no progress'
        written += n
    drive_one(r, r.prep_close(fh))
    return written


def read_file(r, dirfd, name, size=4096):
    """Read `name` under `dirfd` via the ring and return the bytes."""
    fh = drive_one(r, r.prep_openat2(dirfd, name, os.O_RDONLY))
    buf = bytearray(size)
    n = drive_one(r, r.prep_pread(fh, buf, 0))
    drive_one(r, r.prep_close(fh))
    return bytes(buf[:n])
