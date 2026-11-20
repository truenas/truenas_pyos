# NOTE: tests are provided in tests/utils/test_io.py
# Any updates to this file should have corresponding updates to tests

from contextlib import contextmanager
import errno
import os
import stat
import typing
import uuid

import truenas_os


class SymlinkInPathError(OSError):
    """Raised by safe_open() and safe_open_raw() when a symlink is detected in the path."""
    def __init__(self, path: str) -> None:
        super().__init__(errno.ELOOP, 'Symlink detected in path', path)


def _safe_openat2(path: str, flags: int, dir_fd: int, mode: int = 0o666, resolve: int = 0) -> int:
    """The opener of safe_open() and safe_open_raw(); see safe_open_raw() for what it refuses."""
    resolve |= truenas_os.RESOLVE_NO_SYMLINKS
    try:
        if flags & os.O_CREAT:
            # The kernel's O_CREAT checks (fs.protected_regular, fs.protected_fifos) apply as to any creating open
            return truenas_os.openat2(path, flags | os.O_CLOEXEC, dir_fd=dir_fd, mode=mode, resolve=resolve)

        try:
            # O_DIRECTORY fails anything but a directory before opening it
            return truenas_os.openat2(path, flags | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=dir_fd, resolve=resolve)
        except NotADirectoryError:
            pass

        # O_PATH neither blocks on a FIFO nor runs a device's open method
        path_fd = truenas_os.openat2(path, os.O_PATH | os.O_CLOEXEC, dir_fd=dir_fd, resolve=resolve)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise SymlinkInPathError(path) from e
        raise

    try:
        st_mode = os.fstat(path_fd).st_mode
        if not (stat.S_ISREG(st_mode) or stat.S_ISDIR(st_mode)):
            raise OSError(errno.EINVAL, 'Not a regular file or directory', path)

        # Reopen the file just checked, so nothing can be swapped in; without O_NONBLOCK this waits out a lease
        # break. Symlinks were refused above, and the /proc magic link itself would fail O_NOFOLLOW.
        try:
            return os.open(f'/proc/self/fd/{path_fd}', flags & ~os.O_NOFOLLOW)
        except OSError as e:
            e.filename = path
            raise
    finally:
        os.close(path_fd)


@contextmanager
def safe_open(path: str | bytes, mode: str = 'r', buffering: int = -1,
              encoding: str | None = None, errors: str | None = None,
              newline: str | None = None, *,
              dir_fd: int = truenas_os.AT_FDCWD,
              resolve: int = 0) -> typing.Generator[typing.IO[typing.Any], None, None]:
    """Drop-in for open() that opens path as safe_open_raw() does, preventing
    symlink-based TOCTOU attacks.

    Args:
        path: Path to the file to open. May be absolute or relative to dir_fd.
        mode: File open mode string, as accepted by open() (default: 'r').
        buffering: Buffering policy, as accepted by open() (default: -1).
        encoding: Text encoding, as accepted by open() (default: None).
        errors: Error handler for encoding, as accepted by open() (default: None).
        newline: Newline handling, as accepted by open() (default: None).
        dir_fd: Directory file descriptor to resolve path relative to
                (default: AT_FDCWD, i.e. the process current working directory).
        resolve: RESOLVE_* flags to add to RESOLVE_NO_SYMLINKS, which is always
                 set, such as RESOLVE_BENEATH or RESOLVE_NO_XDEV (default: 0).

    Yields:
        File object for the opened file.

    Raises:
        SymlinkInPathError: If any component of path is a symlink.
        FileNotFoundError: If path does not exist and mode does not imply creation.
        OSError: EINVAL if a mode that does not create the file ('r', 'r+')
                 names a FIFO, socket or device node; EXDEV if resolve refuses
                 path; otherwise the errors of open().

    Note:
        - Uses RESOLVE_NO_SYMLINKS, so symlinks anywhere in the path are rejected,
          not just at the final component.
        - When dir_fd is supplied, path may be relative; openat2 resolves it
          against the given directory fd.
        - A mode that does not create the file never blocks on a FIFO or opens
          a device node, and waits out a lease break (see safe_open_raw()).
    """
    def _opener(p: str, flags: int) -> int:
        return _safe_openat2(p, flags, dir_fd, resolve=resolve)

    with open(path, mode, buffering, encoding, errors, newline, opener=_opener) as f:
        yield f


