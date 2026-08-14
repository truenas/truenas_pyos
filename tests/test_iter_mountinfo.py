# SPDX-License-Identifier: LGPL-3.0-or-later

"""Tests for iter_mountinfo()'s tolerance of mounts that vanish mid-iteration."""

import errno

import pytest
import truenas_os

from truenas_os_pyutils import mount as mount_utils


class _FakeMountIter:
    """Yields the given items, raising any OSError it is handed instead of yielding it.

    Stands in for truenas_os.iter_mount(), whose C iterator advances its cursor
    before calling statmount(2) and so keeps working after raising.
    """

    def __init__(self, items):
        self._items = iter(items)

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self._items)
        if isinstance(item, OSError):
            raise item
        return item


def test_vanished_mount_is_skipped(monkeypatch):
    """A mount that disappears between listmount(2) and statmount(2) is skipped."""
    real = list(truenas_os.iter_mount(statmount_flags=truenas_os.STATMOUNT_ALL))
    assert len(real) >= 2, "need at least two mounts on the system to test this"

    items = [real[0], OSError(errno.ENOENT, 'No such file or directory'), real[1]]
    monkeypatch.setattr(truenas_os, 'iter_mount', lambda **kw: _FakeMountIter(items))

    got = list(mount_utils.iter_mountinfo(as_dict=False, include_snapshot_mounts=True))
    assert got == [real[0], real[1]]


def test_other_oserror_propagates(monkeypatch):
    """An error that is not ENOENT is a real failure and must not be swallowed."""
    real = list(truenas_os.iter_mount(statmount_flags=truenas_os.STATMOUNT_ALL))
    assert real

    items = [real[0], OSError(errno.EIO, 'Input/output error')]
    monkeypatch.setattr(truenas_os, 'iter_mount', lambda **kw: _FakeMountIter(items))

    with pytest.raises(OSError) as exc:
        list(mount_utils.iter_mountinfo(as_dict=False, include_snapshot_mounts=True))

    assert exc.value.errno == errno.EIO


def test_iteration_completes_without_injected_errors():
    """Sanity: the rewritten loop still returns every mount on the system."""
    expected = len(list(truenas_os.iter_mount(statmount_flags=truenas_os.STATMOUNT_ALL)))
    got = len(list(mount_utils.iter_mountinfo(as_dict=False, include_snapshot_mounts=True)))
    assert got == expected
