# SPDX-License-Identifier: LGPL-3.0-or-later

import argparse
import dataclasses
import grp
import os
import pwd
import sys

import truenas_os as t


# ── permission / flag tables (mirror _getfacl.py) ────────────────────────────

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

_NFS4_PERM_FROM_CHAR = {c: bit for bit, c in _NFS4_PERM_CHARS}
_NFS4_FLAG_FROM_CHAR = {c: bit for bit, c in _NFS4_FLAG_CHARS}

def _nfs4_perm_union(*perms):
    result = t.NFS4Perm(0)
    for p in perms:
        result |= p
    return result

_NFS4_FULL_SET = _nfs4_perm_union(*[bit for bit, _ in _NFS4_PERM_CHARS])

_NFS4_PERM_SETS = {
    'full_set':   _NFS4_FULL_SET,
    'modify_set': _NFS4_FULL_SET & ~(t.NFS4Perm.WRITE_ACL | t.NFS4Perm.WRITE_OWNER),
    'read_set':   _nfs4_perm_union(t.NFS4Perm.READ_DATA, t.NFS4Perm.READ_NAMED_ATTRS,
                                   t.NFS4Perm.READ_ATTRIBUTES, t.NFS4Perm.READ_ACL),
    'write_set':  _nfs4_perm_union(t.NFS4Perm.WRITE_DATA, t.NFS4Perm.APPEND_DATA,
                                   t.NFS4Perm.WRITE_NAMED_ATTRS, t.NFS4Perm.WRITE_ATTRIBUTES),
}

_NFS4_TYPE_FROM_STR = {
    'allow': t.NFS4AceType.ALLOW,
    'deny':  t.NFS4AceType.DENY,
    'audit': t.NFS4AceType.AUDIT,
    'alarm': t.NFS4AceType.ALARM,
}

_POSIX_PERM_CHARS = (
    (t.POSIXPerm.READ,    'r'),
    (t.POSIXPerm.WRITE,   'w'),
    (t.POSIXPerm.EXECUTE, 'x'),
)

_POSIX_PERM_FROM_CHAR = {c: bit for bit, c in _POSIX_PERM_CHARS}


# ── NFS4 recursive inheritance state ─────────────────────────────────────────

@dataclasses.dataclass(slots=True)
class _NFS4InheritedAcls:
    d1_file: t.NFS4ACL
    d1_dir:  t.NFS4ACL
    d2_file: t.NFS4ACL
    d2_dir:  t.NFS4ACL

    @classmethod
    def from_root(cls, root_acl):
        d1_dir = root_acl.generate_inherited_acl(is_dir=True)
        return cls(
            d1_file=root_acl.generate_inherited_acl(is_dir=False),
            d1_dir=d1_dir,
            d2_file=d1_dir.generate_inherited_acl(is_dir=False),
            d2_dir=d1_dir.generate_inherited_acl(is_dir=True),
        )

    def pick(self, depth, is_dir):
        if depth == 1:
            return self.d1_dir if is_dir else self.d1_file
        return self.d2_dir if is_dir else self.d2_file


# ── mode-to-ACL helpers ───────────────────────────────────────────────────────

_MODE_R_NFS4 = (t.NFS4Perm.READ_DATA | t.NFS4Perm.READ_NAMED_ATTRS |
                t.NFS4Perm.READ_ATTRIBUTES | t.NFS4Perm.READ_ACL |
                t.NFS4Perm.SYNCHRONIZE)
_MODE_W_NFS4 = (t.NFS4Perm.WRITE_DATA | t.NFS4Perm.APPEND_DATA |
                t.NFS4Perm.WRITE_NAMED_ATTRS | t.NFS4Perm.WRITE_ATTRIBUTES |
                t.NFS4Perm.WRITE_ACL | t.NFS4Perm.WRITE_OWNER |
                t.NFS4Perm.DELETE_CHILD)
_MODE_X_NFS4 = t.NFS4Perm.EXECUTE


def _mode_to_nfs4_perm(bits):
    p = t.NFS4Perm(0)
    if bits & 4:
        p |= _MODE_R_NFS4
    if bits & 2:
        p |= _MODE_W_NFS4
    if bits & 1:
        p |= _MODE_X_NFS4
    return p


