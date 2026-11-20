import errno
import os
import pathlib
import stat
import subprocess
import sys

import pytest
import truenas_os
from truenas_os_pyutils.io import (
    SymlinkInPathError,
    atomic_replace,
    atomic_write,
    safe_makedirs,
    safe_open,
    safe_open_raw,
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


def test_safe_open_fifo_raises(tmp_path):
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)

    with pytest.raises(OSError) as exc_info:
        with safe_open(str(fifo)) as fh:
            fh.read()

    assert exc_info.value.errno == errno.EINVAL


def test_safe_open_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        with safe_open(str(tmp_path)) as fh:
            fh.read()


# ── safe_open_raw ─────────────────────────────────────────────────────────────

def test_safe_open_raw_regular_file(tmp_path):
    f = tmp_path / 'test.txt'
    f.write_text('data')
    with safe_open_raw(str(f)) as fd:
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        assert not os.get_inheritable(fd)
        assert os.read(fd, 16) == b'data'

    with pytest.raises(OSError) as exc_info:
        os.fstat(fd)

    assert exc_info.value.errno == errno.EBADF


def test_safe_open_raw_directory(tmp_path):
    with safe_open_raw(str(tmp_path)) as fd:
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        assert not os.get_inheritable(fd)


def test_safe_open_raw_symlink_raises(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'real' / 'file.txt').write_text('data')
    (tmp_path / 'link').symlink_to(tmp_path / 'real')

    for path in ('link', 'link/file.txt'):
        with pytest.raises(SymlinkInPathError):
            with safe_open_raw(str(tmp_path / path)):
                pass


@pytest.mark.parametrize('kind,flags', [
    ('fifo', os.O_RDONLY),
    ('fifo', os.O_WRONLY),
    ('socket', os.O_RDONLY),
    ('chardev', os.O_RDONLY),
], ids=['fifo-read', 'fifo-write', 'socket', 'chardev'])
def test_safe_open_raw_special_file_raises(tmp_path, kind, flags):
    """Anything but a regular file or directory is refused before it is opened:
    a FIFO does not wait for its other end, and a device's open method does
    not run."""
    path = tmp_path / kind
    if kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'socket':
        os.mknod(path, stat.S_IFSOCK | 0o600)
    else:
        path = '/dev/null'

    with pytest.raises(OSError) as exc_info:
        with safe_open_raw(str(path), flags):
            pass

    assert exc_info.value.errno == errno.EINVAL


# Holds a write lease on argv[1] -- the same kernel object as an NFSv4 write
# delegation held by knfsd -- and releases it 0.2 s after the break starts.
# Same holder as tests/test_fsiter.py.
_LEASE_HOLDER = r'''
import fcntl, os, signal, sys, time
fd = os.open(sys.argv[1], os.O_RDWR)
signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGIO])
try:
    fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_WRLCK)
except OSError as e:
    print('nolease', e.errno, flush=True)
    sys.exit(0)
print('ready', flush=True)
if signal.sigtimedwait([signal.SIGIO], 10) is None:
    print('nobreak', flush=True)
    sys.exit(1)
time.sleep(0.2)
fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_UNLCK)
print('released', flush=True)
'''


@pytest.mark.parametrize('nonblock', [False, True], ids=['wait', 'nonblock'])
def test_safe_open_raw_lease_break(tmp_path, nonblock):
    """An open that breaks a lease waits for the holder to release it, unless
    the caller passes O_NONBLOCK."""
    leased = tmp_path / 'leased'
    leased.write_bytes(b'x' * 64)

    with subprocess.Popen(
        [sys.executable, '-c', _LEASE_HOLDER, str(leased)],
        stdout=subprocess.PIPE, text=True,
    ) as holder:
        try:
            line = holder.stdout.readline()
            status = line.split()
            if status[:1] == ['nolease']:
                pytest.skip(f'no write lease here: {os.strerror(int(status[1]))}')
            assert status == ['ready'], line

            if nonblock:
                with pytest.raises(BlockingIOError):
                    with safe_open_raw(str(leased), os.O_RDONLY | os.O_NONBLOCK):
                        pass
            else:
                with safe_open_raw(str(leased)):
                    pass

            out, _ = holder.communicate(timeout=20)
        finally:
            if holder.poll() is None:
                holder.kill()

    assert holder.returncode == 0
    assert out.split() == ['released']


# ── safe_makedirs ─────────────────────────────────────────────────────────────

def test_safe_makedirs_creates_parents(tmp_path):
    target = tmp_path / 'a' / 'b' / 'c'
    safe_makedirs(str(target), 0o700)

    assert target.is_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    # Parents get 0o777 less the umask, as os.makedirs() gives them
    (tmp_path / 'ref').mkdir(0o777)
    ref_mode = stat.S_IMODE((tmp_path / 'ref').stat().st_mode)
    assert stat.S_IMODE((tmp_path / 'a').stat().st_mode) == ref_mode
    assert stat.S_IMODE((tmp_path / 'a' / 'b').stat().st_mode) == ref_mode


