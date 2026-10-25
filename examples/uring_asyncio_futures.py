#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""An awaitable futures facade over truenas_os.Uring.

Where uring_callback_chain.py drives the ring with per-op completion
callbacks (each callback submitting the next op), this example wraps the
same surface in asyncio Futures so ring operations compose with ``await``
and ``asyncio.gather`` and failed completions surface as OSError instead
of hand-checked ``res < 0`` codes.

The whole event-loop integration is three ideas:

  1. the ring fd polls readable while completions are pending, so one
     ``loop.add_reader(ring.ringfd(), ...)`` drives everything; completion
     callbacks fire inside ``reap()`` on the loop thread and can resolve
     Futures directly;
  2. each op's Future rides into its completion callback as
     ``private_data``;
  3. prep_* handles are STAGED, and one ``call_soon``-deferred flush
     submits everything staged in the same event-loop tick as a single
     batch -- so a burst of concurrent awaiters (a ``gather``) costs one
     ``io_uring_enter``, not one per op.

Op starters are plain functions returning a bare Future.  That is
deliberate: a coroutine layer would leave the prepped ``UringOp`` handle
as a local of a suspendable frame, and a cancelled task retains its
CancelledError traceback -- pinning the frame, the handle, and therefore
the op's slot accounting.  Plain functions have no frame to pin.
"""

import asyncio
import os
import sys
import tempfile

from truenas_os import Uring

NBLOCKS = 8
BLOCKSZ = 4096


class AsyncUring:
    """Futures facade over one Uring, bound to the running event loop."""

    def __init__(self, *, entries=1024):
        self._loop = asyncio.get_running_loop()
        self._ring = Uring(entries=entries)
        self._staged = []            # [(UringOp, Future)] awaiting flush
        self._flush_scheduled = False
        # 1. reap exactly when completions are pending
        self._loop.add_reader(self._ring.ringfd(), self._on_readable)

    # -- completion side ----------------------------------------------------

    def _on_readable(self):
        # Every op carries a callback, so reap()'s return list is empty
        # and _complete() below does all the delivery.
        self._ring.reap(0)

    def _complete(self, comp, fut):
        # 2. the completion tuple meets its Future again
        token, res, result = comp
        if fut.done():               # cancelled while in flight
            return
        if res < 0:
            fut.set_exception(OSError(-res, os.strerror(-res)))
        else:
            fut.set_result(result)   # fd / nbytes / StatxResult / None

    # -- submission side ----------------------------------------------------

    def _start(self, prepfn):
        """Begin one op: prep now, submit at the end of this loop tick."""
        fut = self._loop.create_future()
        handle = prepfn(self._complete, fut)
        self._staged.append((handle, fut))
        if not self._flush_scheduled:
            # 3. one submit per loop tick batches every op staged in it
            self._flush_scheduled = True
            self._loop.call_soon(self._flush)
        return fut

    def _flush(self):
        self._flush_scheduled = False
        staged, self._staged = self._staged, []
        # A Future cancelled before submission: dropping its unsubmitted
        # handle reclaims the op slot.
        live = [(h, f) for h, f in staged if not f.done()]
        if not live:
            return
        try:
            self._ring.submit([h for h, _ in live])
        except BaseException as exc:  # all-or-nothing: nothing submitted
            for _, fut in live:
                if not fut.done():
                    fut.set_exception(exc)

    # -- ops ----------------------------------------------------------------

    def openat2(self, dirfd, path, flags, mode=0):
        """Resolves to the new process fd the open created."""
        return self._start(lambda cb, fut: self._ring.prep_openat2(
            dirfd, path, flags, mode, callback=cb, private_data=fut))

    def preadv2(self, fd, buffers, offset=0, flags=0):
        return self._start(lambda cb, fut: self._ring.prep_preadv2(
            fd, buffers, offset, flags, cb, fut))

    def pwritev2(self, fd, buffers, offset=0, flags=0):
        return self._start(lambda cb, fut: self._ring.prep_pwritev2(
            fd, buffers, offset, flags, cb, fut))

    def statx(self, dirfd, path, flags=0):
        return self._start(lambda cb, fut: self._ring.prep_statx(
            dirfd, path, flags, callback=cb, private_data=fut))

    def close(self, fd):
        """Close `fd` via the ring; as with os.close, order it last."""
        return self._start(lambda cb, fut: self._ring.prep_close(
            fd, cb, fut))

    # -- lifecycle ----------------------------------------------------------

    async def aclose(self):
        if self._staged:
            self._flush()
        while self._ring.inflight:
            await asyncio.sleep(0)   # reader keeps reaping between ticks
        self._loop.remove_reader(self._ring.ringfd())
        self._ring.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


async def run_demo():
    with tempfile.TemporaryDirectory() as tmp:
        # openat2 resolves relative to an anchor directory fd (there is no
        # AT_FDCWD or absolute-path surface).
        dirfd = os.open(tmp, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            async with AsyncUring(entries=64) as ring:
                fd = await ring.openat2(
                    dirfd, b'demo.bin',
                    os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
                print('opened demo.bin at fd %d' % fd)

                # One vectored gather-write moves all the blocks in a single
                # operation; a burst of concurrent single-block writes staged
                # in the same tick would also submit as one batch.
                blocks = [bytes([0x41 + i]) * BLOCKSZ for i in range(NBLOCKS)]
                written = await ring.pwritev2(fd, blocks, 0)
                print('pwritev2 of %d blocks -> %d bytes' % (NBLOCKS, written))

                st = await ring.statx(dirfd, b'demo.bin')
                print('statx: size %d' % st.stx_size)

                bufs = [bytearray(BLOCKSZ) for _ in range(NBLOCKS)]
                await ring.preadv2(fd, bufs, 0)
                if [bytes(b) for b in bufs] != blocks:
                    print('read-back MISMATCH', file=sys.stderr)
                    return 1
                print('read back %d blocks, contents verified' % NBLOCKS)

                await ring.close(fd)
        finally:
            os.close(dirfd)
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(run_demo()))
