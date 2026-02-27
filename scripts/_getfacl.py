# SPDX-License-Identifier: LGPL-3.0-or-later

import argparse
import grp
import json
import os
import pwd
import sys

import truenas_os as t


_NFS4_PERM_CHARS = (
    (t.NFS4Perm.READ_DATA,         'r'),
    (t.NFS4Perm.WRITE_DATA,        'w'),
    (t.NFS4Perm.APPEND_DATA,       'a'),
    (t.NFS4Perm.READ_NAMED_ATTRS,  'R'),
    (t.NFS4Perm.WRITE_NAMED_ATTRS, 'W'),
    (t.NFS4Perm.EXECUTE,           'x'),
    (t.NFS4Perm.DELETE_CHILD,      'D'),
    (t.NFS4Perm.DELETE,            'd'),
    (t.NFS4Perm.READ_ATTRIBUTES,   'p'),
    (t.NFS4Perm.WRITE_ATTRIBUTES,  'P'),
    (t.NFS4Perm.READ_ACL,          'c'),
    (t.NFS4Perm.WRITE_ACL,         'C'),
    (t.NFS4Perm.WRITE_OWNER,       'o'),
    (t.NFS4Perm.SYNCHRONIZE,       's'),
)

_NFS4_FLAG_CHARS = (
    (t.NFS4Flag.FILE_INHERIT,         'f'),
    (t.NFS4Flag.DIRECTORY_INHERIT,    'd'),
    (t.NFS4Flag.NO_PROPAGATE_INHERIT, 'n'),
    (t.NFS4Flag.INHERIT_ONLY,         'i'),
    (t.NFS4Flag.SUCCESSFUL_ACCESS,    'S'),
    (t.NFS4Flag.FAILED_ACCESS,        'F'),
    (t.NFS4Flag.IDENTIFIER_GROUP,     'g'),
    (t.NFS4Flag.INHERITED,            'I'),
)

_NFS4_TYPE_STR = {
    t.NFS4AceType.ALLOW: 'allow',
    t.NFS4AceType.DENY:  'deny',
    t.NFS4AceType.AUDIT: 'audit',
    t.NFS4AceType.ALARM: 'alarm',
}

_POSIX_TAG_PREFIX = {
    t.POSIXTag.USER_OBJ:  'user',
    t.POSIXTag.USER:      'user',
    t.POSIXTag.GROUP_OBJ: 'group',
    t.POSIXTag.GROUP:     'group',
    t.POSIXTag.MASK:      'mask',
    t.POSIXTag.OTHER:     'other',
}

_POSIX_PERM_CHARS = (
    (t.POSIXPerm.READ,    'r'),
    (t.POSIXPerm.WRITE,   'w'),
    (t.POSIXPerm.EXECUTE, 'x'),
)


def _name_of_uid(uid, numeric):
    if not numeric:
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            pass
    return str(uid)


def _name_of_gid(gid, numeric):
    if not numeric:
        try:
            return grp.getgrgid(gid).gr_name
        except KeyError:
            pass
    return str(gid)


def _get_fhandle_hex(fd):
    try:
        fh = t.fhandle(path='', dir_fd=fd, flags=t.FH_AT_EMPTY_PATH)
        return bytes(fh).hex()
    except (OSError, NotImplementedError):
        return None


def _nfs4_perm_str(mask):
    return ''.join(c if mask & bit else '-' for bit, c in _NFS4_PERM_CHARS)


def _nfs4_flag_str(flags):
    return ''.join(c if flags & bit else '-' for bit, c in _NFS4_FLAG_CHARS)


def _nfs4_who_str(ace, numeric):
    wt = ace.who_type
    if wt == t.NFS4Who.OWNER:
        return 'owner@'
    if wt == t.NFS4Who.GROUP:
        return 'group@'
    if wt == t.NFS4Who.EVERYONE:
        return 'everyone@'
    uid = ace.who_id
    if ace.ace_flags & t.NFS4Flag.IDENTIFIER_GROUP:
        return 'group:' + _name_of_gid(uid, numeric)
    return 'user:' + _name_of_uid(uid, numeric)


def _posix_perm_str(perms):
    return ''.join(c if perms & bit else '-' for bit, c in _POSIX_PERM_CHARS)


def _posix_qualifier(ace, numeric):
    if ace.tag in (t.POSIXTag.USER_OBJ, t.POSIXTag.GROUP_OBJ,
                   t.POSIXTag.MASK, t.POSIXTag.OTHER):
        return ''
    if ace.tag == t.POSIXTag.USER:
        return _name_of_uid(ace.id, numeric)
    return _name_of_gid(ace.id, numeric)


