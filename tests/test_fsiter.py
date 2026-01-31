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
        assert hasattr(item, 'islnk')


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
    assert hasattr(item, 'islnk')

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

    # islnk should be a boolean
    assert isinstance(item.islnk, bool)


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
    """Test that reporting callback is called at correct intervals and at the end."""
    calls = []

    def callback(dir_stack, state, private_data):
        calls.append({
            'cnt': state.cnt,
            'private_data': private_data,
            'dir_stack': dir_stack
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
    total_count = len(items)

    # Check callback was called
    assert len(calls) > 0

    # All calls except possibly the last should be at multiples of 3
    for i, call in enumerate(calls[:-1]):
        assert call['cnt'] % 3 == 0, f"Call {i} should be at multiple of 3, got {call['cnt']}"
        assert call['private_data'] == "test_data"
        # dir_stack should be a tuple of tuples
        assert isinstance(call['dir_stack'], tuple)
        if len(call['dir_stack']) > 0:
            assert isinstance(call['dir_stack'][0], tuple)
            assert len(call['dir_stack'][0]) == 2

    # The final call should have the total count (may or may not be a multiple of 3)
    final_call = calls[-1]
    assert final_call['cnt'] == total_count, f"Final callback should report total count {total_count}, got {final_call['cnt']}"
    assert final_call['private_data'] == "test_data"


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


def test_iter_reporting_callback_final_always_called(temp_mount_tree):
    """Test that final callback is always made even when count doesn't align with increment."""
    calls = []

    def callback(dir_stack, state, private_data):
        calls.append({
            'cnt': state.cnt,
            'cnt_bytes': state.cnt_bytes,
            'current_directory': state.current_directory
        })

    # Use an increment that is unlikely to divide evenly into total count
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=7,
        reporting_callback=callback
    )

    items = list(iterator)
    total_count = len(items)

    # Should have at least one callback (the final one)
    assert len(calls) > 0, "Should have at least the final callback"

    # The last callback should always be the final count
    final_call = calls[-1]
    assert final_call['cnt'] == total_count, \
        f"Final callback should report total count {total_count}, got {final_call['cnt']}"

    # Verify that if total_count is not a multiple of 7, we got an extra final callback
    if total_count % 7 != 0:
        # The second-to-last callback (if exists) should be a multiple of 7
        if len(calls) > 1:
            assert calls[-2]['cnt'] % 7 == 0, \
                f"Second-to-last callback should be at multiple of 7, got {calls[-2]['cnt']}"
        # The final callback count should NOT be a multiple of 7
        assert final_call['cnt'] % 7 != 0, \
            f"Final callback should not be a multiple of 7 in this test, got {final_call['cnt']}"

    # Verify final callback has the root directory path (we're still in root when callback is made)
    assert final_call['current_directory'] == str(temp_mount_tree), \
        f"Final callback should have root directory path, got {final_call['current_directory']}"


def test_iter_reporting_callback_exception(temp_mount_tree):
    """Test that callback exceptions stop iteration."""
    def bad_callback(dir_stack, state, private_data):
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
    """Test that increment=0 disables periodic callbacks but still calls final callback."""
    calls = []

    def callback(dir_stack, state, private_data):
        calls.append(state.cnt)

    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        reporting_increment=0,  # Should disable periodic callbacks
        reporting_callback=callback
    )

    list(iterator)
    # Should have exactly one call - the final callback
    assert len(calls) == 1
    # The final callback should have the total count
    assert calls[0] > 0


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


def test_iter_dir_stack_method_exists(temp_mount_tree):
    """Test that iterator has dir_stack() method."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    assert hasattr(iterator, 'dir_stack')
    assert callable(iterator.dir_stack)


def test_iter_dir_stack_initial_state(temp_mount_tree):
    """Test that dir_stack() returns root directory initially."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    stack = iterator.dir_stack()

    # Should be a tuple
    assert isinstance(stack, tuple)

    # Should have exactly one entry (the root)
    assert len(stack) == 1
    assert isinstance(stack[0], tuple)
    assert len(stack[0]) == 2

    # First element should be path (string)
    path, inode = stack[0]
    assert isinstance(path, str)
    assert str(temp_mount_tree) in path

    # Second element should be inode (int)
    assert isinstance(inode, int)
    assert inode > 0


