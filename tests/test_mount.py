# SPDX-License-Identifier: LGPL-3.0-or-later

import pytest
import truenas_os


def test_mount_functions_exist():
    """Test that mount functions are available."""
    assert hasattr(truenas_os, 'listmount')
    assert hasattr(truenas_os, 'statmount')


def test_mount_constants_exist():
    """Test that STATMOUNT_* constants are defined."""
    assert hasattr(truenas_os, 'STATMOUNT_SB_BASIC')
    assert hasattr(truenas_os, 'STATMOUNT_MNT_BASIC')
    assert hasattr(truenas_os, 'STATMOUNT_PROPAGATE_FROM')
    assert hasattr(truenas_os, 'STATMOUNT_MNT_ROOT')
    assert hasattr(truenas_os, 'STATMOUNT_MNT_POINT')
    assert hasattr(truenas_os, 'STATMOUNT_FS_TYPE')
    assert hasattr(truenas_os, 'STATMOUNT_MNT_NS_ID')
    assert hasattr(truenas_os, 'STATMOUNT_MNT_OPTS')


def test_statmount_result_type_exists():
    """Test that StatmountResult type exists."""
    assert hasattr(truenas_os, 'StatmountResult')


def test_listmount_default():
    """Test listmount() with default parameters returns mount IDs."""
    mounts = truenas_os.listmount()
    assert isinstance(mounts, list)
    assert len(mounts) > 0
    # All entries should be integers
    for mount_id in mounts:
        assert isinstance(mount_id, int)
        assert mount_id > 0


def test_listmount_with_specific_mount():
    """Test listmount() with a specific mount ID."""
    # Get all mounts first
    all_mounts = truenas_os.listmount()
    assert len(all_mounts) > 0

    # List children of first mount (may be empty)
    children = truenas_os.listmount(mnt_id=all_mounts[0])
    assert isinstance(children, list)
    for mount_id in children:
        assert isinstance(mount_id, int)


def test_listmount_pagination():
    """Test listmount() pagination with last_mnt_id."""
    # Get first batch
    all_mounts = truenas_os.listmount()
    assert len(all_mounts) > 0

    if len(all_mounts) > 1:
        # Test pagination by getting mounts after the first one
        remaining = truenas_os.listmount(last_mnt_id=all_mounts[0])
        # remaining should be a subset or disjoint from all_mounts
        assert isinstance(remaining, list)


def test_statmount_basic():
    """Test statmount() with basic parameters."""
    mounts = truenas_os.listmount()
    assert len(mounts) > 0

    # Get info for first mount
    sm = truenas_os.statmount(mounts[0])
    assert isinstance(sm, truenas_os.StatmountResult)

    # With default mask, should have basic info
    assert sm.mnt_id is not None
    assert isinstance(sm.mnt_id, int)
    assert sm.mnt_parent_id is not None
    assert isinstance(sm.mnt_parent_id, int)


def test_statmount_all_fields():
    """Test statmount() with all fields requested."""
    mounts = truenas_os.listmount()
    assert len(mounts) > 0

    mask = (truenas_os.STATMOUNT_MNT_BASIC |
            truenas_os.STATMOUNT_SB_BASIC |
            truenas_os.STATMOUNT_MNT_ROOT |
            truenas_os.STATMOUNT_MNT_POINT |
            truenas_os.STATMOUNT_FS_TYPE |
            truenas_os.STATMOUNT_MNT_OPTS)

    sm = truenas_os.statmount(mounts[0], mask=mask)
    assert isinstance(sm, truenas_os.StatmountResult)

    # Check that we got the requested fields
    assert sm.mnt_id is not None
    assert sm.mnt_point is not None
    assert sm.fs_type is not None
    assert isinstance(sm.mask, int)


def test_statmount_result_fields():
    """Test that StatmountResult has all expected fields."""
    mounts = truenas_os.listmount()
    sm = truenas_os.statmount(mounts[0])

    # Check all 19 fields exist
    expected_fields = [
        'mnt_id', 'mnt_parent_id', 'mnt_id_old', 'mnt_parent_id_old',
        'mnt_root', 'mnt_point', 'mnt_attr', 'mnt_propagation',
        'mnt_peer_group', 'mnt_master', 'propagate_from', 'fs_type',
        'mnt_ns_id', 'mnt_opts', 'sb_dev_major', 'sb_dev_minor',
        'sb_magic', 'sb_flags', 'mask'
    ]

    for field in expected_fields:
        assert hasattr(sm, field), f"Missing field: {field}"


def test_statmount_mask_filtering():
    """Test that statmount() respects the mask parameter."""
    mounts = truenas_os.listmount()

    # Request only basic mount info
    sm = truenas_os.statmount(mounts[0], mask=truenas_os.STATMOUNT_MNT_BASIC)

    # Should have basic info
    assert sm.mnt_id is not None
    assert sm.mnt_parent_id is not None

    # Should NOT have filesystem type (not in mask)
    assert sm.fs_type is None
    assert sm.mnt_point is None