@contextmanager
def safe_open_raw(path: str, flags: int = os.O_RDONLY, *, dir_fd: int = truenas_os.AT_FDCWD,
                  mode: int = 0o666, resolve: int = 0) -> typing.Generator[int, None, None]:
    """Open path with openat2(RESOLVE_NO_SYMLINKS) and yield the fd, closing it on exit.

    Without O_CREAT, only a regular file or directory is opened. Anything else
    (a FIFO, socket or device node) is refused before it is opened, so a FIFO
    cannot block the open and a device's open method never runs. An open that
    breaks a lease, such as an NFSv4 delegation, waits for the break as
    open(2) does; pass O_NONBLOCK to fail with EWOULDBLOCK instead.

    With O_CREAT, flags are passed to openat2() as they are, so the kernel's
    O_CREAT checks (fs.protected_regular, fs.protected_fifos) apply.

    Args:
        path: Path to open. May be absolute or relative to dir_fd.
        flags: os.O_* flags, as for os.open() (default: O_RDONLY).
        dir_fd: Directory file descriptor to resolve path relative to
                (default: AT_FDCWD, i.e. the process current working directory).
        mode: Permissions for a file created by O_CREAT (default: 0o666).
        resolve: RESOLVE_* flags to add to RESOLVE_NO_SYMLINKS, which is always
                 set, such as RESOLVE_BENEATH or RESOLVE_NO_XDEV (default: 0).

    Yields:
        A non-inheritable file descriptor, closed when the context exits.

    Raises:
        SymlinkInPathError: If any component of path is a symlink.
        OSError: EINVAL if, without O_CREAT, path is neither a regular file
                 nor a directory; EXDEV if resolve refuses path; otherwise the
                 errors of open(2).
    """
    fd = _safe_openat2(path, flags, dir_fd, mode, resolve)
    try:
        yield fd
    finally:
        os.close(fd)


def safe_makedirs(path: str | os.PathLike[str], mode: int = 0o777, exist_ok: bool = False, *,
                  dir_fd: int = truenas_os.AT_FDCWD, resolve: int = 0) -> None:
    """Create the directory path and any missing parents, as os.makedirs() does, without following symlinks.

    Each missing component is created with mkdirat() relative to an fd for
    its parent, and then opened with openat2(RESOLVE_NO_SYMLINKS), so a
    symlink anywhere in path fails the call instead of redirecting where
    directories are created. As with os.makedirs(), mode applies to the last
    directory only; missing parents are created with 0o777, and the umask
    applies to both.

    Args:
        path: Directory to create. May be absolute or relative to dir_fd.
        mode: Permissions for the last directory (default: 0o777).
        exist_ok: If False (the default), fail if path already exists.
        dir_fd: Directory file descriptor to resolve a relative path against
                (default: AT_FDCWD, i.e. the process current working directory).
        resolve: RESOLVE_* flags to add to RESOLVE_NO_SYMLINKS, which is always
                 set, such as RESOLVE_BENEATH or RESOLVE_NO_XDEV (default: 0).
                 They apply to each component's lookup beneath its parent, so
                 RESOLVE_BENEATH refuses any '..' in path.

    Raises:
        SymlinkInPathError: If any component of path is a symlink.
        FileExistsError: If path already exists and exist_ok is False, or
                         exists and is not a directory.
        NotADirectoryError: If a component before the last exists but is not
                            a directory.
        OSError: EXDEV if resolve refuses path; otherwise the errors of
                 mkdir(2) and openat2(2).
    """
    path = os.fspath(path)
    if not path:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)

    resolve |= truenas_os.RESOLVE_NO_SYMLINKS
    names = [name for name in path.split('/') if name not in ('', '.')]
    try:
        fd = truenas_os.openat2('/' if path.startswith('/') else '.', os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC,
                                dir_fd=dir_fd, resolve=resolve)
    except OSError as e:
        e.filename = path
        raise

    try:
        if not names and not exist_ok:
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)

        for i, name in enumerate(names):
            last = i == len(names) - 1
            try:
                try:
                    os.mkdir(name, mode if last else 0o777, dir_fd=fd)
                except FileExistsError:
                    if last and not exist_ok:
                        raise

                next_fd = truenas_os.openat2(name, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=fd,
                                             resolve=resolve)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise SymlinkInPathError(path) from e
                if last and e.errno == errno.ENOTDIR:
                    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path) from e
                # Name the path the caller passed, not the one component
                e.filename = path
                raise

            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)


