# SPDX-License-Identifier: LGPL-3.0-or-later

import pytest
import truenas_os
import os
import stat
import time


def test_iter_filesystem_exists():
    """Test that iter_filesystem_contents function exists."""
    assert hasattr(truenas_os, 'iter_filesystem_contents')


def test_iter_types_exist():
    """Test that iterator types are available."""
    assert hasattr(truenas_os, 'FilesystemIterState')
    assert hasattr(truenas_os, 'IterInstance')


@pytest.fixture
def temp_mount_tree(tmp_path):
    """Create a temporary directory tree for testing.

    Creates structure:
    /tmp_path/
        file1.txt (100 bytes)
        file2.txt (200 bytes)
        dir1/
            nested1.txt (50 bytes)
            nested2.txt (75 bytes)
        dir2/
            subdir/
                deep.txt (25 bytes)
        emptydir/
    """
    # Create files and directories
    (tmp_path / "file1.txt").write_bytes(b"x" * 100)
    (tmp_path / "file2.txt").write_bytes(b"y" * 200)

    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    (dir1 / "nested1.txt").write_bytes(b"a" * 50)
    (dir1 / "nested2.txt").write_bytes(b"b" * 75)

    dir2 = tmp_path / "dir2"
    dir2.mkdir()
    subdir = dir2 / "subdir"
    subdir.mkdir()
    (subdir / "deep.txt").write_bytes(b"c" * 25)

    emptydir = tmp_path / "emptydir"
    emptydir.mkdir()

    return tmp_path


def get_filesystem_name(path):
    """Get the filesystem name (device) for a given path."""
    return str(path)


def test_iter_basic_iteration(temp_mount_tree):
    """Test basic iteration over a directory tree."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    items = list(iterator)

    # Should have directories and files
    assert len(items) > 0

    # Each item should be an IterInstance
    for item in items:
        assert isinstance(item, truenas_os.IterInstance)
        assert hasattr(item, 'parent')
        assert hasattr(item, 'name')
        assert hasattr(item, 'fd')
        assert hasattr(item, 'statxinfo')
        assert hasattr(item, 'isdir')


def test_iter_instance_fields(temp_mount_tree):
    """Test that IterInstance has all expected fields."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Get first item
    item = next(iterator)

    # Check required fields
    assert hasattr(item, 'parent')
    assert hasattr(item, 'name')
    assert hasattr(item, 'fd')
    assert hasattr(item, 'statxinfo')
    assert hasattr(item, 'isdir')

    # Parent and name should be strings
    assert isinstance(item.parent, str)
    assert isinstance(item.name, str)

    # FD should be an integer
    assert isinstance(item.fd, int)
    assert item.fd >= 0

    # statxinfo should be a StatxResult
    assert isinstance(item.statxinfo, truenas_os.StatxResult)

    # isdir should be a boolean
    assert isinstance(item.isdir, bool)


def test_iter_filesystem_state(temp_mount_tree):
    """Test FilesystemIterState structure."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Iterator should have get_stats method
    assert hasattr(iterator, 'get_stats')

    # Get initial stats
    stats = iterator.get_stats()
    assert isinstance(stats, truenas_os.FilesystemIterState)

    # Check fields exist
    assert hasattr(stats, 'cnt')
    assert hasattr(stats, 'cnt_bytes')
    assert hasattr(stats, 'current_directory')

    # Initial count should be 0
    assert stats.cnt == 0
    assert stats.cnt_bytes == 0

    # Current directory should be the mount point
    assert stats.current_directory == str(temp_mount_tree)


def test_iter_updates_counters(temp_mount_tree):
    """Test that iteration updates cnt and cnt_bytes."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Consume a few items
    items = []
    for i, item in enumerate(iterator):
        items.append(item)
        if i >= 2:
            break

    # Get stats
    stats = iterator.get_stats()

    # Count should be updated
    assert stats.cnt > 0

    # cnt_bytes should include file sizes
    # (Directories don't contribute to cnt_bytes)
    assert stats.cnt_bytes >= 0


def test_iter_is_iterator_protocol(temp_mount_tree):
    """Test that iterator follows Python iterator protocol."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Should have __iter__ and __next__
    assert hasattr(iterator, '__iter__')
    assert hasattr(iterator, '__next__')

    # __iter__ should return self
    assert iter(iterator) is iterator

    # Should be able to call next() directly
    item = next(iterator)
    assert isinstance(item, truenas_os.IterInstance)


def test_iter_exhaustion(temp_mount_tree):
    """Test that iterator properly exhausts and raises StopIteration."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Exhaust the iterator
    items = list(iterator)
    assert len(items) >= 0

    # Further next() calls should raise StopIteration
    with pytest.raises(StopIteration):
        next(iterator)