def test_safe_makedirs_exist_ok(tmp_path):
    target = str(tmp_path / 'a')
    safe_makedirs(target)
    safe_makedirs(target, exist_ok=True)

    with pytest.raises(FileExistsError):
        safe_makedirs(target)


def test_safe_makedirs_dir_fd(tmp_path):
    dirfd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        safe_makedirs('a/b', dir_fd=dirfd)
    finally:
        os.close(dirfd)

    assert (tmp_path / 'a' / 'b').is_dir()


def test_safe_makedirs_symlink_creates_nothing(tmp_path):
    """A symlink in the path fails the call before anything is created where it
    points, where os.makedirs() would create link/a/b inside elsewhere."""
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / 'link').symlink_to(elsewhere)

    for path in ('link', 'link/a/b'):
        with pytest.raises(SymlinkInPathError):
            safe_makedirs(str(tmp_path / path), exist_ok=True)

    assert list(elsewhere.iterdir()) == []


def test_safe_makedirs_file_in_path_raises(tmp_path):
    (tmp_path / 'file').write_text('data')

    with pytest.raises(NotADirectoryError) as exc_info:
        safe_makedirs(str(tmp_path / 'file' / 'a'))

    assert exc_info.value.filename == str(tmp_path / 'file' / 'a')

    with pytest.raises(FileExistsError):
        safe_makedirs(str(tmp_path / 'file'), exist_ok=True)


@pytest.mark.parametrize('path', ['', '/', '.', 'file', 'file/a', 'dir', 'new/sub', pathlib.Path('new/sub')])
@pytest.mark.parametrize('exist_ok', [False, True])
def test_safe_makedirs_matches_os_makedirs(tmp_path, monkeypatch, path, exist_ok):
    results = []
    for fn in (os.makedirs, safe_makedirs):
        cwd = tmp_path / fn.__name__
        cwd.mkdir()
        (cwd / 'file').write_text('data')
        (cwd / 'dir').mkdir()
        monkeypatch.chdir(cwd)
        try:
            fn(path, exist_ok=exist_ok)
            results.append(sorted(str(p.relative_to(cwd)) for p in cwd.rglob('*')))
        except OSError as e:
            results.append([type(e), e.errno, e.filename])

    assert results[0] == results[1]


# ── resolve ───────────────────────────────────────────────────────────────────

