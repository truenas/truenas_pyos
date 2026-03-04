# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Tests for the pure-Python formatting logic in scripts/_getfacl.py.

These tests exercise text and JSON output functions not covered by the C
extension tests.  No filesystem access is required.
"""

import truenas_os as t

from _truenas_os_scripts._getfacl import (
    _nfs4_perm_str,
    _nfs4_flag_str,
    _nfs4_who_str,
    _posix_perm_str,
    _posix_qualifier,
    _format_nfs4_text,
    _format_posix_text,
    _nfs4_ace_to_dict,
    _posix_ace_to_dict,
    _format_nfs4_json,
    _format_posix_json,
)


# ── shared ACL objects ────────────────────────────────────────────────────────

_NFS4_ACL = t.NFS4ACL.from_aces([
    t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
              t.NFS4Perm.READ_DATA | t.NFS4Perm.WRITE_DATA | t.NFS4Perm.EXECUTE,
              t.NFS4Who.OWNER),
    t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
              t.NFS4Perm.WRITE_DATA, t.NFS4Who.EVERYONE),
])

_POSIX_ACL = t.POSIXACL.from_aces([
    t.POSIXAce(t.POSIXTag.USER_OBJ,
               t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE),
    t.POSIXAce(t.POSIXTag.GROUP_OBJ, t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
    t.POSIXAce(t.POSIXTag.OTHER, t.POSIXPerm(0)),
    t.POSIXAce(t.POSIXTag.USER_OBJ,
               t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
    t.POSIXAce(t.POSIXTag.OTHER, t.POSIXPerm(0), default=True),
])


# ── _nfs4_perm_str ────────────────────────────────────────────────────────────

def test_nfs4_perm_str_all_set():
    all_perms = (
        t.NFS4Perm.READ_DATA         | t.NFS4Perm.WRITE_DATA        |
        t.NFS4Perm.APPEND_DATA       | t.NFS4Perm.READ_NAMED_ATTRS  |
        t.NFS4Perm.WRITE_NAMED_ATTRS | t.NFS4Perm.EXECUTE           |
        t.NFS4Perm.DELETE_CHILD      | t.NFS4Perm.DELETE            |
        t.NFS4Perm.READ_ATTRIBUTES   | t.NFS4Perm.WRITE_ATTRIBUTES  |
        t.NFS4Perm.READ_ACL          | t.NFS4Perm.WRITE_ACL         |
        t.NFS4Perm.WRITE_OWNER       | t.NFS4Perm.SYNCHRONIZE
    )
    assert _nfs4_perm_str(all_perms) == 'rwaRWxDdpPcCos'


def test_nfs4_perm_str_none_set():
    assert _nfs4_perm_str(t.NFS4Perm(0)) == '--------------'


def test_nfs4_perm_str_read_execute():
    s = _nfs4_perm_str(t.NFS4Perm.READ_DATA | t.NFS4Perm.EXECUTE)
    assert s[0] == 'r'   # READ_DATA is position 0
    assert s[5] == 'x'   # EXECUTE   is position 5
    assert s.count('-') == 12


# ── _nfs4_flag_str ────────────────────────────────────────────────────────────

def test_nfs4_flag_str_all_set():
    # IDENTIFIER_GROUP (g) is intentionally excluded from text output;
    # it is implicit in the 'group:' who prefix, matching FreeBSD getfacl.
    all_flags = (
        t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT |
        t.NFS4Flag.NO_PROPAGATE_INHERIT | t.NFS4Flag.INHERIT_ONLY |
        t.NFS4Flag.SUCCESSFUL_ACCESS | t.NFS4Flag.FAILED_ACCESS |
        t.NFS4Flag.IDENTIFIER_GROUP | t.NFS4Flag.INHERITED
    )
    assert _nfs4_flag_str(all_flags) == 'fdniSFI'


def test_nfs4_flag_str_none_set():
    assert _nfs4_flag_str(t.NFS4Flag(0)) == '-------'


def test_nfs4_flag_str_inherit_subset():
    s = _nfs4_flag_str(t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT)
    assert s[0] == 'f'
    assert s[1] == 'd'
    assert s.count('-') == 5


# ── _nfs4_who_str ─────────────────────────────────────────────────────────────

def test_nfs4_who_str_owner():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    assert _nfs4_who_str(ace, numeric=True) == 'owner@'


def test_nfs4_who_str_group():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA, t.NFS4Who.GROUP)
    assert _nfs4_who_str(ace, numeric=True) == 'group@'


def test_nfs4_who_str_everyone():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA, t.NFS4Who.EVERYONE)
    assert _nfs4_who_str(ace, numeric=True) == 'everyone@'


def test_nfs4_who_str_named_user_numeric():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, 1234)
    assert _nfs4_who_str(ace, numeric=True) == 'user:1234'


def test_nfs4_who_str_named_group_numeric():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.IDENTIFIER_GROUP,
                    t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, 5678)
    assert _nfs4_who_str(ace, numeric=True) == 'group:5678'


# ── _posix_perm_str ───────────────────────────────────────────────────────────

def test_posix_perm_str_rwx():
    assert _posix_perm_str(
        t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE
    ) == 'rwx'


def test_posix_perm_str_read_only():
    assert _posix_perm_str(t.POSIXPerm.READ) == 'r--'


def test_posix_perm_str_none():
    assert _posix_perm_str(t.POSIXPerm(0)) == '---'


# ── _posix_qualifier ──────────────────────────────────────────────────────────

def test_posix_qualifier_special_tags_empty():
    for tag in (t.POSIXTag.USER_OBJ, t.POSIXTag.GROUP_OBJ,
                t.POSIXTag.MASK, t.POSIXTag.OTHER):
        ace = t.POSIXAce(tag, t.POSIXPerm.READ)
        assert _posix_qualifier(ace, numeric=True) == ''


def test_posix_qualifier_named_user_numeric():
    ace = t.POSIXAce(t.POSIXTag.USER, t.POSIXPerm.READ, id=1001)
    assert _posix_qualifier(ace, numeric=True) == '1001'


def test_posix_qualifier_named_group_numeric():
    ace = t.POSIXAce(t.POSIXTag.GROUP, t.POSIXPerm.READ, id=2002)
    assert _posix_qualifier(ace, numeric=True) == '2002'


# ── _format_nfs4_text ─────────────────────────────────────────────────────────

def test_format_nfs4_text_headers_present():
    out = _format_nfs4_text('/mnt/data/f', _NFS4_ACL, 0, 0, None,
                            numeric=True, quiet=False)
    assert '# file: /mnt/data/f' in out
    assert '# owner:' in out
    assert '# group:' in out


def test_format_nfs4_text_quiet_omits_headers():
    out = _format_nfs4_text('/x', _NFS4_ACL, 0, 0, None,
                            numeric=True, quiet=True)
    assert '# file:'  not in out
    assert '# owner:' not in out


def test_format_nfs4_text_fhandle_included():
    out = _format_nfs4_text('/x', _NFS4_ACL, 0, 0, 'deadbeef',
                            numeric=True, quiet=False)
    assert '# fhandle: deadbeef' in out


def test_format_nfs4_text_fhandle_absent_when_none():
    out = _format_nfs4_text('/x', _NFS4_ACL, 0, 0, None,
                            numeric=True, quiet=False)
    assert '# fhandle:' not in out


def test_format_nfs4_text_ace_lines():
    out = _format_nfs4_text('/x', _NFS4_ACL, 0, 0, None,
                            numeric=True, quiet=True)
    lines = out.splitlines()
    assert len(lines) == 2
    # C extension may reorder ACEs (e.g. DENY before ALLOW); check presence
    assert any(l.startswith('owner@:')    and l.endswith(':allow') for l in lines)
    assert any(l.startswith('everyone@:') and l.endswith(':deny')  for l in lines)


# ── _format_posix_text ────────────────────────────────────────────────────────

def test_format_posix_text_access_ace_count():
    out = _format_posix_text('/x', _POSIX_ACL, 0, 0, None,
                             numeric=True, quiet=True)
    access = [l for l in out.splitlines() if not l.startswith('default:')]
    assert len(access) == 3


def test_format_posix_text_default_ace_count():
    out = _format_posix_text('/x', _POSIX_ACL, 0, 0, None,
                             numeric=True, quiet=True)
    defaults = [l for l in out.splitlines() if l.startswith('default:')]
    assert len(defaults) == 2


def test_format_posix_text_perm_strings():
    out = _format_posix_text('/x', _POSIX_ACL, 0, 0, None,
                             numeric=True, quiet=True)
    assert 'user::rwx'  in out
    assert 'other::---' in out


# ── _nfs4_ace_to_dict ─────────────────────────────────────────────────────────

def test_nfs4_ace_to_dict_has_required_keys():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    assert set(_nfs4_ace_to_dict(ace, numeric=True)) == {
        'who', 'perms', 'flags', 'type'
    }


def test_nfs4_ace_to_dict_owner_allow():
    ace = t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                    t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    d = _nfs4_ace_to_dict(ace, numeric=True)
    assert d['who']  == 'owner@'
    assert d['type'] == 'allow'
    assert 'READ_DATA' in d['perms']
    assert d['flags'] == []


def test_nfs4_ace_to_dict_deny_with_inherit_flags():
    ace = t.NFS4Ace(t.NFS4AceType.DENY,
                    t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT,
                    t.NFS4Perm.WRITE_DATA, t.NFS4Who.GROUP)
    d = _nfs4_ace_to_dict(ace, numeric=True)
    assert d['type'] == 'deny'
    assert 'FILE_INHERIT'      in d['flags']
    assert 'DIRECTORY_INHERIT' in d['flags']


# ── _posix_ace_to_dict ────────────────────────────────────────────────────────

def test_posix_ace_to_dict_has_required_keys():
    ace = t.POSIXAce(t.POSIXTag.USER_OBJ, t.POSIXPerm.READ)
    assert set(_posix_ace_to_dict(ace, numeric=True)) == {
        'tag', 'qualifier', 'perms', 'default'
    }


def test_posix_ace_to_dict_user_obj():
    ace = t.POSIXAce(t.POSIXTag.USER_OBJ,
                     t.POSIXPerm.READ | t.POSIXPerm.WRITE)
    d = _posix_ace_to_dict(ace, numeric=True)
    assert d['tag']       == 'USER_OBJ'
    assert d['qualifier'] is None
    assert d['default']   is False
    assert 'READ'  in d['perms']
    assert 'WRITE' in d['perms']


def test_posix_ace_to_dict_named_user_qualifier():
    ace = t.POSIXAce(t.POSIXTag.USER, t.POSIXPerm.READ, id=1001)
    assert _posix_ace_to_dict(ace, numeric=True)['qualifier'] == '1001'


def test_posix_ace_to_dict_default_flag():
    ace = t.POSIXAce(t.POSIXTag.OTHER, t.POSIXPerm(0), default=True)
    assert _posix_ace_to_dict(ace, numeric=True)['default'] is True


# ── _format_nfs4_json ─────────────────────────────────────────────────────────

def test_format_nfs4_json_required_keys():
    d = _format_nfs4_json('/x', _NFS4_ACL, 0, 0, None, numeric=True)
    assert {'path', 'uid', 'gid', 'owner', 'group',
            'acl_type', 'acl_flags', 'trivial', 'aces'} <= set(d)


def test_format_nfs4_json_acl_type():
    assert _format_nfs4_json('/x', _NFS4_ACL, 0, 0, None,
                             numeric=True)['acl_type'] == 'NFS4'


def test_format_nfs4_json_ace_count():
    assert len(_format_nfs4_json('/x', _NFS4_ACL, 0, 0, None,
                                 numeric=True)['aces']) == 2


def test_format_nfs4_json_fhandle_present_when_given():
    d = _format_nfs4_json('/x', _NFS4_ACL, 0, 0, 'cafebabe', numeric=True)
    assert d['fhandle'] == 'cafebabe'


def test_format_nfs4_json_fhandle_absent_when_none():
    assert 'fhandle' not in _format_nfs4_json('/x', _NFS4_ACL, 0, 0, None,
                                              numeric=True)


# ── _format_posix_json ────────────────────────────────────────────────────────

def test_format_posix_json_required_keys():
    d = _format_posix_json('/x', _POSIX_ACL, 0, 0, None, numeric=True)
    assert {'path', 'uid', 'gid', 'owner', 'group',
            'acl_type', 'trivial', 'aces'} <= set(d)


def test_format_posix_json_acl_type():
    assert _format_posix_json('/x', _POSIX_ACL, 0, 0, None,
                              numeric=True)['acl_type'] == 'POSIX'


def test_format_posix_json_aces_include_defaults():
    d = _format_posix_json('/x', _POSIX_ACL, 0, 0, None, numeric=True)
    # _POSIX_ACL has 3 access + 2 default = 5 total
    assert len(d['aces']) == 5
    assert sum(1 for a in d['aces'] if a.get('default')) == 2
