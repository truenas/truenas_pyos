# SPDX-License-Identifier: LGPL-3.0-or-later

import pytest
import os
import struct

import truenas_os


def test_module_import():
    """Test that the module can be imported successfully."""
    assert hasattr(truenas_os, 'fhandle')


def test_fhandle_type_exists():
    """Test that fhandle type has expected attributes."""
    assert hasattr(truenas_os.fhandle, '__init__')
    assert hasattr(truenas_os.fhandle, '__bytes__')
    assert hasattr(truenas_os.fhandle, 'open')
    assert hasattr(truenas_os.fhandle, 'mount_id')


def test_fhandle_from_path():
    """Test creating fhandle from a file path."""
    fh = truenas_os.fhandle(path="/tmp")
    assert fh is not None
    assert isinstance(fh, truenas_os.fhandle)

    # Check mount_id property
    mount_id = fh.mount_id
    assert mount_id is not None
    assert isinstance(mount_id, int)
    assert mount_id >= 0


def test_fhandle_from_tempfile(tmp_path):
    """Test creating fhandle from a temporary file."""
    temp_file = tmp_path / "test_file"
    temp_file.touch()

    fh = truenas_os.fhandle(path=str(temp_file))
    assert fh is not None
    assert fh.mount_id is not None


def test_fhandle_bytes_serialization():
    """Test that __bytes__ returns bytes."""
    fh = truenas_os.fhandle(path="/tmp")
    serialized = bytes(fh)

    assert isinstance(serialized, bytes)
    assert len(serialized) > 0
    # Should at least contain header (handle_bytes + handle_type)
    assert len(serialized) >= 8


def test_fhandle_bytes_structure():
    """Test that bytes contain valid struct file_handle."""
    fh = truenas_os.fhandle(path="/tmp")
    serialized = bytes(fh)

    # Parse the header
    handle_bytes, handle_type = struct.unpack('II', serialized[:8])

    # handle_bytes should match actual data length
    expected_size = 8 + handle_bytes
    assert len(serialized) == expected_size


def test_fhandle_from_bytes():
    """Test creating fhandle from serialized bytes."""
    # First create and serialize a fhandle
    fh1 = truenas_os.fhandle(path="/tmp")
    mount_id = fh1.mount_id
    serialized = bytes(fh1)

    # Now recreate from bytes
    fh2 = truenas_os.fhandle(handle_bytes=serialized, mount_id=mount_id)
    assert fh2 is not None
    assert fh2.mount_id == mount_id


def test_fhandle_round_trip():
    """Test serializing and deserializing produces equivalent handle."""
    # Create original handle
    fh1 = truenas_os.fhandle(path="/tmp")
    mount_id1 = fh1.mount_id
    serialized1 = bytes(fh1)

    # Recreate from bytes
    fh2 = truenas_os.fhandle(handle_bytes=serialized1, mount_id=mount_id1)
    mount_id2 = fh2.mount_id
    serialized2 = bytes(fh2)

    # They should be equivalent
    assert mount_id1 == mount_id2
    assert serialized1 == serialized2


def test_fhandle_invalid_path():
    """Test that invalid path raises appropriate error."""
    with pytest.raises(OSError):
        truenas_os.fhandle(path="/nonexistent/path/that/does/not/exist")


def test_fhandle_no_arguments():
    """Test that calling fhandle with no arguments raises error."""
    with pytest.raises(ValueError, match="Either 'path' or 'handle_bytes' must be specified"):
        truenas_os.fhandle()


def test_fhandle_both_path_and_bytes():
    """Test that specifying both path and handle_bytes raises error."""
    fh = truenas_os.fhandle(path="/tmp")
    serialized = bytes(fh)

    with pytest.raises(ValueError, match="Cannot specify both 'path' and 'handle_bytes'"):
        truenas_os.fhandle(path="/tmp", handle_bytes=serialized, mount_id=fh.mount_id)


def test_fhandle_bytes_without_mount_id():
    """Test that handle_bytes without mount_id raises error."""
    fh = truenas_os.fhandle(path="/tmp")
    serialized = bytes(fh)

    with pytest.raises(ValueError, match="'mount_id' is required"):
        truenas_os.fhandle(handle_bytes=serialized)


