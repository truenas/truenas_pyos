# SPDX-License-Identifier: LGPL-3.0-or-later
"""Reactor lifecycle, event-loop attachment, and personality registration."""

import asyncio
import threading

import pytest

from truenas_os import uring

from .conftest import asyncio_test, reactor, requires_io_uring


# ── construction ─────────────────────────────────────────────────────────────

@requires_io_uring
def test_construct_and_close():
    r = uring.Reactor(entries=16, files=16)
    assert r.closed is False
    assert r.attached is False
    assert r.inflight == 0
    assert r.fileno() >= 0
    r.close()
    assert r.closed is True


@requires_io_uring
def test_close_is_idempotent():
    r = uring.Reactor(entries=8, files=8)
    r.close()
    r.close()
    assert r.closed is True


@requires_io_uring
def test_fileno_after_close_raises():
    r = uring.Reactor(entries=8, files=8)
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.fileno()


@pytest.mark.parametrize('kwargs', [
    {'entries': 0},
    {'files': 0},
])
def test_zero_sizes_rejected(kwargs):
    with pytest.raises(ValueError):
        uring.Reactor(**kwargs)


def test_entries_bounded_by_user_data_slot_field():
    """The user_data slot field is 24 bits, so the op table cannot exceed it."""
    with pytest.raises(ValueError, match='24 bits'):
        uring.Reactor(entries=0x1000000)


# ── attachment ───────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_attach_and_detach():
    loop = asyncio.get_running_loop()
    r = uring.Reactor(entries=8, files=8)
    try:
        r.attach(loop)
        assert r.attached is True
        r.detach()
        assert r.attached is False
        r.detach()                      # idempotent
    finally:
        r.close()


@requires_io_uring
@asyncio_test
async def test_double_attach_rejected():
    loop = asyncio.get_running_loop()
    async with reactor() as r:
        with pytest.raises(RuntimeError, match='already attached'):
            r.attach(loop)


@requires_io_uring
@asyncio_test
async def test_submission_without_attach_rejected(tmp_path):
    r = uring.Reactor(entries=8, files=8)
    try:
        pers = r.register_self()
        anchor = uring.Anchor(str(tmp_path))
        with pytest.raises(RuntimeError, match='not attached'):
            await r.statx(pers, anchor, b'x')
        anchor.close()
    finally:
        r.close()


@requires_io_uring
@asyncio_test
async def test_reap_on_idle_ring_is_harmless():
    """io_uring_poll() may report EPOLLIN with an empty CQ ring.

    The kernel says so explicitly: it does not flush the overflow list from
    poll. A wakeup that finds nothing is normal, not an error.
    """
    async with reactor() as r:
        assert r._reap() == 0
        assert r._reap() == 0


@requires_io_uring
@asyncio_test
async def test_attach_after_close_rejected():
    loop = asyncio.get_running_loop()
    r = uring.Reactor(entries=8, files=8)
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.attach(loop)


# ── personalities ────────────────────────────────────────────────────────────

@requires_io_uring
@asyncio_test
async def test_register_self_needs_no_privilege():
    """Registering your own credentials is unprivileged.

    This is why the whole per-SQE stamp path is testable without root.
    """
    async with reactor() as r:
        pers = r.register_self()
        assert pers.id != 0


@requires_io_uring
@asyncio_test
async def test_personality_ids_start_at_one_and_are_not_reused():
    """Pins the kernel's XA_FLAGS_ALLOC1 cyclic-allocation contract.

    Id 0 is never handed out, which is what lets sqe->personality == 0 mean
    'ambient credentials' with no possibility of collision. Allocation is
    cyclic, so a freed id is not immediately reissued.
    """
    async with reactor() as r:
        first = r.register_self()
        assert first.id == 1

        second = r.register_self()
        third = r.register_self()
        assert second.id == 2
        assert third.id == 3

        second.unregister()
        fourth = r.register_self()
        assert fourth.id != second.id, 'freed id was immediately reused'
        assert fourth.id != 0


@requires_io_uring
@asyncio_test
async def test_unregister_is_idempotent():
    async with reactor() as r:
        pers = r.register_self()
        pers.unregister()
        assert pers.id == 0
        pers.unregister()
        assert pers.id == 0


@requires_io_uring
@asyncio_test
async def test_personality_repr():
    async with reactor() as r:
        pers = r.register_self()
        assert 'id=' in repr(pers)
        pers.unregister()
        assert 'unregistered' in repr(pers)


@requires_io_uring
@asyncio_test
async def test_register_from_another_thread_succeeds():
    """The ring must stay registrable from a task other than the submitter.

    A SINGLE_ISSUER ring sets ctx->submitter_task, after which
    io_uring_register() from any other task fails with EEXIST. The credential
    broker is a separate process, so it would be locked out entirely. This
    pins that the reactor does not set that flag (nor DEFER_TASKRUN, which
    requires it).
    """
    async with reactor() as r:
        result = {}

        def worker():
            try:
                result['pers'] = r.register_self()
            except OSError as exc:
                result['errno'] = exc.errno

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert 'errno' not in result, (
            'cross-task register failed with errno %r -- the ring must not '
            'use IORING_SETUP_SINGLE_ISSUER' % result.get('errno')
        )
        assert result['pers'].id != 0


@requires_io_uring
@asyncio_test
async def test_register_after_close_rejected():
    r = uring.Reactor(entries=8, files=8)
    r.close()
    with pytest.raises(ValueError, match='closed'):
        r.register_self()
