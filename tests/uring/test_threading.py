# SPDX-License-Identifier: LGPL-3.0-or-later
"""Concurrency: many threads may submit/cancel while a single thread reaps.

The submission queue is serialized by an internal PyMutex; the pool, the
inflight counter and the single-consumer completion queue ride the GIL. close()
is fail-closed against a racing submit. These tests hammer those paths from
multiple threads and assert no crash, no lost completions, and clean rejection.
"""

import os
import select
import threading
import time

import pytest

from truenas_os import Uring

from .conftest import open_dir, requires_io_uring, ring_env


@requires_io_uring
def test_concurrent_submit_single_reap(tmp_path):
    """Many threads submit concurrently while one thread reaps; every completion
    is accounted for and nothing corrupts the ring."""
    (tmp_path / 'f').write_bytes(b'x')
    n_threads = 8
    per = 100
    total = n_threads * per
    with ring_env(tmp_path, entries=256) as (r, dirfd):
        seen = []
        errors = []
        start = threading.Barrier(n_threads + 1)

        def worker():
            try:
                start.wait()
                done = 0
                while done < per:
                    try:
                        # statx: no fd involved, so pure submit contention
                        r.submit([r.prep_statx(dirfd, b'f')])
                        done += 1
                    except BlockingIOError:
                        time.sleep(0)          # pool full: let the reaper catch up
            except Exception as e:
                errors.append(e)

        def reaper():
            start.wait()
            deadline = time.monotonic() + 30
            while len(seen) < total and time.monotonic() < deadline:
                select.select([r.ringfd()], [], [], 0.2)
                seen.extend(r.reap())

        workers = [threading.Thread(target=worker) for _ in range(n_threads)]
        rt = threading.Thread(target=reaper)
        rt.start()
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=30)
        rt.join(timeout=35)

        assert not errors, errors
        assert len(seen) == total
        assert r.inflight == 0
        assert all(res == 0 for (_ud, res, _result) in seen)


@requires_io_uring
def test_close_races_concurrent_submits(tmp_path):
    """close() while other threads hammer submit(): fail-closed, never a crash.
    A racing submit gets ValueError (closed) or BlockingIOError (pool full)."""
    (tmp_path / 'f').write_bytes(b'x')
    r = Uring(entries=128)
    dirfd = open_dir(str(tmp_path))
    errors = []
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                r.submit([r.prep_statx(dirfd, b'f')])
            except (ValueError, BlockingIOError):
                pass                           # closed / pool full -- expected
            except Exception as e:
                errors.append(e)
                return

    workers = [threading.Thread(target=worker) for _ in range(4)]
    for w in workers:
        w.start()
    time.sleep(0.05)                           # let them get going
    r.close()                                  # fence + teardown under concurrency
    stop.set()
    for w in workers:
        w.join(timeout=10)
    os.close(dirfd)

    assert not errors, errors
    assert r.closed is True
    with pytest.raises(ValueError):
        r.submit([])                           # cleanly rejected after close


@requires_io_uring
def test_callback_offloads_to_worker_that_submits_back(tmp_path):
    """The motivating pattern: a completion callback hands work to another thread
    which submits a follow-up op back into the ring while the reactor reaps."""
    (tmp_path / 'f').write_bytes(b'data')
    with ring_env(tmp_path) as (r, dirfd):
        done = threading.Event()
        results = []
        threads = []

        def follow_up():
            # runs on a worker thread: submit a second op back into the ring while
            # the reactor thread is reaping.
            r.submit([r.prep_statx(dirfd, b'f',
                                   callback=lambda c: (results.append(c),
                                                       done.set()))])

        def on_first(completion):
            results.append(completion)         # fired on the reaper thread
            t = threading.Thread(target=follow_up)
            threads.append(t)
            t.start()

        r.submit([r.prep_statx(dirfd, b'f', callback=on_first)])
        deadline = time.monotonic() + 15
        while not done.is_set() and time.monotonic() < deadline:
            select.select([r.ringfd()], [], [], 0.2)
            r.reap()
        for t in threads:
            t.join(timeout=5)

        assert done.is_set()
        assert len(results) == 2               # first op + the follow-up
