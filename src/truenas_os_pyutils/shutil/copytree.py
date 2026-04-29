# Recursive directory-tree copy.
#
# Driven by truenas_os.iter_filesystem_contents (fsiter): depth-first,
# GIL released, mountpoint-validated.  Cross-mount recursion is performed
# by enumerating child mounts via iter_mount after the root pass — this
# mirrors the AclTool pattern in middleware plugins/filesystem_/utils.py.
# ZFS snapshot mounts and the .zfs ctldir are always skipped.
#
# Tests are in tests/utils/test_shutil_copytree.py.
from __future__ import annotations

import enum
import os
from typing import Any

from collections.abc import Callable
from dataclasses import dataclass
from os import (
    O_CREAT,
    O_DIRECTORY,
    O_EXCL,
    O_NOFOLLOW,
    O_RDONLY,
    O_RDWR,
    O_TRUNC,
    close,
    fchown,
    fstat,
    makedev,
    mkdir,
    readlink,
    stat_result,
    symlink,
    utime,
)
from pathlib import Path

import truenas_os
from truenas_os import RESOLVE_NO_SYMLINKS, flistxattr, openat2

from ..mount import statmount as _statmount
from .copy import (
    clone_file,
    clone_or_copy_file,
    copy_file_userspace,
    copy_permissions,
    copy_sendfile,
    copy_xattrs,
)


__all__ = [
    "CLONETREE_ROOT_DEPTH",
    "DEF_CP_FLAGS",
    "CopyFlags",
    "CopyTreeConfig",
    "CopyTreeOp",
    "CopyTreeStats",
    "ReportingCallback",
    "copytree",
]


# ── Constants ────────────────────────────────────────────────────────────────

CLONETREE_ROOT_DEPTH = 0

# include/os/linux/zfs/sys/zfs_ctldir.h in ZFS source: fixed inode for the
# .zfs ctldir at the root of every ZFS dataset.  Detecting this inode lets us
# avoid descending into a user-visible snapshot directory.
_ZFSCTL_INO_ROOT = 0x0000FFFFFFFFFFFF

_STATX_DEFAULT_MASK = (
    truenas_os.STATX_BASIC_STATS
    | truenas_os.STATX_BTIME
    | truenas_os.STATX_MNT_ID_UNIQUE
)

# STATMOUNT_SB_SOURCE requires kernel 6.18+; the C extension defines the
# constant only when the header has it.  When unavailable the traverse code
# falls back to the mountpoint as the filesystem source name (matches the
# fsiter fallback path that is also gated on STATMOUNT_SB_SOURCE in the C
# code), so requesting the field here is best-effort.
_STATMOUNT_TRAVERSE_FLAGS = truenas_os.STATMOUNT_MNT_POINT | truenas_os.STATMOUNT_FS_TYPE
if hasattr(truenas_os, "STATMOUNT_SB_SOURCE"):
    _STATMOUNT_TRAVERSE_FLAGS |= truenas_os.STATMOUNT_SB_SOURCE


# ── Public types ─────────────────────────────────────────────────────────────


class CopyFlags(enum.IntFlag):
    """Flags specifying which metadata to copy from source to destination."""

    XATTRS = 0x0001  # copy user / trusted / security namespace xattrs
    PERMISSIONS = 0x0002  # copy ACL xattrs (or fchmod if no ACL is present)
    TIMESTAMPS = 0x0004  # copy atime / mtime (in nanoseconds)
    OWNER = 0x0008  # copy uid / gid


class CopyTreeOp(enum.Enum):
    """Available options for customizing how each file is copied.

    DEFAULT is generally the right choice (try a block clone first, fall
    through to zero-copy sendfile, and finally a userspace copy if neither
    is supported).

    USERSPACE should be used for special filesystems such as procfs / sysfs
    that may not properly support copy_file_range or sendfile.
    """

    DEFAULT = enum.auto()  # try clone, fall through eventually to userspace
    CLONE = enum.auto()  # attempt block clone; fail the operation if not supported
    SENDFILE = enum.auto()  # attempt sendfile (with fallthrough to copyfileobj)
    USERSPACE = enum.auto()  # same as shutil.copyfileobj


