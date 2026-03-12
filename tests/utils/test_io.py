import errno
import os
import stat

import pytest

from truenas_os_utils.io import (
    SymlinkInPathError,
    atomic_replace,
    atomic_write,
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