def test_statmount_root_mount():
    """Test statmount() on root filesystem."""
    mounts = truenas_os.listmount()

    mask = (truenas_os.STATMOUNT_MNT_BASIC |
            truenas_os.STATMOUNT_MNT_POINT |
            truenas_os.STATMOUNT_FS_TYPE)

    # Find root mount
    found_root = False
    for mnt_id in mounts:
        sm = truenas_os.statmount(mnt_id, mask=mask)
        if sm.mnt_point == '/':
            # Found root mount
            found_root = True
            assert sm.fs_type is not None
            assert isinstance(sm.fs_type, str)
            assert sm.mnt_id is not None
            break

    assert found_root, "Should find root mount at /"


def test_statmount_invalid_mount_id():
    """Test that statmount() raises OSError for invalid mount ID."""
    with pytest.raises(OSError):
        # Use a mount ID that definitely doesn't exist
        truenas_os.statmount(999999999)


def test_iter_mount_exists():
    """Test that iter_mount function exists."""
    assert hasattr(truenas_os, 'iter_mount')


def test_iter_mount_basic():
    """Test basic iter_mount() iteration."""
    count = 0
    for mount_info in truenas_os.iter_mount():
        count += 1
        assert isinstance(mount_info, truenas_os.StatmountResult)
        assert mount_info.mnt_id is not None
        assert isinstance(mount_info.mnt_id, int)

    assert count > 0, "Should have at least one mount"


def test_iter_mount_returns_same_as_listmount():
    """Test that iter_mount returns same mount IDs as listmount."""
    # Get mount IDs from listmount
    list_mounts = set(truenas_os.listmount())

    # Get mount IDs from iter_mount
    iter_mounts = set(m.mnt_id for m in truenas_os.iter_mount())

    assert list_mounts == iter_mounts, "iter_mount should return same mount IDs as listmount"


def test_iter_mount_with_flags():
    """Test iter_mount() with custom statmount_flags."""
    flags = (truenas_os.STATMOUNT_MNT_BASIC |
             truenas_os.STATMOUNT_SB_BASIC |
             truenas_os.STATMOUNT_MNT_POINT |
             truenas_os.STATMOUNT_FS_TYPE)

    count = 0
    for mount_info in truenas_os.iter_mount(statmount_flags=flags):
        count += 1
        assert isinstance(mount_info, truenas_os.StatmountResult)
        # With these flags, these fields should be present
        assert mount_info.mnt_id is not None
        assert mount_info.mnt_point is not None
        assert mount_info.fs_type is not None
        if count >= 3:
            break

    assert count > 0


def test_iter_mount_minimal_flags():
    """Test iter_mount() with only MNT_BASIC flag."""
    flags = truenas_os.STATMOUNT_MNT_BASIC

    for mount_info in truenas_os.iter_mount(statmount_flags=flags):
        assert isinstance(mount_info, truenas_os.StatmountResult)
        assert mount_info.mnt_id is not None
        # These should be None as they weren't requested
        assert mount_info.fs_type is None
        assert mount_info.mnt_point is None
        break  # Just check first one


def test_iter_mount_is_iterator():
    """Test that iter_mount returns an actual iterator."""
    iterator = truenas_os.iter_mount()

    # Should have __iter__ and __next__
    assert hasattr(iterator, '__iter__')
    assert hasattr(iterator, '__next__')

    # Should be able to call next() directly
    first = next(iterator)
    assert isinstance(first, truenas_os.StatmountResult)

    second = next(iterator)
    assert isinstance(second, truenas_os.StatmountResult)

    # First and second should have different mount IDs
    assert first.mnt_id != second.mnt_id


def test_iter_mount_multiple_iterations():
    """Test that we can create multiple independent iterators."""
    # First iteration
    mounts1 = list(truenas_os.iter_mount())

    # Second iteration should give same results
    mounts2 = list(truenas_os.iter_mount())

    assert len(mounts1) == len(mounts2)
    assert [m.mnt_id for m in mounts1] == [m.mnt_id for m in mounts2]


def test_iter_mount_with_specific_mount():
    """Test iter_mount() with a specific mount ID."""
    # Get all mounts first
    all_mounts = truenas_os.listmount()
    assert len(all_mounts) > 0

    # Iterate children of first mount
    children = list(truenas_os.iter_mount(mnt_id=all_mounts[0]))

    # Should be a list of StatmountResult objects
    for mount_info in children:
        assert isinstance(mount_info, truenas_os.StatmountResult)
        assert mount_info.mnt_id is not None


def test_iter_mount_exhaustion():
    """Test that iterator properly exhausts and raises StopIteration."""
    iterator = truenas_os.iter_mount()

    # Exhaust the iterator
    mounts = list(iterator)
    assert len(mounts) > 0

    # Further next() calls should raise StopIteration
    with pytest.raises(StopIteration):
        next(iterator)


def test_iter_mount_finds_root():
    """Test that iter_mount can find the root filesystem."""
    flags = (truenas_os.STATMOUNT_MNT_BASIC |
             truenas_os.STATMOUNT_MNT_POINT |
             truenas_os.STATMOUNT_FS_TYPE)

    found_root = False
    for mount_info in truenas_os.iter_mount(statmount_flags=flags):
        if mount_info.mnt_point == '/':
            found_root = True
            assert mount_info.fs_type is not None
            assert isinstance(mount_info.fs_type, str)
            break

    assert found_root, "Should find root mount at /"
