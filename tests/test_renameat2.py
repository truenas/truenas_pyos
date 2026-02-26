# SPDX-License-Identifier: LGPL-3.0-or-later

import pytest
import truenas_os
import os
import tempfile


def test_renameat2_function_exists():
    """Test that renameat2 function is available."""
    assert hasattr(truenas_os, 'renameat2')


def test_renameat2_constants_exist():
    """Test that AT_RENAME_* constants are defined."""
    assert hasattr(truenas_os, 'AT_RENAME_NOREPLACE')
    assert hasattr(truenas_os, 'AT_RENAME_EXCHANGE')
    assert hasattr(truenas_os, 'AT_RENAME_WHITEOUT')


def test_renameat2_constant_values():
    """Test that AT_RENAME_* constants have correct values."""
    assert truenas_os.AT_RENAME_NOREPLACE == 0x0001
    assert truenas_os.AT_RENAME_EXCHANGE == 0x0002
    assert truenas_os.AT_RENAME_WHITEOUT == 0x0004


def test_renameat2_basic_rename():
    """Test basic rename operation with flags=0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, 'source.txt')
        dst = os.path.join(tmpdir, 'dest.txt')

        # Create source file
        with open(src, 'w') as f:
            f.write('test content')

        # Perform rename
        truenas_os.renameat2(src, dst, flags=0)

        # Verify
        assert os.path.exists(dst)
        assert not os.path.exists(src)
        with open(dst, 'r') as f:
            assert f.read() == 'test content'


def test_renameat2_noreplace_success():
    """Test AT_RENAME_NOREPLACE when destination doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, 'source.txt')
        dst = os.path.join(tmpdir, 'dest.txt')

        # Create source file
        with open(src, 'w') as f:
            f.write('source content')

        # Rename with NOREPLACE (should succeed)
        truenas_os.renameat2(src, dst, flags=truenas_os.AT_RENAME_NOREPLACE)

        # Verify
        assert os.path.exists(dst)
        assert not os.path.exists(src)
        with open(dst, 'r') as f:
            assert f.read() == 'source content'


def test_renameat2_noreplace_failure():
    """Test AT_RENAME_NOREPLACE when destination exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, 'source.txt')
        dst = os.path.join(tmpdir, 'dest.txt')

        # Create both files
        with open(src, 'w') as f:
            f.write('source content')
        with open(dst, 'w') as f:
            f.write('dest content')

        # Rename with NOREPLACE should fail
        with pytest.raises(OSError) as exc_info:
            truenas_os.renameat2(src, dst, flags=truenas_os.AT_RENAME_NOREPLACE)

        # Should raise EEXIST (file exists) error
        assert exc_info.value.errno == 17  # EEXIST

        # Both files should still exist
        assert os.path.exists(src)
        assert os.path.exists(dst)

        # Content should be unchanged
        with open(src, 'r') as f:
            assert f.read() == 'source content'
        with open(dst, 'r') as f:
            assert f.read() == 'dest content'


def test_renameat2_exchange():
    """Test AT_RENAME_EXCHANGE to atomically swap two files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_a = os.path.join(tmpdir, 'file_a.txt')
        file_b = os.path.join(tmpdir, 'file_b.txt')

        # Create files with different content
        with open(file_a, 'w') as f:
            f.write('content A')
        with open(file_b, 'w') as f:
            f.write('content B')

        # Exchange files
        truenas_os.renameat2(file_a, file_b, flags=truenas_os.AT_RENAME_EXCHANGE)

        # Verify both files still exist
        assert os.path.exists(file_a)
        assert os.path.exists(file_b)

        # Verify content was swapped
        with open(file_a, 'r') as f:
            assert f.read() == 'content B'
        with open(file_b, 'r') as f:
            assert f.read() == 'content A'


def test_renameat2_exchange_with_nonexistent():
    """Test that AT_RENAME_EXCHANGE fails if either file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_a = os.path.join(tmpdir, 'file_a.txt')
        file_b = os.path.join(tmpdir, 'nonexistent.txt')

        # Create only one file
        with open(file_a, 'w') as f:
            f.write('content A')

        # Exchange should fail
        with pytest.raises(OSError) as exc_info:
            truenas_os.renameat2(file_a, file_b, flags=truenas_os.AT_RENAME_EXCHANGE)

        # Should raise ENOENT (no such file or directory)
        assert exc_info.value.errno == 2  # ENOENT


def test_renameat2_with_dirfd():
    """Test renameat2 with directory file descriptors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create source and dest directories
        src_dir = os.path.join(tmpdir, 'src')
        dst_dir = os.path.join(tmpdir, 'dst')
        os.mkdir(src_dir)
        os.mkdir(dst_dir)

        # Create file in source directory
        src_file = os.path.join(src_dir, 'file.txt')
        with open(src_file, 'w') as f:
            f.write('test content')

        # Open directory file descriptors
        src_dirfd = os.open(src_dir, os.O_RDONLY | os.O_DIRECTORY)
        dst_dirfd = os.open(dst_dir, os.O_RDONLY | os.O_DIRECTORY)

        try:
            # Rename using relative paths with dirfds
            truenas_os.renameat2('file.txt', 'renamed.txt',
                                src_dir_fd=src_dirfd,
                                dst_dir_fd=dst_dirfd,
                                flags=0)

            # Verify
            assert not os.path.exists(src_file)
            assert os.path.exists(os.path.join(dst_dir, 'renamed.txt'))

            with open(os.path.join(dst_dir, 'renamed.txt'), 'r') as f:
                assert f.read() == 'test content'
        finally:
            os.close(src_dirfd)
            os.close(dst_dirfd)