def _mode_to_posix_perm(bits):
    p = t.POSIXPerm(0)
    if bits & 4:
        p |= t.POSIXPerm.READ
    if bits & 2:
        p |= t.POSIXPerm.WRITE
    if bits & 1:
        p |= t.POSIXPerm.EXECUTE
    return p


def _make_trivial_nfs4(stat_mode):
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  _mode_to_nfs4_perm((stat_mode >> 6) & 7), t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.IDENTIFIER_GROUP,
                  _mode_to_nfs4_perm((stat_mode >> 3) & 7), t.NFS4Who.GROUP),
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  _mode_to_nfs4_perm(stat_mode & 7), t.NFS4Who.EVERYONE),
    ]
    return t.NFS4ACL.from_aces(aces)


def _make_trivial_posix(stat_mode):
    aces = [
        t.POSIXAce(t.POSIXTag.USER_OBJ,
                   _mode_to_posix_perm((stat_mode >> 6) & 7)),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ,
                   _mode_to_posix_perm((stat_mode >> 3) & 7)),
        t.POSIXAce(t.POSIXTag.OTHER,
                   _mode_to_posix_perm(stat_mode & 7)),
    ]
    return t.POSIXACL.from_aces(aces)


# ── name resolution ───────────────────────────────────────────────────────────

def _resolve_uid(s):
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return pwd.getpwnam(s).pw_uid
    except KeyError:
        raise ValueError(f'unknown user: {s!r}') from None


def _resolve_gid(s):
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return grp.getgrnam(s).gr_gid
    except KeyError:
        raise ValueError(f'unknown group: {s!r}') from None


# ── entry text splitting ──────────────────────────────────────────────────────

def _split_entries(text):
    result = []
    for line in text.replace(',', '\n').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        result.append(line)
    return result


# ── NFS4 ACE parsing ──────────────────────────────────────────────────────────

def _parse_nfs4_perms(s):
    if s in _NFS4_PERM_SETS:
        return _NFS4_PERM_SETS[s]
    mask = t.NFS4Perm(0)
    for ch in s:
        if ch == '-':
            continue
        if ch not in _NFS4_PERM_FROM_CHAR:
            raise ValueError(f'invalid NFS4 perm char: {ch!r}')
        mask |= _NFS4_PERM_FROM_CHAR[ch]
    return mask


def _parse_nfs4_flags(s):
    flags = t.NFS4Flag(0)
    for ch in s:
        if ch == '-':
            continue
        if ch not in _NFS4_FLAG_FROM_CHAR:
            raise ValueError(f'invalid NFS4 flag char: {ch!r}')
        flags |= _NFS4_FLAG_FROM_CHAR[ch]
    return flags


def _parse_nfs4_ace(s):
    if s.startswith(('owner@:', 'group@:', 'everyone@:')):
        at = s.index(':')
        who_str = s[:at]
        rest = s[at + 1:]
    elif s.startswith(('user:', 'group:')):
        parts = s.split(':', 4)
        if len(parts) < 5:
            raise ValueError(f'invalid NFS4 ACE: {s!r}')
        who_str = f'{parts[0]}:{parts[1]}'
        rest = ':'.join(parts[2:])
    else:
        raise ValueError(f'invalid NFS4 ACE: {s!r}')

    rparts = rest.split(':')
    if len(rparts) != 3:
        raise ValueError(f'invalid NFS4 ACE: {s!r}')
    perms_str, flags_str, type_str = rparts

    perms = _parse_nfs4_perms(perms_str)
    flags = _parse_nfs4_flags(flags_str)

    if type_str not in _NFS4_TYPE_FROM_STR:
        raise ValueError(f'invalid NFS4 ACE type: {type_str!r}')
    ace_type = _NFS4_TYPE_FROM_STR[type_str]

    if who_str == 'owner@':
        return t.NFS4Ace(ace_type, flags, perms, t.NFS4Who.OWNER)
    if who_str == 'group@':
        return t.NFS4Ace(ace_type, flags, perms, t.NFS4Who.GROUP)
    if who_str == 'everyone@':
        return t.NFS4Ace(ace_type, flags, perms, t.NFS4Who.EVERYONE)
    if who_str.startswith('user:'):
        uid = _resolve_uid(who_str[5:])
        return t.NFS4Ace(ace_type, flags, perms, t.NFS4Who.NAMED, uid)
    gid = _resolve_gid(who_str[6:])
    flags |= t.NFS4Flag.IDENTIFIER_GROUP
    return t.NFS4Ace(ace_type, flags, perms, t.NFS4Who.NAMED, gid)


