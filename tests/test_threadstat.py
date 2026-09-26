# SPDX-License-Identifier: LGPL-3.0-or-later

import glob
import os
import select
import subprocess
import sys
import threading
import time

import pytest
import truenas_threadstat

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason='loading BPF programs requires root')

PAGE_SIZE = os.sysconf('SC_PAGESIZE')
CLK_TCK = os.sysconf('SC_CLK_TCK')
TICK_NS = 10_000_000

CHURN = '''
import ctypes, threading, time
libc = ctypes.CDLL(None)
def spin():
    libc.prctl(15, b"worker", 0, 0, 0)
    end = time.monotonic() + 0.2
    while time.monotonic() < end:
        pass
for _ in range(5):
    t = threading.Thread(target=spin)
    t.start()
    t.join()
print("done", flush=True)
time.sleep(600)
'''


def spawn_sleeper():
    proc = subprocess.Popen(['sleep', '600'])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with open(f'/proc/{proc.pid}/stat') as f:
            if f.read().startswith(f'{proc.pid} (sleep) S'):
                return proc
        time.sleep(0.01)
    proc.kill()
    raise TimeoutError('sleep did not start')


def find(sample, tid):
    return next((t for t in sample.threads if t.tid == tid), None)


def all_tids():
    return {int(p.rsplit('/', 1)[1]) for p in glob.glob('/proc/[0-9]*/task/[0-9]*')}


@pytest.fixture
def sampler():
    with truenas_threadstat.ThreadSampler() as s:
        yield s


@pytest.fixture
def sleeper():
    proc = spawn_sleeper()
    yield proc
    proc.kill()
    proc.wait()


@pytest.fixture
def spinner():
    proc = subprocess.Popen([sys.executable, '-c', 'while True: pass'])
    yield proc
    proc.kill()
    proc.wait()


def test_busy_thread(sampler, spinner):
    sampler.sample()
    time.sleep(0.5)
    sample = sampler.sample()
    t = find(sample, spinner.pid)
    assert t is not None
    assert t.tgid == spinner.pid
    assert t.state == 'R'
    assert 0.3 * sample.elapsed_ns < t.cpu_ns <= sample.elapsed_ns + TICK_NS
    assert t.rss > 0


def test_new_thread_covers_whole_life(sampler, sleeper):
    t = find(sampler.sample(), sleeper.pid)
    with open(f'/proc/{sleeper.pid}/schedstat') as f:
        runtime, run_delay = (int(x) for x in f.read().split()[:2])
    assert t is not None
    assert t.cpu_ns == runtime
    assert t.wait_ns == run_delay


def test_idle_thread_filtered(sampler, sleeper):
    sampler.sample()
    time.sleep(0.1)
    assert find(sampler.sample(), sleeper.pid) is None
    t = find(sampler.sample(include_idle=True), sleeper.pid)
    assert t is not None
    assert t.cpu_ns == t.wait_ns == 0


def test_fields_match_procfs(sampler, sleeper):
    t = find(sampler.sample(include_idle=True), sleeper.pid)
    with open(f'/proc/{sleeper.pid}/stat') as f:
        stat = f.read().rsplit(')', 1)[1].split()
    with open(f'/proc/{sleeper.pid}/statm') as f:
        resident = int(f.read().split()[1])
    assert t is not None
    assert t.tgid == sleeper.pid
    assert t.name == 'sleep'
    assert t.process_name == 'sleep'
    assert t.state == stat[0] == 'S'
    assert t.cpu == int(stat[36])
    assert t.rss == resident * PAGE_SIZE


def test_exited_threads_counted_once(sampler):
    proc = subprocess.Popen([sys.executable, '-c', CHURN], stdout=subprocess.PIPE)
    try:
        total = 0
        workers = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            done = bool(select.select([proc.stdout], [], [], 0.05)[0])
            for t in sampler.sample().threads:
                if t.tgid != proc.pid:
                    continue
                total += t.cpu_ns + t.exited_cpu_ns
                assert t.exited_cpu_ns == 0 or t.tid == t.tgid
                if t.tid != t.tgid:
                    workers += 1
                    assert t.name == 'worker'
            if done:
                break
        with open(f'/proc/{proc.pid}/comm') as f:
            comm = f.read().strip()
        with open(f'/proc/{proc.pid}/stat') as f:
            stat = f.read().rsplit(')', 1)[1].split()
        found = find(sampler.sample(include_idle=True), proc.pid)
    finally:
        proc.kill()
        proc.wait()

    expected = (int(stat[11]) + int(stat[12])) * 1_000_000_000 // CLK_TCK
    assert workers > 0
    assert found is not None and found.process_name == comm
    assert expected > 900_000_000
    assert abs(total - expected) < 5 * TICK_NS


def test_kernel_thread_rss(sampler):
    t = find(sampler.sample(include_idle=True), 2)
    assert t is not None
    assert t.name == 'kthreadd'
    assert t.rss == 0


def test_include_idle_lists_all_threads(sampler):
    before = all_tids()
    sample = sampler.sample(include_idle=True)
    after = all_tids()
    tids = [t.tid for t in sample.threads]
    assert (before & after) <= set(tids)
    assert os.getpid() in tids
    assert threading.get_native_id() in tids
    assert tids == sorted(tids)
    assert all(t.state in 'RSDTtXZPI' for t in sample.threads)


def test_elapsed(sampler):
    sampler.sample()
    time.sleep(0.2)
    assert 200_000_000 <= sampler.sample().elapsed_ns < 1_000_000_000


def test_close():
    s = truenas_threadstat.ThreadSampler()
    s.close()
    s.close()
    with pytest.raises(ValueError):
        s.sample()
    with pytest.raises(ValueError):
        with s:
            pass

    with truenas_threadstat.ThreadSampler() as s:
        pass
    with pytest.raises(ValueError):
        s.sample()


def test_bad_arguments(sampler):
    with pytest.raises(TypeError):
        truenas_threadstat.ThreadSampler(1)
    with pytest.raises(TypeError):
        sampler.sample(True)