def test_iter_relative_path(temp_mount_tree):
    """Test iteration with a relative_path parameter."""
    # Iterate only within dir1 subdirectory
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        relative_path="dir1"
    )

    items = list(iterator)

    # Should find items in dir1
    # All parent paths should contain dir1
    for item in items:
        assert "dir1" in item.parent


def test_iter_btime_cutoff(temp_mount_tree):
    """Test btime_cutoff filtering."""
    # Set cutoff to distant past - should skip all newly created files
    # (btime cutoff skips files NEWER than the cutoff)
    past_time = int(time.time()) - 86400 * 365  # 1 year ago

    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        btime_cutoff=past_time
    )

    items = list(iterator)

    # Should still see directories, but files created now should be skipped
    # All our test files are newer than 1 year ago, so they should be skipped
    files = [item for item in items if not item.isdir]

    # With past cutoff, no files should be yielded (all are newer than cutoff)
    assert len(files) == 0


def test_iter_file_open_flags(temp_mount_tree):
    """Test file_open_flags parameter."""
    # Use O_RDONLY flag
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        file_open_flags=os.O_RDONLY
    )

    # Should be able to iterate
    item = next(iterator)
    assert isinstance(item, truenas_os.IterInstance)


def test_iter_invalid_mountpoint():
    """Test that invalid mountpoint raises OSError."""
    with pytest.raises(OSError):
        iterator = truenas_os.iter_filesystem_contents(
            "/nonexistent/path",
            "fake_filesystem"
        )
        # Try to get first item to trigger error
        next(iterator)


def test_iter_not_a_directory(tmp_path):
    """Test that iterating a file (not directory) raises error."""
    # Create a file
    testfile = tmp_path / "testfile.txt"
    testfile.write_text("test")

    with pytest.raises((OSError, NotADirectoryError)):
        iterator = truenas_os.iter_filesystem_contents(
            str(testfile),
            get_filesystem_name(testfile)
        )
        next(iterator)


def test_iter_resume_token_validation():
    """Test that resume_token_data must be exactly 16 bytes."""
    with pytest.raises(ValueError, match="must be exactly 16 bytes"):
        # Try to create iterator with wrong-sized resume token
        truenas_os.iter_filesystem_contents(
            "/tmp",
            "test_fs",
            resume_token_name="user.resume_token",
            resume_token_data=b"short"  # Only 5 bytes, should fail
        )


def test_iter_fd_cleanup(temp_mount_tree):
    """Test that file descriptors are properly closed."""
    # Get initial open file count
    pid = os.getpid()
    initial_fds = len(os.listdir(f"/proc/{pid}/fd"))

    # Create and exhaust iterator
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    items = list(iterator)

    # Delete iterator
    del iterator
    del items

    # Check file descriptors after cleanup
    final_fds = len(os.listdir(f"/proc/{pid}/fd"))

    # FDs should be cleaned up (allow small variance for test overhead)
    assert final_fds <= initial_fds + 5


def test_iter_statx_info_complete(temp_mount_tree):
    """Test that statxinfo contains all expected data."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    item = next(iterator)
    st = item.statxinfo

    # Should have all basic statx fields
    assert hasattr(st, 'stx_mode')
    assert hasattr(st, 'stx_size')
    assert hasattr(st, 'stx_uid')
    assert hasattr(st, 'stx_gid')
    assert hasattr(st, 'stx_ino')
    assert hasattr(st, 'stx_mtime')
    assert hasattr(st, 'stx_btime')  # Birth time should be included

    # Mode should match file type
    if item.isdir:
        assert stat.S_ISDIR(st.stx_mode)
    else:
        assert stat.S_ISREG(st.stx_mode)


def test_iter_path_accuracy(temp_mount_tree):
    """Test that reported paths are accurate."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # All parent paths should start with the mount point
    mount_point = str(temp_mount_tree)

    # Check each item as we iterate (fd is only valid during iteration)
    count = 0
    for item in iterator:
        assert item.parent.startswith(mount_point)

        # Verify the fd actually points to the constructed path
        full_path = os.path.join(item.parent, item.name)
        fd_path = os.readlink(f"/proc/self/fd/{item.fd}")
        assert fd_path == full_path

        count += 1

    # Should have found some items
    assert count > 0


def test_iter_multiple_iterators_independent(temp_mount_tree):
    """Test that multiple iterators are independent."""
    iter1 = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    iter2 = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Advance first iterator
    item1 = next(iter1)

    # Second iterator should start from beginning
    item2 = next(iter2)

    # Both should be valid
    assert isinstance(item1, truenas_os.IterInstance)
    assert isinstance(item2, truenas_os.IterInstance)


def test_iter_empty_directory(tmp_path):
    """Test iteration over an empty directory."""
    emptydir = tmp_path / "empty"
    emptydir.mkdir()

    iterator = truenas_os.iter_filesystem_contents(
        str(emptydir),
        get_filesystem_name(emptydir)
    )

    items = list(iterator)

    # Should complete without error, but yield no items
    assert isinstance(items, list)
    assert len(items) == 0


