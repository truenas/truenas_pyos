# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Tests for the parsing, ACL modification, and live setfacl logic in
scripts/_setfacl.py.

Pure-Python tests exercise parsing and modification without touching the
filesystem.  Live tests use the nfs4_dataset / posix_dataset fixtures from
the parent conftest.
"""

import os

import pytest
import truenas_os as t

from _truenas_os_scripts._getfacl import _nfs4_perm_str, _nfs4_flag_str
from _truenas_os_scripts._setfacl import (
    _split_entries,
    _parse_nfs4_perms,
    _parse_nfs4_flags,
    _parse_nfs4_ace,
    _parse_nfs4_who_spec,
    _parse_posix_ace,
    _parse_posix_remove_spec,
    _mode_to_nfs4_perm,
    _mode_to_posix_perm,
    _make_trivial_nfs4,
    _make_trivial_posix,
    _apply_nfs4_modify,
    _apply_nfs4_remove,
    _recalc_posix_mask,
    _apply_posix_modify,
    _apply_posix_remove,
    _remove_posix_default,
    _parse_restore_file,
    _do_setfacl_fd,
)


# ── _split_entries ────────────────────────────────────────────────────────────

def test_split_entries_blank_lines_ignored():
    assert _split_entries('\n\n  \n') == []


def test_split_entries_comment_lines_ignored():
    result = _split_entries('# file: /x\n# owner: root\nowner@:r:--:allow')
    assert result == ['owner@:r:--:allow']


def test_split_entries_comma_separated():
    result = _split_entries('owner@:r:--:allow,group@:r:--:allow')
    assert result == ['owner@:r:--:allow', 'group@:r:--:allow']


def test_split_entries_newline_separated():
    result = _split_entries('owner@:r:--:allow\neveryone@:r:--:allow')
    assert result == ['owner@:r:--:allow', 'everyone@:r:--:allow']


def test_split_entries_strips_whitespace():
    result = _split_entries('  owner@:r:--:allow  \n  group@:r:--:allow  ')
    assert result == ['owner@:r:--:allow', 'group@:r:--:allow']


# ── _parse_nfs4_perms ─────────────────────────────────────────────────────────

def test_parse_nfs4_perms_all_chars():
    mask = _parse_nfs4_perms('rwaRWxDdpPcCos')
    assert mask & t.NFS4Perm.READ_DATA
    assert mask & t.NFS4Perm.WRITE_DATA
    assert mask & t.NFS4Perm.SYNCHRONIZE


def test_parse_nfs4_perms_dashes_are_zero():
    assert int(_parse_nfs4_perms('--------------')) == 0


def test_parse_nfs4_perms_read_only():
    mask = _parse_nfs4_perms('r-------------')
    assert mask & t.NFS4Perm.READ_DATA
    assert not (mask & t.NFS4Perm.WRITE_DATA)


def test_parse_nfs4_perms_invalid_char_raises():
    with pytest.raises(ValueError, match='invalid NFS4 perm char'):
        _parse_nfs4_perms('z')


# ── _parse_nfs4_flags ─────────────────────────────────────────────────────────

def test_parse_nfs4_flags_all_chars():
    flags = _parse_nfs4_flags('fdniSFgI')
    assert flags & t.NFS4Flag.FILE_INHERIT
    assert flags & t.NFS4Flag.DIRECTORY_INHERIT
    assert flags & t.NFS4Flag.INHERITED


def test_parse_nfs4_flags_dashes_are_zero():
    assert int(_parse_nfs4_flags('--------')) == 0


def test_parse_nfs4_flags_invalid_char_raises():
    with pytest.raises(ValueError, match='invalid NFS4 flag char'):
        _parse_nfs4_flags('z')


# ── _parse_nfs4_ace ───────────────────────────────────────────────────────────

def test_parse_nfs4_ace_owner_allow():
    ace = _parse_nfs4_ace('owner@:r-------------:--------:allow')
    assert ace.who_type    == t.NFS4Who.OWNER
    assert ace.ace_type    == t.NFS4AceType.ALLOW
    assert ace.access_mask & t.NFS4Perm.READ_DATA


def test_parse_nfs4_ace_group_deny():
    ace = _parse_nfs4_ace('group@:-w------------:--------:deny')
    assert ace.who_type == t.NFS4Who.GROUP
    assert ace.ace_type == t.NFS4AceType.DENY


def test_parse_nfs4_ace_everyone_allow():
    ace = _parse_nfs4_ace('everyone@:r-------------:--------:allow')
    assert ace.who_type == t.NFS4Who.EVERYONE


def test_parse_nfs4_ace_named_user():
    ace = _parse_nfs4_ace('user:1001:r-------------:--------:allow')
    assert ace.who_type == t.NFS4Who.NAMED
    assert ace.who_id   == 1001
    assert not (ace.ace_flags & t.NFS4Flag.IDENTIFIER_GROUP)


def test_parse_nfs4_ace_named_group():
    ace = _parse_nfs4_ace('group:2002:r-------------:--------:allow')
    assert ace.who_type == t.NFS4Who.NAMED
    assert ace.who_id   == 2002
    assert ace.ace_flags & t.NFS4Flag.IDENTIFIER_GROUP


def test_parse_nfs4_ace_invalid_type_raises():
    with pytest.raises(ValueError, match='invalid NFS4 ACE type'):
        _parse_nfs4_ace('owner@:r-------------:--------:bogus')


def test_parse_nfs4_ace_invalid_format_raises():
    with pytest.raises(ValueError):
        _parse_nfs4_ace('notanace')


# ── _parse_nfs4_who_spec ──────────────────────────────────────────────────────

def test_parse_nfs4_who_spec_owner():
    who_type, who_id, is_group = _parse_nfs4_who_spec('owner@')
    assert who_type == t.NFS4Who.OWNER
    assert not is_group


def test_parse_nfs4_who_spec_group():
    who_type, _, _ = _parse_nfs4_who_spec('group@')
    assert who_type == t.NFS4Who.GROUP


def test_parse_nfs4_who_spec_everyone():
    who_type, _, _ = _parse_nfs4_who_spec('everyone@')
    assert who_type == t.NFS4Who.EVERYONE


def test_parse_nfs4_who_spec_named_user():
    who_type, who_id, is_group = _parse_nfs4_who_spec('user:1001')
    assert who_type == t.NFS4Who.NAMED
    assert who_id   == 1001
    assert not is_group


def test_parse_nfs4_who_spec_named_group():
    who_type, who_id, is_group = _parse_nfs4_who_spec('group:2002')
    assert who_type == t.NFS4Who.NAMED
    assert who_id   == 2002
    assert is_group


def test_parse_nfs4_who_spec_invalid_raises():
    with pytest.raises(ValueError, match='invalid NFS4 who spec'):
        _parse_nfs4_who_spec('bogus')


# ── _parse_posix_ace ──────────────────────────────────────────────────────────

def test_parse_posix_ace_user_obj():
    ace = _parse_posix_ace('user::rwx')
    assert ace.tag    == t.POSIXTag.USER_OBJ
    assert ace.perms  == t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE
    assert ace.default is False


def test_parse_posix_ace_named_user():
    ace = _parse_posix_ace('user:1001:r--')
    assert ace.tag   == t.POSIXTag.USER
    assert ace.id    == 1001
    assert ace.perms == t.POSIXPerm.READ


def test_parse_posix_ace_group_obj():
    ace = _parse_posix_ace('group::r-x')
    assert ace.tag == t.POSIXTag.GROUP_OBJ


def test_parse_posix_ace_named_group():
    ace = _parse_posix_ace('group:2002:rw-')
    assert ace.tag == t.POSIXTag.GROUP
    assert ace.id  == 2002


def test_parse_posix_ace_mask():
    assert _parse_posix_ace('mask::r-x').tag == t.POSIXTag.MASK


def test_parse_posix_ace_other_no_perms():
    ace = _parse_posix_ace('other::---')
    assert ace.tag       == t.POSIXTag.OTHER
    assert int(ace.perms) == 0


def test_parse_posix_ace_default_prefix():
    ace = _parse_posix_ace('default:user::rwx')
    assert ace.tag     == t.POSIXTag.USER_OBJ
    assert ace.default is True


def test_parse_posix_ace_invalid_tag_raises():
    with pytest.raises(ValueError, match='invalid POSIX tag'):
        _parse_posix_ace('bogus::rwx')


# ── _parse_posix_remove_spec ──────────────────────────────────────────────────

def test_parse_posix_remove_spec_user_obj():
    tag, uid, default = _parse_posix_remove_spec('user:')
    assert tag     == t.POSIXTag.USER_OBJ
    assert uid     == -1
    assert default is False


def test_parse_posix_remove_spec_named_user():
    tag, uid, _ = _parse_posix_remove_spec('user:1001')
    assert tag == t.POSIXTag.USER
    assert uid == 1001


def test_parse_posix_remove_spec_default_group():
    tag, uid, default = _parse_posix_remove_spec('default:group:2002')
    assert tag     == t.POSIXTag.GROUP
    assert uid     == 2002
    assert default is True


def test_parse_posix_remove_spec_mask():
    tag, _, _ = _parse_posix_remove_spec('mask:')
    assert tag == t.POSIXTag.MASK


# ── _mode_to_nfs4_perm ────────────────────────────────────────────────────────

def test_mode_to_nfs4_perm_read_bit():
    p = _mode_to_nfs4_perm(4)
    assert     p & t.NFS4Perm.READ_DATA
    assert not (p & t.NFS4Perm.WRITE_DATA)
    assert not (p & t.NFS4Perm.EXECUTE)


def test_mode_to_nfs4_perm_write_bit():
    p = _mode_to_nfs4_perm(2)
    assert     p & t.NFS4Perm.WRITE_DATA
    assert not (p & t.NFS4Perm.READ_DATA)


def test_mode_to_nfs4_perm_execute_bit():
    assert _mode_to_nfs4_perm(1) & t.NFS4Perm.EXECUTE


def test_mode_to_nfs4_perm_zero():
    assert int(_mode_to_nfs4_perm(0)) == 0


# ── _mode_to_posix_perm ───────────────────────────────────────────────────────

def test_mode_to_posix_perm_rwx():
    assert _mode_to_posix_perm(7) == (
        t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE
    )


def test_mode_to_posix_perm_read_only():
    assert _mode_to_posix_perm(4) == t.POSIXPerm.READ


def test_mode_to_posix_perm_zero():
    assert int(_mode_to_posix_perm(0)) == 0


# ── _make_trivial_nfs4 ────────────────────────────────────────────────────────

def test_make_trivial_nfs4_ace_count():
    assert len(_make_trivial_nfs4(0o755).aces) == 3


def test_make_trivial_nfs4_owner_gets_rwx_on_0o755():
    acl   = _make_trivial_nfs4(0o755)
    owner = next(a for a in acl.aces if a.who_type == t.NFS4Who.OWNER)
    assert owner.access_mask & t.NFS4Perm.READ_DATA
    assert owner.access_mask & t.NFS4Perm.WRITE_DATA
    assert owner.access_mask & t.NFS4Perm.EXECUTE


def test_make_trivial_nfs4_group_no_write_on_0o755():
    acl   = _make_trivial_nfs4(0o755)
    group = next(a for a in acl.aces if a.who_type == t.NFS4Who.GROUP)
    assert     group.access_mask & t.NFS4Perm.READ_DATA
    assert not (group.access_mask & t.NFS4Perm.WRITE_DATA)


def test_make_trivial_nfs4_000_no_perms():
    for ace in _make_trivial_nfs4(0o000).aces:
        assert int(ace.access_mask) == 0


# ── _make_trivial_posix ───────────────────────────────────────────────────────

def test_make_trivial_posix_ace_count():
    assert len(_make_trivial_posix(0o644).aces) == 3


def test_make_trivial_posix_user_obj_rw_on_0o644():
    acl = _make_trivial_posix(0o644)
    uo  = next(a for a in acl.aces if a.tag == t.POSIXTag.USER_OBJ)
    assert     uo.perms & t.POSIXPerm.READ
    assert     uo.perms & t.POSIXPerm.WRITE
    assert not (uo.perms & t.POSIXPerm.EXECUTE)


def test_make_trivial_posix_other_no_perms_on_0o640():
    acl   = _make_trivial_posix(0o640)
    other = next(a for a in acl.aces if a.tag == t.POSIXTag.OTHER)
    assert int(other.perms) == 0


# ── _apply_nfs4_modify ────────────────────────────────────────────────────────

_BASE_NFS4 = t.NFS4ACL.from_aces([
    t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
              t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER),
    t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
              t.NFS4Perm.READ_DATA, t.NFS4Who.EVERYONE),
])


def test_apply_nfs4_modify_replaces_existing():
    new = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA | t.NFS4Perm.WRITE_DATA,
                    t.NFS4Who.OWNER)
    result = _apply_nfs4_modify(_BASE_NFS4, [new])
    assert len(result.aces) == 2
    owner = next(a for a in result.aces if a.who_type == t.NFS4Who.OWNER)
    assert owner.access_mask & t.NFS4Perm.WRITE_DATA


def test_apply_nfs4_modify_appends_new():
    new = t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                    t.NFS4Perm.WRITE_DATA, t.NFS4Who.GROUP)
    result = _apply_nfs4_modify(_BASE_NFS4, [new])
    assert len(result.aces) == 3
    group = next(a for a in result.aces if a.who_type == t.NFS4Who.GROUP)
    assert group.ace_type == t.NFS4AceType.DENY


# ── _apply_nfs4_remove ────────────────────────────────────────────────────────

def test_apply_nfs4_remove_by_who_type():
    result = _apply_nfs4_remove(_BASE_NFS4, [(t.NFS4Who.EVERYONE, -1, False)])
    assert len(result.aces) == 1
    assert result.aces[0].who_type == t.NFS4Who.OWNER


def test_apply_nfs4_remove_named_user():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, 1001),
    ])
    result = _apply_nfs4_remove(acl, [(t.NFS4Who.NAMED, 1001, False)])
    assert len(result.aces) == 1
    assert result.aces[0].who_type == t.NFS4Who.OWNER


def test_apply_nfs4_remove_nonexistent_is_noop():
    result = _apply_nfs4_remove(_BASE_NFS4, [(t.NFS4Who.GROUP, -1, False)])
    assert len(result.aces) == 2


# ── _recalc_posix_mask ────────────────────────────────────────────────────────

def test_recalc_posix_mask_no_named_entries_no_mask_added():
    aces = [
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.OTHER,     t.POSIXPerm(0)),
    ]
    result = _recalc_posix_mask(aces)
    assert all(a.tag != t.POSIXTag.MASK for a in result)


def test_recalc_posix_mask_is_union_of_named_and_group_obj():
    aces = [
        t.POSIXAce(t.POSIXTag.USER_OBJ,
                   t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE),
        t.POSIXAce(t.POSIXTag.USER,
                   t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, id=1001),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.OTHER,     t.POSIXPerm(0)),
    ]
    result = _recalc_posix_mask(aces)
    mask = next(a for a in result if a.tag == t.POSIXTag.MASK)
    # union of USER:1001(r-x) and GROUP_OBJ(r--) = r-x
    assert     mask.perms & t.POSIXPerm.READ
    assert     mask.perms & t.POSIXPerm.EXECUTE
    assert not (mask.perms & t.POSIXPerm.WRITE)


def test_recalc_posix_mask_replaces_existing_mask():
    aces = [
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
        t.POSIXAce(t.POSIXTag.USER,       t.POSIXPerm.EXECUTE, id=1001),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ,  t.POSIXPerm(0)),
        t.POSIXAce(t.POSIXTag.MASK,
                   t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE),
        t.POSIXAce(t.POSIXTag.OTHER,      t.POSIXPerm(0)),
    ]
    result    = _recalc_posix_mask(aces)
    masks     = [a for a in result if a.tag == t.POSIXTag.MASK]
    assert len(masks) == 1
    # union of USER:1001(--x) and GROUP_OBJ(---) = --x
    assert masks[0].perms == t.POSIXPerm.EXECUTE


# ── _apply_posix_modify ───────────────────────────────────────────────────────

_BASE_POSIX = t.POSIXACL.from_aces([
    t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
    t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ),
    t.POSIXAce(t.POSIXTag.OTHER,     t.POSIXPerm(0)),
])


def test_apply_posix_modify_replaces_existing():
    new    = t.POSIXAce(t.POSIXTag.USER_OBJ,
                        t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE)
    result = _apply_posix_modify(_BASE_POSIX, [new], recalc_mask=False)
    uo     = next(a for a in result.aces if a.tag == t.POSIXTag.USER_OBJ)
    assert uo.perms & t.POSIXPerm.EXECUTE


def test_apply_posix_modify_adds_named_user_and_recalcs_mask():
    new    = t.POSIXAce(t.POSIXTag.USER,
                        t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, id=1001)
    result = _apply_posix_modify(_BASE_POSIX, [new], recalc_mask=True)
    named  = [a for a in result.aces if a.tag == t.POSIXTag.USER]
    assert len(named) == 1
    assert named[0].id == 1001
    assert any(a.tag == t.POSIXTag.MASK for a in result.aces)


# ── _apply_posix_remove ───────────────────────────────────────────────────────

def test_apply_posix_remove_named_user():
    acl = t.POSIXACL.from_aces([
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
        t.POSIXAce(t.POSIXTag.USER,       t.POSIXPerm.READ, id=1001),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ,  t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.MASK,       t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.OTHER,      t.POSIXPerm(0)),
    ])
    result = _apply_posix_remove(acl, [(t.POSIXTag.USER, 1001, False)])
    assert all(not (a.tag == t.POSIXTag.USER and a.id == 1001)
               for a in result.aces)


def test_apply_posix_remove_default_ace():
    acl = t.POSIXACL.from_aces([
        t.POSIXAce(t.POSIXTag.USER_OBJ, t.POSIXPerm.READ | t.POSIXPerm.WRITE),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.OTHER, t.POSIXPerm(0)),
        t.POSIXAce(t.POSIXTag.USER_OBJ, t.POSIXPerm.READ, default=True),
        t.POSIXAce(t.POSIXTag.OTHER, t.POSIXPerm(0), default=True),
    ])
    result = _apply_posix_remove(acl, [(t.POSIXTag.USER_OBJ, -1, True)])
    assert not any(a.tag == t.POSIXTag.USER_OBJ and a.default
                   for a in result.default_aces)


# ── getfacl format → setfacl parse roundtrip ─────────────────────────────────

def _nfs4_ace_to_line(ace):
    who_map = {
        t.NFS4Who.OWNER:    'owner@',
        t.NFS4Who.GROUP:    'group@',
        t.NFS4Who.EVERYONE: 'everyone@',
    }
    type_map = {
        t.NFS4AceType.ALLOW: 'allow',
        t.NFS4AceType.DENY:  'deny',
    }
    return (f'{who_map[ace.who_type]}:{_nfs4_perm_str(ace.access_mask)}'
            f':{_nfs4_flag_str(ace.ace_flags)}:{type_map[ace.ace_type]}')


def test_nfs4_text_roundtrip_owner_allow():
    original = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                         t.NFS4Perm.READ_DATA | t.NFS4Perm.EXECUTE,
                         t.NFS4Who.OWNER)
    parsed = _parse_nfs4_ace(_nfs4_ace_to_line(original))
    assert parsed.who_type    == original.who_type
    assert parsed.ace_type    == original.ace_type
    assert parsed.access_mask == original.access_mask


def test_nfs4_text_roundtrip_deny_with_flags():
    original = t.NFS4Ace(t.NFS4AceType.DENY,
                         t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT,
                         t.NFS4Perm.WRITE_DATA, t.NFS4Who.EVERYONE)
    parsed = _parse_nfs4_ace(_nfs4_ace_to_line(original))
    assert parsed.ace_type    == t.NFS4AceType.DENY
    assert parsed.ace_flags   == original.ace_flags
    assert parsed.access_mask == original.access_mask


def _posix_ace_to_line(ace):
    tag_prefix = {
        t.POSIXTag.USER_OBJ: 'user',  t.POSIXTag.USER:      'user',
        t.POSIXTag.GROUP_OBJ:'group', t.POSIXTag.GROUP:     'group',
        t.POSIXTag.MASK:     'mask',  t.POSIXTag.OTHER:     'other',
    }
    if ace.tag in (t.POSIXTag.USER_OBJ, t.POSIXTag.GROUP_OBJ,
                   t.POSIXTag.MASK, t.POSIXTag.OTHER):
        qual = ''
    else:
        qual = str(ace.id)
    perm_str = ''.join(
        c if ace.perms & bit else '-'
        for bit, c in ((t.POSIXPerm.READ, 'r'), (t.POSIXPerm.WRITE, 'w'),
                       (t.POSIXPerm.EXECUTE, 'x'))
    )
    prefix = 'default:' if ace.default else ''
    return f'{prefix}{tag_prefix[ace.tag]}:{qual}:{perm_str}'


def test_posix_text_roundtrip_user_obj():
    original = t.POSIXAce(t.POSIXTag.USER_OBJ,
                           t.POSIXPerm.READ | t.POSIXPerm.WRITE)
    parsed = _parse_posix_ace(_posix_ace_to_line(original))
    assert parsed.tag    == original.tag
    assert parsed.perms  == original.perms
    assert parsed.default is False


def test_posix_text_roundtrip_named_user():
    original = t.POSIXAce(t.POSIXTag.USER, t.POSIXPerm.READ, id=1001)
    parsed   = _parse_posix_ace(_posix_ace_to_line(original))
    assert parsed.tag   == t.POSIXTag.USER
    assert parsed.id    == 1001
    assert parsed.perms == t.POSIXPerm.READ


def test_posix_text_roundtrip_default_ace():
    original = t.POSIXAce(t.POSIXTag.OTHER, t.POSIXPerm(0), default=True)
    parsed   = _parse_posix_ace(_posix_ace_to_line(original))
    assert parsed.tag     == t.POSIXTag.OTHER
    assert parsed.default is True


# ── _remove_posix_default ─────────────────────────────────────────────────────

def test_remove_posix_default_clears_default_aces():
    acl = t.POSIXACL.from_aces([
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.OTHER,     t.POSIXPerm(0)),
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ, default=True),
        t.POSIXAce(t.POSIXTag.OTHER,     t.POSIXPerm(0), default=True),
    ])
    result = _remove_posix_default(acl)
    assert result.default_aces == []
    assert len(result.aces) == 3


def test_remove_posix_default_preserves_access_aces():
    acl = t.POSIXACL.from_aces([
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
        t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ),
        t.POSIXAce(t.POSIXTag.OTHER,     t.POSIXPerm(0)),
        t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ, default=True),
    ])
    result = _remove_posix_default(acl)
    uo = next(a for a in result.aces if a.tag == t.POSIXTag.USER_OBJ)
    assert uo.perms == t.POSIXPerm.READ | t.POSIXPerm.WRITE


# ── _parse_restore_file ───────────────────────────────────────────────────────

def test_parse_restore_file_single_block():
    dump = (
        '# file: /mnt/data/foo\n'
        '# owner: root\n'
        '# group: wheel\n'
        'user::rwx\n'
        'group::r-x\n'
        'other::r-x\n'
    )
    blocks = _parse_restore_file(dump)
    assert len(blocks) == 1
    path, entries = blocks[0]
    assert path == '/mnt/data/foo'
    assert entries == ['user::rwx', 'group::r-x', 'other::r-x']


def test_parse_restore_file_multiple_blocks():
    dump = (
        '# file: /a\n'
        'user::rwx\n'
        'group::r-x\n'
        'other::---\n'
        '\n'
        '# file: /b\n'
        'user::rw-\n'
        'group::r--\n'
        'other::---\n'
    )
    blocks = _parse_restore_file(dump)
    assert len(blocks) == 2
    assert blocks[0][0] == '/a'
    assert blocks[1][0] == '/b'
    assert blocks[1][1] == ['user::rw-', 'group::r--', 'other::---']


def test_parse_restore_file_skips_comment_lines():
    dump = (
        '# file: /x\n'
        '# owner: root\n'
        '# group: root\n'
        '# fhandle: deadbeef\n'
        'user::rwx\n'
    )
    _, entries = _parse_restore_file(dump)[0]
    assert entries == ['user::rwx']


def test_parse_restore_file_nfs4_entries():
    dump = (
        '# file: /mnt/tank/dir\n'
        '# owner: root\n'
        '# group: wheel\n'
        'owner@:rwaRWxDdpPcCos:--------:allow\n'
        'group@:r-x----------:--------:allow\n'
        'everyone@:r-x--------:--------:allow\n'
    )
    _, entries = _parse_restore_file(dump)[0]
    assert len(entries) == 3
    assert entries[0].startswith('owner@:')


def test_parse_restore_file_empty_input():
    assert _parse_restore_file('') == []


# ── live tests ────────────────────────────────────────────────────────────────

def _open_file(directory, name):
    return os.open(os.path.join(directory, name),
                   os.O_RDWR | os.O_CREAT, 0o644)


def _open_dir(directory, name):
    path = os.path.join(directory, name)
    if os.path.exists(path) and not os.path.isdir(path):
        os.unlink(path)
    os.makedirs(path, 0o755, exist_ok=True)
    return os.open(path, os.O_RDONLY)


def _do(fd, **kw):
    """Call _do_setfacl_fd with safe defaults for unspecified params."""
    defaults = dict(strip=False, remove_default=False, remove_entries=[],
                    modify_entries=[], acl_file_entries=None,
                    no_mask=False, default_only=False)
    defaults.update(kw)
    return _do_setfacl_fd(fd, **defaults)


def test_do_setfacl_fd_strip_posix(posix_dataset):
    fd = _open_file(posix_dataset, 'setfacl_strip_posix')
    try:
        extended = t.POSIXACL.from_aces([
            t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
            t.POSIXAce(t.POSIXTag.USER,       t.POSIXPerm.READ, id=os.getuid()),
            t.POSIXAce(t.POSIXTag.GROUP_OBJ,  t.POSIXPerm.READ),
            t.POSIXAce(t.POSIXTag.MASK,       t.POSIXPerm.READ),
            t.POSIXAce(t.POSIXTag.OTHER,      t.POSIXPerm(0)),
        ])
        t.fsetacl(fd, extended)
        _do(fd, strip=True)
        result = t.fgetacl(fd)
        assert not any(a.tag == t.POSIXTag.USER for a in result.aces)
    finally:
        os.close(fd)


def test_do_setfacl_fd_modify_posix_adds_named_user(posix_dataset):
    fd = _open_file(posix_dataset, 'setfacl_modify_posix')
    try:
        _do(fd, strip=True)
        _do(fd, modify_entries=[f'user:{os.getuid()}:r--'])
        result = t.fgetacl(fd)
        named  = [a for a in result.aces
                  if a.tag == t.POSIXTag.USER and a.id == os.getuid()]
        assert len(named) == 1
        assert named[0].perms == t.POSIXPerm.READ
    finally:
        os.close(fd)


def test_do_setfacl_fd_remove_default_posix(posix_dataset):
    fd = _open_dir(posix_dataset, 'setfacl_rmdefault_posix')
    uid = os.getuid()
    try:
        # Use a named user entry so the access ACL is extended (non-trivial)
        # and therefore stored as an xattr.  Trivial ACLs may be absorbed
        # into mode bits by the filesystem and read back as empty aces.
        with_default = t.POSIXACL.from_aces([
            t.POSIXAce(t.POSIXTag.USER_OBJ,  t.POSIXPerm.READ | t.POSIXPerm.WRITE),
            t.POSIXAce(t.POSIXTag.USER,       t.POSIXPerm.READ, id=uid),
            t.POSIXAce(t.POSIXTag.GROUP_OBJ,  t.POSIXPerm.READ),
            t.POSIXAce(t.POSIXTag.MASK,       t.POSIXPerm.READ),
            t.POSIXAce(t.POSIXTag.OTHER,      t.POSIXPerm(0)),
            t.POSIXAce(t.POSIXTag.USER_OBJ,   t.POSIXPerm.READ, default=True),
            t.POSIXAce(t.POSIXTag.GROUP_OBJ,  t.POSIXPerm.READ, default=True),
            t.POSIXAce(t.POSIXTag.OTHER,      t.POSIXPerm(0), default=True),
        ])
        t.fsetacl(fd, with_default)
        _do(fd, remove_default=True)
        result = t.fgetacl(fd)
        assert result.default_aces == []
        assert any(a.tag == t.POSIXTag.USER and a.id == uid for a in result.aces)
    finally:
        os.close(fd)


def test_do_setfacl_fd_default_only_adds_default_ace(posix_dataset):
    fd = _open_dir(posix_dataset, 'setfacl_default_only_posix')
    try:
        _do(fd, strip=True)
        _do(fd, modify_entries=[f'user:{os.getuid()}:r--'], default_only=True)
        result = t.fgetacl(fd)
        default_named = [a for a in result.default_aces
                         if a.tag == t.POSIXTag.USER and a.id == os.getuid()]
        assert len(default_named) == 1
        assert default_named[0].perms == t.POSIXPerm.READ
        assert not any(a.tag == t.POSIXTag.USER and a.id == os.getuid()
                       for a in result.aces)
    finally:
        os.close(fd)


def test_do_setfacl_fd_strip_nfs4(nfs4_dataset):
    fd = _open_file(nfs4_dataset, 'setfacl_strip_nfs4')
    try:
        _do(fd, strip=True)
        result    = t.fgetacl(fd)
        who_types = {a.who_type for a in result.aces}
        assert t.NFS4Who.OWNER    in who_types
        assert t.NFS4Who.EVERYONE in who_types
        assert not any(a.who_type == t.NFS4Who.NAMED for a in result.aces)
    finally:
        os.close(fd)


def test_do_setfacl_fd_add_then_remove_named_user_nfs4(nfs4_dataset):
    fd  = _open_file(nfs4_dataset, 'setfacl_remove_nfs4')
    uid = os.getuid()
    try:
        _do(fd, modify_entries=[f'user:{uid}:rwaRWxDdpPcCos:--------:allow'])
        assert any(a.who_type == t.NFS4Who.NAMED and a.who_id == uid
                   for a in t.fgetacl(fd).aces)
        _do(fd, remove_entries=[f'user:{uid}'])
        assert not any(a.who_type == t.NFS4Who.NAMED and a.who_id == uid
                       for a in t.fgetacl(fd).aces)
    finally:
        os.close(fd)