def _trivial_posix_from_mode(mode):
    """Return a minimal 3-entry POSIXACL synthesised from inode mode bits."""
    def _p(bits):
        p = t.POSIXPerm(0)
        if bits & 4: p |= t.POSIXPerm.READ
        if bits & 2: p |= t.POSIXPerm.WRITE
        if bits & 1: p |= t.POSIXPerm.EXECUTE
        return p
    return t.POSIXACL.from_aces([
        t.POSIXAce(t.POSIXTag.USER_OBJ,  _p((mode >> 6) & 7)),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ, _p((mode >> 3) & 7)),
        t.POSIXAce(t.POSIXTag.OTHER,     _p(mode & 7)),
    ])


def _format_nfs4_text(path, acl, uid, gid, fh_hex, numeric, quiet):
    lines = []
    if not quiet:
        lines.append(f'# file: {path}')
        lines.append(f'# owner: {_name_of_uid(uid, numeric)}')
        lines.append(f'# group: {_name_of_gid(gid, numeric)}')
        if fh_hex is not None:
            lines.append(f'# fhandle: {fh_hex}')
    for ace in acl.aces:
        who = _nfs4_who_str(ace, numeric)
        perms = _nfs4_perm_str(ace.access_mask)
        flags = _nfs4_flag_str(ace.ace_flags)
        atype = _NFS4_TYPE_STR.get(ace.ace_type, str(int(ace.ace_type)))
        lines.append(f'{who}:{perms}:{flags}:{atype}')
    return '\n'.join(lines)


def _format_posix_text(path, acl, uid, gid, fh_hex, numeric, quiet):
    lines = []
    if not quiet:
        lines.append(f'# file: {path}')
        lines.append(f'# owner: {_name_of_uid(uid, numeric)}')
        lines.append(f'# group: {_name_of_gid(gid, numeric)}')
        if fh_hex is not None:
            lines.append(f'# fhandle: {fh_hex}')
    for ace in acl.aces:
        tag = _POSIX_TAG_PREFIX[ace.tag]
        qual = _posix_qualifier(ace, numeric)
        perms = _posix_perm_str(ace.perms)
        lines.append(f'{tag}:{qual}:{perms}')
    for ace in acl.default_aces:
        tag = _POSIX_TAG_PREFIX[ace.tag]
        qual = _posix_qualifier(ace, numeric)
        perms = _posix_perm_str(ace.perms)
        lines.append(f'default:{tag}:{qual}:{perms}')
    return '\n'.join(lines)


def _nfs4_ace_to_dict(ace, numeric):
    return {
        'who': _nfs4_who_str(ace, numeric),
        'perms': [p.name for p in t.NFS4Perm if ace.access_mask & p],
        'flags': [f.name for f in t.NFS4Flag if ace.ace_flags & f],
        'type': _NFS4_TYPE_STR.get(ace.ace_type, str(int(ace.ace_type))),
    }


def _posix_ace_to_dict(ace, numeric):
    qual = _posix_qualifier(ace, numeric)
    return {
        'tag': ace.tag.name,
        'qualifier': qual if qual else None,
        'perms': [p.name for p in t.POSIXPerm if ace.perms & p],
        'default': ace.default,
    }


def _format_nfs4_json(path, acl, uid, gid, fh_hex, numeric):
    d = {
        'path': path,
        'uid': uid,
        'gid': gid,
        'owner': _name_of_uid(uid, numeric),
        'group': _name_of_gid(gid, numeric),
        'acl_type': 'NFS4',
        'acl_flags': [f.name for f in t.NFS4ACLFlag if acl.acl_flags & f],
        'trivial': acl.trivial,
        'aces': [_nfs4_ace_to_dict(ace, numeric) for ace in acl.aces],
    }
    if fh_hex is not None:
        d['fhandle'] = fh_hex
    return d


def _format_posix_json(path, acl, uid, gid, fh_hex, numeric):
    d = {
        'path': path,
        'uid': uid,
        'gid': gid,
        'owner': _name_of_uid(uid, numeric),
        'group': _name_of_gid(gid, numeric),
        'acl_type': 'POSIX',
        'trivial': acl.trivial,
        'aces': ([_posix_ace_to_dict(ace, numeric) for ace in acl.aces] +
                 [_posix_ace_to_dict(ace, numeric) for ace in acl.default_aces]),
    }
    if fh_hex is not None:
        d['fhandle'] = fh_hex
    return d


def _fs_source_from_proc_mounts(mountpoint):
    """Read /proc/mounts to find the filesystem source for a mountpoint."""
    with open('/proc/mounts') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == mountpoint:
                return parts[0]
    return mountpoint