def _parse_nfs4_who_spec(s):
    if s.startswith('owner@'):
        return (t.NFS4Who.OWNER, -1, False)
    if s.startswith('group@'):
        return (t.NFS4Who.GROUP, -1, False)
    if s.startswith('everyone@'):
        return (t.NFS4Who.EVERYONE, -1, False)
    if s.startswith('user:'):
        qual = s[5:].split(':')[0]
        return (t.NFS4Who.NAMED, _resolve_uid(qual), False)
    if s.startswith('group:'):
        qual = s[6:].split(':')[0]
        return (t.NFS4Who.NAMED, _resolve_gid(qual), True)
    raise ValueError(f'invalid NFS4 who spec: {s!r}')


# ── POSIX ACE parsing ─────────────────────────────────────────────────────────

def _parse_posix_perms(s):
    perms = t.POSIXPerm(0)
    for ch in s:
        if ch == '-':
            continue
        if ch not in _POSIX_PERM_FROM_CHAR:
            raise ValueError(f'invalid POSIX perm char: {ch!r}')
        perms |= _POSIX_PERM_FROM_CHAR[ch]
    return perms


def _parse_posix_ace(s):
    default = s.startswith('default:')
    if default:
        s = s[8:]
    parts = s.split(':')
    if len(parts) != 3:
        raise ValueError(f'invalid POSIX ACE: {s!r}')
    tag_str, qual_str, perms_str = parts
    perms = _parse_posix_perms(perms_str)
    if tag_str == 'user':
        if not qual_str:
            return t.POSIXAce(t.POSIXTag.USER_OBJ, perms, default=default)
        return t.POSIXAce(t.POSIXTag.USER, perms,
                          _resolve_uid(qual_str), default=default)
    if tag_str == 'group':
        if not qual_str:
            return t.POSIXAce(t.POSIXTag.GROUP_OBJ, perms, default=default)
        return t.POSIXAce(t.POSIXTag.GROUP, perms,
                          _resolve_gid(qual_str), default=default)
    if tag_str == 'mask':
        return t.POSIXAce(t.POSIXTag.MASK, perms, default=default)
    if tag_str == 'other':
        return t.POSIXAce(t.POSIXTag.OTHER, perms, default=default)
    raise ValueError(f'invalid POSIX tag: {tag_str!r}')


def _parse_posix_remove_spec(s):
    default = s.startswith('default:')
    if default:
        s = s[8:]
    parts = s.split(':', 2)
    tag_str = parts[0]
    qual_str = parts[1].split(':')[0] if len(parts) > 1 else ''
    if tag_str == 'user':
        if not qual_str:
            return (t.POSIXTag.USER_OBJ, -1, default)
        return (t.POSIXTag.USER, _resolve_uid(qual_str), default)
    if tag_str == 'group':
        if not qual_str:
            return (t.POSIXTag.GROUP_OBJ, -1, default)
        return (t.POSIXTag.GROUP, _resolve_gid(qual_str), default)
    if tag_str == 'mask':
        return (t.POSIXTag.MASK, -1, default)
    if tag_str == 'other':
        return (t.POSIXTag.OTHER, -1, default)
    raise ValueError(f'invalid POSIX remove spec: {s!r}')


# ── ACL modification helpers ──────────────────────────────────────────────────

def _apply_nfs4_modify(acl, new_aces):
    aces = list(acl.aces)
    for new_ace in new_aces:
        found = -1
        for i, ace in enumerate(aces):
            if ace.who_type != new_ace.who_type:
                continue
            if ace.ace_type != new_ace.ace_type:
                continue
            if ace.who_type == t.NFS4Who.NAMED:
                if ace.who_id != new_ace.who_id:
                    continue
                if (bool(ace.ace_flags & t.NFS4Flag.IDENTIFIER_GROUP) !=
                        bool(new_ace.ace_flags & t.NFS4Flag.IDENTIFIER_GROUP)):
                    continue
            found = i
            break
        if found >= 0:
            aces[found] = new_ace
        else:
            aces.append(new_ace)
    return t.NFS4ACL.from_aces(aces, acl.acl_flags)


