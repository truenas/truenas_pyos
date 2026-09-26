# truenas_threadstat

Per-thread CPU usage like `top -H` shows, read through a BPF task iterator
instead of `/proc`. One pass over all threads costs about 0.6 µs per thread,
compared to about 20 µs per thread for a `/proc` walk.

## Requirements

- The caller must be root or have `CAP_BPF` and `CAP_PERFMON`.
- The kernel must be built with `CONFIG_DEBUG_INFO_BTF`.
- Building needs `clang`, `bpftool`, and `libbpf-dev`, and running needs `libbpf1`.

## Usage

```python
import time
import truenas_threadstat

with truenas_threadstat.ThreadSampler() as sampler:
    while True:
        time.sleep(1)
        sample = sampler.sample()
        for t in sorted(sample.threads, key=lambda t: t.cpu_ns, reverse=True):
            pct = 100 * t.cpu_ns / sample.elapsed_ns
            print(t.tid, t.name, t.state, f'{pct:.1f}')
```

## `ThreadSampler()`

Loads the BPF program and takes a baseline snapshot. Loading takes about 30 ms.
Call `close()` or use a `with` block to unload it.

### `sample(*, include_idle=False)`

Returns a `ThreadSample` for the time since the previous call, or since the
sampler was created. Threads that used no CPU time are left out unless
`include_idle` is true. For a thread that started during the interval, the
interval fields cover its whole life.

The object is not safe to share between threads. A concurrent call raises
`RuntimeError`.

## `ThreadSample`

| Field | Description |
|---|---|
| `elapsed_ns` | Nanoseconds since the previous sample |
| `threads` | List of `ThreadStat` sorted by `tid` |

## `ThreadStat`

| Field | Description |
|---|---|
| `tid` | Thread ID |
| `tgid` | Process ID |
| `name` | Thread name |
| `process_name` | Name of the process main thread |
| `state` | State letter as shown by `top` |
| `cpu` | CPU the thread last ran on |
| `cpu_ns` | CPU time used during the interval |
| `exited_cpu_ns` | CPU time used during the interval by threads of the process that exited, set only on the main thread row |
| `wait_ns` | Time spent waiting for a CPU during the interval |
| `rss` | Process resident memory in bytes, or `None` if the process exited |

`rss` matches the `RES` column of `top`. It is read from `/proc/PID/statm` once
per process in the sample, because the kernel keeps it in per-CPU counters that
BPF cannot sum exactly. A normal sample reads it only for processes that used
CPU time, but `include_idle=True` reads it for every process.

For per-process CPU time, add `cpu_ns` and `exited_cpu_ns` over rows with the
same `tgid`. This matches the process view of `top`. The main thread row is
included whenever `exited_cpu_ns` is nonzero. Processes that start and exit
between samples are not reported.
