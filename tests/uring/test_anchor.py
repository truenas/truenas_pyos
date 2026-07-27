# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Anchor and Leaf -- the confinement primitives.

Leaf validation is load-bearing, not hygiene: the plain *at opcodes
(RENAMEAT, UNLINKAT, MKDIRAT, SYMLINKAT, LINKAT, STATX) honour no RESOLVE_*
flags at all, so a multi-component or '..' name would walk wherever it liked.
Only OPENAT2 can be confined in-kernel, via RESOLVE_BENEATH. These tests exist
to keep that boundary from eroding.
"""

import errno
import os

import pytest

from truenas_os import uring

from .conftest import asyncio_test, reactor_env, requires_io_uring


# ── Anchor lifecycle ─────────────────────────────────────────────────────────

def test_anchor_opens_directory(tmp_path):
    a = uring.Anchor(str(tmp_path))
    try:
        assert a.fileno() >= 0
        assert a.path == os.fsencode(str(tmp_path))
        assert a.closed is False
        assert 'Anchor' in repr(a)
    finally:
        a.close()


def test_anchor_accepts_str_bytes_and_pathlike(tmp_path):
    for arg in (str(tmp_path), os.fsencode(str(tmp_path)), tmp_path):
        a = uring.Anchor(arg)
        try:
            assert a.fileno() >= 0
        finally:
            a.close()


def test_anchor_close_is_idempotent(tmp_path):
    a = uring.Anchor(str(tmp_path))
    a.close()
    a.close()
    assert a.closed is True


def test_anchor_fileno_after_close_raises(tmp_path):
    a = uring.Anchor(str(tmp_path))
    a.close()
    with pytest.raises(ValueError, match='closed'):
        a.fileno()


def test_anchor_context_manager(tmp_path):
    with uring.Anchor(str(tmp_path)) as a:
        assert a.fileno() >= 0
    assert a.closed is True


def test_anchor_on_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        uring.Anchor(str(tmp_path / 'does-not-exist'))


def test_anchor_on_file_raises_notadirectory(tmp_path):
    f = tmp_path / 'regular'
    f.write_bytes(b'x')
    with pytest.raises(NotADirectoryError):
        uring.Anchor(str(f))


def test_anchor_does_not_follow_symlink(tmp_path):
    """Anchors open O_NOFOLLOW: a symlinked directory is refused.

    The errno depends on the flag combination -- O_NOFOLLOW alone reports
    ELOOP, but combined with O_DIRECTORY the kernel reports ENOTDIR. The
    invariant under test is the refusal, not which of the two it picks.
    """
    target = tmp_path / 'real'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(OSError) as exc:
        uring.Anchor(str(link))
    assert exc.value.errno in (errno.ELOOP, errno.ENOTDIR)

    # ...and the same directory opens fine by its real name.
    uring.Anchor(str(target)).close()


# ── Leaf validation ──────────────────────────────────────────────────────────

# statx is currently the only consumer of the leaf validator; the five
# directory-entry ops that also need it arrive with the M2 sweep.
BAD_LEAVES = [
    (b'a/b', 'single path component'),
    (b'sub/', 'single path component'),
    (b'/absolute', 'single path component'),
    (b'..', "'..'"),
    (b'.', "'.'"),
    (b'', 'empty'),
]


@requires_io_uring
@pytest.mark.parametrize('leaf,message', BAD_LEAVES)
@asyncio_test
async def test_leaf_rejected(tmp_path, leaf, message):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(ValueError, match=message):
            await r.statx(pers, anchor, leaf)


@requires_io_uring
@asyncio_test
async def test_leaf_rejects_embedded_nul(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(ValueError):
            await r.statx(pers, anchor, b'a\x00b')


@requires_io_uring
@asyncio_test
async def test_valid_leaf_accepted(tmp_path):
    (tmp_path / 'plain').write_bytes(b'data')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        st = await r.statx(pers, anchor, b'plain')
        assert st.stx_size == 4


@requires_io_uring
@asyncio_test
async def test_leaf_rejection_happens_before_submission(tmp_path):
    """A rejected leaf must not consume an op slot."""
    async with reactor_env(tmp_path) as (r, pers, anchor):
        for _ in range(200):          # far more than the op table holds
            with pytest.raises(ValueError):
                await r.statx(pers, anchor, b'a/b')
        assert r.inflight == 0


@requires_io_uring
@asyncio_test
async def test_closed_anchor_rejected(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        anchor.close()
        with pytest.raises(ValueError, match='closed'):
            await r.statx(pers, anchor, b'anything')


@requires_io_uring
@asyncio_test
async def test_non_anchor_rejected(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(TypeError, match='Anchor'):
            await r.statx(pers, str(tmp_path), b'x')
