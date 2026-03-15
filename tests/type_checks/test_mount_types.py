"""
Type-level tests for truenas_os_pyutils.mount.

Each test uses assert_type() to pin the static type mypy must infer,
particularly for the as_dict overloads. The tests are also valid pytest
tests that execute at runtime.
"""
from typing import assert_type

import truenas_os
from truenas_os_pyutils.mount import StatmountResultDict, iter_mountinfo, statmount


def test_statmount_default_returns_dict() -> None:
    result = statmount(path='/')
    assert_type(result, StatmountResultDict)


def test_statmount_as_dict_true_returns_dict() -> None:
    result = statmount(path='/', as_dict=True)
    assert_type(result, StatmountResultDict)


def test_statmount_as_dict_false_returns_raw() -> None:
    result = statmount(path='/', as_dict=False)
    assert_type(result, truenas_os.StatmountResult)


def test_iter_mountinfo_default_yields_dicts() -> None:
    for entry in iter_mountinfo():
        assert_type(entry, StatmountResultDict)
        break


def test_iter_mountinfo_as_dict_true_yields_dicts() -> None:
    for entry in iter_mountinfo(as_dict=True):
        assert_type(entry, StatmountResultDict)
        break


def test_iter_mountinfo_as_dict_false_yields_raw() -> None:
    for entry in iter_mountinfo(as_dict=False):
        assert_type(entry, truenas_os.StatmountResult)
        break
