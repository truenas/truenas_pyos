import errno
import os
import stat

import pytest

from truenas_os_pyutils.io import (
    SymlinkInPathError,
    atomic_replace,
    atomic_write,
    safe_copy,
    safe_copytree,
    safe_open,
)


# ── SymlinkInPathError ────────────────────────────────────────────────────────

def test_symlink_in_path_error_is_oserror():
    assert issubclass(SymlinkInPathError, OSError)


def test_symlink_in_path_error_errno():
    exc = SymlinkInPathError('/some/path')
    assert exc.errno == errno.ELOOP


def test_symlink_in_path_error_filename():
    exc = SymlinkInPathError('/some/path')
    assert exc.filename == '/some/path'


# ── safe_open ─────────────────────────────────────────────────────────────────

def test_safe_open_read(tmp_path):
    f = tmp_path / 'test.txt'
    f.write_text('hello')
    with safe_open(str(f)) as fh:
        assert fh.read() == 'hello'


def test_safe_open_write(tmp_path):
    f = tmp_path / 'test.txt'
    with safe_open(str(f), 'w') as fh:
        fh.write('world')
        fh.flush()
    assert f.read_text() == 'world'


def test_safe_open_append(tmp_path):
    f = tmp_path / 'test.txt'
    f.write_text('hello')
    with safe_open(str(f), 'a') as fh:
        fh.write(' world')
        fh.flush()
    assert f.read_text() == 'hello world'


def test_safe_open_creates_file(tmp_path):
    f = tmp_path / 'new.txt'
    assert not f.exists()
    with safe_open(str(f), 'w') as fh:
        fh.write('created')
        fh.flush()
    assert f.read_text() == 'created'


def test_safe_open_symlink_raises(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('secret')
    link = tmp_path / 'link.txt'
    link.symlink_to(target)

    with pytest.raises(SymlinkInPathError) as exc_info:
        with safe_open(str(link)) as fh:
            fh.read()

    assert exc_info.value.errno == errno.ELOOP


def test_safe_open_symlink_in_path_raises(tmp_path):
    real_dir = tmp_path / 'real'
    real_dir.mkdir()
    (real_dir / 'file.txt').write_text('data')

    link_dir = tmp_path / 'link_dir'
    link_dir.symlink_to(real_dir)

    with pytest.raises(SymlinkInPathError) as exc_info:
        with safe_open(str(link_dir / 'file.txt')) as fh:
            fh.read()

    assert exc_info.value.errno == errno.ELOOP


def test_safe_open_encoding(tmp_path):
    f = tmp_path / 'test.txt'
    f.write_bytes('héllo'.encode('utf-8'))
    with safe_open(str(f), encoding='utf-8') as fh:
        assert fh.read() == 'héllo'


def test_safe_open_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        with safe_open(str(tmp_path / 'nonexistent.txt')) as fh:
            fh.read()


def test_safe_open_dir_fd(tmp_path):
    f = tmp_path / 'test.txt'
    f.write_text('via dir_fd')
    dirfd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with safe_open('test.txt', dir_fd=dirfd) as fh:
            assert fh.read() == 'via dir_fd'
    finally:
        os.close(dirfd)


def test_safe_open_dir_fd_symlink_raises(tmp_path):
    real = tmp_path / 'real.txt'
    real.write_text('data')
    link = tmp_path / 'link.txt'
    link.symlink_to(real)

    dirfd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(SymlinkInPathError):
            with safe_open('link.txt', dir_fd=dirfd) as fh:
                fh.read()
    finally:
        os.close(dirfd)


# ── atomic_replace ────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def atomic_dir(tmp_path_factory):
    return tmp_path_factory.mktemp('atomic')


def test_atomic_replace_creates_new_file(atomic_dir):
    target = str(atomic_dir / 'new_file.txt')
    data = b'Hello, World!'
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=data)
    with open(target, 'rb') as f:
        assert f.read() == data


def test_atomic_replace_replaces_existing_file(atomic_dir):
    target = str(atomic_dir / 'existing_file.txt')
    with open(target, 'wb') as f:
        f.write(b'Original content')

    new_data = b'New content'
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=new_data)
    with open(target, 'rb') as f:
        assert f.read() == new_data


