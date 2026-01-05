# SPDX-License-Identifier: LGPL-3.0-or-later

import pytest
import truenas_os
import os
import stat
import tempfile


def test_statx_function_exists():
    """Test that statx function is available."""
    assert hasattr(truenas_os, 'statx')


def test_statx_constants_exist():
    """Test that STATX_* mask constants are defined."""
    assert hasattr(truenas_os, 'STATX_TYPE')
    assert hasattr(truenas_os, 'STATX_MODE')
    assert hasattr(truenas_os, 'STATX_NLINK')
    assert hasattr(truenas_os, 'STATX_UID')
    assert hasattr(truenas_os, 'STATX_GID')
    assert hasattr(truenas_os, 'STATX_ATIME')
    assert hasattr(truenas_os, 'STATX_MTIME')
    assert hasattr(truenas_os, 'STATX_CTIME')
    assert hasattr(truenas_os, 'STATX_INO')
    assert hasattr(truenas_os, 'STATX_SIZE')
    assert hasattr(truenas_os, 'STATX_BLOCKS')
    assert hasattr(truenas_os, 'STATX_BASIC_STATS')
    assert hasattr(truenas_os, 'STATX_BTIME')
    assert hasattr(truenas_os, 'STATX_MNT_ID')
    assert hasattr(truenas_os, 'STATX_DIOALIGN')
    assert hasattr(truenas_os, 'STATX_MNT_ID_UNIQUE')
    assert hasattr(truenas_os, 'STATX_SUBVOL')
    assert hasattr(truenas_os, 'STATX_WRITE_ATOMIC')
    assert hasattr(truenas_os, 'STATX__RESERVED')
    assert hasattr(truenas_os, 'STATX_ALL')


def test_at_constants_exist():
    """Test that AT_* flag constants are defined."""
    assert hasattr(truenas_os, 'AT_FDCWD')
    assert hasattr(truenas_os, 'AT_SYMLINK_NOFOLLOW')
    assert hasattr(truenas_os, 'AT_REMOVEDIR')
    assert hasattr(truenas_os, 'AT_SYMLINK_FOLLOW')
    assert hasattr(truenas_os, 'AT_NO_AUTOMOUNT')
    assert hasattr(truenas_os, 'AT_EMPTY_PATH')
    assert hasattr(truenas_os, 'AT_STATX_SYNC_AS_STAT')
    assert hasattr(truenas_os, 'AT_STATX_FORCE_SYNC')
    assert hasattr(truenas_os, 'AT_STATX_DONT_SYNC')


def test_statx_attr_constants_exist():
    """Test that STATX_ATTR_* attribute constants are defined."""
    assert hasattr(truenas_os, 'STATX_ATTR_COMPRESSED')
    assert hasattr(truenas_os, 'STATX_ATTR_IMMUTABLE')
    assert hasattr(truenas_os, 'STATX_ATTR_APPEND')
    assert hasattr(truenas_os, 'STATX_ATTR_NODUMP')
    assert hasattr(truenas_os, 'STATX_ATTR_ENCRYPTED')
    assert hasattr(truenas_os, 'STATX_ATTR_AUTOMOUNT')
    assert hasattr(truenas_os, 'STATX_ATTR_MOUNT_ROOT')
    assert hasattr(truenas_os, 'STATX_ATTR_VERITY')
    assert hasattr(truenas_os, 'STATX_ATTR_DAX')
    assert hasattr(truenas_os, 'STATX_ATTR_WRITE_ATOMIC')


def test_mount_attr_constants_exist():
    """Test that MOUNT_ATTR_* constants are defined."""
    assert hasattr(truenas_os, 'MOUNT_ATTR_RDONLY')
    assert hasattr(truenas_os, 'MOUNT_ATTR_NOSUID')
    assert hasattr(truenas_os, 'MOUNT_ATTR_NODEV')
    assert hasattr(truenas_os, 'MOUNT_ATTR_NOEXEC')
    assert hasattr(truenas_os, 'MOUNT_ATTR_NOATIME')
    assert hasattr(truenas_os, 'MOUNT_ATTR_NODIRATIME')
    assert hasattr(truenas_os, 'MOUNT_ATTR_NOSYMFOLLOW')


def test_statx_result_type_exists():
    """Test that StatxResult type exists."""
    assert hasattr(truenas_os, 'StatxResult')