def atomic_replace(
    *,
    temp_path: str,
    target_file: str,
    data: bytes,
    uid: int = 0,
    gid: int = 0,
    perms: int = 0o644
) -> None:
    """Atomically replace a file's contents with symlink race protection.

    Uses openat2 with RESOLVE_NO_SYMLINKS and renameat2 with AT_RENAME_EXCHANGE
    to safely replace a file's contents without risk of:
    - Partially written files visible to readers
    - Symlink race conditions (TOCTOU attacks)
    - Data loss if write operation fails

    The function creates a temporary file in a secure temporary directory,
    writes the data with proper ownership and permissions, syncs to disk,
    then atomically exchanges it with the target file.

    Args:
        temp_path: Directory for temporary file creation. Must be on the same
                   filesystem as target_file and must not contain symlinks in path.
        target_file: Absolute path to the file to replace. Path must not contain
                     symlinks.
        data: Binary data to write to the file.
        uid: User ID for file ownership (default: 0/root). Use -1 to preserve existing file's uid.
        gid: Group ID for file ownership (default: 0/root). Use -1 to preserve existing file's gid.
        perms: File permissions as octal integer (default: 0o644).

    Raises:
        OSError: If openat2/renameat2 operations fail.

    Note:
        - temp_path and target_file must be on the same filesystem for rename to work
        - If target_file doesn't exist, uses regular rename instead of exchange
        - If an intermediate symlink is detected during openat2 call then errno
          will be set to ELOOP
        - When uid/gid are -1, the existing file's ownership is preserved if it exists
    """
    with atomic_write(target_file, "wb", tmppath=temp_path, uid=uid, gid=gid, perms=perms) as f:
        f.write(data)


@contextmanager
def _temporary_directory_at(dir_fd: int) -> typing.Generator[int, None, None]:
    """Create a private temporary directory beneath `dir_fd` and yield an fd for it.

    The directory and anything left in it are removed on exit. Unlike
    tempfile.TemporaryDirectory, nothing here resolves a path string, so a
    symlink in the path cannot redirect where the directory is created or
    removed.
    """
    # A random name cannot be guessed in advance, so it will not already exist
    name = f'tmp{uuid.uuid4().hex}'
    os.mkdir(name, 0o700, dir_fd=dir_fd)

    try:
        fd = truenas_os.openat2(
            name, os.O_DIRECTORY, dir_fd=dir_fd,
            resolve=truenas_os.RESOLVE_BENEATH | truenas_os.RESOLVE_NO_SYMLINKS,
        )
        try:
            yield fd
        finally:
            try:
                # Only atomic_write writes here: the file being written, or the old one after an exchange
                for entry in os.listdir(fd):
                    os.unlink(entry, dir_fd=fd)
            finally:
                os.close(fd)
    finally:
        os.rmdir(name, dir_fd=dir_fd)


@typing.overload
@contextmanager
def atomic_write(target: str, mode: typing.Literal["w"] = "w", *, tmppath: str | None = None,
                 uid: int = 0, gid: int = 0, perms: int = 0o644,
                 noclobber: bool = False) -> typing.Generator[typing.TextIO, None, None]: ...


@typing.overload
@contextmanager
def atomic_write(target: str, mode: typing.Literal["wb"], *, tmppath: str | None = None,
                 uid: int = 0, gid: int = 0, perms: int = 0o644,
                 noclobber: bool = False) -> typing.Generator[typing.BinaryIO, None, None]: ...