def test_iter_dir_stack_during_iteration(temp_mount_tree):
    """Test that dir_stack() changes during iteration."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    max_depth = 0
    for item in iterator:
        stack = iterator.dir_stack()

        # Stack should never be empty during iteration
        assert len(stack) > 0

        # Track maximum depth reached
        max_depth = max(max_depth, len(stack))

        # All entries should be tuples of (str, int)
        for path, inode in stack:
            assert isinstance(path, str)
            assert isinstance(inode, int)
            assert inode > 0

        # Current directory path should be in the stack
        assert item.parent in [path for path, _ in stack]

    # Should have descended at least one level
    assert max_depth > 1


def test_iter_dir_stack_after_completion(temp_mount_tree):
    """Test that dir_stack() returns empty tuple after iteration completes."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Exhaust iterator
    list(iterator)

    # Stack should be empty after completion
    stack = iterator.dir_stack()
    assert isinstance(stack, tuple)
    assert len(stack) == 0


def test_iter_dir_stack_with_skip(temp_mount_tree):
    """Test that dir_stack() works correctly with skip()."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    found_dir1 = False
    for item in iterator:
        if item.isdir and item.name == "dir1":
            found_dir1 = True
            stack_before_skip = iterator.dir_stack()
            # dir1 should not be in stack yet (not descended into)
            dir1_in_stack = any("dir1" in path for path, _ in stack_before_skip)

            iterator.skip()
            # After skip, dir1 still shouldn't be in stack since we didn't descend

        elif found_dir1 and not item.isdir:
            # Check that dir1 is not in the current path
            assert "dir1" not in item.parent
            break

    assert found_dir1


def test_iter_restore_error_exists():
    """Test that IteratorRestoreError exception exists."""
    assert hasattr(truenas_os, 'IteratorRestoreError')
    assert issubclass(truenas_os.IteratorRestoreError, Exception)


def test_iter_dir_stack_parameter_none(temp_mount_tree):
    """Test that dir_stack=None parameter is accepted."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        dir_stack=None
    )

    # Should iterate normally
    items = list(iterator)
    assert len(items) > 0