DEF_CP_FLAGS = (
    CopyFlags.XATTRS | CopyFlags.PERMISSIONS | CopyFlags.OWNER | CopyFlags.TIMESTAMPS
)


# Same shape as truenas_os.iter_filesystem_contents' reporting_callback —
# we pass it through unchanged.  Defined here for type-hint convenience.
ReportingCallback = Callable[
    [tuple[tuple[str, int], ...], "truenas_os.FilesystemIterState", Any],
    Any,
]


@dataclass(frozen=True, slots=True)
class CopyTreeConfig:
    """Configuration for ``copytree()``.

    Attributes:
        reporting_callback: Forwarded to fsiter's ``reporting_callback``
            unchanged.  Invoked every ``reporting_increment`` items with
            ``(dir_stack, FilesystemIterState, private_data)``.
        reporting_private_data: Passed through to ``reporting_callback`` as
            its third argument.
        reporting_increment: Forwarded to fsiter; controls how often the
            callback fires (in items processed).
        raise_error: Re-raise exceptions from metadata copy (xattr/perm/owner/
            timestamp). When False the operation continues despite failures.
        exist_ok: Do not raise if a target file or directory already exists.
        traverse: Recurse into child filesystem mounts under ``src``.  ZFS
            snapshot mounts are always skipped.
        op: Per-file copy operation; see ``CopyTreeOp``.
        flags: Bitmask of metadata categories to preserve.
    """

    reporting_callback: ReportingCallback | None = None
    reporting_private_data: Any = None
    reporting_increment: int = 1000
    raise_error: bool = True
    exist_ok: bool = True
    traverse: bool = False
    op: CopyTreeOp = CopyTreeOp.DEFAULT
    flags: CopyFlags = DEF_CP_FLAGS


@dataclass(slots=True)
class CopyTreeStats:
    """Counters returned from ``copytree``.

    Attributes
    ----------
    dirs : int
        Number of directories created in the destination.
    files : int
        Number of regular files copied.
    symlinks : int
        Number of symlinks recreated.
    bytes : int
        Total bytes written across all regular-file copies.
    """

    dirs: int = 0
    files: int = 0
    symlinks: int = 0
    bytes: int = 0


# ── Internal helpers ─────────────────────────────────────────────────────────


def _path_in_ctldir(path: str) -> bool:
    """Return True if ``path`` lies inside a ZFS ``.zfs`` ctldir.

    Walks parent components looking for a directory named ``.zfs`` whose
    inode matches the fixed ZFS ctldir inode.  This catches user-visible
    snapshot directories so ``copytree`` does not try to copy them.
    """
    p = Path(path)
    if not p.is_absolute():
        raise ValueError(f"{path}: not an absolute path")

    while p.as_posix() != "/":
        if p.name == ".zfs":
            if p.stat().st_ino == _ZFSCTL_INO_ROOT:
                return True
        p = p.parent

    return False


def _get_mount_info(fd: int) -> tuple[str, str, str | None, int]:
    """Resolve ``(mnt_point, fs_source, rel_path, mnt_id)`` for an open fd.

    ``rel_path`` is ``None`` when the fd refers to the mount root itself,
    which lets ``iter_filesystem_contents`` accept it as a positional
    argument.  ``fs_source`` falls back to ``mnt_point`` on kernels without
    ``STATMOUNT_SB_SOURCE`` (kernel < 6.18); fsiter's strict source-name
    check is also gated on that constant in the C code, so the fallback is
    benign.
    """
    sm = _statmount(fd=fd, as_dict=False)
    abs_path = readlink(f"/proc/self/fd/{fd}")
    assert sm.mnt_point is not None
    assert sm.mnt_id is not None
    rel = os.path.relpath(abs_path, sm.mnt_point)
    rel_path = None if rel == "." else rel
    sb_source = getattr(sm, "sb_source", None)
    fs_source = sb_source if sb_source is not None else sm.mnt_point
    return sm.mnt_point, fs_source, rel_path, sm.mnt_id