def _get_mount_info(path):
    stx = t.statx(path, mask=t.STATX_MNT_ID_UNIQUE)
    if hasattr(t, 'STATMOUNT_SB_SOURCE'):
        sm = t.statmount(stx.stx_mnt_id,
                         mask=t.STATMOUNT_MNT_POINT | t.STATMOUNT_SB_SOURCE)
        mountpoint = sm.mnt_point
        fs_name = sm.sb_source
    else:
        # This is is compatiblity for github runner where kernel doesn't
        # return source in statmount output.
        sm = t.statmount(stx.stx_mnt_id, mask=t.STATMOUNT_MNT_POINT)
        mountpoint = sm.mnt_point
        fs_name = _fs_source_from_proc_mounts(mountpoint)
    abs_path = os.path.realpath(path)
    rel = os.path.relpath(abs_path, mountpoint)
    rel_path = None if rel == '.' else rel
    return mountpoint, fs_name, rel_path


def _output_acl(path, fd, acl, uid, gid, numeric, quiet, use_json, skip_base):
    if skip_base and acl.trivial:
        return
    fh_hex = _get_fhandle_hex(fd)
    if isinstance(acl, t.NFS4ACL):
        if use_json:
            print(json.dumps(_format_nfs4_json(path, acl, uid, gid,
                                               fh_hex, numeric)))
        else:
            print(_format_nfs4_text(path, acl, uid, gid, fh_hex, numeric,
                                    quiet))
            print()
    else:
        if use_json:
            print(json.dumps(_format_posix_json(path, acl, uid, gid,
                                                fh_hex, numeric)))
        else:
            print(_format_posix_text(path, acl, uid, gid, fh_hex, numeric,
                                     quiet))
            print()


def _process_fd(path, fd, uid, gid, numeric, quiet, use_json, skip_base):
    acl = t.fgetacl(fd)
    if isinstance(acl, t.POSIXACL) and not acl.aces:
        st = os.fstat(fd)
        acl = t.POSIXACL.from_aces(
            list(_trivial_posix_from_mode(st.st_mode).aces) +
            list(acl.default_aces)
        )
    _output_acl(path, fd, acl, uid, gid, numeric, quiet, use_json, skip_base)


def _process_path(path, numeric, quiet, use_json, skip_base):
    fd = t.openat2(path, flags=os.O_RDONLY, resolve=t.RESOLVE_NO_SYMLINKS)
    try:
        acl = t.fgetacl(fd)
        st = os.fstat(fd)
        if isinstance(acl, t.POSIXACL) and not acl.aces:
            acl = t.POSIXACL.from_aces(
                list(_trivial_posix_from_mode(st.st_mode).aces) +
                list(acl.default_aces)
            )
        _output_acl(path, fd, acl, st.st_uid, st.st_gid, numeric, quiet,
                    use_json, skip_base)
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser(
        prog='truenas_getfacl',
        description='Display ACL entries for files.',
    )
    ap.add_argument('-R', '--recursive', action='store_true',
                    help='Process directories recursively; '
                         'does not follow symlinks or cross device boundaries')
    ap.add_argument('-n', '--numeric', action='store_true',
                    help='Display numeric UIDs/GIDs')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='Omit comment headers (text mode only)')
    ap.add_argument('-s', '--skip-base', action='store_true',
                    help='Skip files that only have the base ACL entries '
                         '(i.e. trivial ACL derived from mode bits)')
    ap.add_argument('-j', '--json', dest='use_json', action='store_true',
                    help='Output ACLs as JSONL (one object per line)')
    ap.add_argument('path', nargs='+')
    args = ap.parse_args()

    rc = 0
    for path in args.path:
        try:
            _process_path(path, args.numeric, args.quiet, args.use_json,
                          args.skip_base)
        except OSError as e:
            print(f'truenas_getfacl: {path}: {e}', file=sys.stderr)
            rc = 1

        if not args.recursive or not os.path.isdir(path):
            continue

        try:
            mountpoint, fs_name, rel_path = _get_mount_info(path)
        except OSError as e:
            print(f'truenas_getfacl: {path}: {e}', file=sys.stderr)
            rc = 1
            continue

        with t.iter_filesystem_contents(mountpoint, fs_name,
                                        relative_path=rel_path) as it:
            for item in it:
                full_path = os.path.join(item.parent, item.name)
                if item.islnk:
                    os.close(item.fd)
                    continue
                try:
                    _process_fd(full_path, item.fd,
                                item.statxinfo.stx_uid,
                                item.statxinfo.stx_gid,
                                args.numeric, args.quiet, args.use_json,
                                args.skip_base)
                except OSError as e:
                    print(f'truenas_getfacl: {full_path}: {e}',
                          file=sys.stderr)
                    rc = 1
                finally:
                    os.close(item.fd)

    sys.exit(rc)


if __name__ == '__main__':
    main()
