# SPDX-License-Identifier: LGPL-3.0-or-later
"""
POSIX ACL compatibility tests: truenas_setfacl/truenas_getfacl vs
system setfacl/getfacl.

All tests are skipped automatically when any of the four CLI tools is absent
from PATH, or when the posix_dataset fixture is unavailable.

setfacl equivalence tests apply the same operation to two separate files
(one via system setfacl, one via truenas_setfacl) and compare the resulting
kernel ACL state read back via truenas_os.fgetacl.

getfacl equivalence tests set an ACL with system setfacl, then compare the
text output of `getfacl -n -c` against `truenas_getfacl -n -q`.
"""

import os
import shutil
import subprocess

import pytest
import truenas_os as t


# ── tool availability ─────────────────────────────────────────────────────────

_TOOLS = ('setfacl', 'getfacl', 'truenas_setfacl', 'truenas_getfacl')


# ── helpers ───────────────────────────────────────────────────────────────────

def _new_file(directory, name, mode=0o644):
    """Create a plain file and strip any existing ACL."""
    path = os.path.join(directory, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    os.close(fd)
    subprocess.run(['setfacl', '-b', path], check=True)
    return path


def _new_dir(directory, name, mode=0o755):
    """Create a directory and strip both access and default ACLs."""
    path = os.path.join(directory, name)
    os.makedirs(path, mode, exist_ok=True)
    subprocess.run(['setfacl', '-b', path], check=True)
    subprocess.run(['setfacl', '-k', path], check=True)
    return path


def _read_acl_norm(path):
    """Read kernel POSIXACL and return a frozenset of (tag, id, perms, default) tuples."""
    fd = os.open(path, os.O_RDONLY)
    try:
        acl = t.fgetacl(fd)
    finally:
        os.close(fd)
    if not isinstance(acl, t.POSIXACL):
        raise TypeError(f'expected POSIXACL, got {type(acl).__name__}')
    result = set()
    for ace in list(acl.aces) + list(acl.default_aces):
        result.add((int(ace.tag), ace.id, int(ace.perms), ace.default))
    return frozenset(result)


def _sys_sf(*args, path):
    """Run system setfacl with args on path."""
    subprocess.run(['setfacl'] + list(args) + [path], check=True)


def _tn_sf(*args, path):
    """Run truenas_setfacl with args on path."""
    subprocess.run(['truenas_setfacl'] + list(args) + [path], check=True)


def _getfacl_lines(path):
    """Run getfacl -n -c and return stripped non-blank, non-header ACE lines.

    Trailing effective-rights comments (e.g. '\\t# effective: r--') are
    stripped so lines are directly comparable with truenas_getfacl output.
    """
    r = subprocess.run(['getfacl', '-n', '-c', path],
                       capture_output=True, text=True, check=True)
    lines = []
    for line in r.stdout.splitlines():
        # strip trailing effective-rights comment
        if '\t#' in line:
            line = line[:line.index('\t#')]
        line = line.strip()
        if line and not line.startswith('#'):
            lines.append(line)
    return lines


def _tn_getfacl_lines(path):
    """Run truenas_getfacl -n -q and return stripped non-blank ACE lines."""
    r = subprocess.run(['truenas_getfacl', '-n', '-q', path],
                       capture_output=True, text=True, check=True)
    lines = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            lines.append(line)
    return lines


# ── setfacl equivalence tests ─────────────────────────────────────────────────

def test_setfacl_compat_modify_user_obj(posix_dataset):
    fa = _new_file(posix_dataset, 'sf_user_obj_sys')
    fb = _new_file(posix_dataset, 'sf_user_obj_tn')
    _sys_sf('-m', 'user::rwx', path=fa)
    _tn_sf('-m', 'user::rwx', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_add_named_user(posix_dataset):
    uid = str(os.getuid())
    fa = _new_file(posix_dataset, 'sf_named_user_sys')
    fb = _new_file(posix_dataset, 'sf_named_user_tn')
    _sys_sf('-m', f'user:{uid}:r--', path=fa)
    _tn_sf('-m', f'user:{uid}:r--', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_add_named_group(posix_dataset):
    gid = str(os.getgid())
    fa = _new_file(posix_dataset, 'sf_named_grp_sys')
    fb = _new_file(posix_dataset, 'sf_named_grp_tn')
    _sys_sf('-m', f'group:{gid}:r-x', path=fa)
    _tn_sf('-m', f'group:{gid}:r-x', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_set_mask(posix_dataset):
    uid = str(os.getuid())
    fa = _new_file(posix_dataset, 'sf_mask_sys')
    fb = _new_file(posix_dataset, 'sf_mask_tn')
    _sys_sf('-m', f'user:{uid}:rwx', path=fa)
    _sys_sf('-m', 'mask::r--', path=fa)
    _tn_sf('-m', f'user:{uid}:rwx', path=fb)
    _tn_sf('-m', 'mask::r--', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_multiple_entries(posix_dataset):
    fa = _new_file(posix_dataset, 'sf_multi_sys')
    fb = _new_file(posix_dataset, 'sf_multi_tn')
    _sys_sf('-m', 'user::rwx,group::r--,other::---', path=fa)
    _tn_sf('-m', 'user::rwx,group::r--,other::---', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_strip(posix_dataset):
    uid = str(os.getuid())
    fa = _new_file(posix_dataset, 'sf_strip_sys')
    fb = _new_file(posix_dataset, 'sf_strip_tn')
    _sys_sf('-m', f'user:{uid}:rwx', path=fa)
    _sys_sf('-b', path=fa)
    _tn_sf('-m', f'user:{uid}:rwx', path=fb)
    _tn_sf('-b', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_remove_named_user(posix_dataset):
    uid = str(os.getuid())
    fa = _new_file(posix_dataset, 'sf_rm_user_sys')
    fb = _new_file(posix_dataset, 'sf_rm_user_tn')
    _sys_sf('-m', f'user:{uid}:rwx', path=fa)
    _sys_sf('-x', f'user:{uid}', path=fa)
    _tn_sf('-m', f'user:{uid}:rwx', path=fb)
    _tn_sf('-x', f'user:{uid}', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_no_mask_flag(posix_dataset):
    uid = str(os.getuid())
    fa = _new_file(posix_dataset, 'sf_nomask_sys')
    fb = _new_file(posix_dataset, 'sf_nomask_tn')
    _sys_sf('-n', '-m', f'user:{uid}:rwx', path=fa)
    _tn_sf('-n', '-m', f'user:{uid}:rwx', path=fb)
    assert _read_acl_norm(fa) == _read_acl_norm(fb)


def test_setfacl_compat_dir_add_default(posix_dataset):
    uid = str(os.getuid())
    da = _new_dir(posix_dataset, 'sf_dir_dflt_sys')
    db = _new_dir(posix_dataset, 'sf_dir_dflt_tn')
    _sys_sf('-d', '-m', f'user:{uid}:r--', path=da)
    _tn_sf('-d', '-m', f'user:{uid}:r--', path=db)
    assert _read_acl_norm(da) == _read_acl_norm(db)


def test_setfacl_compat_dir_remove_default(posix_dataset):
    uid = str(os.getuid())
    da = _new_dir(posix_dataset, 'sf_dir_rmd_sys')
    db = _new_dir(posix_dataset, 'sf_dir_rmd_tn')
    _sys_sf('-d', '-m', f'user:{uid}:r--', path=da)
    _sys_sf('-k', path=da)
    _tn_sf('-d', '-m', f'user:{uid}:r--', path=db)
    _tn_sf('-k', path=db)
    assert _read_acl_norm(da) == _read_acl_norm(db)


def test_setfacl_compat_dir_remove_default_entry(posix_dataset):
    uid = str(os.getuid())
    da = _new_dir(posix_dataset, 'sf_dir_rmde_sys')
    db = _new_dir(posix_dataset, 'sf_dir_rmde_tn')
    _sys_sf('-d', '-m', f'user:{uid}:r--', path=da)
    _sys_sf('-d', '-x', f'user:{uid}', path=da)
    _tn_sf('-d', '-m', f'user:{uid}:r--', path=db)
    _tn_sf('-d', '-x', f'user:{uid}', path=db)
    assert _read_acl_norm(da) == _read_acl_norm(db)


# ── getfacl equivalence tests ─────────────────────────────────────────────────

def test_getfacl_compat_trivial_file(posix_dataset):
    """Trivial mode-only file: truenas_getfacl must synthesise 3 base entries."""
    path = _new_file(posix_dataset, 'gf_trivial_file', mode=0o644)
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_extended_file(posix_dataset):
    """File with a named user entry and auto-computed mask."""
    uid = str(os.getuid())
    path = _new_file(posix_dataset, 'gf_extended_file')
    _sys_sf('-m', f'user:{uid}:r--', path=path)
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_dir_trivial(posix_dataset):
    """Trivial mode-only directory: same synthesis requirement as files."""
    path = _new_dir(posix_dataset, 'gf_trivial_dir')
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_dir_default_only(posix_dataset):
    """Directory with trivial access ACL + default ACL.

    truenas_getfacl must show the 3 synthesised access entries AND the
    default entries (previously it showed only the default entries).
    """
    uid = str(os.getuid())
    path = _new_dir(posix_dataset, 'gf_dflt_only')
    _sys_sf('-d', '-m', f'user:{uid}:r--', path=path)
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_dir_access_and_default(posix_dataset):
    """Directory with extended access ACL and default ACL."""
    uid = str(os.getuid())
    path = _new_dir(posix_dataset, 'gf_access_and_dflt')
    _sys_sf('-m', f'user:{uid}:rwx', path=path)
    _sys_sf('-d', '-m', f'user:{uid}:r--', path=path)
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_multiple_named(posix_dataset):
    """File with multiple named users and a named group."""
    uid = str(os.getuid())
    gid = str(os.getgid())
    path = _new_file(posix_dataset, 'gf_multi_named')
    _sys_sf('-m', f'user:{uid}:r--,group:{gid}:r-x', path=path)
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_quiet_flag(posix_dataset):
    """getfacl -n -c and truenas_getfacl -n -q omit headers; ACE lines match."""
    uid = str(os.getuid())
    path = _new_file(posix_dataset, 'gf_quiet')
    _sys_sf('-m', f'user:{uid}:r--', path=path)
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


def test_getfacl_compat_with_headers_ace_lines(posix_dataset):
    """Headers may differ; non-comment ACE lines must be identical."""
    uid = str(os.getuid())
    path = _new_file(posix_dataset, 'gf_headers')
    _sys_sf('-m', f'user:{uid}:rwx', path=path)
    # _getfacl_lines and _tn_getfacl_lines already strip comment/header lines
    assert sorted(_getfacl_lines(path)) == sorted(_tn_getfacl_lines(path))


# ── recursive helpers ─────────────────────────────────────────────────────────

def _new_tree(directory, name):
    """Create a small mixed tree with trivial ACLs.

    Layout::

        <name>/
            file1   0o644
            file2   0o644
            sub/
                file3   0o644

    Returns the root path.
    """
    root = _new_dir(directory, name)
    _new_file(root, 'file1')
    _new_file(root, 'file2')
    sub = _new_dir(root, 'sub')
    _new_file(sub, 'file3')
    return root


def _new_dir_tree(directory, name):
    """Create a small dirs-only tree with trivial ACLs.

    Layout::

        <name>/
            sub/

    Returns the root path.  Used for default-ACL tests where the kernel
    rejects setting default ACLs on regular files.
    """
    root = _new_dir(directory, name)
    _new_dir(root, 'sub')
    return root


def _path_aces(path):
    """Return a frozenset of (tag, id, perms, default) for one path."""
    fd = os.open(path, os.O_RDONLY)
    try:
        acl = t.fgetacl(fd)
    finally:
        os.close(fd)
    if not isinstance(acl, t.POSIXACL):
        return frozenset()
    return frozenset(
        (int(a.tag), a.id, int(a.perms), a.default)
        for a in list(acl.aces) + list(acl.default_aces)
    )


def _read_tree_norm(root):
    """Walk a tree and return frozenset of (relpath, tag, id, perms, default).

    Paths with trivial/empty ACLs contribute no tuples, so two trees with
    identical kernel ACL state will compare equal.
    """
    result = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel = os.path.relpath(dirpath, root)
        for tup in _path_aces(dirpath):
            result.add((rel,) + tup)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel_f = os.path.relpath(path, root)
            for tup in _path_aces(path):
                result.add((rel_f,) + tup)
    return frozenset(result)


# ── recursive setfacl equivalence tests ──────────────────────────────────────

def test_setfacl_compat_recursive_add_named_user(posix_dataset):
    """-R -m user:UID:r-- propagates to every node in the tree."""
    uid = str(os.getuid())
    ta = _new_tree(posix_dataset, 'rf_add_user_sys')
    tb = _new_tree(posix_dataset, 'rf_add_user_tn')
    _sys_sf('-R', '-m', f'user:{uid}:r--', path=ta)
    _tn_sf('-R', '-m', f'user:{uid}:r--', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)


def test_setfacl_compat_recursive_strip(posix_dataset):
    """-R -b removes all extended entries from every node."""
    uid = str(os.getuid())
    ta = _new_tree(posix_dataset, 'rf_strip_sys')
    tb = _new_tree(posix_dataset, 'rf_strip_tn')
    # Seed both trees with extended ACLs using system setfacl.
    _sys_sf('-R', '-m', f'user:{uid}:rwx', path=ta)
    _sys_sf('-R', '-m', f'user:{uid}:rwx', path=tb)
    _sys_sf('-R', '-b', path=ta)
    _tn_sf('-R', '-b', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)


def test_setfacl_compat_recursive_remove_named_user(posix_dataset):
    """-R -x user:UID: mask recalculated per node; nodes without the entry unaffected."""
    uid = str(os.getuid())
    ta = _new_tree(posix_dataset, 'rf_rm_user_sys')
    tb = _new_tree(posix_dataset, 'rf_rm_user_tn')
    # Set named user on root and sub/file3 only; file1, file2 stay trivial.
    for path in [ta, os.path.join(ta, 'sub', 'file3')]:
        _sys_sf('-m', f'user:{uid}:rwx', path=path)
    for path in [tb, os.path.join(tb, 'sub', 'file3')]:
        _sys_sf('-m', f'user:{uid}:rwx', path=path)
    _sys_sf('-R', '-x', f'user:{uid}', path=ta)
    _tn_sf('-R', '-x', f'user:{uid}', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)


def test_setfacl_compat_recursive_no_mask(posix_dataset):
    """-R -n -m: mask created (GROUP_OBJ perms) but not recalculated to union."""
    uid = str(os.getuid())
    ta = _new_tree(posix_dataset, 'rf_nomask_sys')
    tb = _new_tree(posix_dataset, 'rf_nomask_tn')
    _sys_sf('-R', '-n', '-m', f'user:{uid}:rwx', path=ta)
    _tn_sf('-R', '-n', '-m', f'user:{uid}:rwx', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)


def test_setfacl_compat_recursive_add_default(posix_dataset):
    """-R -d -m user:UID:r-- sets a default ACL on every directory."""
    uid = str(os.getuid())
    # Dirs-only tree: kernel rejects default ACLs on regular files.
    ta = _new_dir_tree(posix_dataset, 'rf_add_dflt_sys')
    tb = _new_dir_tree(posix_dataset, 'rf_add_dflt_tn')
    _sys_sf('-R', '-d', '-m', f'user:{uid}:r--', path=ta)
    _tn_sf('-R', '-d', '-m', f'user:{uid}:r--', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)


def test_setfacl_compat_recursive_remove_default(posix_dataset):
    """-R -k strips the default ACL from every directory."""
    uid = str(os.getuid())
    ta = _new_dir_tree(posix_dataset, 'rf_rm_dflt_sys')
    tb = _new_dir_tree(posix_dataset, 'rf_rm_dflt_tn')
    _sys_sf('-R', '-d', '-m', f'user:{uid}:r--', path=ta)
    _sys_sf('-R', '-d', '-m', f'user:{uid}:r--', path=tb)
    _sys_sf('-R', '-k', path=ta)
    _tn_sf('-R', '-k', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)


def test_setfacl_compat_recursive_remove_default_entry(posix_dataset):
    """-R -d -x user:UID removes one entry from the default ACL of each directory.

    This exercises the bug where a dir with only a default ACL (trivial access
    ACL, aces==[]) would lose its access section when the default entry was
    removed.
    """
    uid = str(os.getuid())
    ta = _new_dir_tree(posix_dataset, 'rf_rm_dflt_e_sys')
    tb = _new_dir_tree(posix_dataset, 'rf_rm_dflt_e_tn')
    _sys_sf('-R', '-d', '-m', f'user:{uid}:r--', path=ta)
    _sys_sf('-R', '-d', '-m', f'user:{uid}:r--', path=tb)
    _sys_sf('-R', '-d', '-x', f'user:{uid}', path=ta)
    _tn_sf('-R', '-d', '-x', f'user:{uid}', path=tb)
    assert _read_tree_norm(ta) == _read_tree_norm(tb)