@pytest.fixture
def base_fd(tmp_path):
    (tmp_path / 'base').mkdir()
    (tmp_path / 'base' / 'inside').write_text('inside')
    (tmp_path / 'outside').write_text('outside')
    fd = os.open(str(tmp_path / 'base'), os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def test_safe_open_resolve_beneath(tmp_path, base_fd):
    with safe_open('inside', dir_fd=base_fd, resolve=truenas_os.RESOLVE_BENEATH) as fh:
        assert fh.read() == 'inside'

    with safe_open('../outside', dir_fd=base_fd) as fh:
        assert fh.read() == 'outside'

    for path in ('../outside', str(tmp_path / 'outside')):
        with pytest.raises(OSError) as exc_info:
            with safe_open(path, dir_fd=base_fd, resolve=truenas_os.RESOLVE_BENEATH) as fh:
                fh.read()

        assert exc_info.value.errno == errno.EXDEV


def test_safe_open_raw_resolve_beneath(tmp_path, base_fd):
    for flags in (os.O_RDONLY, os.O_RDWR | os.O_CREAT):
        with pytest.raises(OSError) as exc_info:
            with safe_open_raw('../created', flags, dir_fd=base_fd, resolve=truenas_os.RESOLVE_BENEATH):
                pass

        assert exc_info.value.errno == errno.EXDEV

    assert not (tmp_path / 'created').exists()


def test_safe_open_resolve_keeps_no_symlinks(tmp_path, base_fd):
    (tmp_path / 'base' / 'link').symlink_to('inside')

    with pytest.raises(SymlinkInPathError):
        with safe_open('link', dir_fd=base_fd, resolve=truenas_os.RESOLVE_IN_ROOT) as fh:
            fh.read()


def test_safe_open_raw_resolve_no_xdev():
    root_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        with safe_open_raw('proc/version', dir_fd=root_fd):
            pass

        with pytest.raises(OSError) as exc_info:
            with safe_open_raw('proc/version', dir_fd=root_fd, resolve=truenas_os.RESOLVE_NO_XDEV):
                pass

        assert exc_info.value.errno == errno.EXDEV
    finally:
        os.close(root_fd)


def test_safe_makedirs_resolve_beneath(tmp_path, base_fd):
    safe_makedirs('a/b', dir_fd=base_fd, resolve=truenas_os.RESOLVE_BENEATH)
    assert (tmp_path / 'base' / 'a' / 'b').is_dir()

    for path in ('../escaped', 'a/../c', str(tmp_path / 'absolute')):
        with pytest.raises(OSError) as exc_info:
            safe_makedirs(path, dir_fd=base_fd, resolve=truenas_os.RESOLVE_BENEATH)

        assert exc_info.value.errno == errno.EXDEV
        assert exc_info.value.filename == path

    assert sorted(p.name for p in tmp_path.iterdir()) == ['base', 'outside']
    assert sorted(p.name for p in (tmp_path / 'base').iterdir()) == ['a', 'inside']


def test_safe_makedirs_resolve_no_xdev():
    with pytest.raises(OSError) as exc_info:
        safe_makedirs('/proc/not-created', resolve=truenas_os.RESOLVE_NO_XDEV)

    assert exc_info.value.errno == errno.EXDEV


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


def test_atomic_write_noclobber_creates_new(atomic_dir):
    target = str(atomic_dir / 'noclobber_new.txt')
    assert not os.path.exists(target)
    with atomic_write(target, noclobber=True) as f:
        f.write('fresh')
    with open(target) as f:
        assert f.read() == 'fresh'


def test_atomic_write_noclobber_creates_new_binary(atomic_dir):
    target = str(atomic_dir / 'noclobber_new.bin')
    with atomic_write(target, 'wb', noclobber=True) as f:
        f.write(b'\x00\x01\x02')
    with open(target, 'rb') as f:
        assert f.read() == b'\x00\x01\x02'


def test_atomic_write_noclobber_raises_on_existing(atomic_dir):
    target = str(atomic_dir / 'noclobber_existing.txt')
    with open(target, 'w') as f:
        f.write('original')

    with pytest.raises(FileExistsError) as exc_info:
        with atomic_write(target, noclobber=True) as f:
            f.write('should not be written')

    assert exc_info.value.errno == errno.EEXIST
    # Original content is preserved
    with open(target) as f:
        assert f.read() == 'original'


def test_atomic_write_noclobber_raises_on_existing_symlink(atomic_dir):
    real = atomic_dir / 'real_target.txt'
    real.write_text('real content')
    link = atomic_dir / 'noclobber_symlink.txt'
    link.symlink_to(real)

    with pytest.raises(FileExistsError) as exc_info:
        with atomic_write(str(link), noclobber=True) as f:
            f.write('should not be written')

    assert exc_info.value.errno == errno.EEXIST


def test_atomic_write_noclobber_default_is_false(atomic_dir):
    target = str(atomic_dir / 'default_replaces.txt')
    with open(target, 'w') as f:
        f.write('original')
    with atomic_write(target) as f:
        f.write('replaced')
    with open(target) as f:
        assert f.read() == 'replaced'


# ── atomic_write: symlinked directories and temporary directory cleanup ───────

def test_atomic_write_symlinked_parent_creates_nothing(tmp_path):
    """A symlink in the target's directory path fails with ELOOP before the
    temporary directory is created, so nothing appears where it points."""
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / 'etc').symlink_to(elsewhere)

    with pytest.raises(OSError) as exc_info:
        with atomic_write(str(tmp_path / 'etc' / 'hostname')) as f:
            f.write('should not be written')

    assert exc_info.value.errno == errno.ELOOP
    assert list(elsewhere.iterdir()) == []


def test_atomic_write_symlinked_tmppath_creates_nothing(tmp_path):
    target_dir = tmp_path / 'target'
    target_dir.mkdir()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / 'tmp_link').symlink_to(elsewhere)

    with pytest.raises(OSError) as exc_info:
        with atomic_write(str(target_dir / 'file.txt'), tmppath=str(tmp_path / 'tmp_link')) as f:
            f.write('should not be written')

    assert exc_info.value.errno == errno.ELOOP
    assert list(elsewhere.iterdir()) == []
    assert list(target_dir.iterdir()) == []


@pytest.mark.parametrize('existing', [False, True], ids=['new', 'replace'])
def test_atomic_write_leaves_no_temporary_directory(tmp_path, existing):
    target = tmp_path / 'file.txt'
    if existing:
        target.write_text('original')

    with atomic_write(str(target)) as f:
        f.write('written')

    assert target.read_text() == 'written'
    assert [p.name for p in tmp_path.iterdir()] == ['file.txt']


def test_atomic_write_leaves_no_temporary_directory_on_exception(tmp_path):
    target = tmp_path / 'file.txt'
    target.write_text('original')

    with pytest.raises(ValueError):
        with atomic_write(str(target)) as f:
            f.write('partial')
            raise ValueError('abort')

    assert target.read_text() == 'original'
    assert [p.name for p in tmp_path.iterdir()] == ['file.txt']


def test_atomic_write_leaves_no_temporary_directory_in_tmppath(tmp_path):
    target_dir = tmp_path / 'target'
    target_dir.mkdir()
    tmp_dir = tmp_path / 'tmp'
    tmp_dir.mkdir()

    with atomic_write(str(target_dir / 'file.txt'), tmppath=str(tmp_dir)) as f:
        f.write('written')

    assert (target_dir / 'file.txt').read_text() == 'written'
    assert list(tmp_dir.iterdir()) == []
