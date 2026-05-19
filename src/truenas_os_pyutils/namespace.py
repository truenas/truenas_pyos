"""User-namespace helpers for idmapped mounts.

Context-manager wrapper around :func:`truenas_os.create_idmap_userns`. The
heavy lifting (clone3 + /proc map writes + pidfd ioctl) lives in the C
extension and is GIL-free during the syscall sequence.

A process-wide cache keyed on ``(uid_map, gid_map)`` keeps the per-call
cost amortised — clone3 is forked exactly once per distinct map for the
lifetime of the process. Container workloads typically share a single
``(host_base, host_range)`` across many containers and many mounts, so
after the first call this becomes a sub-microsecond dict lookup + dup.
"""
from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Generator, Iterable

import truenas_os
from truenas_os import IdmapMappingEntry


__all__ = ["idmap_userns", "clear_cache"]


# Flat hashable view of one IdmapMappingEntry: (inside, outside, length).
_EntryTuple = tuple[int, int, int]
# Cache key: a pair of (uid_map_tuple, gid_map_tuple).
_CacheKey = tuple[tuple[_EntryTuple, ...], tuple[_EntryTuple, ...]]

# Cache of pinning fds, keyed on the (uid_map, gid_map) tuple of entries.
# Entries are never evicted — callers see at most a handful of distinct
# maps per process lifetime, and evicting introduces close()-races with
# concurrent dup() readers that we can't close without a heavier protocol.
# Process exit cleans the fds up.
_CACHE_LOCK = threading.Lock()
_CACHE: dict[_CacheKey, int] = {}


def _key_for(
    uid_list: list[IdmapMappingEntry],
    gid_list: list[IdmapMappingEntry],
) -> _CacheKey:
    """Hashable, comparison-stable key built from raw entry tuples
    (inside, outside, length)."""
    return (
        tuple((e.inside, e.outside, e.length) for e in uid_list),
        tuple((e.inside, e.outside, e.length) for e in gid_list),
    )


@contextlib.contextmanager
def idmap_userns(
    uid_map: Iterable[IdmapMappingEntry],
    gid_map: Iterable[IdmapMappingEntry],
) -> Generator[int, None, None]:
    """Yield an open fd pinning a user namespace with the given uid/gid
    maps; close the fd on exit.

    Subsequent calls with the same maps reuse the cached pinning fd —
    the costly clone3 dance runs once per distinct ``(uid_map, gid_map)``
    for the lifetime of the process. The fd yielded to the caller is an
    ``os.dup()`` of the cached fd, so the with-block's normal close on
    exit does not affect cache state or other concurrent users.

    Construct map entries via :func:`truenas_os.create_idmap_mapping` —
    that is where validation lives. Raw tuples are rejected at the C
    boundary.

    Feed the yielded fd into ``truenas_os.mount_setattr(
    attr_set=MOUNT_ATTR_IDMAP, userns_fd=fd, ...)`` on a detached mount
    tree from ``truenas_os.open_tree(OPEN_TREE_CLONE)``.

    Privileged map writes (anything other than the caller's own
    EUID/EGID mapped 1:1) require CAP_SETUID and CAP_SETGID in the
    parent user namespace. Root in init_user_ns trivially satisfies
    that.

    Args:
        uid_map: Non-empty iterable of IdmapMappingEntry instances.
        gid_map: Non-empty iterable of IdmapMappingEntry instances.

    Yields:
        int: File descriptor pinning the user namespace.

    Raises:
        TypeError: If an element is not an IdmapMappingEntry. Raw
            tuples are rejected; build entries with
            ``create_idmap_mapping``.
        ValueError: If uid_map or gid_map is empty.
        OSError: If clone3, the /proc map writes, or the
            ``PIDFD_GET_USER_NAMESPACE`` ioctl fail at the kernel
            level.

    Example:
        Idmapped bind-mount of a container rootfs::

            import os
            import truenas_os
            from truenas_os_pyutils.namespace import idmap_userns

            uid = [truenas_os.create_idmap_mapping(0, 100000, 65536)]
            gid = [truenas_os.create_idmap_mapping(0, 100000, 65536)]

            with idmap_userns(uid, gid) as userns_fd:
                tree_fd = truenas_os.open_tree(
                    path="/mnt/tank/container",
                    flags=truenas_os.OPEN_TREE_CLONE
                          | truenas_os.OPEN_TREE_CLOEXEC,
                )
                try:
                    truenas_os.mount_setattr(
                        path="", dirfd=tree_fd,
                        attr_set=truenas_os.MOUNT_ATTR_IDMAP,
                        userns_fd=userns_fd,
                        flags=truenas_os.AT_EMPTY_PATH,
                    )
                    truenas_os.mount_setattr(
                        path="", dirfd=tree_fd,
                        propagation=truenas_os.MS_SLAVE,
                        flags=truenas_os.AT_EMPTY_PATH,
                    )
                    truenas_os.move_mount(
                        from_path="", from_dirfd=tree_fd,
                        to_path="/run/containers/root/<uuid>",
                        flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH,
                    )
                finally:
                    os.close(tree_fd)
    """
    uid_list = list(uid_map)
    gid_list = list(gid_map)

    # Mirror the C-level type and emptiness checks here. The C call
    # already enforces both, but cache hits would otherwise skip past
    # them — a raw-tuple input with the same shape as a previously-cached
    # IdmapMappingEntry would smuggle through.
    for which, seq in (("uid_map", uid_list), ("gid_map", gid_list)):
        for i, entry in enumerate(seq):
            if not isinstance(entry, IdmapMappingEntry):
                raise TypeError(
                    f"{which}[{i}] must be an IdmapMappingEntry "
                    "(use truenas_os.create_idmap_mapping)"
                )
    if not uid_list or not gid_list:
        raise ValueError(
            "create_idmap_userns: uid_map and gid_map must be non-empty"
        )

    key = _key_for(uid_list, gid_list)

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            cached = truenas_os.create_idmap_userns(
                uid_map=uid_list, gid_map=gid_list,
            )
            _CACHE[key] = cached
        # dup inside the lock so we never observe a torn cache state
        # (entries never disappear today, but holding the lock keeps
        # the contract robust against future churn).
        dup_fd = os.dup(cached)
    try:
        yield dup_fd
    finally:
        os.close(dup_fd)


def clear_cache() -> None:
    """Close and drop all cached pinning fds.

    For tests and explicit shutdown paths. Idempotent. Safe to call
    while other threads are inside :func:`idmap_userns` — they hold
    their own dup'd fds, independent of the cached originals.
    """
    with _CACHE_LOCK:
        fds = list(_CACHE.values())
        _CACHE.clear()
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass
