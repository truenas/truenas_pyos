# SPDX-License-Identifier: LGPL-3.0-or-later
"""
The six implemented operations, oracled against ordinary blocking I/O.

Every operation is anchored and personality-stamped. There is no AT_FDCWD
surface and no absolute-path surface to test, by design.
"""

import asyncio
import os

import pytest

import truenas_os
from truenas_os import uring

from .conftest import (
    asyncio_test,
    read_file,
    reactor_env,
    requires_io_uring,
    write_file,
)


# ── open ─────────────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_open_returns_fixedfile_not_fd(tmp_path):
    """An explicit-index install materialises no process file descriptor."""
    (tmp_path / 'f').write_bytes(b'data')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        assert isinstance(fh, uring.FixedFile)
        assert fh.closed is False
        assert isinstance(fh.slot, int)
        assert 'FixedFile' in repr(fh)
        await r.close_file(fh)
        assert fh.closed is True


@requires_io_uring
@asyncio_test
async def test_open_missing_file(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(FileNotFoundError):
            await r.open(pers, anchor, b'nope', flags=os.O_RDONLY)


@requires_io_uring
@asyncio_test
async def test_open_multi_component_path_allowed(tmp_path):
    """OPENAT2 is the one operation that may resolve multiple components.

    RESOLVE_BENEATH confines it in-kernel, which the *at opcodes cannot do.
    """
    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'f').write_bytes(b'nested')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        assert await read_file(r, pers, anchor, b'sub/f') == b'nested'


@requires_io_uring
@asyncio_test
async def test_resolve_beneath_blocks_escape(tmp_path):
    sub = tmp_path / 'sub'
    sub.mkdir()
    async with reactor_env(sub) as (r, pers, anchor):
        with pytest.raises(OSError) as exc:
            await r.open(pers, anchor, b'../escaped', flags=os.O_RDONLY)
        # RESOLVE_BENEATH reports an attempted escape as EXDEV.
        assert exc.value.errno == 18


@requires_io_uring
@asyncio_test
async def test_open_rejects_o_cloexec(tmp_path):
    """O_CLOEXEC is invalid with a fixed-file install; reject it up front."""
    (tmp_path / 'f').write_bytes(b'x')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(ValueError, match='O_CLOEXEC'):
            await r.open(pers, anchor, b'f',
                         flags=os.O_RDONLY | os.O_CLOEXEC)


@requires_io_uring
@asyncio_test
async def test_open_creat_creates_file(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'new',
                          flags=os.O_CREAT | os.O_WRONLY, mode=0o600)
        await r.close_file(fh)
    assert (tmp_path / 'new').exists()


@requires_io_uring
@asyncio_test
async def test_failed_open_returns_its_file_slot(tmp_path):
    """A failed open installed nothing, so its reserved slot must come back.

    Otherwise the fixed-file table leaks a slot per failure.
    """
    async with reactor_env(tmp_path, files=4) as (r, pers, anchor):
        for _ in range(50):             # far more than the table holds
            with pytest.raises(FileNotFoundError):
                await r.open(pers, anchor, b'missing', flags=os.O_RDONLY)
        # The table must still be usable.
        fh = await r.open(pers, anchor, b'ok',
                          flags=os.O_CREAT | os.O_WRONLY, mode=0o644)
        await r.close_file(fh)


# ── pread / pwrite ───────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_write_then_read_round_trip(tmp_path):
    payload = b'hello io_uring\n'
    async with reactor_env(tmp_path) as (r, pers, anchor):
        assert await write_file(r, pers, anchor, b'f', payload) == len(payload)
        assert (tmp_path / 'f').read_bytes() == payload
        assert await read_file(r, pers, anchor, b'f') == payload


@requires_io_uring
@asyncio_test
async def test_pread_is_positional(tmp_path):
    """A registered file exposes no file position: every op takes an offset."""
    (tmp_path / 'f').write_bytes(b'0123456789')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(3)
        assert await r.pread(pers, fh, buf, 4) == 3
        assert bytes(buf) == b'456'
        # Re-reading the same offset yields the same bytes: no position moved.
        assert await r.pread(pers, fh, buf, 4) == 3
        assert bytes(buf) == b'456'
        await r.close_file(fh)


@requires_io_uring
@asyncio_test
async def test_pwrite_at_offset(tmp_path):
    (tmp_path / 'f').write_bytes(b'AAAAAAAAAA')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_WRONLY)
        assert await r.pwrite(pers, fh, b'ZZ', 4) == 2
        await r.close_file(fh)
    assert (tmp_path / 'f').read_bytes() == b'AAAAZZAAAA'


@requires_io_uring
@asyncio_test
async def test_pread_past_eof_returns_zero(tmp_path):
    (tmp_path / 'f').write_bytes(b'short')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(16)
        assert await r.pread(pers, fh, buf, 1000) == 0
        await r.close_file(fh)


@requires_io_uring
@asyncio_test
async def test_pread_accepts_memoryview(tmp_path):
    (tmp_path / 'f').write_bytes(b'abcdef')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        buf = bytearray(6)
        n = await r.pread(pers, fh, memoryview(buf), 0)
        await r.close_file(fh)
    assert bytes(buf[:n]) == b'abcdef'


@requires_io_uring
@asyncio_test
async def test_pread_rejects_readonly_buffer(tmp_path):
    """bytes is not writable, so it cannot be a read destination."""
    (tmp_path / 'f').write_bytes(b'abc')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        with pytest.raises((TypeError, BufferError)):
            await r.pread(pers, fh, b'immutable', 0)
        await r.close_file(fh)


# ── fsync ────────────────────────────────────────────────────────────────────

@requires_io_uring
@pytest.mark.parametrize('datasync', [False, True])
@asyncio_test
async def test_fsync(tmp_path, datasync):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f',
                          flags=os.O_CREAT | os.O_WRONLY, mode=0o644)
        await r.pwrite(pers, fh, b'durable', 0)
        assert await r.fsync(pers, fh, datasync=datasync) is None
        await r.close_file(fh)
    assert (tmp_path / 'f').read_bytes() == b'durable'


# ── statx ────────────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_statx_matches_truenas_os_statx(tmp_path):
    """The reactor builds StatxResult with truenas_os's own packing.

    Same shared object, so it is a direct call -- there is no second copy of
    the field list to drift.
    """
    target = tmp_path / 'f'
    target.write_bytes(b'0123456789')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        got = await r.statx(pers, anchor, b'f')

    ref = truenas_os.statx(str(target))
    assert isinstance(got, truenas_os.StatxResult)
    assert got.stx_size == ref.stx_size == 10
    assert got.stx_ino == ref.stx_ino
    assert got.stx_mode == ref.stx_mode
    assert got.stx_uid == ref.stx_uid
    assert got.stx_gid == ref.stx_gid


@requires_io_uring
@asyncio_test
async def test_statx_missing_entry(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(FileNotFoundError):
            await r.statx(pers, anchor, b'absent')


@requires_io_uring
@asyncio_test
async def test_statx_symlink_nofollow(tmp_path):
    (tmp_path / 'target').write_bytes(b'0123456789')
    (tmp_path / 'link').symlink_to(tmp_path / 'target')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        followed = await r.statx(pers, anchor, b'link')
        nofollow = await r.statx(pers, anchor, b'link',
                                 flags=truenas_os.AT_SYMLINK_NOFOLLOW)
    assert followed.stx_size == 10
    assert nofollow.stx_size != 10          # the link itself


# ── close ────────────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_close_frees_the_slot_for_reuse(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    async with reactor_env(tmp_path, files=2) as (r, pers, anchor):
        for _ in range(20):             # ten times the table size
            fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
            await r.close_file(fh)
        assert r.inflight == 0


@requires_io_uring
@asyncio_test
async def test_operations_on_closed_file_rejected(tmp_path):
    (tmp_path / 'f').write_bytes(b'x')
    async with reactor_env(tmp_path) as (r, pers, anchor):
        fh = await r.open(pers, anchor, b'f', flags=os.O_RDONLY)
        await r.close_file(fh)
        with pytest.raises(ValueError, match='closed'):
            await r.pread(pers, fh, bytearray(4), 0)


# ── personality argument validation ──────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_personality_is_mandatory(tmp_path):
    """There is deliberately no ambient-identity overload."""
    async with reactor_env(tmp_path) as (r, pers, anchor):
        with pytest.raises(TypeError, match='Personality'):
            await r.statx(None, anchor, b'x')


@requires_io_uring
@asyncio_test
async def test_unregistered_personality_rejected(tmp_path):
    async with reactor_env(tmp_path) as (r, pers, anchor):
        dead = r.register_self()
        dead.unregister()
        with pytest.raises(ValueError, match='unregistered'):
            await r.statx(dead, anchor, b'x')


@requires_io_uring
@asyncio_test
async def test_personality_from_another_reactor_rejected(tmp_path):
    """Personality ids index one ring's table and are not portable."""
    loop = asyncio.get_running_loop()
    async with reactor_env(tmp_path) as (r, pers, anchor):
        other = uring.Reactor(entries=8, files=8)
        try:
            other.attach(loop)
            foreign = other.register_self()
            with pytest.raises(ValueError, match='different Reactor'):
                await r.statx(foreign, anchor, b'x')
        finally:
            other.close()