def _apply_nfs4_remove(acl, remove_specs):
    aces = []
    for ace in acl.aces:
        remove = False
        for who_type, who_id, is_group in remove_specs:
            if ace.who_type != who_type:
                continue
            if who_type == t.NFS4Who.NAMED:
                if ace.who_id != who_id:
                    continue
                if (bool(ace.ace_flags & t.NFS4Flag.IDENTIFIER_GROUP) !=
                        is_group):
                    continue
            remove = True
            break
        if not remove:
            aces.append(ace)
    return t.NFS4ACL.from_aces(aces, acl.acl_flags)


def _recalc_posix_mask(all_aces):
    def _process_section(aces_in, is_default):
        has_ext = any(a.tag in (t.POSIXTag.USER, t.POSIXTag.GROUP)
                      for a in aces_in)
        has_mask = any(a.tag == t.POSIXTag.MASK for a in aces_in)
        # Nothing to do for a plain 3-entry (trivial) section.
        if not has_ext and not has_mask:
            return aces_in
        # Mask value = union of all named USER/GROUP entries + GROUP_OBJ.
        # When no named entries remain (e.g. after -x) the mask is set to
        # GROUP_OBJ perms — acl_calc_mask includes ACL_GROUP_OBJ in the union:
        # https://cgit.git.savannah.nongnu.org/cgit/acl.git/tree/libacl/acl_calc_mask.c
        mask_perm = t.POSIXPerm(0)
        for a in aces_in:
            if a.tag in (t.POSIXTag.USER, t.POSIXTag.GROUP, t.POSIXTag.GROUP_OBJ):
                mask_perm |= a.perms
        result = []
        mask_updated = False
        for a in aces_in:
            if a.tag == t.POSIXTag.MASK:
                result.append(t.POSIXAce(t.POSIXTag.MASK, mask_perm,
                                         default=is_default))
                mask_updated = True
            else:
                result.append(a)
        if not mask_updated:
            # Named entries present but no existing mask: insert one.
            idx = next((i for i, a in enumerate(result)
                        if a.tag == t.POSIXTag.GROUP_OBJ),
                       len(result))
            result.insert(idx + 1, t.POSIXAce(t.POSIXTag.MASK, mask_perm,
                                               default=is_default))
        return result

    access_aces = [a for a in all_aces if not a.default]
    default_aces = [a for a in all_aces if a.default]
    return _process_section(access_aces, False) + _process_section(default_aces, True)


def _ensure_posix_mask(all_aces):
    """Add a MASK entry only when named entries exist but no MASK is present.

    Used with --no-mask: we do not recalculate an existing mask, but fsetacl
    still requires one whenever named USER or GROUP entries are present.
    The initial mask value mirrors what standard setfacl produces with -n:
    it uses GROUP_OBJ's permissions rather than the full union.
    """
    def _process_section(aces_in, is_default):
        has_ext = any(a.tag in (t.POSIXTag.USER, t.POSIXTag.GROUP)
                      for a in aces_in)
        has_mask = any(a.tag == t.POSIXTag.MASK for a in aces_in)
        if not has_ext or has_mask:
            return aces_in
        group_perm = next(
            (a.perms for a in aces_in if a.tag == t.POSIXTag.GROUP_OBJ),
            t.POSIXPerm(0),
        )
        result = list(aces_in)
        idx = next((i for i, a in enumerate(result)
                    if a.tag == t.POSIXTag.GROUP_OBJ), len(result))
        result.insert(idx + 1, t.POSIXAce(t.POSIXTag.MASK, group_perm,
                                           default=is_default))
        return result

    access_aces = [a for a in all_aces if not a.default]
    default_aces = [a for a in all_aces if a.default]
    return _process_section(access_aces, False) + _process_section(default_aces, True)