def _select_copy_fn(op: CopyTreeOp) -> Callable[[int, int], int]:
    match op:
        case CopyTreeOp.DEFAULT:
            return clone_or_copy_file
        case CopyTreeOp.CLONE:
            return clone_file
        case CopyTreeOp.SENDFILE:
            return copy_sendfile
        case CopyTreeOp.USERSPACE:
            return copy_file_userspace
        case _:
            raise ValueError(f"{op}: unexpected copy operation")


@dataclass(frozen=True, slots=True)
class _Frame:
    """One entry on _CopyTreeRunner's destination-side directory stack.

    ``dst_fd`` is a directory fd in the destination tree.  Ownership is
    documented on _CopyTreeRunner: every frame except the mount-root
    frame at the bottom of each _process_mount pass is runner-owned and
    gets closed in _pop_frame; the mount-root frame's fd is owned by
    the caller and is removed from the stack without closing.

    ``src_statx`` is the StatxResult of the corresponding source
    directory, kept around so we can stamp its atime/mtime on
    ``dst_fd`` during ascent (after children have been written).
    """

    dst_fd: int
    src_statx: truenas_os.StatxResult


# ── Recursive copy runner (private) ──────────────────────────────────────────


class _CopyTreeRunner:
    """Drives a single ``copytree()`` call.

    Attributes
    ----------
    config : CopyTreeConfig
        Immutable copy configuration passed in at construction.
    stats : CopyTreeStats
        Mutable counters returned to ``copytree``'s caller.  Updated
        in place as files / dirs / symlinks / bytes are processed.
    c_fn : Callable[[int, int], int]
        Per-file copy primitive selected from ``config.op``
        (``clone_or_copy_file`` / ``clone_file`` / ``copy_sendfile`` /
        ``copy_file_userspace``).
    src_fd : int
        Caller-owned source-root directory fd.  Borrowed for the
        lifetime of the runner; not closed here.
    dst_fd : int
        Caller-owned destination-root directory fd.  Borrowed for the
        lifetime of the runner; not closed here.
    src_root_real : str
        ``readlink('/proc/self/fd/<src_fd>')`` — the canonical
        absolute path of ``src_fd``.  Used to compute relative paths
        when traversing into child mounts.
    target_st : os.stat_result
        ``fstat(dst_fd)`` snapshot.  Used by ``_is_dst_into_self`` to
        skip an entry whose dev_t + inode match the dst root
        (prevents copying the destination back into itself).
    frames : list[_Frame]
        Destination-side directory stack — one ``_Frame`` per source
        directory level we've descended into.  See `Notes`.

    Notes
    -----
    fd ownership in ``frames``:

    - ``_Frame.dst_fd`` entries are **owned by this runner**.  We open
      them ourselves (``mkdir`` + ``openat2`` in ``_do_mkdir``) and
      close them in ``_pop_frame`` after applying timestamps, or in
      the ``_process_mount`` ``finally`` cleanup on exception.  The
      one exception is the mount-root frame at the bottom of each
      ``_process_mount`` pass: that frame holds an fd owned by the
      *caller* of ``_process_mount`` (``copytree`` for the outer
      mount, ``_traverse_child_mounts`` for child mounts), and is
      removed from the stack without closing.

    - ``_Frame.src_statx`` is just a ``StatxResult`` value snapshot
      used to stamp atime/mtime onto ``dst_fd`` during ascent.

    Source-side fds (``item.fd`` from fsiter) never enter the frame
    stack: the iterator owns those and recycles them at the next
    ``next()`` call.

    On ascent, directory timestamps are applied last — after all
    children have been written so their writes don't bump the
    directory's own mtime.
    """

    __slots__ = (
        "config",
        "stats",
        "c_fn",
        "src_fd",
        "dst_fd",
        "src_root_real",
        "target_st",
        "frames",
    )

    def __init__(self, config: CopyTreeConfig, src_fd: int, dst_fd: int) -> None:
        """Initialise per-call runner state.

        ``src_fd`` and ``dst_fd`` are borrowed from the caller for the
        full lifetime of the runner (resolved canonical path, dst-root
        stat snapshot, and selected copy primitive are captured here).
        """
        self.config = config
        self.stats = CopyTreeStats()
        self.c_fn = _select_copy_fn(config.op)
        self.src_fd = src_fd
        self.dst_fd = dst_fd
        self.src_root_real = readlink(f"/proc/self/fd/{src_fd}")
        # st_ino + st_dev of the destination root: used to detect copying
        # the destination back into itself (e.g. dst is a subdirectory of src).
        self.target_st: stat_result = fstat(dst_fd)
        # frames[i] is one _Frame per destination-side directory level we
        # have descended into.  See class docstring for ownership rules.
        self.frames: list[_Frame] = []

    # ── per-entry handlers ───────────────────────────────────────────────

    def _do_mkfile(self, item: truenas_os.IterInstance, dst_file_fd: int) -> None:
        """Copy file metadata + data from ``item.fd`` to ``dst_file_fd``."""
        flags = self.config.flags
        xattrs: list[str] = []
        if flags & (CopyFlags.PERMISSIONS | CopyFlags.XATTRS):
            xattrs = flistxattr(item.fd)

        if flags & CopyFlags.PERMISSIONS:
            try:
                copy_permissions(
                    item.fd, dst_file_fd, xattrs, item.statxinfo.stx_mode
                )
            except Exception:
                if self.config.raise_error:
                    raise

        if flags & CopyFlags.XATTRS:
            try:
                copy_xattrs(item.fd, dst_file_fd, xattrs)
            except Exception:
                if self.config.raise_error:
                    raise

        if flags & CopyFlags.OWNER:
            fchown(dst_file_fd, item.statxinfo.stx_uid, item.statxinfo.stx_gid)

        self.stats.bytes += self.c_fn(item.fd, dst_file_fd)

        # Write timestamps last so that data and metadata writes do not
        # bump them.
        if flags & CopyFlags.TIMESTAMPS:
            ns_ts = (item.statxinfo.stx_atime_ns, item.statxinfo.stx_mtime_ns)
            try:
                utime(dst_file_fd, ns=ns_ts)
            except Exception:
                if self.config.raise_error:
                    raise

    def _do_mkdir(self, item: truenas_os.IterInstance, parent_dst_fd: int) -> int:
        """Create the destination subdirectory and copy non-timestamp metadata.

        Returns an open fd to the new directory; the caller pushes it on the
        frame stack and is responsible for ``utime`` + ``close`` on ascent.
        """
        try:
            mkdir(item.name, dir_fd=parent_dst_fd)
        except FileExistsError:
            if not self.config.exist_ok:
                raise

        new_dir_fd = openat2(
            item.name, O_DIRECTORY | O_NOFOLLOW,
            dir_fd=parent_dst_fd, resolve=RESOLVE_NO_SYMLINKS,
        )
        try:
            flags = self.config.flags
            xattrs: list[str] = []
            if flags & (CopyFlags.PERMISSIONS | CopyFlags.XATTRS):
                xattrs = flistxattr(item.fd)

            if flags & CopyFlags.PERMISSIONS:
                copy_permissions(
                    item.fd, new_dir_fd, xattrs, item.statxinfo.stx_mode
                )

            if flags & CopyFlags.XATTRS:
                copy_xattrs(item.fd, new_dir_fd, xattrs)

            if flags & CopyFlags.OWNER:
                fchown(new_dir_fd, item.statxinfo.stx_uid, item.statxinfo.stx_gid)
        except Exception:
            if self.config.raise_error:
                close(new_dir_fd)
                raise

        return new_dir_fd

    def _handle_symlink(
        self, item: truenas_os.IterInstance, dst_dir_fd: int
    ) -> None:
        """Recreate the source symlink ``item`` under ``dst_dir_fd``.

        ``item.fd`` is an O_PATH | O_NOFOLLOW fd from fsiter, so we read
        the target with ``readlinkat(fd, "")`` (Python's
        ``os.readlink('', dir_fd=fd)``) and recreate via ``symlinkat``.
        The new symlink is created with the same target string; we do
        not preserve symlink owner / mtime (matching standard
        copytree behaviour).  ``FileExistsError`` is suppressed when
        ``config.exist_ok`` is True.
        """
        target = readlink("", dir_fd=item.fd)
        try:
            symlink(target, item.name, dir_fd=dst_dir_fd)
        except FileExistsError:
            if not self.config.exist_ok:
                raise

    # ── frame stack ──────────────────────────────────────────────────────

    def _pop_frame(self) -> None:
        """Pop a runner-owned frame, applying TIMESTAMPS, then close.

        Only valid for frames the runner owns (every frame except the
        mount-root frame at the bottom of each ``_process_mount`` pass).
        Mount-root frames are removed without closing — their fds belong
        to the caller.
        """
        frame = self.frames.pop()
        if self.config.flags & CopyFlags.TIMESTAMPS:
            try:
                utime(
                    frame.dst_fd,
                    ns=(frame.src_statx.stx_atime_ns, frame.src_statx.stx_mtime_ns),
                )
            except Exception:
                if self.config.raise_error:
                    close(frame.dst_fd)
                    raise
        close(frame.dst_fd)

    def _is_dst_into_self(self, item: truenas_os.IterInstance) -> bool:
        """True if ``item`` is the destination root.

        Checked via dev_t + inode, since bind mounts of the same filesystem
        share ``stx_dev`` but differ in ``stx_mnt_id``.
        """
        if item.statxinfo.stx_ino != self.target_st.st_ino:
            return False
        return (
            makedev(item.statxinfo.stx_dev_major, item.statxinfo.stx_dev_minor)
            == self.target_st.st_dev
        )

    # ── per-mount processing ─────────────────────────────────────────────

    def _process_mount(
        self,
        src_root_fd: int,
        mnt_point: str,
        fs_name: str,
        rel_path: str | None,
        root_dst_fd: int,
    ) -> None:
        """Iterate one filesystem mount and apply its copy operations.

        ``src_root_fd`` and ``root_dst_fd`` are owned by the caller; this
        method does not close them.  Their metadata is applied at the end
        of iteration so children are processed before the directory's
        timestamps are stamped.
        """
        root_stat = truenas_os.statx(
            "",
            dir_fd=src_root_fd,
            flags=truenas_os.AT_EMPTY_PATH,
            mask=_STATX_DEFAULT_MASK,
        )
        root_xattrs: list[str] = []
        if self.config.flags & (CopyFlags.PERMISSIONS | CopyFlags.XATTRS):
            root_xattrs = flistxattr(src_root_fd)

        # Stack invariant: at function exit (normal or exceptional) the
        # frame count is restored.
        initial_len = len(self.frames)
        self.frames.append(_Frame(root_dst_fd, root_stat))
        try:
            with truenas_os.iter_filesystem_contents(
                mnt_point,
                fs_name,
                rel_path,
                reporting_increment=self.config.reporting_increment,
                reporting_callback=self.config.reporting_callback,
                reporting_private_data=self.config.reporting_private_data,
                include_symlinks=True,
            ) as it:
                for item in it:
                    # fsiter's dir_stack semantics:
                    # - file/symlink entries: stack = ancestors only
                    # - directory entries: stack = ancestors + the dir itself
                    #   (already pushed for upcoming descent or skip)
                    # frames[i] mirrors ancestor i, so the target frame
                    # count is len(stack) for files and len(stack)-1 for
                    # dirs (we push the new dir's frame after mkdir).
                    ds_len = len(it.dir_stack())
                    target = ds_len - 1 if item.isdir else ds_len
                    while len(self.frames) > initial_len + target:
                        self._pop_frame()

                    dst_dir_fd = self.frames[-1].dst_fd

                    if item.isdir:
                        if item.name == ".zfs" and _path_in_ctldir(
                            os.path.join(item.parent, item.name)
                        ):
                            it.skip()
                            continue
                        if self._is_dst_into_self(item):
                            it.skip()
                            continue

                        new_dst_fd = self._do_mkdir(item, dst_dir_fd)
                        self.frames.append(_Frame(new_dst_fd, item.statxinfo))
                        self.stats.dirs += 1

                    elif item.isreg:
                        open_flags = O_RDWR | O_NOFOLLOW | O_CREAT | O_TRUNC
                        if not self.config.exist_ok:
                            open_flags |= O_EXCL
                        # mode=0o600 is a safe creation default; the
                        # final mode is applied by copy_permissions
                        # inside _do_mkfile when CopyFlags.PERMISSIONS
                        # is set.  Without it the file stays
                        # owner-private — safer than 0o644 / umask.
                        dst_file_fd = openat2(
                            item.name, open_flags,
                            dir_fd=dst_dir_fd,
                            mode=0o600,
                            resolve=RESOLVE_NO_SYMLINKS,
                        )
                        try:
                            self._do_mkfile(item, dst_file_fd)
                        finally:
                            close(dst_file_fd)
                        self.stats.files += 1

                    elif item.islnk:
                        self._handle_symlink(item, dst_dir_fd)
                        self.stats.symlinks += 1

                    # Other irregular types (sockets, fifos, devices)
                    # are intentionally not copied.

            # Normal path: drain runner-owned child frames (with
            # timestamp + close).  The remaining bottom frame holds
            # the mount-root fd, which the caller of _process_mount
            # owns — we must NOT close it.
            while len(self.frames) > initial_len + 1:
                self._pop_frame()
            self._apply_root_metadata(
                src_root_fd, root_dst_fd, root_xattrs, root_stat
            )
        finally:
            # Cleanup invariant: at function exit, frames length is
            # restored to ``initial_len``.  In the normal path only the
            # mount-root frame remains here; on exception, an arbitrary
            # number of runner-owned child frames may still be present.
            # Close runner-owned fds; skip the mount-root fd because the
            # caller owns it.
            while len(self.frames) > initial_len:
                frame = self.frames.pop()
                if frame.dst_fd is root_dst_fd:
                    continue
                try:
                    close(frame.dst_fd)
                except OSError:
                    pass

    def _apply_root_metadata(
        self,
        src_root_fd: int,
        root_dst_fd: int,
        xattrs: list[str],
        src_stat: truenas_os.StatxResult,
    ) -> None:
        """Stamp permissions / xattrs / owner / timestamps on the mount root.

        fsiter never yields the directory it was started from, so the
        runner has to apply that directory's metadata itself.  Called
        once per ``_process_mount`` pass after iteration completes —
        timestamps go last so the prior writes don't bump them.
        Errors during metadata copy are swallowed when
        ``config.raise_error`` is False (best-effort semantics for
        non-critical metadata).
        """
        flags = self.config.flags
        try:
            if flags & CopyFlags.PERMISSIONS:
                copy_permissions(src_root_fd, root_dst_fd, xattrs, src_stat.stx_mode)

            if flags & CopyFlags.XATTRS:
                copy_xattrs(src_root_fd, root_dst_fd, xattrs)

            if flags & CopyFlags.OWNER:
                fchown(root_dst_fd, src_stat.stx_uid, src_stat.stx_gid)

            if flags & CopyFlags.TIMESTAMPS:
                utime(
                    root_dst_fd,
                    ns=(src_stat.stx_atime_ns, src_stat.stx_mtime_ns),
                )
        except Exception:
            if self.config.raise_error:
                raise

    # ── entry point ──────────────────────────────────────────────────────

    def run(self) -> CopyTreeStats:
        """Execute the copy and return ``CopyTreeStats``.

        Resolves the source mount via ``_get_mount_info``, runs one
        ``_process_mount`` pass against it, and (when
        ``config.traverse=True``) repeats for each child mount under
        ``src_fd`` via ``_traverse_child_mounts``.
        """
        mnt_point, fs_name, rel_path, mnt_id = _get_mount_info(self.src_fd)
        self._process_mount(self.src_fd, mnt_point, fs_name, rel_path, self.dst_fd)

        if self.config.traverse:
            self._traverse_child_mounts(mnt_id)

        return self.stats

    def _traverse_child_mounts(self, root_mnt_id: int) -> None:
        """Run ``_process_mount`` for each child mount under the source root."""
        prefix = self.src_root_real + "/"
        for entry in truenas_os.iter_mount(
            mnt_id=root_mnt_id, statmount_flags=_STATMOUNT_TRAVERSE_FLAGS
        ):
            child_mnt = entry.mnt_point
            if child_mnt is None or not child_mnt.startswith(prefix):
                continue
            entry_sb_source = getattr(entry, "sb_source", None)
            # Skip ZFS snapshot mounts: they are read-only and transient,
            # so destination writes (mkdir/setxattr/utime) would fail with
            # EROFS or expire mid-copy.
            if (
                entry.fs_type == "zfs"
                and entry_sb_source is not None
                and "@" in entry_sb_source
            ):
                continue
            child_fs_source = (
                entry_sb_source if entry_sb_source is not None else child_mnt
            )

            rel = child_mnt[len(self.src_root_real):].lstrip("/")
            child_src_fd = openat2(
                child_mnt,
                O_RDONLY | O_DIRECTORY,
                resolve=RESOLVE_NO_SYMLINKS,
            )
            try:
                child_dst_fd = openat2(
                    rel,
                    O_RDONLY | O_DIRECTORY,
                    dir_fd=self.dst_fd,
                    resolve=RESOLVE_NO_SYMLINKS,
                )
                try:
                    self._process_mount(
                        child_src_fd,
                        child_mnt,
                        child_fs_source,
                        None,
                        child_dst_fd,
                    )
                finally:
                    close(child_dst_fd)
            finally:
                close(child_src_fd)


