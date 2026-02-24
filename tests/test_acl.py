# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Tests for ACL construction, encoding, and live fgetacl/fsetacl round-trips.

Encoding tests require no filesystem and always run.

POSIX live tests use the posix_dataset fixture (ZFS posixacl dataset or
/POSIXACL fallback).  If the filesystem does not support POSIX ACLs the
test fails — no silencing of EOPNOTSUPP.

NFS4 live tests use the nfs4_dataset fixture (ZFS nfsv4 dataset or
/NFS4ACL fallback).  Tests fail if the path does not have nfsv4 acltype.
"""

import os
import struct

import pytest
import truenas_os as t


# ── binary layout helpers ────────────────────────────────────────────────────

_NFS4_HDR  = struct.Struct('>II')       # acl_flags, naces
_NFS4_ACE  = struct.Struct('>IIIII')    # type, flags, iflag, access_mask, who

_POSIX_HDR = struct.Struct('<I')        # version
_POSIX_ACE = struct.Struct('<HHI')      # tag, perm, id
POSIX_SPECIAL_ID = 0xFFFFFFFF


def _unpack_nfs4_acl(data):
    acl_flags, naces = _NFS4_HDR.unpack_from(data, 0)
    aces = []
    for i in range(naces):
        aces.append(_NFS4_ACE.unpack_from(data, _NFS4_HDR.size + i * _NFS4_ACE.size))
    return acl_flags, aces


def _unpack_posix_acl(data):
    version, = _POSIX_HDR.unpack_from(data, 0)
    n = (len(data) - _POSIX_HDR.size) // _POSIX_ACE.size
    entries = [_POSIX_ACE.unpack_from(data, _POSIX_HDR.size + i * _POSIX_ACE.size)
               for i in range(n)]
    return version, entries



def _open_file(directory, name='testfile'):
    return os.open(os.path.join(directory, name),
                   os.O_RDWR | os.O_CREAT, 0o644)


def _open_dir(directory, name='testdir'):
    path = os.path.join(directory, name)
    os.makedirs(path, exist_ok=True)
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY)


# ── shared ACL fixtures ──────────────────────────────────────────────────────

_MINIMAL_POSIX_ACES = [
    t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
               perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE),
    t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
               perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
    t.POSIXAce(tag=t.POSIXTag.OTHER,
               perms=t.POSIXPerm(0)),
]

_EXTENDED_POSIX_ACES = [
    t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
               perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE),
    t.POSIXAce(tag=t.POSIXTag.USER,
               perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, id=1001),
    t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
               perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
    t.POSIXAce(tag=t.POSIXTag.MASK,
               perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
    t.POSIXAce(tag=t.POSIXTag.OTHER,
               perms=t.POSIXPerm(0)),
]

_NFS4_FULL = (
    t.NFS4Perm.READ_DATA        | t.NFS4Perm.WRITE_DATA       |
    t.NFS4Perm.APPEND_DATA      | t.NFS4Perm.READ_NAMED_ATTRS |
    t.NFS4Perm.WRITE_NAMED_ATTRS| t.NFS4Perm.EXECUTE          |
    t.NFS4Perm.DELETE_CHILD     | t.NFS4Perm.READ_ATTRIBUTES  |
    t.NFS4Perm.WRITE_ATTRIBUTES | t.NFS4Perm.DELETE           |
    t.NFS4Perm.READ_ACL         | t.NFS4Perm.WRITE_ACL        |
    t.NFS4Perm.WRITE_OWNER      | t.NFS4Perm.SYNCHRONIZE
)
_NFS4_READ_EXEC = (
    t.NFS4Perm.READ_DATA        | t.NFS4Perm.READ_NAMED_ATTRS |
    t.NFS4Perm.EXECUTE          | t.NFS4Perm.READ_ATTRIBUTES  |
    t.NFS4Perm.READ_ACL         | t.NFS4Perm.SYNCHRONIZE
)

_BASE_NFS4_ACES = [
    t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),                      _NFS4_FULL,      t.NFS4Who.OWNER),
    t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.IDENTIFIER_GROUP,        _NFS4_READ_EXEC, t.NFS4Who.GROUP),
    t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),                      _NFS4_READ_EXEC, t.NFS4Who.EVERYONE),
]


# ═══════════════════════════════════════════════════════════════════════════
# Module symbols
# ═══════════════════════════════════════════════════════════════════════════

def test_functions_present():
    for name in ('fgetacl', 'fsetacl', 'fsetacl_nfs4', 'fsetacl_posix'):
        assert callable(getattr(t, name)), f'missing function: {name}'


def test_nfs4_enums_present():
    for name in ('NFS4AceType', 'NFS4Who', 'NFS4Perm', 'NFS4Flag', 'NFS4ACLFlag'):
        assert hasattr(t, name), f'missing enum: {name}'


def test_posix_enums_present():
    for name in ('POSIXTag', 'POSIXPerm'):
        assert hasattr(t, name), f'missing enum: {name}'


def test_types_present():
    for name in ('NFS4Ace', 'NFS4ACL', 'POSIXAce', 'POSIXACL'):
        assert hasattr(t, name), f'missing type: {name}'


# ═══════════════════════════════════════════════════════════════════════════
# NFS4 enum values
# ═══════════════════════════════════════════════════════════════════════════

def test_nfs4_ace_type_values():
    assert t.NFS4AceType.ALLOW == 0
    assert t.NFS4AceType.DENY  == 1
    assert t.NFS4AceType.AUDIT == 2
    assert t.NFS4AceType.ALARM == 3


def test_nfs4_who_values():
    assert t.NFS4Who.NAMED    == 0
    assert t.NFS4Who.OWNER    == 1
    assert t.NFS4Who.GROUP    == 2
    assert t.NFS4Who.EVERYONE == 3


def test_nfs4_perm_values():
    assert t.NFS4Perm.READ_DATA         == 0x00000001
    assert t.NFS4Perm.WRITE_DATA        == 0x00000002
    assert t.NFS4Perm.APPEND_DATA       == 0x00000004
    assert t.NFS4Perm.READ_NAMED_ATTRS  == 0x00000008
    assert t.NFS4Perm.WRITE_NAMED_ATTRS == 0x00000010
    assert t.NFS4Perm.EXECUTE           == 0x00000020
    assert t.NFS4Perm.DELETE_CHILD      == 0x00000040
    assert t.NFS4Perm.READ_ATTRIBUTES   == 0x00000080
    assert t.NFS4Perm.WRITE_ATTRIBUTES  == 0x00000100
    assert t.NFS4Perm.DELETE            == 0x00010000
    assert t.NFS4Perm.READ_ACL          == 0x00020000
    assert t.NFS4Perm.WRITE_ACL         == 0x00040000
    assert t.NFS4Perm.WRITE_OWNER       == 0x00080000
    assert t.NFS4Perm.SYNCHRONIZE       == 0x00100000


def test_nfs4_flag_values():
    assert t.NFS4Flag.FILE_INHERIT         == 0x00000001
    assert t.NFS4Flag.DIRECTORY_INHERIT    == 0x00000002
    assert t.NFS4Flag.NO_PROPAGATE_INHERIT == 0x00000004
    assert t.NFS4Flag.INHERIT_ONLY         == 0x00000008
    assert t.NFS4Flag.SUCCESSFUL_ACCESS    == 0x00000010
    assert t.NFS4Flag.FAILED_ACCESS        == 0x00000020
    assert t.NFS4Flag.IDENTIFIER_GROUP     == 0x00000040
    assert t.NFS4Flag.INHERITED            == 0x00000080


def test_nfs4_acl_flag_values():
    assert t.NFS4ACLFlag.AUTO_INHERIT  == 0x0001
    assert t.NFS4ACLFlag.PROTECTED     == 0x0002
    assert t.NFS4ACLFlag.DEFAULTED     == 0x0004
    assert t.NFS4ACLFlag.ACL_IS_TRIVIAL == 0x10000
    assert t.NFS4ACLFlag.ACL_IS_DIR    == 0x20000


def test_nfs4_perm_combination():
    combo = t.NFS4Perm.READ_DATA | t.NFS4Perm.EXECUTE
    assert int(combo) == 0x00000021
    assert t.NFS4Perm.READ_DATA  in combo
    assert t.NFS4Perm.EXECUTE    in combo
    assert t.NFS4Perm.WRITE_DATA not in combo


def test_nfs4_acl_flag_combination():
    assert int(t.NFS4ACLFlag.PROTECTED | t.NFS4ACLFlag.AUTO_INHERIT) == 0x0003


# ═══════════════════════════════════════════════════════════════════════════
# POSIX enum values
# ═══════════════════════════════════════════════════════════════════════════

def test_posix_tag_values():
    assert t.POSIXTag.USER_OBJ  == 0x0001
    assert t.POSIXTag.USER      == 0x0002
    assert t.POSIXTag.GROUP_OBJ == 0x0004
    assert t.POSIXTag.GROUP     == 0x0008
    assert t.POSIXTag.MASK      == 0x0010
    assert t.POSIXTag.OTHER     == 0x0020


def test_posix_perm_values():
    assert t.POSIXPerm.EXECUTE == 0x01
    assert t.POSIXPerm.WRITE   == 0x02
    assert t.POSIXPerm.READ    == 0x04


def test_posix_perm_combination():
    rwx = t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE
    assert int(rwx) == 0x07
    assert t.POSIXPerm.READ    in rwx
    assert t.POSIXPerm.WRITE   in rwx
    assert t.POSIXPerm.EXECUTE in rwx


# ═══════════════════════════════════════════════════════════════════════════
# NFS4Ace construction
# ═══════════════════════════════════════════════════════════════════════════

def test_nfs4ace_special_owner():
    ace = t.NFS4Ace(ace_type=t.NFS4AceType.ALLOW, ace_flags=t.NFS4Flag(0),
                    access_mask=t.NFS4Perm.READ_DATA, who_type=t.NFS4Who.OWNER)
    assert ace.ace_type    == t.NFS4AceType.ALLOW
    assert ace.ace_flags   == t.NFS4Flag(0)
    assert ace.access_mask == t.NFS4Perm.READ_DATA
    assert ace.who_type    == t.NFS4Who.OWNER
    assert ace.who_id      == -1


def test_nfs4ace_named_user():
    ace = t.NFS4Ace(ace_type=t.NFS4AceType.DENY, ace_flags=t.NFS4Flag.FILE_INHERIT,
                    access_mask=t.NFS4Perm.WRITE_DATA, who_type=t.NFS4Who.NAMED,
                    who_id=1001)
    assert ace.who_type == t.NFS4Who.NAMED
    assert ace.who_id   == 1001


def test_nfs4ace_repr():
    ace = t.NFS4Ace(ace_type=t.NFS4AceType.ALLOW, ace_flags=t.NFS4Flag(0),
                    access_mask=t.NFS4Perm.EXECUTE, who_type=t.NFS4Who.EVERYONE)
    r = repr(ace)
    assert 'NFS4Ace'  in r
    assert 'ALLOW'    in r
    assert 'EVERYONE' in r


def test_nfs4ace_all_who_types():
    for who in (t.NFS4Who.OWNER, t.NFS4Who.GROUP, t.NFS4Who.EVERYONE):
        ace = t.NFS4Ace(ace_type=t.NFS4AceType.ALLOW, ace_flags=t.NFS4Flag(0),
                        access_mask=t.NFS4Perm.READ_DATA, who_type=who)
        assert ace.who_type == who
        assert ace.who_id   == -1


def test_nfs4ace_all_ace_types():
    for atype in (t.NFS4AceType.ALLOW, t.NFS4AceType.DENY,
                  t.NFS4AceType.AUDIT, t.NFS4AceType.ALARM):
        ace = t.NFS4Ace(ace_type=atype, ace_flags=t.NFS4Flag(0),
                        access_mask=t.NFS4Perm.READ_DATA, who_type=t.NFS4Who.OWNER)
        assert ace.ace_type == atype


def test_nfs4ace_combined_flags():
    flags = t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT
    ace = t.NFS4Ace(ace_type=t.NFS4AceType.ALLOW, ace_flags=flags,
                    access_mask=t.NFS4Perm.READ_DATA, who_type=t.NFS4Who.OWNER)
    assert t.NFS4Flag.FILE_INHERIT      in ace.ace_flags
    assert t.NFS4Flag.DIRECTORY_INHERIT in ace.ace_flags


# ═══════════════════════════════════════════════════════════════════════════
# NFS4ACL construction and binary encoding
# ═══════════════════════════════════════════════════════════════════════════

def test_nfs4acl_construct_from_empty_bytes():
    acl = t.NFS4ACL(_NFS4_HDR.pack(0, 0))
    assert len(acl) == 0
    assert acl.aces == []
    assert acl.acl_flags == t.NFS4ACLFlag(0)


def test_nfs4acl_construct_from_bytes_single_ace():
    hdr = _NFS4_HDR.pack(0, 1)
    ace = _NFS4_ACE.pack(0, 0, 1, int(t.NFS4Perm.READ_DATA), 1)
    acl = t.NFS4ACL(hdr + ace)
    assert len(acl) == 1
    p = acl.aces[0]
    assert p.ace_type    == t.NFS4AceType.ALLOW
    assert p.ace_flags   == t.NFS4Flag(0)
    assert p.access_mask == t.NFS4Perm.READ_DATA
    assert p.who_type    == t.NFS4Who.OWNER
    assert p.who_id      == -1


def test_nfs4acl_construct_from_bytes_named_user():
    hdr = _NFS4_HDR.pack(0, 1)
    ace = _NFS4_ACE.pack(0, 0, 0, int(t.NFS4Perm.WRITE_DATA), 1001)
    acl = t.NFS4ACL(hdr + ace)
    assert acl.aces[0].who_type == t.NFS4Who.NAMED
    assert acl.aces[0].who_id   == 1001


def test_nfs4acl_from_aces_header():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ])
    raw_flags, raw_naces = _NFS4_HDR.unpack_from(bytes(acl), 0)
    assert raw_flags == 0
    assert raw_naces == 1


def test_nfs4acl_from_aces_acl_flags_in_header():
    flags = t.NFS4ACLFlag.PROTECTED | t.NFS4ACLFlag.AUTO_INHERIT
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ], acl_flags=flags)
    raw_flags, _ = _NFS4_HDR.unpack_from(bytes(acl), 0)
    assert raw_flags == int(flags)


def test_nfs4acl_from_aces_size():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ] * 3)
    assert len(bytes(acl)) == 8 + 3 * 20
    assert len(acl) == 3


def test_nfs4acl_special_owner_encoding():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ])
    _, aces = _unpack_nfs4_acl(bytes(acl))
    ace_type, ace_flags, iflag, access_mask, who = aces[0]
    assert iflag       == 1
    assert who         == 1
    assert ace_type    == 0
    assert access_mask == int(t.NFS4Perm.READ_DATA)


def test_nfs4acl_special_group_encoding():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.GROUP)
    ])
    _, aces = _unpack_nfs4_acl(bytes(acl))
    _, _, iflag, _, who = aces[0]
    assert iflag == 1
    assert who   == 2


def test_nfs4acl_special_everyone_encoding():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.EVERYONE)
    ])
    _, aces = _unpack_nfs4_acl(bytes(acl))
    _, _, iflag, _, who = aces[0]
    assert iflag == 1
    assert who   == 3


def test_nfs4acl_named_user_encoding():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, who_id=1234)
    ])
    _, aces = _unpack_nfs4_acl(bytes(acl))
    _, _, iflag, _, who = aces[0]
    assert iflag == 0
    assert who   == 1234


def test_nfs4acl_big_endian():
    """access_mask bytes must be big-endian."""
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.DELETE, t.NFS4Who.OWNER)
    ])
    # DELETE=0x00010000; access_mask is 4th u32 in ACE (offset 8+12=20)
    assert bytes(acl)[8 + 12 : 8 + 16] == b'\x00\x01\x00\x00'


def test_nfs4acl_deny_type_encoded():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                  t.NFS4Perm.WRITE_DATA, t.NFS4Who.EVERYONE)
    ])
    _, aces = _unpack_nfs4_acl(bytes(acl))
    assert aces[0][0] == 1  # DENY


def test_nfs4acl_multiple_aces():
    # from_aces canonicalises: deny+noinherit < deny+inherit < allow+noinherit < allow+inherit
    aces_in = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.FILE_INHERIT,
                  t.NFS4Perm.READ_DATA | t.NFS4Perm.EXECUTE, t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, who_id=500),
        t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                  t.NFS4Perm.WRITE_DATA, t.NFS4Who.EVERYONE),
    ]
    acl = t.NFS4ACL.from_aces(aces_in)
    assert len(acl) == 3
    # MS canonical order: explicit+deny < explicit+allow < inherited+deny < inherited+allow.
    # FILE_INHERIT (0x01) is a propagation flag, not the INHERITED (0x80) flag, so
    # ALLOW+FILE_INHERIT is still explicit+allow — same bucket as plain ALLOW.
    # Stable sort preserves original input order within the same bucket.
    assert acl.aces[0].ace_type == t.NFS4AceType.DENY
    assert acl.aces[0].who_type == t.NFS4Who.EVERYONE
    assert acl.aces[1].ace_type == t.NFS4AceType.ALLOW
    assert acl.aces[1].who_type == t.NFS4Who.OWNER
    assert acl.aces[2].ace_type == t.NFS4AceType.ALLOW
    assert acl.aces[2].who_type == t.NFS4Who.NAMED
    assert acl.aces[2].who_id   == 500


def test_nfs4acl_from_aces_empty():
    acl = t.NFS4ACL.from_aces([])
    assert len(acl) == 0
    assert acl.aces == []


def test_nfs4acl_all_nfs4_flags_encoded():
    all_flags = (t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT |
                 t.NFS4Flag.INHERIT_ONLY | t.NFS4Flag.INHERITED)
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, all_flags,
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ])
    _, aces = _unpack_nfs4_acl(bytes(acl))
    assert aces[0][1] == int(all_flags)


def test_nfs4acl_round_trip_bytes_unchanged():
    aces_in = [
        t.NFS4Ace(t.NFS4AceType.ALLOW,
                  t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT,
                  t.NFS4Perm.READ_DATA | t.NFS4Perm.EXECUTE | t.NFS4Perm.READ_ATTRIBUTES,
                  t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                  t.NFS4Perm.WRITE_DATA, t.NFS4Who.NAMED, who_id=1001),
    ]
    acl1 = t.NFS4ACL.from_aces(aces_in, acl_flags=t.NFS4ACLFlag.PROTECTED)
    acl2 = t.NFS4ACL.from_aces(acl1.aces, acl_flags=acl1.acl_flags)
    assert bytes(acl1) == bytes(acl2)


def test_nfs4acl_round_trip_acl_flags():
    flags = t.NFS4ACLFlag.AUTO_INHERIT | t.NFS4ACLFlag.DEFAULTED
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ], acl_flags=flags)
    assert acl.acl_flags == flags


def test_nfs4acl_round_trip_all_perm_bits():
    all_perms = (
        t.NFS4Perm.READ_DATA | t.NFS4Perm.WRITE_DATA | t.NFS4Perm.APPEND_DATA |
        t.NFS4Perm.EXECUTE   | t.NFS4Perm.DELETE      | t.NFS4Perm.READ_ACL   |
        t.NFS4Perm.WRITE_ACL | t.NFS4Perm.WRITE_OWNER | t.NFS4Perm.SYNCHRONIZE
    )
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0), all_perms, t.NFS4Who.OWNER)
    ])
    assert acl.aces[0].access_mask == all_perms


def test_nfs4acl_len_and_bytes_consistency():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ] * 5)
    assert len(acl) == 5
    assert len(bytes(acl)) == 8 + 5 * 20


def test_nfs4acl_repr():
    acl = t.NFS4ACL.from_aces([
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.OWNER)
    ])
    assert 'NFS4ACL' in repr(acl)
    assert 'aces'    in repr(acl)


def test_nfs4acl_wrong_type_raises():
    with pytest.raises(TypeError):
        t.NFS4ACL.from_aces(['not an ace'])


def test_nfs4acl_bytes_constructor_requires_bytes():
    with pytest.raises(TypeError):
        t.NFS4ACL("not bytes")


def test_nfs4acl_trivial_flag_set():
    flags_val = int(t.NFS4ACLFlag.ACL_IS_TRIVIAL)
    data = _NFS4_HDR.pack(flags_val, 0)
    assert t.NFS4ACL(data).trivial is True


def test_nfs4acl_trivial_flag_clear():
    data = _NFS4_HDR.pack(0, 0)
    assert t.NFS4ACL(data).trivial is False


def test_nfs4acl_trivial_empty_bytes():
    # Zero-length data (ENODATA sentinel) is considered trivial.
    assert t.NFS4ACL(b"").trivial is True


# ═══════════════════════════════════════════════════════════════════════════
# POSIXAce construction
# ═══════════════════════════════════════════════════════════════════════════

def test_posixace_user_obj():
    ace = t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                     perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE)
    assert ace.tag     == t.POSIXTag.USER_OBJ
    assert ace.perms   == t.POSIXPerm.READ | t.POSIXPerm.WRITE
    assert ace.id      == -1
    assert ace.default == False


def test_posixace_named_user():
    ace = t.POSIXAce(tag=t.POSIXTag.USER, perms=t.POSIXPerm.READ, id=1001)
    assert ace.tag == t.POSIXTag.USER
    assert ace.id  == 1001


def test_posixace_default_flag():
    ace = t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True)
    assert ace.default == True


def test_posixace_repr():
    ace = t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ, perms=t.POSIXPerm.EXECUTE)
    assert 'POSIXAce'  in repr(ace)
    assert 'GROUP_OBJ' in repr(ace)


# ═══════════════════════════════════════════════════════════════════════════
# POSIXACL construction and binary encoding
# ═══════════════════════════════════════════════════════════════════════════

def test_posixacl_construct_from_bytes():
    buf  = _POSIX_HDR.pack(2)
    buf += _POSIX_ACE.pack(int(t.POSIXTag.USER_OBJ), int(t.POSIXPerm.READ),
                           POSIX_SPECIAL_ID)
    acl = t.POSIXACL(buf)
    assert acl.aces[0].tag   == t.POSIXTag.USER_OBJ
    assert acl.aces[0].perms == t.POSIXPerm.READ
    assert acl.aces[0].id    == -1


def test_posixacl_no_default_gives_empty():
    acl = t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES)
    assert acl.default_aces  == []
    assert acl.default_bytes() is None


def test_posixacl_from_aces_header_version():
    acl = t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES)
    version, _ = _unpack_posix_acl(acl.access_bytes())
    assert version == 2


def test_posixacl_from_aces_entry_count():
    acl = t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES)
    _, entries = _unpack_posix_acl(acl.access_bytes())
    assert len(entries) == 3


def test_posixacl_user_obj_encodes_special_id():
    acl = t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES)
    _, entries = _unpack_posix_acl(acl.access_bytes())
    user_obj = next(e for e in entries if e[0] == int(t.POSIXTag.USER_OBJ))
    assert user_obj[2] == POSIX_SPECIAL_ID


def test_posixacl_named_user_id_preserved():
    acl = t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES)
    _, entries = _unpack_posix_acl(acl.access_bytes())
    user = next(e for e in entries if e[0] == int(t.POSIXTag.USER))
    assert user[2] == 1001


def test_posixacl_mask_encodes_special_id():
    acl = t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES)
    _, entries = _unpack_posix_acl(acl.access_bytes())
    mask = next(e for e in entries if e[0] == int(t.POSIXTag.MASK))
    assert mask[2] == POSIX_SPECIAL_ID


def test_posixacl_little_endian():
    """Perm field must be little-endian."""
    acl = t.POSIXACL.from_aces([
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ, perms=t.POSIXPerm.READ)
    ])
    # perm is bytes 2-3 of the first entry (offset 4 + 2)
    assert acl.access_bytes()[4 + 2 : 4 + 4] == b'\x04\x00'


def test_posixacl_default_aces_separated():
    aces = list(_MINIMAL_POSIX_ACES) + [
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True),
    ]
    acl = t.POSIXACL.from_aces(aces)
    _, access  = _unpack_posix_acl(acl.access_bytes())
    _, default = _unpack_posix_acl(acl.default_bytes())
    assert len(access)  == 3
    assert len(default) == 2


def test_posixacl_round_trip_access_bytes():
    acl1 = t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES)
    acl2 = t.POSIXACL.from_aces(acl1.aces)
    assert acl1.access_bytes() == acl2.access_bytes()


def test_posixacl_round_trip_with_default():
    aces = list(_MINIMAL_POSIX_ACES) + [
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True),
    ]
    acl1 = t.POSIXACL.from_aces(aces)
    acl2 = t.POSIXACL.from_aces(acl1.aces + acl1.default_aces)
    assert acl1.access_bytes()  == acl2.access_bytes()
    assert acl1.default_bytes() == acl2.default_bytes()


def test_posixacl_round_trip_tag_and_perm():
    acl = t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES)
    for orig, parsed in zip(_EXTENDED_POSIX_ACES, acl.aces):
        assert parsed.tag   == orig.tag
        assert parsed.perms == orig.perms


def test_posixacl_repr():
    acl = t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES)
    assert 'POSIXACL' in repr(acl)
    assert 'aces'     in repr(acl)


def test_posixacl_wrong_type_raises():
    with pytest.raises(TypeError):
        t.POSIXACL.from_aces(['not an ace'])


def test_posixacl_bytes_constructor_requires_bytes():
    with pytest.raises(TypeError):
        t.POSIXACL("not bytes")


def test_posixacl_default_data_must_be_bytes_or_none():
    with pytest.raises(TypeError):
        t.POSIXACL(_POSIX_HDR.pack(2), "not bytes")


def test_posixacl_trivial_empty_access_no_default():
    # b"" + None is the ENODATA sentinel returned by fgetacl.
    assert t.POSIXACL(b"").trivial is True


def test_posixacl_trivial_nonempty_access_is_false():
    acl = t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES)
    assert acl.trivial is False


def test_posixacl_trivial_with_default_is_false():
    # Even empty access + non-None default is not trivial.
    assert t.POSIXACL(b"", _POSIX_HDR.pack(2)).trivial is False


# ═══════════════════════════════════════════════════════════════════════════
# Live POSIXACL — fgetacl / fsetacl
# (posix_dataset: ZFS posixacl dataset, or tmpdir fallback)
# ═══════════════════════════════════════════════════════════════════════════

def test_posix_fgetacl_returns_posixacl(posix_dataset):
    fd = _open_file(posix_dataset)
    try:
        assert isinstance(t.fgetacl(fd), t.POSIXACL)
    finally:
        os.close(fd)


def test_posix_fsetacl_fgetacl_round_trip_file(posix_dataset):
    # Use an extended ACL (has named USER + MASK) so the kernel stores an
    # actual xattr rather than folding the 3-entry minimal ACL into mode bits.
    acl_out = t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES)
    fd = _open_file(posix_dataset)
    try:
        t.fsetacl(fd, acl_out)
        acl_in  = t.fgetacl(fd)
        tags_in = {a.tag for a in acl_in.aces}
        assert t.POSIXTag.USER_OBJ  in tags_in
        assert t.POSIXTag.GROUP_OBJ in tags_in
        assert t.POSIXTag.OTHER     in tags_in

        uo_out = next(a for a in acl_out.aces if a.tag == t.POSIXTag.USER_OBJ)
        uo_in  = next(a for a in acl_in.aces  if a.tag == t.POSIXTag.USER_OBJ)
        assert uo_in.perms == uo_out.perms
    finally:
        os.close(fd)


def test_posix_fsetacl_named_user(posix_dataset):
    aces = list(_MINIMAL_POSIX_ACES) + [
        t.POSIXAce(tag=t.POSIXTag.USER,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, id=os.getuid()),
        t.POSIXAce(tag=t.POSIXTag.MASK,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
    ]
    fd = _open_file(posix_dataset, 'named_user')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(aces))
        named = [a for a in t.fgetacl(fd).aces if a.tag == t.POSIXTag.USER]
        assert len(named) == 1
        assert named[0].id    == os.getuid()
        assert named[0].perms == t.POSIXPerm.READ | t.POSIXPerm.EXECUTE
    finally:
        os.close(fd)


def test_posix_fsetacl_named_group(posix_dataset):
    aces = list(_MINIMAL_POSIX_ACES) + [
        t.POSIXAce(tag=t.POSIXTag.GROUP, perms=t.POSIXPerm.READ, id=os.getgid()),
        t.POSIXAce(tag=t.POSIXTag.MASK,  perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
    ]
    fd = _open_file(posix_dataset, 'named_group')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(aces))
        groups = [a for a in t.fgetacl(fd).aces if a.tag == t.POSIXTag.GROUP]
        assert len(groups) == 1
        assert groups[0].id    == os.getgid()
        assert groups[0].perms == t.POSIXPerm.READ
    finally:
        os.close(fd)


def test_posix_fsetacl_default_acl_on_directory(posix_dataset):
    default_aces = [
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE,
                   default=True),
        t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.MASK,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True),
    ]
    fd = _open_dir(posix_dataset)
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(list(_MINIMAL_POSIX_ACES) + default_aces))
        acl_in = t.fgetacl(fd)
        default_tags = {a.tag for a in acl_in.default_aces}
        assert t.POSIXTag.USER_OBJ  in default_tags
        assert t.POSIXTag.GROUP_OBJ in default_tags
        assert t.POSIXTag.OTHER     in default_tags
    finally:
        os.close(fd)


def test_posix_fsetacl_removes_default_acl(posix_dataset):
    default_aces = [
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ, perms=t.POSIXPerm.READ, default=True),
        t.POSIXAce(tag=t.POSIXTag.MASK,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True),
    ]
    fd = _open_dir(posix_dataset, 'rmdefault')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(list(_MINIMAL_POSIX_ACES) + default_aces))
        assert t.fgetacl(fd).default_aces != []

        t.fsetacl(fd, t.POSIXACL.from_aces(_MINIMAL_POSIX_ACES))
        assert t.fgetacl(fd).default_aces == []
    finally:
        os.close(fd)


def test_posix_fsetacl_raw_bytes_interface(posix_dataset):
    # Extended ACL so the kernel stores an xattr (minimal ACL → mode bits only).
    acl_out = t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES)
    fd = _open_file(posix_dataset, 'raw_bytes')
    try:
        t.fsetacl_posix(fd, acl_out.access_bytes(), None)
        acl_in = t.fgetacl(fd)
        assert isinstance(acl_in, t.POSIXACL)
        assert t.POSIXTag.USER_OBJ in {a.tag for a in acl_in.aces}
    finally:
        os.close(fd)


def test_posix_fgetacl_bad_fd_raises(posix_dataset):
    with pytest.raises(OSError):
        t.fgetacl(-1)


def test_posix_fsetacl_wrong_type_raises(posix_dataset):
    fd = _open_file(posix_dataset, 'wrongtype')
    try:
        with pytest.raises(TypeError):
            t.fsetacl(fd, "not an acl")
    finally:
        os.close(fd)


def test_posix_fsetacl_none_removes_acl(posix_dataset):
    # Set an extended ACL, verify it's present, then remove and verify it's gone.
    fd = _open_file(posix_dataset, 'remove_acl')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES))
        assert isinstance(t.fgetacl(fd), t.POSIXACL)
        t.fsetacl(fd, None)
        # After removal the kernel returns a synthetic mode-based ACL with
        # just the three base entries (USER_OBJ, GROUP_OBJ, OTHER) — no
        # extended named USER or MASK entry.
        acl_after = t.fgetacl(fd)
        assert isinstance(acl_after, t.POSIXACL)
        tags = {a.tag for a in acl_after.aces}
        assert t.POSIXTag.USER not in tags
        assert t.POSIXTag.MASK not in tags
    finally:
        os.close(fd)


def test_posix_fsetacl_none_idempotent(posix_dataset):
    # Calling fsetacl(fd, None) when no xattr exists must not raise.
    fd = _open_file(posix_dataset, 'remove_acl_empty')
    try:
        t.fsetacl(fd, None)  # must not raise
    finally:
        os.close(fd)


def test_posix_trivial_false_after_set(posix_dataset):
    fd = _open_file(posix_dataset, 'trivial_set')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES))
        assert t.fgetacl(fd).trivial is False
    finally:
        os.close(fd)


def test_posix_trivial_true_after_removal(posix_dataset):
    fd = _open_file(posix_dataset, 'trivial_remove')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(_EXTENDED_POSIX_ACES))
        assert t.fgetacl(fd).trivial is False
        t.fsetacl(fd, None)
        assert t.fgetacl(fd).trivial is True
    finally:
        os.close(fd)


def test_posix_inherited_acl_matches_kernel(posix_dataset):
    """generate_inherited_acl() must produce ACLs matching what the kernel
    sets on new children created inside a directory with a default ACL.

    An extended default ACL (with a named USER and MASK entry) is used so
    the kernel always stores an explicit access ACL xattr on new children —
    a minimal default ACL would be absorbed into mode bits only.

    umask is forced to 0 and mode=0o777 so no permission bits are masked,
    making the inherited access ACL byte-for-byte equal to the default ACL.
    """
    # Extended default ACL: named USER entry forces a MASK entry and an
    # explicit xattr on every child created under the directory.
    default_aces = [
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE,
                   default=True),
        t.POSIXAce(tag=t.POSIXTag.USER,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE,
                   id=os.getuid(), default=True),
        t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.MASK,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE,
                   default=True),
        t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True),
    ]
    parent_path = os.path.join(posix_dataset, 'inh_posix')
    os.makedirs(parent_path, exist_ok=True)
    parent_fd = os.open(parent_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent_acl = t.POSIXACL.from_aces(list(_MINIMAL_POSIX_ACES) + default_aces)
        t.fsetacl(parent_fd, parent_acl)

        lib_file_acl = parent_acl.generate_inherited_acl(is_dir=False)
        lib_dir_acl  = parent_acl.generate_inherited_acl(is_dir=True)

        old_umask = os.umask(0)
        try:
            # ── file child ───────────────────────────────────────────────
            file_path = os.path.join(parent_path, 'child_file')
            fd = os.open(file_path, os.O_CREAT | os.O_RDWR, 0o777)
            try:
                kern = t.fgetacl(fd)
                assert kern.access_bytes() == lib_file_acl.access_bytes()
                assert kern.default_bytes() is None
                assert lib_file_acl.default_bytes() is None
            finally:
                os.close(fd)

            # ── directory child ──────────────────────────────────────────
            dir_path = os.path.join(parent_path, 'child_dir')
            os.mkdir(dir_path, 0o777)
            fd = os.open(dir_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                kern = t.fgetacl(fd)
                assert kern.access_bytes()  == lib_dir_acl.access_bytes()
                assert kern.default_bytes() == lib_dir_acl.default_bytes()
            finally:
                os.close(fd)
        finally:
            os.umask(old_umask)
    finally:
        os.close(parent_fd)


# ═══════════════════════════════════════════════════════════════════════════
# Live NFS4ACL — fgetacl / fsetacl
# (nfs4_dataset: ZFS nfsv4 dataset; skipped if ZFS unavailable)
# ═══════════════════════════════════════════════════════════════════════════

def test_nfs4_fgetacl_returns_nfs4acl(nfs4_dataset):
    fd = _open_file(nfs4_dataset)
    try:
        assert isinstance(t.fgetacl(fd), t.NFS4ACL)
    finally:
        os.close(fd)


def test_nfs4_fsetacl_fgetacl_round_trip(nfs4_dataset):
    """Every field of every ACE must survive fsetacl → fgetacl."""
    acl_out = t.NFS4ACL.from_aces(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset)
    try:
        t.fsetacl(fd, acl_out)
        acl_in = t.fgetacl(fd)
        assert isinstance(acl_in, t.NFS4ACL)
        assert len(acl_in) == len(acl_out)
        for out_ace, in_ace in zip(acl_out.aces, acl_in.aces):
            assert in_ace.ace_type    == out_ace.ace_type
            assert in_ace.ace_flags   == out_ace.ace_flags
            assert in_ace.access_mask == out_ace.access_mask
            assert in_ace.who_type    == out_ace.who_type
            assert in_ace.who_id      == out_ace.who_id
    finally:
        os.close(fd)


def test_nfs4_fsetacl_acl_flags_preserved(nfs4_dataset):
    flags = t.NFS4ACLFlag.PROTECTED | t.NFS4ACLFlag.AUTO_INHERIT
    fd = _open_file(nfs4_dataset, 'aclflags')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(_BASE_NFS4_ACES, acl_flags=flags))
        acl_in = t.fgetacl(fd)
        assert t.NFS4ACLFlag.PROTECTED    in acl_in.acl_flags
        assert t.NFS4ACLFlag.AUTO_INHERIT in acl_in.acl_flags
    finally:
        os.close(fd)


def test_nfs4_fsetacl_named_user_preserved(nfs4_dataset):
    uid  = os.getuid()
    mask = t.NFS4Perm.READ_DATA | t.NFS4Perm.EXECUTE
    aces = list(_BASE_NFS4_ACES) + [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0), mask,
                  t.NFS4Who.NAMED, who_id=uid),
    ]
    fd = _open_file(nfs4_dataset, 'named_user')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        named = [a for a in t.fgetacl(fd).aces
                 if a.who_type == t.NFS4Who.NAMED and a.who_id == uid]
        assert len(named) == 1
        assert t.NFS4Perm.READ_DATA in named[0].access_mask
        assert t.NFS4Perm.EXECUTE   in named[0].access_mask
    finally:
        os.close(fd)


def test_nfs4_fsetacl_named_group_preserved(nfs4_dataset):
    gid  = os.getgid()
    aces = list(_BASE_NFS4_ACES) + [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.IDENTIFIER_GROUP,
                  t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, who_id=gid),
    ]
    fd = _open_file(nfs4_dataset, 'named_group')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        named_grp = [
            a for a in t.fgetacl(fd).aces
            if a.who_type == t.NFS4Who.NAMED
            and t.NFS4Flag.IDENTIFIER_GROUP in a.ace_flags
            and a.who_id == gid
        ]
        assert len(named_grp) == 1
    finally:
        os.close(fd)


def test_nfs4_fsetacl_deny_entry_preserved(nfs4_dataset):
    # DENY on named users is valid; DENY on special principals (OWNER@/GROUP@/EVERYONE@)
    # is rejected by nfs4acl_valid().
    uid = 501
    deny_mask = t.NFS4Perm.WRITE_DATA | t.NFS4Perm.APPEND_DATA
    aces = [
        t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0), deny_mask,
                  t.NFS4Who.NAMED, who_id=uid),
    ] + list(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'deny')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        deny_aces = [a for a in t.fgetacl(fd).aces
                     if a.ace_type == t.NFS4AceType.DENY
                     and a.who_type == t.NFS4Who.NAMED
                     and a.who_id == uid]
        assert len(deny_aces) >= 1
        assert t.NFS4Perm.WRITE_DATA  in deny_aces[0].access_mask
        assert t.NFS4Perm.APPEND_DATA in deny_aces[0].access_mask
    finally:
        os.close(fd)


def test_nfs4_fsetacl_inherit_flags_on_directory(nfs4_dataset):
    inherit = t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_FULL,      t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_READ_EXEC, t.NFS4Who.EVERYONE),
    ]
    fd = _open_dir(nfs4_dataset)
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        owner_ace = next(
            a for a in t.fgetacl(fd).aces
            if a.who_type == t.NFS4Who.OWNER and a.ace_type == t.NFS4AceType.ALLOW
        )
        assert t.NFS4Flag.FILE_INHERIT      in owner_ace.ace_flags
        assert t.NFS4Flag.DIRECTORY_INHERIT in owner_ace.ace_flags
    finally:
        os.close(fd)


def test_nfs4_fgetacl_on_directory_returns_nfs4acl(nfs4_dataset):
    fd = _open_dir(nfs4_dataset, 'dir_type_check')
    try:
        assert isinstance(t.fgetacl(fd), t.NFS4ACL)
    finally:
        os.close(fd)


def test_nfs4_fsetacl_raw_bytes_interface(nfs4_dataset):
    acl_out = t.NFS4ACL.from_aces(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'raw_bytes')
    try:
        t.fsetacl_nfs4(fd, bytes(acl_out))
        acl_in = t.fgetacl(fd)
        assert isinstance(acl_in, t.NFS4ACL)
        assert len(acl_in) == len(acl_out)
    finally:
        os.close(fd)


def test_nfs4_fsetacl_multiple_round_trips_stable(nfs4_dataset):
    """Writing the same ACL twice must produce identical bytes on read-back."""
    acl_out = t.NFS4ACL.from_aces(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'stable')
    try:
        t.fsetacl(fd, acl_out)
        first  = bytes(t.fgetacl(fd))
        t.fsetacl(fd, acl_out)
        second = bytes(t.fgetacl(fd))
        assert first == second
    finally:
        os.close(fd)


def test_nfs4_fgetacl_bad_fd_raises(nfs4_dataset):
    with pytest.raises(OSError):
        t.fgetacl(-1)


def test_nfs4_fsetacl_wrong_type_raises(nfs4_dataset):
    fd = _open_file(nfs4_dataset, 'wrongtype')
    try:
        with pytest.raises(TypeError):
            t.fsetacl(fd, "not an acl")
    finally:
        os.close(fd)


def test_nfs4_fsetacl_none_removes_acl(nfs4_dataset):
    # Set a non-trivial ACL, verify it's present, remove it, then confirm the
    # filesystem synthesises a trivial (mode-bit equivalent) ACL indicated by
    # NFS4ACLFlag.ACL_IS_TRIVIAL in the returned acl_flags.
    inherit = t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_FULL,      t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_READ_EXEC, t.NFS4Who.EVERYONE),
    ]
    fd = _open_dir(nfs4_dataset, 'remove_acl')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        assert t.NFS4ACLFlag.ACL_IS_TRIVIAL not in t.fgetacl(fd).acl_flags
        t.fsetacl(fd, None)
        assert t.NFS4ACLFlag.ACL_IS_TRIVIAL in t.fgetacl(fd).acl_flags
    finally:
        os.close(fd)


def test_nfs4_fsetacl_none_idempotent(nfs4_dataset):
    # Calling fsetacl(fd, None) when no xattr is set must not raise.
    fd = _open_file(nfs4_dataset, 'remove_acl_empty')
    try:
        t.fsetacl(fd, None)  # must not raise
    finally:
        os.close(fd)


def test_nfs4_trivial_false_after_set(nfs4_dataset):
    inherit = t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_FULL,      t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_READ_EXEC, t.NFS4Who.EVERYONE),
    ]
    fd = _open_dir(nfs4_dataset, 'trivial_set')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        assert t.fgetacl(fd).trivial is False
    finally:
        os.close(fd)


def test_nfs4_trivial_true_after_removal(nfs4_dataset):
    inherit = t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_FULL,      t.NFS4Who.OWNER),
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_READ_EXEC, t.NFS4Who.EVERYONE),
    ]
    fd = _open_dir(nfs4_dataset, 'trivial_remove')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        assert t.fgetacl(fd).trivial is False
        t.fsetacl(fd, None)
        assert t.fgetacl(fd).trivial is True
    finally:
        os.close(fd)


def test_nfs4_inherited_acl_matches_kernel(nfs4_dataset):
    """generate_inherited_acl() ACE structure must match what ZFS sets on
    new children.

    A named-user ACE is included so the inherited ACL is guaranteed
    non-trivial (ZFS normalises ACLs expressible as mode bits into the
    3-ACE trivial form, which would break an OWNER@/EVERYONE@-only test).

    ZFS may adjust access_mask values based on the file-creation mode, so
    only ace_type, ace_flags, who_type, and who_id are compared — not
    access_mask.  The critical checks are:
      • ACE count matches our prediction.
      • Every kernel ACE has INHERITED set.
      • File-child ACEs have no propagation flags.
      • Directory-child ACEs retain FILE_INHERIT / DIRECTORY_INHERIT and
        have INHERIT_ONLY cleared.
    """
    FILE_INH = t.NFS4Flag.FILE_INHERIT
    DIR_INH  = t.NFS4Flag.DIRECTORY_INHERIT
    inherit  = FILE_INH | DIR_INH
    INHERITED = t.NFS4Flag.INHERITED
    PROP_FLAGS = FILE_INH | DIR_INH | t.NFS4Flag.NO_PROPAGATE_INHERIT | t.NFS4Flag.INHERIT_ONLY

    uid = os.getuid()
    parent_aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_FULL,      t.NFS4Who.OWNER),
        # Named-user ACE: can never be expressed as mode bits → non-trivial
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_READ_EXEC, t.NFS4Who.NAMED, who_id=uid),
        t.NFS4Ace(t.NFS4AceType.ALLOW, inherit, _NFS4_READ_EXEC, t.NFS4Who.EVERYONE),
    ]
    parent_path = os.path.join(nfs4_dataset, 'inh_nfs4')
    os.makedirs(parent_path, exist_ok=True)
    parent_fd = os.open(parent_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent_acl = t.NFS4ACL.from_aces(parent_aces)
        t.fsetacl(parent_fd, parent_acl)

        lib_file_acl = parent_acl.generate_inherited_acl(is_dir=False)
        lib_dir_acl  = parent_acl.generate_inherited_acl(is_dir=True)

        def _structural(ace):
            return (ace.ace_type, ace.ace_flags, ace.who_type, ace.who_id)

        # ── file child ───────────────────────────────────────────────────
        file_path = os.path.join(parent_path, 'child_file')
        fd = os.open(file_path, os.O_CREAT | os.O_RDWR, 0o644)
        os.close(fd)
        fd = os.open(file_path, os.O_RDONLY)
        try:
            kern = t.fgetacl(fd)
            assert not kern.trivial, "file child inherited ACL must not be trivial"
            assert len(kern) == len(lib_file_acl), \
                f"file ACE count: kernel={len(kern)}, lib={len(lib_file_acl)}"
            for k, l in zip(kern.aces, lib_file_acl.aces):
                assert INHERITED     in k.ace_flags, "INHERITED must be set"
                assert PROP_FLAGS & k.ace_flags == t.NFS4Flag(0), \
                    "no propagation flags on file ACE"
                assert _structural(k) == _structural(l)
        finally:
            os.close(fd)

        # ── directory child ──────────────────────────────────────────────
        dir_path = os.path.join(parent_path, 'child_dir')
        os.makedirs(dir_path, exist_ok=True)
        fd = os.open(dir_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            kern = t.fgetacl(fd)
            assert not kern.trivial, "dir child inherited ACL must not be trivial"
            assert len(kern) == len(lib_dir_acl), \
                f"dir ACE count: kernel={len(kern)}, lib={len(lib_dir_acl)}"
            for k, l in zip(kern.aces, lib_dir_acl.aces):
                assert INHERITED in k.ace_flags, "INHERITED must be set"
                assert FILE_INH  in k.ace_flags, "FILE_INHERIT must propagate"
                assert DIR_INH   in k.ace_flags, "DIRECTORY_INHERIT must propagate"
                assert t.NFS4Flag.INHERIT_ONLY not in k.ace_flags, \
                    "INHERIT_ONLY must be cleared on dir child"
                assert _structural(k) == _structural(l)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


# ═══════════════════════════════════════════════════════════════════════════
# NFS4ACL validation (nfs4acl_valid)
# ═══════════════════════════════════════════════════════════════════════════

def test_nfs4_valid_deny_owner_rejected(nfs4_dataset):
    aces = [t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                      t.NFS4Perm.WRITE_DATA, t.NFS4Who.OWNER)]
    fd = _open_file(nfs4_dataset, 'val_deny_owner')
    try:
        with pytest.raises(ValueError, match='DENY'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_deny_group_rejected(nfs4_dataset):
    aces = [t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                      t.NFS4Perm.WRITE_DATA, t.NFS4Who.GROUP)]
    fd = _open_file(nfs4_dataset, 'val_deny_group')
    try:
        with pytest.raises(ValueError, match='DENY'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_deny_everyone_rejected(nfs4_dataset):
    aces = [t.NFS4Ace(t.NFS4AceType.DENY, t.NFS4Flag(0),
                      t.NFS4Perm.WRITE_DATA, t.NFS4Who.EVERYONE)]
    fd = _open_file(nfs4_dataset, 'val_deny_everyone')
    try:
        with pytest.raises(ValueError, match='DENY'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_inherit_only_without_propagation_rejected(nfs4_dataset):
    # INHERIT_ONLY without FILE_INHERIT or DIRECTORY_INHERIT is invalid.
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.INHERIT_ONLY,
                  t.NFS4Perm.READ_DATA, t.NFS4Who.EVERYONE),
    ] + list(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'val_inherit_only')
    try:
        with pytest.raises(ValueError, match='INHERIT_ONLY'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_inherit_only_with_file_inherit_accepted(nfs4_dataset):
    # INHERIT_ONLY is valid when paired with FILE_INHERIT (on a directory).
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW,
                  t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.INHERIT_ONLY,
                  t.NFS4Perm.READ_DATA, t.NFS4Who.EVERYONE),
        t.NFS4Ace(t.NFS4AceType.ALLOW,
                  t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT,
                  _NFS4_FULL, t.NFS4Who.OWNER),
    ]
    fd = _open_dir(nfs4_dataset, 'val_inherit_only_ok')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))  # must not raise
    finally:
        os.close(fd)


def test_nfs4_valid_file_inherit_on_file_rejected(nfs4_dataset):
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.FILE_INHERIT,
                  _NFS4_FULL, t.NFS4Who.OWNER),
    ] + list(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'val_file_inherit_on_file')
    try:
        with pytest.raises(ValueError, match='only valid on directories'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_dir_inherit_on_file_rejected(nfs4_dataset):
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.DIRECTORY_INHERIT,
                  _NFS4_FULL, t.NFS4Who.OWNER),
    ] + list(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'val_dir_inherit_on_file')
    try:
        with pytest.raises(ValueError, match='only valid on directories'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_no_propagate_inherit_on_file_rejected(nfs4_dataset):
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag.NO_PROPAGATE_INHERIT,
                  _NFS4_FULL, t.NFS4Who.OWNER),
    ] + list(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'val_nopropagate_on_file')
    try:
        with pytest.raises(ValueError, match='only valid on directories'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
    finally:
        os.close(fd)


def test_nfs4_valid_directory_without_inheritable_rejected(nfs4_dataset):
    # _BASE_NFS4_ACES has no FILE_INHERIT or DIRECTORY_INHERIT — invalid on a dir.
    fd = _open_dir(nfs4_dataset, 'val_dir_no_inherit')
    try:
        with pytest.raises(ValueError, match='directory ACL'):
            t.fsetacl(fd, t.NFS4ACL.from_aces(_BASE_NFS4_ACES))
    finally:
        os.close(fd)


# ═══════════════════════════════════════════════════════════════════════════
# POSIXACL validation (posixacl_valid)
# ═══════════════════════════════════════════════════════════════════════════

def _make_posix_blob(*entries):
    """Build a raw POSIX ACL blob from (tag, perm, xid) tuples."""
    buf = _POSIX_HDR.pack(2)
    for tag, perm, xid in entries:
        buf += _POSIX_ACE.pack(tag, perm, xid)
    return buf


def test_posix_valid_default_acl_on_file_rejected(posix_dataset):
    # A default ACL is only valid on a directory.
    aces = list(_EXTENDED_POSIX_ACES) + [
        t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                   perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE, default=True),
        t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
                   perms=t.POSIXPerm.READ, default=True),
        t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0), default=True),
    ]
    fd = _open_file(posix_dataset, 'val_default_on_file')
    try:
        with pytest.raises(ValueError, match='[Dd]efault'):
            t.fsetacl(fd, t.POSIXACL.from_aces(aces))
    finally:
        os.close(fd)


def test_posix_valid_missing_user_obj_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),       POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_no_user_obj')
    try:
        with pytest.raises(ValueError, match='USER_OBJ'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_missing_group_obj_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),       POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_no_group_obj')
    try:
        with pytest.raises(ValueError, match='GROUP_OBJ'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_missing_other_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_no_other')
    try:
        with pytest.raises(ValueError, match='OTHER'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_named_user_without_mask_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ | t.POSIXPerm.WRITE), POSIX_SPECIAL_ID),
        (int(t.POSIXTag.USER),       int(t.POSIXPerm.READ),                     1001),
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),                     POSIX_SPECIAL_ID),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),                        POSIX_SPECIAL_ID),
        # MASK absent
    )
    fd = _open_file(posix_dataset, 'val_named_no_mask')
    try:
        with pytest.raises(ValueError, match='MASK'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_named_group_without_mask_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ | t.POSIXPerm.WRITE), POSIX_SPECIAL_ID),
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),                     POSIX_SPECIAL_ID),
        (int(t.POSIXTag.GROUP),      int(t.POSIXPerm.READ),                     2001),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),                        POSIX_SPECIAL_ID),
        # MASK absent
    )
    fd = _open_file(posix_dataset, 'val_named_grp_no_mask')
    try:
        with pytest.raises(ValueError, match='MASK'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_duplicate_mask_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ | t.POSIXPerm.WRITE), POSIX_SPECIAL_ID),
        (int(t.POSIXTag.USER),       int(t.POSIXPerm.READ),                     1001),
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),                     POSIX_SPECIAL_ID),
        (int(t.POSIXTag.MASK),       int(t.POSIXPerm.READ),                     POSIX_SPECIAL_ID),
        (int(t.POSIXTag.MASK),       int(t.POSIXPerm.READ),                     POSIX_SPECIAL_ID),  # duplicate
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),                        POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_dup_mask')
    try:
        with pytest.raises(ValueError, match='MASK'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_duplicate_user_obj_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.WRITE),   POSIX_SPECIAL_ID),  # duplicate
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),       POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_dup_user_obj')
    try:
        with pytest.raises(ValueError, match='USER_OBJ'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_named_user_with_special_id_rejected(posix_dataset):
    # USER tag with id=0xFFFFFFFF is invalid; that sentinel means "no uid".
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.USER),       int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),  # bad
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.MASK),       int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),       POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_user_special_id')
    try:
        with pytest.raises(ValueError, match='uid'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


def test_posix_valid_named_group_with_special_id_rejected(posix_dataset):
    blob = _make_posix_blob(
        (int(t.POSIXTag.USER_OBJ),  int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.GROUP_OBJ), int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.GROUP),      int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),  # bad
        (int(t.POSIXTag.MASK),       int(t.POSIXPerm.READ),    POSIX_SPECIAL_ID),
        (int(t.POSIXTag.OTHER),     int(t.POSIXPerm(0)),       POSIX_SPECIAL_ID),
    )
    fd = _open_file(posix_dataset, 'val_group_special_id')
    try:
        with pytest.raises(ValueError, match='gid'):
            t.fsetacl_posix(fd, blob, None)
    finally:
        os.close(fd)


# ═══════════════════════════════════════════════════════════════════════════
# Boundary tests — large ACL counts
# ═══════════════════════════════════════════════════════════════════════════

def test_nfs4acl_encoding_1000_aces():
    """NFS4ACL.from_aces must encode 1000 ACEs without error (no filesystem needed)."""
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, who_id=i)
        for i in range(1, 998)
    ] + list(_BASE_NFS4_ACES)
    acl = t.NFS4ACL.from_aces(aces)
    assert len(acl) == 1000
    assert len(bytes(acl)) == 8 + 1000 * 20


def test_nfs4_fsetacl_1000_aces_live(nfs4_dataset):
    """Live filesystem must accept a 1000-entry NFS4 ACL on a file."""
    aces = [
        t.NFS4Ace(t.NFS4AceType.ALLOW, t.NFS4Flag(0),
                  t.NFS4Perm.READ_DATA, t.NFS4Who.NAMED, who_id=i)
        for i in range(1, 998)
    ] + list(_BASE_NFS4_ACES)
    fd = _open_file(nfs4_dataset, 'boundary_1000')
    try:
        t.fsetacl(fd, t.NFS4ACL.from_aces(aces))
        assert len(t.fgetacl(fd)) == 1000
    finally:
        os.close(fd)


def test_posixacl_encoding_32_entries():
    """POSIXACL.from_aces must encode a 32-entry ACL (28 named users + 4 required) without error."""
    aces = (
        [t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                    perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE)]
        + [t.POSIXAce(tag=t.POSIXTag.USER, perms=t.POSIXPerm.READ, id=1000 + i)
           for i in range(28)]
        + [t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
                      perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
           t.POSIXAce(tag=t.POSIXTag.MASK,
                      perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
           t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0))]
    )
    acl = t.POSIXACL.from_aces(aces)
    _, entries = _unpack_posix_acl(acl.access_bytes())
    assert len(entries) == 32


def test_posix_fsetacl_32_entries_live(posix_dataset):
    """Live filesystem must accept a 32-entry POSIX ACL (28 named users)."""
    aces = (
        [t.POSIXAce(tag=t.POSIXTag.USER_OBJ,
                    perms=t.POSIXPerm.READ | t.POSIXPerm.WRITE | t.POSIXPerm.EXECUTE)]
        + [t.POSIXAce(tag=t.POSIXTag.USER, perms=t.POSIXPerm.READ, id=1000 + i)
           for i in range(28)]
        + [t.POSIXAce(tag=t.POSIXTag.GROUP_OBJ,
                      perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
           t.POSIXAce(tag=t.POSIXTag.MASK,
                      perms=t.POSIXPerm.READ | t.POSIXPerm.EXECUTE),
           t.POSIXAce(tag=t.POSIXTag.OTHER, perms=t.POSIXPerm(0))]
    )
    fd = _open_file(posix_dataset, 'boundary_32')
    try:
        t.fsetacl(fd, t.POSIXACL.from_aces(aces))
        named = [a for a in t.fgetacl(fd).aces if a.tag == t.POSIXTag.USER]
        assert len(named) == 28
    finally:
        os.close(fd)
