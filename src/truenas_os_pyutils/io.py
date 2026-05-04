# NOTE: tests are provided in tests/utils/test_io.py
# Any updates to this file should have corresponding updates to tests

from contextlib import contextmanager
import errno
import os
from tempfile import TemporaryDirectory
import typing

import truenas_os


class SymlinkInPathError(OSError):
    """Raised by safe_open() when a symlink is detected in the path."""
    def __init__(self, path: str) -> None:
        super().__init__(errno.ELOOP, 'Symlink detected in path', path)


@contextmanager
def safe_open(path: str | bytes, mode: str = 'r', buffering: int = -1,
              encoding: str | None = None, errors: str | None = None,
              newline: str | None = None, *,
              dir_fd: int = truenas_os.AT_FDCWD) -> typing.Generator[typing.IO[typing.Any], None, None]:
    """Drop-in for open() that uses openat2(RESOLVE_NO_SYMLINKS) as the opener,
    preventing symlink-based TOCTOU attacks.

    Args:
        path: Path to the file to open. May be absolute or relative to dir_fd.
        mode: File open mode string, as accepted by open() (default: 'r').
        buffering: Buffering policy, as accepted by open() (default: -1).
        encoding: Text encoding, as accepted by open() (default: None).
        errors: Error handler for encoding, as accepted by open() (default: None).
        newline: Newline handling, as accepted by open() (default: None).
        dir_fd: Directory file descriptor to resolve path relative to
                (default: AT_FDCWD, i.e. the process current working directory).

    Yields:
        File object for the opened file.

    Raises:
        SymlinkInPathError: If any component of path is a symlink.
        FileNotFoundError: If path does not exist and mode does not imply creation.
        OSError: For other openat2 failures.

    Note:
        - Uses RESOLVE_NO_SYMLINKS, so symlinks anywhere in the path are rejected,
          not just at the final component.
        - When dir_fd is supplied, path may be relative; openat2 resolves it
          against the given directory fd.
    """
    def _opener(p: str, flags: int) -> int:
        try:
            return truenas_os.openat2(
                p, flags,
                dir_fd=dir_fd,
                mode=0o666 if (flags & os.O_CREAT) else 0,
                resolve=truenas_os.RESOLVE_NO_SYMLINKS,
            )
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise SymlinkInPathError(p) from e
            raise

    with open(path, mode, buffering, encoding, errors, newline, opener=_opener) as f:
        yield f


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
          will be set to ELOOP

    Example:
        with atomic_write('/etc/config.conf') as f:
            f.write("config data")
        # File is atomically replaced here
    """
    if mode not in ("w", "wb"):
        raise ValueError(f'{mode}: invalid mode. Only "w" and "wb" are supported.')

    if tmppath is None:
        tmppath = os.path.dirname(target)

    with TemporaryDirectory(dir=tmppath) as tmpdir:
        # We're using absolute paths here initially to open dir fds for the write and rename operations. This is
        # generally susceptible to symlink races and so it's being mitigated by setting RESOLVE_NO_SYMLINKS. *IF* an
        # intermediate symlink is discovered during path resolution in kernel (e.g. /etc/default/foo and the
        # `/etc/default` component is a symlink), then this will fail with an OSError with errno set to ELOOP
        dst_dirpath = os.path.dirname(target)
        target_filename = os.path.basename(target)

        dst_dirfd = truenas_os.openat2(dst_dirpath, os.O_DIRECTORY, resolve=truenas_os.RESOLVE_NO_SYMLINKS)
        try:
            src_dirfd = truenas_os.openat2(tmpdir, os.O_DIRECTORY, resolve=truenas_os.RESOLVE_NO_SYMLINKS)
            try:
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
                os.close(src_dirfd)
        finally:
            os.close(dst_dirfd)