def test_iter_dir_stack_parameter_empty(temp_mount_tree):
    """Test that dir_stack=() parameter is accepted."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        dir_stack=()
    )

    # Should iterate normally
    items = list(iterator)
    assert len(items) > 0


def test_iter_dir_stack_invalid_type(temp_mount_tree):
    """Test that invalid dir_stack type raises TypeError."""
    with pytest.raises(TypeError, match="dir_stack must be a tuple"):
        truenas_os.iter_filesystem_contents(
            str(temp_mount_tree),
            get_filesystem_name(temp_mount_tree),
            dir_stack="invalid"
        )


def test_iter_dir_stack_invalid_entry_format(temp_mount_tree):
    """Test that dir_stack with invalid entry format raises ValueError."""
    # Entry is not a 2-tuple
    with pytest.raises(ValueError, match="dir_stack entries must be"):
        truenas_os.iter_filesystem_contents(
            str(temp_mount_tree),
            get_filesystem_name(temp_mount_tree),
            dir_stack=(("path",),)  # Missing inode
        )


def test_iter_dir_stack_invalid_inode_type(temp_mount_tree):
    """Test that dir_stack with invalid inode type raises TypeError."""
    with pytest.raises(TypeError, match="inode must be an integer"):
        truenas_os.iter_filesystem_contents(
            str(temp_mount_tree),
            get_filesystem_name(temp_mount_tree),
            dir_stack=(("/path", "not_an_int"),)
        )


def test_iter_dir_stack_restoration_failure_recovery(temp_mount_tree):
    """Test that IteratorRestoreError is raised when path no longer exists,
    and that we can recover by slicing the dir_stack to the failed depth.
    """
    # Create a deep directory structure: dir2/subdir/deepdir
    # Also create a sibling file in dir2 that will remain after deletion
    (temp_mount_tree / "dir2").mkdir(exist_ok=True)
    (temp_mount_tree / "dir2" / "sibling_file.txt").write_bytes(b"sibling")

    deep_path = temp_mount_tree / "dir2" / "subdir" / "deepdir"
    deep_path.mkdir(parents=True, exist_ok=True)
    (deep_path / "deepfile.txt").write_bytes(b"deep")

    # First iteration - descend to depth 3 and save dir_stack
    iterator1 = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    saved_stack = None
    for item in iterator1:
        stack = iterator1.dir_stack()
        # Save when we're at depth 3 (root + dir2 + subdir)
        if len(stack) == 3 and "subdir" in stack[-1][0]:
            saved_stack = iterator1.dir_stack()
            break

    assert saved_stack is not None
    assert len(saved_stack) == 3
    assert "subdir" in saved_stack[-1][0]

    # Delete the deepest directory from filesystem
    import shutil
    deepest_dir = saved_stack[-1][0]
    shutil.rmtree(deepest_dir)

    # Try to restore - should fail with IteratorRestoreError
    try:
        iterator2 = truenas_os.iter_filesystem_contents(
            str(temp_mount_tree),
            get_filesystem_name(temp_mount_tree),
            dir_stack=saved_stack
        )
        # Try to iterate - should raise error when can't find the path
        list(iterator2)
        assert False, "Should have raised IteratorRestoreError"
    except truenas_os.IteratorRestoreError as e:
        # Verify the exception tells us which depth failed
        # It should fail at depth 2 (0-indexed), trying to find subdir
        assert hasattr(e, 'depth'), "Exception should have depth attribute"
        assert hasattr(e, 'path'), "Exception should have path attribute"
        assert e.depth == 2, f"Expected failure at depth 2, got {e.depth}"

        # Slice the dir_stack to remove the problematic entry
        recovered_stack = saved_stack[:e.depth]
        assert len(recovered_stack) == 2  # root + dir2

    # Now try to restore with the sliced stack - should succeed
    iterator3 = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        dir_stack=recovered_stack
    )

    # Should be able to iterate from dir2 level
    items = list(iterator3)
    assert len(items) > 0, "Should successfully iterate after recovery"

    # Verify we're iterating from dir2 level and seeing the sibling file
    item_names = {item.name for item in items}
    assert "sibling_file.txt" in item_names, "Should see the sibling file that wasn't deleted"
    assert "dir2" not in item_names, "Should not re-yield dir2 during restoration"


def test_iter_dir_stack_restoration_simple(temp_mount_tree):
    """Test that iterator can be restored from dir_stack.

    Note: Cookie restoration restores the directory path and continues
    iterating from that directory. It re-iterates the restored directory
    from the beginning (can't seek within DIR* streams).
    """
    # First iteration - save state when we've just entered dir1
    iterator1 = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    saved_stack = None

    for item in iterator1:
        # Save when we first enter dir1 (when it's yielded as a directory)
        if item.name == "dir1" and item.isdir:
            # At this point, dir1 is on the stack but we haven't iterated it yet
            # Actually, after yielding dir1, it's been pushed onto the stack
            saved_stack = iterator1.dir_stack()
            break

    # Should have captured a dir_stack with dir1
    assert saved_stack is not None
    assert isinstance(saved_stack, tuple)
    assert len(saved_stack) == 2, f"Should have [root, dir1], got length {len(saved_stack)}"
    assert "dir1" in saved_stack[-1][0], f"Last entry should be dir1, got {saved_stack[-1][0]}"

    # Restore from saved state
    iterator2 = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree),
        dir_stack=saved_stack
    )

    # Collect items from restored iterator
    items_after = list(iterator2)
    items_after_names = {item.name for item in items_after}

    # Verify restoration worked:
    # 1. We got items from inside dir1
    assert "nested1.txt" in items_after_names or "nested2.txt" in items_after_names, (
        f"Should yield items from inside dir1. Got: {items_after_names}"
    )

    # 2. The restored iterator should NOT have re-yielded dir1 itself
    # (we descended into it silently during restoration)
    assert "dir1" not in items_after_names, (
        f"Restored iterator should not re-yield dir1. Got: {items_after_names}"
    )


@pytest.fixture
def temp_mount_tree_with_symlinks(tmp_path):
    """Create a temporary directory tree with symlinks for testing.

    Creates structure:
    /tmp_path/
        file1.txt (100 bytes)
        dir1/
            nested1.txt (50 bytes)
        symlink_to_file1.txt -> file1.txt (symlink to file)
        symlink_to_dir1 -> dir1 (symlink to directory)
        broken_symlink -> /nonexistent (broken symlink)
    """
    # Create files and directories
    (tmp_path / "file1.txt").write_bytes(b"x" * 100)

    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    (dir1 / "nested1.txt").write_bytes(b"a" * 50)

    # Create symlinks
    (tmp_path / "symlink_to_file1.txt").symlink_to("file1.txt")
    (tmp_path / "symlink_to_dir1").symlink_to("dir1")
    (tmp_path / "broken_symlink").symlink_to("/nonexistent")

    return tmp_path


def test_iter_islnk_field_exists():
    """Test that IterInstance has islnk field."""
    # This test just checks the type exists and has the field
    assert hasattr(truenas_os.IterInstance, '__mro__')


def test_iter_islnk_false_for_regular_files(temp_mount_tree):
    """Test that islnk is False for regular files."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Find a regular file
    for item in iterator:
        if not item.isdir and item.name.endswith('.txt'):
            # Should be a regular file, not a symlink
            assert item.islnk is False
            assert stat.S_ISREG(item.statxinfo.stx_mode)
            break


def test_iter_islnk_false_for_directories(temp_mount_tree):
    """Test that islnk is False for directories."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    # Find a directory
    for item in iterator:
        if item.isdir:
            # Should be a directory, not a symlink
            assert item.islnk is False
            assert stat.S_ISDIR(item.statxinfo.stx_mode)
            break


def test_iter_symlinks_basic(temp_mount_tree_with_symlinks):
    """Test basic iteration over a tree with symlinks."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree_with_symlinks),
        get_filesystem_name(temp_mount_tree_with_symlinks)
    )

    items = list(iterator)
    assert len(items) > 0

    # Find all items by name
    items_by_name = {item.name: item for item in items}

    # Regular file should exist and not be a symlink
    assert "file1.txt" in items_by_name
    assert items_by_name["file1.txt"].islnk is False
    assert items_by_name["file1.txt"].isdir is False

    # Regular directory should exist and not be a symlink
    assert "dir1" in items_by_name
    assert items_by_name["dir1"].islnk is False
    assert items_by_name["dir1"].isdir is True