# ── Public entry point ───────────────────────────────────────────────────────


def copytree(src: str, dst: str, config: CopyTreeConfig) -> CopyTreeStats:
    """Recursively copy ``src`` to ``dst`` preserving selected metadata.

    Iteration is driven by ``truenas_os.iter_filesystem_contents`` (fsiter),
    which traverses depth-first in C with the GIL released.  Cross-mount
    recursion is performed by enumerating child mounts via ``iter_mount``
    after the root pass — controlled by ``config.traverse``.  ZFS snapshot
    mounts and the ``.zfs`` ctldir are always skipped.

    Args:
        src: Absolute path of the source directory.
        dst: Absolute path of the destination.  Created if it does not exist.
        config: Copy configuration; see ``CopyTreeConfig``.

    Returns:
        ``CopyTreeStats`` with counts for directories, files, symlinks, and
        bytes written.

    Raises:
        ValueError: ``src`` or ``dst`` is not an absolute path.
        OSError: ``ELOOP`` if a path component was replaced with a symlink
            during traversal (possible symlink attack); ``EOPNOTSUPP`` for
            ACL-type mismatches or destinations that disable xattrs; the
            usual errnos otherwise.
        PermissionError: ``fchmod`` failed because the destination dataset
            uses ZFS ``aclmode=restricted``.
    """
    for p in (src, dst):
        if not os.path.isabs(p):
            raise ValueError(f"{p}: absolute path is required")

    src_fd = openat2(
        src,
        O_RDONLY | O_DIRECTORY,
        resolve=RESOLVE_NO_SYMLINKS,
    )
    try:
        try:
            mkdir(dst)
        except FileExistsError:
            if not config.exist_ok:
                raise

        dst_fd = openat2(
            dst,
            O_RDONLY | O_DIRECTORY,
            resolve=RESOLVE_NO_SYMLINKS,
        )
        try:
            return _CopyTreeRunner(config, src_fd, dst_fd).run()
        finally:
            close(dst_fd)
    finally:
        close(src_fd)