def test_atomic_replace_sets_permissions(atomic_dir):
    target = str(atomic_dir / 'perms_file.txt')
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=b'data', perms=0o644)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644


def test_atomic_replace_sets_ownership(atomic_dir):
    target = str(atomic_dir / 'owner_file.txt')
    uid, gid = os.getuid(), os.getgid()
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=b'data', uid=uid, gid=gid)
    st = os.stat(target)
    assert st.st_uid == uid
    assert st.st_gid == gid


def test_atomic_replace_empty_data(atomic_dir):
    target = str(atomic_dir / 'empty_file.txt')
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=b'')
    assert os.path.getsize(target) == 0


def test_atomic_replace_binary_data(atomic_dir):
    target = str(atomic_dir / 'binary_file.bin')
    data = bytes([0x00, 0x01, 0x02, 0xFF, 0xFE, 0xFD])
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=data)
    with open(target, 'rb') as f:
        assert f.read() == data


def test_atomic_replace_nonexistent_temp_path_raises(atomic_dir):
    target = str(atomic_dir / 'will_fail.txt')
    with pytest.raises((OSError, FileNotFoundError)):
        atomic_replace(
            temp_path=str(atomic_dir / 'nonexistent'),
            target_file=target,
            data=b'data',
        )


def test_atomic_replace_preserves_uid_when_minus_one(atomic_dir):
    target = str(atomic_dir / 'preserve_uid.txt')
    with open(target, 'wb') as f:
        f.write(b'initial')
    os.chown(target, 8675309, 8675310)

    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=b'new', uid=-1, gid=1000)
    st = os.stat(target)
    assert st.st_uid == 8675309
    assert st.st_gid == 1000


def test_atomic_replace_preserves_gid_when_minus_one(atomic_dir):
    target = str(atomic_dir / 'preserve_gid.txt')
    with open(target, 'wb') as f:
        f.write(b'initial')
    os.chown(target, 8675309, 8675310)

    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=b'new', uid=1000, gid=-1)
    st = os.stat(target)
    assert st.st_uid == 1000
    assert st.st_gid == 8675310


def test_atomic_replace_minus_one_new_file_defaults_to_zero(atomic_dir):
    target = str(atomic_dir / 'new_minus_one.txt')
    atomic_replace(temp_path=str(atomic_dir), target_file=target, data=b'data', uid=-1, gid=-1)
    st = os.stat(target)
    assert st.st_uid == 0
    assert st.st_gid == 0


# ── atomic_write ──────────────────────────────────────────────────────────────

def test_atomic_write_creates_text_file(atomic_dir):
    target = str(atomic_dir / 'new_text.txt')
    with atomic_write(target) as f:
        f.write('Hello, World!')
    with open(target) as f:
        assert f.read() == 'Hello, World!'


def test_atomic_write_creates_binary_file(atomic_dir):
    target = str(atomic_dir / 'new_binary.bin')
    with atomic_write(target, 'wb') as f:
        f.write(b'binary data')
    with open(target, 'rb') as f:
        assert f.read() == b'binary data'


def test_atomic_write_replaces_existing(atomic_dir):
    target = str(atomic_dir / 'replace_existing.txt')
    with open(target, 'w') as f:
        f.write('original')
    with atomic_write(target) as f:
        f.write('replaced')
    with open(target) as f:
        assert f.read() == 'replaced'


def test_atomic_write_does_not_replace_on_exception(atomic_dir):
    target = str(atomic_dir / 'no_replace_on_exc.txt')
    with open(target, 'w') as f:
        f.write('original')

    with pytest.raises(ValueError):
        with atomic_write(target) as f:
            f.write('partial')
            raise ValueError('abort')

    with open(target) as f:
        assert f.read() == 'original'


def test_atomic_write_sets_permissions(atomic_dir):
    target = str(atomic_dir / 'perms_write.txt')
    with atomic_write(target, perms=0o644) as f:
        f.write('data')
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644


def test_atomic_write_preserves_uid_when_minus_one(atomic_dir):
    target = str(atomic_dir / 'preserve_uid_write.txt')
    with open(target, 'w') as f:
        f.write('initial')
    os.chown(target, 8675309, 8675310)

    with atomic_write(target, uid=-1, gid=1000) as f:
        f.write('new')

    st = os.stat(target)
    assert st.st_uid == 8675309
    assert st.st_gid == 1000