def test_fhandle_empty_bytes():
    """Test that empty handle_bytes raises error."""
    with pytest.raises(ValueError, match="handle_bytes too small"):
        truenas_os.fhandle(handle_bytes=b'', mount_id=123)


def test_fhandle_invalid_bytes():
    """Test that invalid handle_bytes raises error."""
    with pytest.raises(ValueError, match="handle_bytes too small"):
        truenas_os.fhandle(handle_bytes=b'abc', mount_id=123)


def test_fhandle_incomplete_bytes():
    """Test that incomplete handle_bytes structure raises error."""
    # Create a valid header but incomplete data
    incomplete = struct.pack('II', 100, 0) + b'\x00' * 8

    with pytest.raises(ValueError, match="Incorrect encoded handle length"):
        truenas_os.fhandle(handle_bytes=incomplete, mount_id=123)


def test_fhandle_repr():
    """Test the __repr__ method."""
    fh = truenas_os.fhandle(path="/tmp")
    repr_str = repr(fh)

    assert isinstance(repr_str, str)
    assert 'truenas_os.Fhandle' in repr_str
    assert 'mount_id=' in repr_str
    assert '<UNINITIALIZED>' not in repr_str


def test_fhandle_with_flags():
    """Test creating fhandle with flags."""
    # AT_SYMLINK_FOLLOW
    fh = truenas_os.fhandle(path="/tmp", flags=truenas_os.FH_AT_SYMLINK_FOLLOW)
    assert fh is not None
    assert fh.mount_id is not None


def test_flag_constants_exist():
    """Test that flag constants are defined."""
    assert hasattr(truenas_os, 'FH_AT_SYMLINK_FOLLOW')
    assert hasattr(truenas_os, 'FH_AT_EMPTY_PATH')
    assert hasattr(truenas_os, 'FH_AT_HANDLE_FID')
    assert hasattr(truenas_os, 'FH_AT_HANDLE_CONNECTABLE')
    assert hasattr(truenas_os, 'FH_AT_HANDLE_MNT_ID_UNIQUE')

    # Check they are integers
    assert isinstance(truenas_os.FH_AT_SYMLINK_FOLLOW, int)
    assert isinstance(truenas_os.FH_AT_EMPTY_PATH, int)
    assert isinstance(truenas_os.FH_AT_HANDLE_FID, int)
    assert isinstance(truenas_os.FH_AT_HANDLE_CONNECTABLE, int)
    assert isinstance(truenas_os.FH_AT_HANDLE_MNT_ID_UNIQUE, int)

    # Check expected values
    assert truenas_os.FH_AT_SYMLINK_FOLLOW == 0x400
    assert truenas_os.FH_AT_EMPTY_PATH == 0x1000
    assert truenas_os.FH_AT_HANDLE_FID == 0x200
    assert truenas_os.FH_AT_HANDLE_CONNECTABLE == 0x002
    assert truenas_os.FH_AT_HANDLE_MNT_ID_UNIQUE == 0x001


def test_multiple_fhandles_same_path():
    """Test that multiple fhandles to same path work correctly."""
    fh1 = truenas_os.fhandle(path="/tmp")
    fh2 = truenas_os.fhandle(path="/tmp")

    # Should have same mount_id (same filesystem)
    assert fh1.mount_id == fh2.mount_id

    # Serialized forms should be identical
    assert bytes(fh1) == bytes(fh2)


def test_fhandles_different_paths(tmp_path):
    """Test fhandles for different paths."""
    file1 = tmp_path / "file1"
    file2 = tmp_path / "file2"

    file1.touch()
    file2.touch()

    fh1 = truenas_os.fhandle(path=str(file1))
    fh2 = truenas_os.fhandle(path=str(file2))

    # Should have same mount_id (same filesystem)
    assert fh1.mount_id == fh2.mount_id

    # But different handle bytes (different files)
    assert bytes(fh1) != bytes(fh2)