@contextmanager
def atomic_write(target: str, mode: typing.Literal["w", "wb"] = "w", *, tmppath: str | None = None,
                 uid: int = 0, gid: int = 0, perms: int = 0o644,
                 noclobber: bool = False) -> typing.Generator[typing.IO[typing.Any], None, None]:
    """Context manager for atomic file writes with symlink race protection.

    Yields a file-like object for writing. On successful context manager exit,
    atomically replaces the target file using renameat2 with proper synchronization.
    Uses openat2 with RESOLVE_NO_SYMLINKS and renameat2 with AT_RENAME_EXCHANGE
    to safely replace a file's contents without risk of:
    - Partially written files visible to readers
    - Symlink race conditions (TOCTOU attacks)
    - Data loss if write operation fails

    Args:
        target: Absolute path to the file to write/replace. Path must not contain
                symlinks.
        mode: File open mode, either "w" (text) or "wb" (binary). Defaults to "w".
        tmppath: Directory for temporary file creation. If None, uses dirname(target).
                 Must be on same filesystem as target and must not contain symlinks.
        uid: User ID for file ownership (default: 0/root).
        gid: Group ID for file ownership (default: 0/root).
        perms: File permissions as octal integer (default: 0o644).
        noclobber: If True, fail with FileExistsError when target already exists,
                   and use AT_RENAME_NOREPLACE so the rename also fails atomically
                   if target races into existence between the initial check and
                   the rename. Defaults to False (existing target is replaced).

    Yields:
        File-like object for writing

    Raises:
        FileExistsError: If noclobber=True and target already exists.
        OSError: If openat2/renameat2 operations fail.

    Note:
        - tmppath and target must be on the same filesystem for rename to work
        - With noclobber=False (default): existing target replaced via
          AT_RENAME_EXCHANGE, or plain rename when target does not exist
        - With noclobber=True: fail-fast on existing target, then
          AT_RENAME_NOREPLACE on the rename to catch races
        - File is only replaced if the context manager exits successfully
        - If an intermediate symlink is detected during openat2 call then errno
          will be set to ELOOP. This is checked before the temporary directory
          is created, so nothing is created through the symlink.

    Example:
        with atomic_write('/etc/config.conf') as f:
            f.write("config data")
        # File is atomically replaced here
    """
    if mode not in ("w", "wb"):
        raise ValueError(f'{mode}: invalid mode. Only "w" and "wb" are supported.')

    dst_dirpath = os.path.dirname(target)
    target_filename = os.path.basename(target)

    # Open the directories with RESOLVE_NO_SYMLINKS before anything is created in them. *IF* an intermediate
    # symlink is discovered during path resolution in kernel (e.g. /etc/default/foo and the `/etc/default`
    # component is a symlink), then this fails with an OSError with errno set to ELOOP, and nothing has been
    # created through the symlink. The temporary directory is then created relative to the opened fd.
    dst_dirfd = truenas_os.openat2(dst_dirpath, os.O_DIRECTORY, resolve=truenas_os.RESOLVE_NO_SYMLINKS)
    try:
        if tmppath is None:
            tmp_parent_fd = dst_dirfd
        else:
            tmp_parent_fd = truenas_os.openat2(tmppath, os.O_DIRECTORY, resolve=truenas_os.RESOLVE_NO_SYMLINKS)
        try:
            with _temporary_directory_at(tmp_parent_fd) as src_dirfd:
                # Check if target file exists and get its stat
                existing_stat = None
                try:
                    existing_stat = os.lstat(target_filename, dir_fd=dst_dirfd)
                    if uid == -1:
                        uid = existing_stat.st_uid

                    if gid == -1:
                        gid = existing_stat.st_gid

                except FileNotFoundError:
                    pass

                if noclobber and existing_stat is not None:
                    raise FileExistsError(errno.EEXIST, 'Destination exists', target)

                with safe_open(target_filename, mode, dir_fd=src_dirfd) as f:
                    os.fchown(f.fileno(), uid, gid)
                    os.fchmod(f.fileno(), perms)
                    yield f
                    f.flush()
                    os.fsync(f.fileno())

                # Pick rename flags for the requested semantics.
                if noclobber:
                    rename_flags = truenas_os.AT_RENAME_NOREPLACE
                elif existing_stat:
                    rename_flags = truenas_os.AT_RENAME_EXCHANGE
                else:
                    rename_flags = 0

                truenas_os.renameat2(
                    src=target_filename,
                    dst=target_filename,
                    src_dir_fd=src_dirfd,
                    dst_dir_fd=dst_dirfd,
                    flags=rename_flags
                )
        finally:
            if tmp_parent_fd != dst_dirfd:
                os.close(tmp_parent_fd)
    finally:
        os.close(dst_dirfd)