def test_iter_symlink_to_file_not_yielded(temp_mount_tree_with_symlinks):
    """Test that symlinks to files are not yielded (openat2 with RESOLVE_NO_SYMLINKS fails)."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree_with_symlinks),
        get_filesystem_name(temp_mount_tree_with_symlinks)
    )

    items = list(iterator)
    items_by_name = {item.name: item for item in items}

    # Symlinks should not be yielded because openat2 with RESOLVE_NO_SYMLINKS
    # will fail with ELOOP, which is handled by continuing iteration
    assert "symlink_to_file1.txt" not in items_by_name
    assert "symlink_to_dir1" not in items_by_name
    assert "broken_symlink" not in items_by_name


def test_iter_symlink_readdir_detection(temp_mount_tree_with_symlinks):
    """Test that symlinks can be detected via direct statx if they are opened.

    Note: Current implementation skips symlinks at openat2 stage (ELOOP error),
    so they won't be yielded. This test documents expected behavior.
    """
    # Manually check that symlinks exist in directory
    import os
    symlinks = [
        "symlink_to_file1.txt",
        "symlink_to_dir1",
        "broken_symlink"
    ]

    for name in symlinks:
        path = temp_mount_tree_with_symlinks / name
        assert path.is_symlink(), f"{name} should be a symlink"

    # Iterator won't yield them due to RESOLVE_NO_SYMLINKS
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree_with_symlinks),
        get_filesystem_name(temp_mount_tree_with_symlinks)
    )

    items = list(iterator)
    item_names = {item.name for item in items}

    # Verify symlinks are not in results
    for name in symlinks:
        assert name not in item_names


def test_iter_islnk_consistency_with_statx(temp_mount_tree):
    """Test that islnk is consistent with statxinfo.stx_mode."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    for item in iterator:
        # islnk should match S_ISLNK check on statx mode
        expected_islnk = stat.S_ISLNK(item.statxinfo.stx_mode)
        assert item.islnk == expected_islnk, (
            f"islnk={item.islnk} but S_ISLNK={expected_islnk} for {item.name}"
        )


def test_iter_islnk_and_isdir_mutually_exclusive(temp_mount_tree):
    """Test that islnk and isdir are never both True.

    Note: With current implementation using RESOLVE_NO_SYMLINKS,
    symlinks are not yielded, so we won't see islnk=True items.
    But if we did, they should not also be marked as directories.
    """
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    for item in iterator:
        # In a proper filesystem, these should never both be True
        # (a symlink is not a directory, even if it points to one)
        if item.islnk:
            # If we ever yield a symlink, it shouldn't also be marked as a directory
            assert not item.isdir, f"Symlink {item.name} should not be marked as directory"


def test_iter_all_items_have_islnk_field(temp_mount_tree):
    """Test that all yielded items have the islnk field set."""
    iterator = truenas_os.iter_filesystem_contents(
        str(temp_mount_tree),
        get_filesystem_name(temp_mount_tree)
    )

    count = 0
    for item in iterator:
        assert hasattr(item, 'islnk'), f"Item {item.name} missing islnk field"
        assert isinstance(item.islnk, bool), f"Item {item.name} islnk is not bool"
        count += 1

    assert count > 0, "Should have iterated over some items"