def test_atomic_write_validates_mode(atomic_dir):
    target = str(atomic_dir / 'mode_test.txt')
    for mode in ('r', 'rb', 'a', 'r+', 'wt'):
        with pytest.raises(ValueError, match='invalid mode'):
            with atomic_write(target, mode) as f:
                f.write('x')


def test_atomic_write_multiple_writes(atomic_dir):
    target = str(atomic_dir / 'multi_write.txt')
    with atomic_write(target) as f:
        f.write('line1\n')
        f.write('line2\n')
    with open(target) as f:
        assert f.read() == 'line1\nline2\n'


# ── safe_copy ─────────────────────────────────────────────────────────────────

def test_safe_copy_basic(tmp_path):
    src = tmp_path / 'src.bin'
    dst = tmp_path / 'dst.bin'
    src.write_bytes(b'payload')
    rv = safe_copy(str(src), str(dst))
    assert rv == str(dst)
    assert dst.read_bytes() == b'payload'
    assert src.read_bytes() == b'payload'  # source untouched


def test_safe_copy_empty_file(tmp_path):
    src = tmp_path / 'empty.bin'
    dst = tmp_path / 'empty_copy.bin'
    src.write_bytes(b'')
    safe_copy(str(src), str(dst))
    assert dst.read_bytes() == b''


def test_safe_copy_preserves_uid_gid(tmp_path):
    src = tmp_path / 'src.bin'
    dst = tmp_path / 'dst.bin'
    src.write_bytes(b'x')
    os.chown(str(src), 8675309, 8675310)
    safe_copy(str(src), str(dst))
    st = os.stat(str(dst))
    assert st.st_uid == 8675309
    assert st.st_gid == 8675310


def test_safe_copy_preserves_mode(tmp_path):
    src = tmp_path / 'src.bin'
    dst = tmp_path / 'dst.bin'
    src.write_bytes(b'x')
    os.chmod(str(src), 0o600)
    safe_copy(str(src), str(dst))
    assert stat.S_IMODE(os.stat(str(dst)).st_mode) == 0o600


def test_safe_copy_preserves_times(tmp_path):
    src = tmp_path / 'src.bin'
    dst = tmp_path / 'dst.bin'
    src.write_bytes(b'x')
    # Pin the source mtime well in the past (epoch + 1 day) so we can
    # detect that the destination inherited it rather than being stamped
    # at copy time.
    target_atime_ns = 86_400 * 10**9  # 1 day after epoch
    target_mtime_ns = 86_400 * 10**9 + 12_345
    os.utime(str(src), ns=(target_atime_ns, target_mtime_ns))
    safe_copy(str(src), str(dst))
    dst_st = os.stat(str(dst))
    assert dst_st.st_mtime_ns == target_mtime_ns


def test_safe_copy_refuses_existing_dst_file(tmp_path):
    src = tmp_path / 'src.bin'
    dst = tmp_path / 'dst.bin'
    src.write_bytes(b'src-content')
    dst.write_bytes(b'dst-stale')
    with pytest.raises(FileExistsError):
        safe_copy(str(src), str(dst))
    # Pre-existing dst must not be clobbered
    assert dst.read_bytes() == b'dst-stale'


def test_safe_copy_refuses_dst_symlink_squatter(tmp_path):
    # Pre-create a symlink at dst pointing to /etc/passwd. O_EXCL must
    # refuse to follow or replace it; nothing must be written to the
    # symlink's target.
    src = tmp_path / 'src.bin'
    dst = tmp_path / 'dst.bin'
    bait = tmp_path / 'bait.txt'
    bait.write_text('bait-original')
    src.write_bytes(b'attacker-payload')
    os.symlink(str(bait), str(dst))
    with pytest.raises((FileExistsError, SymlinkInPathError)):
        safe_copy(str(src), str(dst))
    assert bait.read_text() == 'bait-original'


