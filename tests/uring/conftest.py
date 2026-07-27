# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Fixtures and helpers for truenas_os.uring tests.

Async test bodies are driven with asyncio.run() from ordinary sync test
functions rather than pytest-asyncio: that plugin is not in the Debian
archive, and CI installs only python3-pytest, so depending on it would make
the package untestable where it is actually built.

Environment gating follows the repo convention. io_uring can legitimately be
absent -- kernel.io_uring_disabled, or a seccomp policy that blocks
io_uring_setup, both common in containers -- so those cases skip. Setting
TRUENAS_PYOS_REQUIRE_IO_URING turns the skip into a hard failure, which is
what CI does so coverage is never silently lost.
"""

import asyncio
import contextlib
import errno
import functools
import os

import pytest

from truenas_os import uring


# ── availability ─────────────────────────────────────────────────────────────

# EPERM/ENOSYS/EACCES mean io_uring is unavailable or blocked in this
# environment. EINVAL is deliberately NOT in this list: a rejected setup
# argument is a bug in the reactor, not an environmental condition, and must
# never be silently skipped.
_UNAVAILABLE = (errno.EPERM, errno.ENOSYS, errno.EACCES)

_REQUIRE_ENV = 'TRUENAS_PYOS_REQUIRE_IO_URING'


def _probe_io_uring():
    """Return None when io_uring works, or a reason string when it does not."""
    try:
        r = uring.Reactor(entries=8, files=8)
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


def require_query():
    """Return a QueryResult, or skip -- unless CI says it must be there.

    IORING_REGISTER_QUERY landed in 6.18, so absence is legitimate on an older
    kernel. But CI runs on the 6.18 TrueNAS kernel and sets
    TRUENAS_PYOS_REQUIRE_IO_URING; there a None means the query path
    regressed, and skipping would hide it.
    """
    q = uring.query()
    if q is None:
        if os.environ.get(_REQUIRE_ENV):
            pytest.fail(
                '%s is set but query() returned None -- '
                'IORING_REGISTER_QUERY is expected on this kernel'
                % _REQUIRE_ENV
            )
        pytest.skip('IORING_REGISTER_QUERY not available (pre-6.18 kernel)')
    return q


# ── async driver ─────────────────────────────────────────────────────────────

def asyncio_test(fn):
    """Run an async test body on a fresh event loop."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ── reactor helpers ──────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def reactor(entries=64, files=64, cq_entries=0):
    """An attached reactor, closed on exit.

    attach() must run on the loop's own thread, so the reactor is built inside
    the async body rather than in a sync fixture.
    """
    loop = asyncio.get_running_loop()
    r = uring.Reactor(entries=entries, files=files, cq_entries=cq_entries)
    try:
        r.attach(loop)
        yield r
    finally:
        r.close()


@contextlib.asynccontextmanager
async def reactor_env(tmp_path, entries=64, files=64):
    """An attached reactor plus a self personality and an anchor on tmp_path.

    Yields (reactor, personality, anchor). register_self() needs no privilege,
    so this is the unprivileged path through the same per-SQE stamp machinery
    a brokered cross-user identity would use.
    """
    async with reactor(entries=entries, files=files) as r:
        pers = r.register_self()
        anchor = uring.Anchor(str(tmp_path))
        try:
            yield r, pers, anchor
        finally:
            anchor.close()


async def write_file(r, pers, anchor, name, data):
    """Create `name` under `anchor` containing `data`, via the reactor."""
    fh = await r.open(pers, anchor, name,
                      flags=os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode=0o644)
    written = 0
    while written < len(data):
        n = await r.pwrite(pers, fh, data[written:], written)
        assert n > 0, 'short write made no progress'
        written += n
    await r.close_file(fh)
    return written


async def read_file(r, pers, anchor, name, size=4096):
    """Read `name` under `anchor` via the reactor and return the bytes."""
    fh = await r.open(pers, anchor, name, flags=os.O_RDONLY)
    buf = bytearray(size)
    n = await r.pread(pers, fh, buf, 0)
    await r.close_file(fh)
    return bytes(buf[:n])