def test_renameat2_same_dirfd():
    """Test renameat2 with same directory file descriptor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create file
        src_file = os.path.join(tmpdir, 'source.txt')
        with open(src_file, 'w') as f:
            f.write('content')

        # Open directory file descriptor
        dirfd = os.open(tmpdir, os.O_RDONLY | os.O_DIRECTORY)

        try:
            # Rename within same directory using dirfd
            truenas_os.renameat2('source.txt', 'dest.txt',
                                src_dir_fd=dirfd,
                                dst_dir_fd=dirfd,
                                flags=0)

            # Verify
            assert not os.path.exists(src_file)
            assert os.path.exists(os.path.join(tmpdir, 'dest.txt'))
        finally:
            os.close(dirfd)


def test_renameat2_directory():
    """Test renaming a directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = os.path.join(tmpdir, 'olddir')
        dst_dir = os.path.join(tmpdir, 'newdir')

        # Create directory with a file inside
        os.mkdir(src_dir)
        with open(os.path.join(src_dir, 'file.txt'), 'w') as f:
            f.write('content')

        # Rename directory
        truenas_os.renameat2(src_dir, dst_dir, flags=0)

        # Verify
        assert not os.path.exists(src_dir)
        assert os.path.exists(dst_dir)
        assert os.path.exists(os.path.join(dst_dir, 'file.txt'))

        with open(os.path.join(dst_dir, 'file.txt'), 'r') as f:
            assert f.read() == 'content'


def test_renameat2_invalid_flags():
    """Test that invalid flag combinations work as expected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, 'source.txt')
        dst = os.path.join(tmpdir, 'dest.txt')

        # Create source file
        with open(src, 'w') as f:
            f.write('content')

        # NOREPLACE and EXCHANGE are mutually exclusive
        with pytest.raises(OSError) as exc_info:
            truenas_os.renameat2(
                src, dst,
                flags=truenas_os.AT_RENAME_NOREPLACE | truenas_os.AT_RENAME_EXCHANGE
            )

        # Should raise EINVAL (invalid argument)
        assert exc_info.value.errno == 22  # EINVAL


def test_renameat2_nonexistent_source():
    """Test that renaming nonexistent file raises OSError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, 'nonexistent.txt')
        dst = os.path.join(tmpdir, 'dest.txt')

        with pytest.raises(OSError) as exc_info:
            truenas_os.renameat2(src, dst, flags=0)

        # Should raise ENOENT
        assert exc_info.value.errno == 2  # ENOENT


def test_renameat2_preserves_inode():
    """Test that basic rename preserves inode number."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, 'source.txt')
        dst = os.path.join(tmpdir, 'dest.txt')

        # Create source file
        with open(src, 'w') as f:
            f.write('content')

        # Get original inode
        src_stat = os.stat(src)
        src_inode = src_stat.st_ino

        # Rename
        truenas_os.renameat2(src, dst, flags=0)

        # Verify inode is preserved
        dst_stat = os.stat(dst)
        assert dst_stat.st_ino == src_inode


def test_renameat2_exchange_preserves_inodes():
    """Test that exchange preserves inode numbers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_a = os.path.join(tmpdir, 'file_a.txt')
        file_b = os.path.join(tmpdir, 'file_b.txt')

        # Create files
        with open(file_a, 'w') as f:
            f.write('content A')
        with open(file_b, 'w') as f:
            f.write('content B')

        # Get original inodes
        inode_a = os.stat(file_a).st_ino
        inode_b = os.stat(file_b).st_ino

        # Exchange
        truenas_os.renameat2(file_a, file_b, flags=truenas_os.AT_RENAME_EXCHANGE)

        # Verify inodes were swapped (file_a now has file_b's inode and vice versa)
        assert os.stat(file_a).st_ino == inode_b
        assert os.stat(file_b).st_ino == inode_a
