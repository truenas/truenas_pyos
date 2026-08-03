#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Chained completion callbacks on truenas_os.Uring, driven by asyncio.

    openat2(O_CREAT) --> pwritev2 --> preadv2 --> print + close
                     ^            ^           ^
                     each arrow is a completion callback submitting the next op

The ring plugs into the event loop with one line:

    loop.add_reader(ring.ringfd(), ring.reap)

The ring fd polls readable whenever completions are pending, so the loop
calls reap() exactly when there is something to reap, and reap() fires the
completion callbacks.  Every step of the chain is prepared and submitted
from the previous step's callback; the terminal callback resolves a future
the coroutine awaits.  Nothing else drives the ring.

A callback'd op is *consumed* -- it never appears in reap()'s returned list
-- so the add_reader reap discards nothing here: every op carries a
callback.

The open's completion result is a regular process file descriptor, and the
later ops name that fd.  It is handed down the chain via private_data
(callbacks take the completion tuple, plus private_data when one was
given).
"""

import asyncio
import os
import sys
import tempfile

from truenas_os import Uring

PAYLOAD = b'hello from io_uring completion callbacks\n'
FILENAME = b'demo.txt'


async def run_chain():
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    errors = []

    def check(step, res):
        """res < 0 is a failed completion: record it and break the chain."""
        if res < 0:
            errors.append('%s failed: %s' % (step, os.strerror(-res)))
            return False
        return True

    def finish():
        if not done.done():
            done.set_result(None)

    with tempfile.TemporaryDirectory() as tmp:
        # openat2 resolves relative to an anchor directory fd (there is no
        # AT_FDCWD or absolute-path surface).
        dirfd = os.open(tmp, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        ring = Uring(entries=8)
        try:
            # The read lands here; the ring pins the buffer while the op is
            # in flight.
            buf = bytearray(4096)

            def on_open(completion):
                token, res, fd = completion
                if not check('open', res):
                    finish()        # a failed open leaves nothing to close
                    return
                print('opened %s at fd %d' % (FILENAME.decode(), fd))
                ring.submit([ring.prep_pwritev2(fd, [PAYLOAD],
                                                callback=on_write,
                                                private_data=fd)])

            def on_write(completion, fd):
                token, res, nwritten = completion
                if not check('write', res):
                    ring.submit([ring.prep_close(fd, on_close)])
                    return
                # A demo-sized write at offset 0 does not short-write; a
                # real caller would loop on nwritten like a plain write(2).
                print('wrote %d bytes' % nwritten)
                ring.submit([ring.prep_preadv2(fd, [buf],
                                               callback=on_read,
                                               private_data=fd)])

            def on_read(completion, fd):
                token, res, nread = completion
                if check('read', res):
                    print('read back: %r' % bytes(buf[:nread]))
                # Done with the file either way: close its fd.
                ring.submit([ring.prep_close(fd, on_close)])

            def on_close(completion):
                token, res, _ = completion
                check('close', res)
                finish()

            # The event loop reaps whenever the ring fd is readable; the
            # callbacks above fire from inside reap().
            loop.add_reader(ring.ringfd(), ring.reap)

            # Kick off the chain.
            ring.submit([ring.prep_openat2(
                dirfd, FILENAME, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600,
                callback=on_open)])

            await done
        finally:
            loop.remove_reader(ring.ringfd())
            ring.close()
            os.close(dirfd)

    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(run_chain()))
