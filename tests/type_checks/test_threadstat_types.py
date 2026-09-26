"""Type-level tests for truenas_threadstat."""
import os
from typing import assert_type

import pytest
from truenas_threadstat import ThreadSample, ThreadSampler, ThreadStat

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason='loading BPF programs requires root')


def test_sample_types() -> None:
    with ThreadSampler() as sampler:
        assert_type(sampler, ThreadSampler)
        sample = sampler.sample(include_idle=True)
        assert_type(sample, ThreadSample)
        assert_type(sample.elapsed_ns, int)
        assert_type(sample.threads, list[ThreadStat])
        for thread in sample.threads:
            assert_type(thread.name, str)
            assert_type(thread.process_name, str)
            assert_type(thread.cpu_ns, int)
            assert_type(thread.exited_cpu_ns, int)
            assert_type(thread.rss, int | None)