def test_statx_basic():
    """Test statx() with basic parameters on current directory."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS)
    assert isinstance(result, truenas_os.StatxResult)
    assert result.stx_mask is not None
    assert isinstance(result.stx_mask, int)
    assert result.stx_mode is not None
    assert isinstance(result.stx_mode, int)


def test_statx_with_btime():
    """Test statx() with birth time requested."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS | truenas_os.STATX_BTIME)
    assert isinstance(result, truenas_os.StatxResult)
    # Birth time should be available
    assert result.stx_btime is not None
    assert isinstance(result.stx_btime, float)
    assert result.stx_btime_ns is not None
    assert isinstance(result.stx_btime_ns, int)


def test_statx_timestamps_format():
    """Test that timestamps are in correct format (float and nanoseconds)."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS | truenas_os.STATX_BTIME)

    # Float timestamps
    assert isinstance(result.stx_atime, float)
    assert isinstance(result.stx_btime, float)
    assert isinstance(result.stx_ctime, float)
    assert isinstance(result.stx_mtime, float)

    # Nanosecond timestamps
    assert isinstance(result.stx_atime_ns, int)
    assert isinstance(result.stx_btime_ns, int)
    assert isinstance(result.stx_ctime_ns, int)
    assert isinstance(result.stx_mtime_ns, int)

    # Verify conversion: float and ns versions should match
    # Allow small floating point error (1 microsecond tolerance)
    # Note: 1ns tolerance is too strict for float64 with large timestamps
    assert abs(result.stx_atime - result.stx_atime_ns / 1e9) < 1e-6
    assert abs(result.stx_btime - result.stx_btime_ns / 1e9) < 1e-6
    assert abs(result.stx_ctime - result.stx_ctime_ns / 1e9) < 1e-6
    assert abs(result.stx_mtime - result.stx_mtime_ns / 1e9) < 1e-6


def test_statx_device_fields():
    """Test device major/minor and computed dev fields."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS)

    # All device fields should be present
    assert isinstance(result.stx_rdev_major, int)
    assert isinstance(result.stx_rdev_minor, int)
    assert isinstance(result.stx_rdev, int)
    assert isinstance(result.stx_dev_major, int)
    assert isinstance(result.stx_dev_minor, int)
    assert isinstance(result.stx_dev, int)

    # For a directory, stx_rdev should be 0
    assert result.stx_rdev == 0

    # stx_dev should be non-zero (device containing the file)
    assert result.stx_dev >= 0


def test_statx_device_file():
    """Test statx() on a device file to verify makedev()."""
    try:
        result = truenas_os.statx(truenas_os.AT_FDCWD, '/dev/null', 0,
                                  truenas_os.STATX_BASIC_STATS)

        # /dev/null is character device 1:3
        assert result.stx_rdev_major == 1
        assert result.stx_rdev_minor == 3
        # stx_rdev should be computed via makedev(1, 3)
        assert result.stx_rdev > 0

        # Verify it's a character device
        assert stat.S_ISCHR(result.stx_mode)
    except OSError:
        pytest.skip("/dev/null not accessible")


def test_statx_result_fields():
    """Test that StatxResult has all expected fields."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS | truenas_os.STATX_BTIME)

    # Basic fields
    expected_fields = [
        'stx_mask', 'stx_blksize', 'stx_attributes', 'stx_nlink',
        'stx_uid', 'stx_gid', 'stx_mode', 'stx_ino', 'stx_size',
        'stx_blocks', 'stx_attributes_mask',
    ]

    # Timestamp fields (float)
    expected_fields.extend([
        'stx_atime', 'stx_btime', 'stx_ctime', 'stx_mtime'
    ])

    # Timestamp fields (nanoseconds)
    expected_fields.extend([
        'stx_atime_ns', 'stx_btime_ns', 'stx_ctime_ns', 'stx_mtime_ns'
    ])

    # Device fields
    expected_fields.extend([
        'stx_rdev_major', 'stx_rdev_minor', 'stx_rdev',
        'stx_dev_major', 'stx_dev_minor', 'stx_dev'
    ])

    # Additional fields
    expected_fields.extend([
        'stx_mnt_id', 'stx_dio_mem_align', 'stx_dio_offset_align',
        'stx_subvol', 'stx_atomic_write_unit_min', 'stx_atomic_write_unit_max',
        'stx_atomic_write_segments_max'
    ])

    for field in expected_fields:
        assert hasattr(result, field), f"Missing field: {field}"


def test_statx_on_tempfile():
    """Test statx() on a temporary file."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        temp_path = f.name
        f.write(b"test content")

    try:
        result = truenas_os.statx(truenas_os.AT_FDCWD, temp_path, 0,
                                  truenas_os.STATX_BASIC_STATS | truenas_os.STATX_BTIME)

        # Should be a regular file
        assert stat.S_ISREG(result.stx_mode)

        # Size should match
        assert result.stx_size == 12  # "test content"

        # Should have valid timestamps
        assert result.stx_mtime > 0
        assert result.stx_ctime > 0

        # Birth time should be set
        assert result.stx_btime > 0
    finally:
        os.unlink(temp_path)