def _apply_posix_modify(acl, new_aces, recalc_mask):
    all_aces = list(acl.aces) + list(acl.default_aces)
    for new_ace in new_aces:
        found = -1
        for i, ace in enumerate(all_aces):
            if (ace.tag == new_ace.tag and ace.id == new_ace.id and
                    ace.default == new_ace.default):
                found = i
                break
        if found >= 0:
            all_aces[found] = new_ace
        else:
            all_aces.append(new_ace)
    if recalc_mask:
        all_aces = _recalc_posix_mask(all_aces)
    return t.POSIXACL.from_aces(all_aces)


def _apply_posix_remove(acl, remove_specs):
    all_aces = list(acl.aces) + list(acl.default_aces)
    result = [a for a in all_aces
              if not any(a.tag == tag and a.id == uid and a.default == dflt
                         for tag, uid, dflt in remove_specs)]
    return t.POSIXACL.from_aces(result)


# ── per-fd operation ──────────────────────────────────────────────────────────

def _remove_posix_default(acl):
    """Return a copy of acl with all default ACEs removed."""
    return t.POSIXACL.from_aces(list(acl.aces))


def _do_setfacl_fd(fd, strip, remove_default, remove_entries, modify_entries,
                   acl_file_entries, no_mask, default_only):
    """Apply operations to fd.  Returns the resulting ACL."""
    acl = t.fgetacl(fd)
    is_posix = isinstance(acl, t.POSIXACL)

    changed = (strip
               or (remove_default and is_posix)
               or bool(remove_entries)
               or bool(modify_entries)
               or acl_file_entries is not None)

    # A trivial POSIX ACL has no xattr — permissions live in the mode bits
    # only and acl.aces is empty.  Incremental operations (modify/remove)
    # need a proper 3-entry base; synthesize it from the current mode.
    # Strip handles this itself below; acl_file_entries replaces the whole ACL.
    # Use `not acl.aces` rather than `acl.trivial`: a directory that has only
    # a default ACL (trivial=False) also has empty access aces and needs the
    # same synthesis so the resulting ACL contains a valid USER_OBJ entry.
    if (not strip and acl_file_entries is None
            and is_posix and not acl.aces
            and (remove_entries or modify_entries)):
        # Synthesise the 3-entry access section from mode bits.  Preserve any
        # existing default ACEs (a dir may have only a default ACL stored, so
        # acl.trivial is False even though acl.aces is empty).
        acl = t.POSIXACL.from_aces(
            list(_make_trivial_posix(os.fstat(fd).st_mode).aces) +
            list(acl.default_aces)
        )

    if strip:
        st = os.fstat(fd)
        if not is_posix:
            acl = _make_trivial_nfs4(st.st_mode)
        else:
            acl = _make_trivial_posix(st.st_mode)

    if remove_default and is_posix:
        if not acl.aces:
            # Access xattr absent (trivial or absorbed into mode bits by the
            # filesystem); reconstruct from mode so the result passes
            # validation when we write it back.
            acl = t.POSIXACL.from_aces(
                list(_make_trivial_posix(os.fstat(fd).st_mode).aces) +
                list(acl.default_aces)
            )
        acl = _remove_posix_default(acl)

    # When --default is active, -m/-x target the default ACL.
    # For POSIX: prefix entries with 'default:' if not already present.
    # For NFS4: no default ACL concept; silently ignore -m/-x.
    if default_only:
        if is_posix:
            # If the default ACL is empty and we're about to add entries,
            # synthesize the required base entries (USER_OBJ, GROUP_OBJ, OTHER)
            # from mode bits so the resulting default ACL is valid.
            if modify_entries and not acl.default_aces:
                base = _make_trivial_posix(os.fstat(fd).st_mode)
                acl = t.POSIXACL.from_aces(
                    list(acl.aces) +
                    [t.POSIXAce(a.tag, a.perms, default=True)
                     for a in base.aces]
                )
            remove_entries = [e if e.startswith('default:') else 'default:' + e
                              for e in remove_entries]
            modify_entries  = [e if e.startswith('default:') else 'default:' + e
                               for e in modify_entries]
        else:
            remove_entries = []
            modify_entries  = []

    if remove_entries:
        if not is_posix:
            specs = [_parse_nfs4_who_spec(e) for e in remove_entries]
            acl = _apply_nfs4_remove(acl, specs)
        else:
            specs = [_parse_posix_remove_spec(e) for e in remove_entries]
            acl = _apply_posix_remove(acl, specs)
            # Reference setfacl recalculates the mask after all commands
            # (including -x) in a single post-loop pass unless -n is set:
            # https://cgit.git.savannah.nongnu.org/cgit/acl.git/tree/tools/do_set.c
            if not no_mask:
                all_aces = _recalc_posix_mask(
                    list(acl.aces) + list(acl.default_aces))
                acl = t.POSIXACL.from_aces(all_aces)

    if modify_entries:
        if not is_posix:
            aces = [_parse_nfs4_ace(e) for e in modify_entries]
            acl = _apply_nfs4_modify(acl, aces)
        else:
            aces = [_parse_posix_ace(e) for e in modify_entries]
            # An explicit mask entry in -m overrides recalculation: do_set.c
            # sets acl_mask_provided=1 and skips acl_calc_mask when set:
            # https://cgit.git.savannah.nongnu.org/cgit/acl.git/tree/tools/do_set.c
            has_explicit_mask = any(a.tag == t.POSIXTag.MASK for a in aces)
            recalc = not no_mask and not has_explicit_mask
            acl = _apply_posix_modify(acl, aces, recalc)
            if not recalc:
                # With --no-mask or explicit mask: fsetacl still requires a
                # MASK when named entries exist.  do_set.c seeds an absent
                # mask via clone_entry(acl, ACL_GROUP_OBJ, &acl, ACL_MASK):
                # https://cgit.git.savannah.nongnu.org/cgit/acl.git/tree/tools/do_set.c
                all_aces = _ensure_posix_mask(
                    list(acl.aces) + list(acl.default_aces))
                acl = t.POSIXACL.from_aces(all_aces)

    if acl_file_entries is not None:
        if not is_posix:
            aces = [_parse_nfs4_ace(e) for e in acl_file_entries]
            acl = t.NFS4ACL.from_aces(aces, acl.acl_flags)
        else:
            aces = [_parse_posix_ace(e) for e in acl_file_entries]
            acl = t.POSIXACL.from_aces(aces)

    if changed:
        t.fsetacl(fd, acl)
    return acl