def test_iter_deep_nesting(tmp_path):
    """Test iteration handles deep directory nesting."""
    # Create a deeply nested structure (but not too deep)
    current = tmp_path
    for i in range(10):
        current = current / f"level{i}"
        current.mkdir()

    # Create a file at the bottom
    (current / "deep_file.txt").write_bytes(b"deep")

    iterator = truenas_os.iter_filesystem_contents(
        str(tmp_path),
        get_filesystem_name(tmp_path)
    )

    items = list(iterator)

    # Should find all directories and the file
    assert len(items) > 0

    # Should find the deep file by name
    assert any(item.name == "deep_file.txt" for item in items)


def test_iter_reporting_callback_basic(temp_mount_tree):
    """Test that reporting callback is called at correct intervals."""
    calls = []

    def callback(state, private_data):
        calls.append({
            'cnt': state.cnt,
            'private_data': private_data
        })

    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=3,
        reporting_callback=callback,
        reporting_private_data="test_data"
    )

    # Consume iterator
    items = list(iterator)

    # Check callback was called at multiples of 3
    assert len(calls) > 0
    for call in calls:
        assert call['cnt'] % 3 == 0
        assert call['private_data'] == "test_data"


def test_iter_reporting_callback_no_callback(temp_mount_tree):
    """Test that iteration works without callback."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=5
        # No callback provided
    )

    items = list(iterator)
    assert len(items) > 0


def test_iter_reporting_callback_none(temp_mount_tree):
    """Test that None callback is handled correctly."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=5,
        reporting_callback=None
    )

    items = list(iterator)
    assert len(items) > 0


def test_iter_reporting_callback_exception(temp_mount_tree):
    """Test that callback exceptions stop iteration."""
    def bad_callback(state, private_data):
        if state.cnt >= 3:
            raise ValueError("Callback error at cnt=3")

    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=1,
        reporting_callback=bad_callback
    )

    # Should raise ValueError from callback
    with pytest.raises(ValueError, match="Callback error at cnt=3"):
        list(iterator)


def test_iter_reporting_callback_not_callable(temp_mount_tree):
    """Test that non-callable callback raises TypeError."""
    with pytest.raises(TypeError, match="reporting_callback must be callable"):
        truenas_os.iter_filesystem_contents(
            str(temp_mount_tree),
            get_filesystem_name(temp_mount_tree),
            reporting_callback="not a function"
        )


def test_iter_reporting_increment_zero(temp_mount_tree):
    """Test that increment=0 disables callbacks."""
    call_count = [0]

    def callback(state, private_data):
        call_count[0] += 1

    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=0,  # Should disable
        reporting_callback=callback
    )

    list(iterator)
    assert call_count[0] == 0  # Never called


def test_iter_skip_directory(temp_mount_tree):
    """Test that skip() prevents recursion into a directory."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    items_found = []
    for item in iterator:
        items_found.append(item.name)
        # Skip recursion into dir1
        if item.isdir and item.name == "dir1":
            iterator.skip()

    # Should have found dir1 itself
    assert "dir1" in items_found

    # Should NOT have found nested1.txt or nested2.txt (inside dir1)
    assert "nested1.txt" not in items_found
    assert "nested2.txt" not in items_found

    # Should still have found items in dir2 (not skipped)
    assert "dir2" in items_found
    assert "deep.txt" in items_found


def test_iter_skip_on_file_raises_error(temp_mount_tree):
    """Test that calling skip() on a file raises ValueError."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Find first file
    for item in iterator:
        if not item.isdir:
            # Try to skip a file - should raise ValueError
            with pytest.raises(ValueError, match="last yielded item was a directory"):
                iterator.skip()
            break


def test_iter_skip_method_exists(temp_mount_tree):
    """Test that iterator has skip() method."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    assert hasattr(iterator, 'skip')
    assert callable(iterator.skip)


def test_iter_skip_all_directories(temp_mount_tree):
    """Test skipping all directories to only iterate files in root."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    items_found = []
    for item in iterator:
        items_found.append((item.name, item.isdir))
        if item.isdir:
            iterator.skip()

    # Should have found root-level files
    assert ("file1.txt", False) in items_found
    assert ("file2.txt", False) in items_found

    # Should have found directories themselves
    assert ("dir1", True) in items_found
    assert ("dir2", True) in items_found
    assert ("emptydir", True) in items_found

    # Should NOT have found any nested content
    assert "nested1.txt" not in [name for name, _ in items_found]
    assert "nested2.txt" not in [name for name, _ in items_found]
    assert "subdir" not in [name for name, _ in items_found]
    assert "deep.txt" not in [name for name, _ in items_found]
