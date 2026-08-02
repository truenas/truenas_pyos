# SPDX-License-Identifier: LGPL-3.0-or-later
"""Uring lifecycle: construction, close, ringfd, and submit/reap availability."""

import os

import pytest

import truenas_os
from truenas_os import Uring, UringOp

from .conftest import drive_one, open_dir, requires_io_uring, ring


# ── module surface ───────────────────────────────────────────────────────────

def test_uring_surface_is_minimal():
    """uring is exposed as exactly two top-level types on truenas_os -- Uring and
    UringOp -- with no capability prober (query/supported_ops/QueryResult), no
    opcode or flag constants, no descriptor/personality/socket surface, and no
    `uring` submodule."""
    assert isinstance(truenas_os.Uring, type)
    assert isinstance(truenas_os.UringOp, type)
    for gone in ('uring', 'Ring', 'RingOp', 'QueryResult'):
        assert not hasattr(truenas_os, gone), gone


# ── construction ─────────────────────────────────────────────────────────────

@requires_io_uring
def test_construct_and_close():
    r = Uring(entries=16)
    assert r.closed is False
    assert r.inflight == 0
    assert r.ringfd() >= 0
    r.close()
    assert r.closed is True


@requires_io_uring
def test_close_is_idempotent():
    r = Uring(entries=8)
    r.close()
    r.close()
    assert r.closed is True


@requires_io_uring
def test_ringfd_after_close_raises():
    r = Uring(entries=8)
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.ringfd()


@pytest.mark.parametrize('kwargs', [
    {'entries': 0},
])
def test_zero_sizes_rejected(kwargs):
    with pytest.raises(ValueError):
        Uring(**kwargs)


@pytest.mark.parametrize('kwargs', [
    {'entries': -1},
    {'entries': 2 ** 32},          # just past the u32 range
    {'entries': 2 ** 32 + 5},
    {'cq_entries': 2 ** 40},
])
def test_out_of_range_sizes_rejected(kwargs):
    """A negative or out-of-range size raises rather than being accepted."""
    with pytest.raises(ValueError):
        Uring(**kwargs)


@requires_io_uring
def test_keyword_only_construction():
    """The size arguments are keyword-only."""
    with pytest.raises(TypeError):
        Uring(8, 8)                # positional -> TypeError


# ── prepare / submit / reap availability ─────────────────────────────────────

@requires_io_uring
def test_prep_after_close_rejected(tmp_path):
    r = Uring(entries=8)
    dirfd = open_dir(str(tmp_path))
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.prep_openat2(dirfd, b'x', os.O_RDONLY)
    os.close(dirfd)


@requires_io_uring
def test_submit_after_close_rejected():
    r = Uring(entries=8)
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.submit([])
    with pytest.raises(ValueError, match='closed'):
        r.submit([], linked=True)


@requires_io_uring
def test_empty_submit_returns_empty_tuple():
    with ring() as r:
        assert r.submit([]) == ()
        assert r.submit([], linked=True) == ()
        assert r.inflight == 0


@requires_io_uring
def test_reap_on_idle_ring_is_harmless():
    """io_uring_poll() may report EPOLLIN with an empty CQ ring.

    A reap that finds nothing is normal, not an error.
    """
    with ring() as r:
        assert r.reap() == []
        assert r.reap() == []


@requires_io_uring
def test_reap_after_close_returns_empty():
    r = Uring(entries=8)
    r.close()
    assert r.reap() == []


@requires_io_uring
def test_reap_negative_max_rejected():
    """A negative cap raises ValueError rather than being accepted."""
    with ring() as r:
        with pytest.raises(ValueError):
            r.reap(-1)


@requires_io_uring
def test_cancel_after_close_rejected():
    r = Uring(entries=8)
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.cancel(0)


@requires_io_uring
def test_reap_returns_plain_tuples(tmp_path):
    """A completion is a plain (token, res, result) 3-tuple, not a
    struct-sequence."""
    (tmp_path / 'f').write_bytes(b'abcd')
    r = Uring(entries=8)
    dirfd = open_dir(str(tmp_path))
    try:
        (ud,) = r.submit([r.prep_openat2(dirfd, b'f', os.O_RDONLY)])
        import select
        comp = None
        while comp is None:
            select.select([r.ringfd()], [], [], 5.0)
            for c in r.reap():
                if c[0] == ud:
                    comp = c
        assert type(comp) is tuple
        assert len(comp) == 3
        token, res, result = comp
        assert token == ud
        assert res >= 0                 # an open's raw result is the new fd
        assert isinstance(result, int)  # ... and result is that same fd
        assert result == res
        drive_one(r, r.prep_close(result))
    finally:
        r.close()
        os.close(dirfd)


# ── handle type ──────────────────────────────────────────────────────────────

@requires_io_uring
def test_ring_op_handle_is_opaque_and_not_constructible():
    """UringOp cannot be instantiated from Python and exposes no members."""
    with pytest.raises(TypeError):
        UringOp()


@requires_io_uring
def test_prep_returns_ring_op_handle(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    r = Uring(entries=8)
    dirfd = open_dir(str(tmp_path))
    try:
        handle = r.prep_openat2(dirfd, b'f', os.O_RDONLY)
        assert isinstance(handle, UringOp)
        drive_one(r, handle)
    finally:
        r.close()
        os.close(dirfd)