# ── restore ───────────────────────────────────────────────────────────────────

def _parse_restore_file(text):
    """Parse getfacl output into a list of (path, [entry_lines]) pairs."""
    blocks = []
    current_path = None
    current_entries = []

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            if current_path is not None:
                blocks.append((current_path, current_entries))
                current_path = None
                current_entries = []
        elif line.startswith('# file: '):
            if current_path is not None:
                blocks.append((current_path, current_entries))
            current_path = line[8:]
            current_entries = []
        elif line.startswith('#'):
            pass  # skip owner/group/fhandle comment lines
        else:
            if current_path is not None:
                current_entries.append(line)

    if current_path is not None:
        blocks.append((current_path, current_entries))

    return blocks


# ── mount info (for fsiter) ───────────────────────────────────────────────────

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
        sm = t.statmount(stx.stx_mnt_id, mask=t.STATMOUNT_MNT_POINT)
        mountpoint = sm.mnt_point
        fs_name = _fs_source_from_proc_mounts(mountpoint)
    abs_path = os.path.realpath(path)
    rel = os.path.relpath(abs_path, mountpoint)
    rel_path = None if rel == '.' else rel
    return mountpoint, fs_name, rel_path


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        prog='truenas_setfacl',
        description='Set ACL entries on files.',
    )
    ap.add_argument('-R', '--recursive', action='store_true',
                    help='Process directories recursively; '
                         'does not follow symlinks or cross device boundaries; '
                         'NFS4: depth-1 entries receive an ACL inherited from '
                         'the root, depth-2+ entries receive an ACL inherited '
                         'from depth 1')
    ap.add_argument('-b', '--strip', action='store_true',
                    help='Strip ACL to minimal mode-derived entries')
    ap.add_argument('-k', '--remove-default', action='store_true',
                    help='Remove the default ACL (POSIX only; no-op on NFS4)')
    ap.add_argument('-d', '--default', dest='default_only', action='store_true',
                    help='Apply -m and -x to the default ACL (POSIX only; '
                         'no-op on NFS4)')
    ap.add_argument('-m', dest='modify', action='append', default=[],
                    metavar='entries',
                    help='Add/replace ACL entries (comma-separated); '
                         'applied after -b, -k, and -x')
    ap.add_argument('-x', dest='remove', action='append', default=[],
                    metavar='entries',
                    help='Remove ACL entries by who/tag (comma-separated); '
                         'applied after -b and -k')
    ap.add_argument('-f', dest='acl_file', default=None, metavar='file',
                    help='Replace entire ACL from file (- for stdin); '
                         'applied last')
    ap.add_argument('-n', '--no-mask', action='store_true',
                    help='Do not recalculate POSIX mask after -m')
    ap.add_argument('--restore', metavar='file',
                    help='Restore ACLs from a getfacl backup file '
                         '(- for stdin); paths are taken from the backup')
    ap.add_argument('path', nargs='*')
    args = ap.parse_args()

    if not args.restore and not args.path:
        ap.error('path arguments are required when not using --restore')

    rc = 0

    if args.restore:
        src = args.restore
        if src == '-':
            text = sys.stdin.read()
        else:
            with open(src) as f:
                text = f.read()
        for path, entries in _parse_restore_file(text):
            fd = None
            try:
                fd = t.openat2(path, flags=os.O_RDONLY,
                               resolve=t.RESOLVE_NO_SYMLINKS)
                _do_setfacl_fd(fd, strip=False, remove_default=False,
                               remove_entries=[], modify_entries=[],
                               acl_file_entries=entries, no_mask=False,
                               default_only=False)
            except (OSError, ValueError) as e:
                print(f'truenas_setfacl: {path}: {e}', file=sys.stderr)
                rc = 1
            finally:
                if fd is not None:
                    os.close(fd)
        sys.exit(rc)

    remove_entries = _split_entries(','.join(args.remove))
    modify_entries = _split_entries(','.join(args.modify))

    acl_file_entries = None
    if args.acl_file:
        if args.acl_file == '-':
            text = sys.stdin.read()
        else:
            with open(args.acl_file) as f:
                text = f.read()
        acl_file_entries = _split_entries(text)

    for path in args.path:
        fd = None
        root_acl = None
        try:
            fd = t.openat2(path, flags=os.O_RDONLY,
                           resolve=t.RESOLVE_NO_SYMLINKS)
            root_acl = _do_setfacl_fd(fd, args.strip, args.remove_default,
                                      remove_entries, modify_entries,
                                      acl_file_entries, args.no_mask,
                                      args.default_only)
        except (OSError, ValueError) as e:
            print(f'truenas_setfacl: {path}: {e}', file=sys.stderr)
            rc = 1
        finally:
            if fd is not None:
                os.close(fd)

        if not args.recursive or not os.path.isdir(path) \
                or root_acl is None:
            continue

        try:
            mountpoint, fs_name, rel_path = _get_mount_info(path)
        except OSError as e:
            print(f'truenas_setfacl: {path}: {e}', file=sys.stderr)
            rc = 1
            continue

        nfs4_inh = None
        if isinstance(root_acl, t.NFS4ACL):
            nfs4_inh = _NFS4InheritedAcls.from_root(root_acl)

        with t.iter_filesystem_contents(mountpoint, fs_name,
                                        relative_path=rel_path) as it:
            for item in it:
                full_path = os.path.join(item.parent, item.name)
                if item.islnk:
                    os.close(item.fd)
                    continue
                try:
                    if nfs4_inh is not None:
                        acl = nfs4_inh.pick(len(it.dir_stack()), item.isdir)
                        t.fsetacl(item.fd, acl)
                    else:
                        _do_setfacl_fd(item.fd, args.strip, args.remove_default,
                                       remove_entries, modify_entries,
                                       acl_file_entries, args.no_mask,
                                       args.default_only)
                except (OSError, ValueError) as e:
                    print(f'truenas_setfacl: {full_path}: {e}',
                          file=sys.stderr)
                    rc = 1
                finally:
                    os.close(item.fd)

    sys.exit(rc)


if __name__ == '__main__':
    main()