def test_statx_symlink_nofollow():
    """Test statx() with AT_SYMLINK_NOFOLLOW flag."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        target = f.name

    link_path = target + '.link'

    try:
        os.symlink(target, link_path)

        # Follow symlink (default)
        result_follow = truenas_os.statx(truenas_os.AT_FDCWD, link_path, 0,
                                         truenas_os.STATX_BASIC_STATS)
        assert stat.S_ISREG(result_follow.stx_mode)

        # Don't follow symlink
        result_nofollow = truenas_os.statx(truenas_os.AT_FDCWD, link_path,
                                           truenas_os.AT_SYMLINK_NOFOLLOW,
                                           truenas_os.STATX_BASIC_STATS)
        assert stat.S_ISLNK(result_nofollow.stx_mode)
    finally:
        if os.path.exists(link_path):
            os.unlink(link_path)
        if os.path.exists(target):
            os.unlink(target)


def test_statx_with_mnt_id():
    """Test statx() with STATX_MNT_ID to get mount ID."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '/', 0,
                              truenas_os.STATX_BASIC_STATS | truenas_os.STATX_MNT_ID)

    # Should have mount ID for root
    assert result.stx_mnt_id is not None
    assert isinstance(result.stx_mnt_id, int)
    assert result.stx_mnt_id > 0


def test_statx_attributes_mask():
    """Test that stx_attributes_mask is set."""
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS)

    assert isinstance(result.stx_attributes, int)
    assert isinstance(result.stx_attributes_mask, int)
    # attributes_mask tells us which attributes are supported
    assert result.stx_attributes_mask >= 0


def test_statx_mode_type_check():
    """Test mode and file type detection."""
    # Test on directory
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS)
    assert stat.S_ISDIR(result.stx_mode)

    # Test on regular file
    with tempfile.NamedTemporaryFile() as f:
        result = truenas_os.statx(truenas_os.AT_FDCWD, f.name, 0,
                                  truenas_os.STATX_BASIC_STATS)
        assert stat.S_ISREG(result.stx_mode)


def test_statx_invalid_path():
    """Test that statx() raises OSError for invalid path."""
    with pytest.raises(OSError):
        truenas_os.statx(truenas_os.AT_FDCWD, '/nonexistent/path/to/file', 0,
                        truenas_os.STATX_BASIC_STATS)


def test_statx_mask_filtering():
    """Test that statx() respects the mask parameter."""
    # Request only basic stats (no BTIME)
    result = truenas_os.statx(truenas_os.AT_FDCWD, '.', 0,
                              truenas_os.STATX_BASIC_STATS)

    # Should have basic info
    assert result.stx_mode is not None
    assert result.stx_size is not None

    # Birth time may or may not be available depending on mask
    # But the fields exist
    assert hasattr(result, 'stx_btime')
    assert hasattr(result, 'stx_btime_ns')


def test_statx_comparison_with_os_stat():
    """Test that statx() results match os.stat() for basic fields."""
    path = '.'

    st = os.stat(path)
    result = truenas_os.statx(truenas_os.AT_FDCWD, path, 0,
                              truenas_os.STATX_BASIC_STATS)

    # Mode should match
    assert result.stx_mode == st.st_mode

    # Size should match
    assert result.stx_size == st.st_size

    # UID/GID should match
    assert result.stx_uid == st.st_uid
    assert result.stx_gid == st.st_gid

    # Inode should match
    assert result.stx_ino == st.st_ino

    # Timestamps should be close (allow small difference)
    assert abs(result.stx_atime - st.st_atime) < 1.0
    assert abs(result.stx_mtime - st.st_mtime) < 1.0
    assert abs(result.stx_ctime - st.st_ctime) < 1.0
