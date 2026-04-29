# File-level copy and clone primitives.
#
# These are standalone — they take open file descriptors and operate on a
# single source/destination pair.  copytree() composes them across a tree;
# callers can also use them directly without involving the recursive walker.
#
# Tests are in tests/utils/test_truenas_shutil_copy.py.
from __future__ import annotations

from errno import EXDEV
from os import (
    SEEK_CUR,
    copy_file_range,
    fchmod,
    fstat,
    lseek,
    sendfile,
)
from shutil import copyfileobj
from stat import S_IMODE

from truenas_os import fgetxattr, fsetxattr


__all__ = [
    "MAX_RW_SZ",
    "clonefile",
    "copy_permissions",
    "copy_xattrs",
    "copyfile",
    "copysendfile",
    "copyuserspace",
]


# Maximum size of a single read/write in the kernel.  Aligning to a page
# boundary keeps copy_file_range / sendfile at their best throughput.
MAX_RW_SZ = 2147483647 & ~4096


_POSIX_ACCESS_XATTR = "system.posix_acl_access"
_POSIX_DEFAULT_XATTR = "system.posix_acl_default"
_ZFS_NATIVE_XATTR = "system.nfs4_acl_xdr"

# All ACL-bearing xattrs (POSIX1E access + default, plus the ZFS NFS4 XDR blob).
ACL_XATTRS = frozenset({_POSIX_ACCESS_XATTR, _POSIX_DEFAULT_XATTR, _ZFS_NATIVE_XATTR})

# ACLs that encode the file's own access permissions; the POSIX *default*
# ACL only governs newly-created children and does not control access to the
# file itself, so it is excluded here.
ACCESS_ACL_XATTRS = frozenset({_POSIX_ACCESS_XATTR, _ZFS_NATIVE_XATTR})


def copy_permissions(
    src_fd: int, dst_fd: int, xattr_list: list[str], mode: int
) -> None:
    """Copy permissions from one file to another.

    Args:
        src_fd: Source file descriptor.
        dst_fd: Destination file descriptor.
        xattr_list: Names of xattrs present on ``src_fd``.
        mode: POSIX mode of ``src_fd``.

    Raises:
        PermissionError: ``fchmod`` failed because the destination dataset
            has a RESTRICTED ZFS ``aclmode`` and ``dst_fd`` already inherited
            an ACL.
        OSError: With ``EOPNOTSUPP`` if the ACL types of ``src_fd`` and
            ``dst_fd`` do not match; otherwise the errnos documented in the
            ``fgetxattr``/``fsetxattr``/``fchmod`` manpages.

    Note:
        If the source has an access ACL xattr, ``fchmod`` is not attempted.
    """
    access_xattrs = set(xattr_list) & ACCESS_ACL_XATTRS
    if not access_xattrs:
        # No ACL xattr controls permissions for this file; mode is authoritative.
        # NOTE: fchmod will raise PermissionError if the ZFS dataset aclmode is
        # RESTRICTED and dst_fd inherited an ACL from its parent.
        fchmod(dst_fd, S_IMODE(mode))
        return

    for xat_name in access_xattrs:
        xat_buf = fgetxattr(src_fd, xat_name)
        fsetxattr(dst_fd, xat_name, xat_buf)


def copy_xattrs(src_fd: int, dst_fd: int, xattr_list: list[str]) -> None:
    """Copy non-ACL xattrs from one file to another.

    Args:
        src_fd: Source file descriptor.
        dst_fd: Destination file descriptor.
        xattr_list: Names of xattrs present on ``src_fd``.

    Raises:
        OSError: With ``EOPNOTSUPP`` if xattr support is disabled on the
            destination filesystem; otherwise the errnos documented in the
            xattr manpages.
    """
    for xat_name in set(xattr_list) - ACL_XATTRS:
        if xat_name.startswith("system"):
            # system xattrs typically denote filesystem-specific handlers
            # that may not be applicable to file copies. Skip silently.
            continue
        xat_buf = fgetxattr(src_fd, xat_name)
        fsetxattr(dst_fd, xat_name, xat_buf)


def copyuserspace(src_fd: int, dst_fd: int) -> int:
    """Userspace-only file copy via ``shutil.copyfileobj``.

    Args:
        src_fd: Source file descriptor.
        dst_fd: Destination file descriptor.

    Returns:
        Number of bytes written, derived via ``fstat`` on the destination.
    """
    src = open(src_fd, "rb", closefd=False)
    dst = open(dst_fd, "wb", closefd=False)
    copyfileobj(src, dst)
    # Flush before fstat: BufferedWriter holds the tail of the data until
    # it is destroyed or explicitly flushed, and dst is still alive at the
    # return statement.
    dst.flush()
    return fstat(dst_fd).st_size


def copysendfile(src_fd: int, dst_fd: int) -> int:
    """Optimized file copy using ``sendfile(2)``.

    Falls back to ``copyuserspace`` if ``sendfile`` writes nothing
    and the destination is still empty (mirrors CPython's
    ``_fastcopy_sendfile`` fallback semantics).

    Args:
        src_fd: Source file descriptor.
        dst_fd: Destination file descriptor.

    Returns:
        Number of bytes written.

    Raises:
        OSError: As documented in the ``sendfile(2)`` manpage.
    """
    offset = 0
    while (sent := sendfile(dst_fd, src_fd, offset, MAX_RW_SZ)) > 0:
        offset += sent

    if offset == 0 and lseek(dst_fd, 0, SEEK_CUR) == 0:
        return copyuserspace(src_fd, dst_fd)

    return offset


def clonefile(src_fd: int, dst_fd: int) -> int:
    """Block-level clone via ``copy_file_range(2)``.

    Args:
        src_fd: Source file descriptor.
        dst_fd: Destination file descriptor.

    Returns:
        Number of bytes written.

    Raises:
        OSError: ``EXDEV`` when source and destination are on different
            filesystems (or different ZFS pools); other errnos as documented
            in ``copy_file_range(2)``.
    """
    offset = 0
    # Loop until copy_file_range returns 0 to catch any TOCTOU races where
    # data is appended after the initial statx call.
    while (
        copied := copy_file_range(
            src_fd, dst_fd, MAX_RW_SZ, offset_src=offset, offset_dst=offset
        )
    ) > 0:
        offset += copied
    return offset


def copyfile(src_fd: int, dst_fd: int) -> int:
    """Try ``clonefile``; on ``EXDEV`` fall back to ``copysendfile``.

    Args:
        src_fd: Source file descriptor.
        dst_fd: Destination file descriptor.

    Returns:
        Number of bytes written.
    """
    try:
        return clonefile(src_fd, dst_fd)
    except OSError as err:
        if err.errno == EXDEV:
            return copysendfile(src_fd, dst_fd)
        raise