def test_safe_copy_rejects_symlink_src(tmp_path):
    real = tmp_path / 'real.bin'
    real.write_bytes(b'real-data')
    src = tmp_path / 'link.bin'
    os.symlink(str(real), str(src))
    dst = tmp_path / 'dst.bin'
    with pytest.raises(SymlinkInPathError):
        safe_copy(str(src), str(dst))


def test_safe_copy_rejects_symlink_in_src_path(tmp_path):
    real_dir = tmp_path / 'real'
    real_dir.mkdir()
    (real_dir / 'src.bin').write_bytes(b'data')
    link_dir = tmp_path / 'link_dir'
    os.symlink(str(real_dir), str(link_dir))
    dst = tmp_path / 'dst.bin'
    with pytest.raises(SymlinkInPathError):
        safe_copy(str(link_dir / 'src.bin'), str(dst))


def test_safe_copy_missing_src(tmp_path):
    with pytest.raises(FileNotFoundError):
        safe_copy(str(tmp_path / 'nonexistent.bin'), str(tmp_path / 'dst.bin'))


# ── safe_copytree ─────────────────────────────────────────────────────────────

def _make_tree(root, files=(('a.txt', b'aa'), ('b.txt', b'bb'))):
    root.mkdir()
    for name, data in files:
        (root / name).write_bytes(data)


def test_safe_copytree_flat(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'dst'
    _make_tree(src)
    rv = safe_copytree(str(src), str(dst))
    assert rv == str(dst)
    assert (dst / 'a.txt').read_bytes() == b'aa'
    assert (dst / 'b.txt').read_bytes() == b'bb'


def test_safe_copytree_preserves_dir_uid_gid(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'dst'
    _make_tree(src)
    os.chown(str(src), 8675309, 8675310)
    safe_copytree(str(src), str(dst))
    st = os.stat(str(dst))
    assert st.st_uid == 8675309
    assert st.st_gid == 8675310


def test_safe_copytree_preserves_file_uid_gid(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'dst'
    _make_tree(src)
    os.chown(str(src / 'a.txt'), 8675309, 8675310)
    safe_copytree(str(src), str(dst))
    st = os.stat(str(dst / 'a.txt'))
    assert st.st_uid == 8675309
    assert st.st_gid == 8675310


def test_safe_copytree_nested(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'top.txt').write_bytes(b'top')
    sub = src / 'sub'
    sub.mkdir()
    (sub / 'inner.txt').write_bytes(b'inner')
    os.chown(str(sub), 8675309, 8675310)
    dst = tmp_path / 'dst'
    safe_copytree(str(src), str(dst))
    assert (dst / 'top.txt').read_bytes() == b'top'
    assert (dst / 'sub' / 'inner.txt').read_bytes() == b'inner'
    sub_st = os.stat(str(dst / 'sub'))
    assert sub_st.st_uid == 8675309
    assert sub_st.st_gid == 8675310


def test_safe_copytree_missing_src(tmp_path):
    with pytest.raises(FileNotFoundError):
        safe_copytree(str(tmp_path / 'nonexistent'), str(tmp_path / 'dst'))


def test_safe_copytree_refuses_existing_dst(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'dst'
    _make_tree(src)
    dst.mkdir()
    with pytest.raises(FileExistsError):
        safe_copytree(str(src), str(dst))


def test_safe_copytree_preserves_file_symlinks(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    target = src / 'target.txt'
    target.write_bytes(b'data')
    link = src / 'link.txt'
    os.symlink('target.txt', str(link))
    dst = tmp_path / 'dst'
    safe_copytree(str(src), str(dst))
    dst_link = dst / 'link.txt'
    assert os.path.islink(str(dst_link))
    assert os.readlink(str(dst_link)) == 'target.txt'


def test_safe_copy_works_as_copytree_copy_function(tmp_path):
    # Direct shutil.copytree with copy_function=safe_copy must preserve
    # file uid/gid (proves the drop-in claim).
    import shutil as _shutil
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'a.txt').write_bytes(b'aa')
    os.chown(str(src / 'a.txt'), 8675309, 8675310)
    dst = tmp_path / 'dst'
    _shutil.copytree(str(src), str(dst), copy_function=safe_copy)
    st = os.stat(str(dst / 'a.txt'))
    assert st.st_uid == 8675309
    assert st.st_gid == 8675310
