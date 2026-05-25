# SPDX-License-Identifier: LGPL-3.0-or-later

"""Tests for create_cred_entry / check_path_access."""

import errno
import os
import pathlib
import tempfile

import pytest

import truenas_os


NEEDS_ROOT = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="check_path_access uses the saved-uid-0 idiom and must run as root",
)
UINT32_MAX = 0xFFFFFFFF
NOBODY_UID = 65534
NOBODY_GID = 65534


def _components_of(path):
    """Build the ancestor-component byte list the middleware passes in.

    Mirrors check_acl_execute_impl: skips the leading '/' and the first path
    segment, then yields each parent directory up to (but not including) the
    leaf.  Returns bytes objects.
    """
    parts = pathlib.Path(path).parts
    return [
        ('/' + '/'.join(parts[1:i])).encode()
        for i in range(2, len(parts))
    ]


# ── create_cred_entry: validation surface ───────────────────────────────────

def test_create_cred_entry_happy_path():
    e = truenas_os.create_cred_entry("alice", 1000, 1000, (1000, 100))
    assert e.id_name == "alice"
    assert e.uid == 1000
    assert e.gid == 1000
    assert e.groups == (1000, 100)
    assert isinstance(e, truenas_os.CredEntry)


def test_create_cred_entry_empty_groups():
    e = truenas_os.create_cred_entry("root", 0, 0, ())
    assert e.groups == ()


def test_create_cred_entry_uint32_bounds():
    e = truenas_os.create_cred_entry("max", UINT32_MAX, UINT32_MAX, (UINT32_MAX,))
    assert e.uid == UINT32_MAX
    assert e.groups == (UINT32_MAX,)


def test_create_cred_entry_rejects_uid_overflow():
    with pytest.raises(ValueError, match=r"uid"):
        truenas_os.create_cred_entry("x", UINT32_MAX + 1, 0, ())


def test_create_cred_entry_rejects_gid_overflow():
    with pytest.raises(ValueError, match=r"gid"):
        truenas_os.create_cred_entry("x", 0, UINT32_MAX + 1, ())


def test_create_cred_entry_rejects_group_overflow():
    with pytest.raises(ValueError, match=r"groups"):
        truenas_os.create_cred_entry("x", 0, 0, (UINT32_MAX + 1,))


def test_create_cred_entry_rejects_non_str_name():
    with pytest.raises(TypeError, match=r"str"):
        truenas_os.create_cred_entry(b"bytes", 0, 0, ())


def test_create_cred_entry_rejects_non_int_group():
    with pytest.raises(TypeError):
        truenas_os.create_cred_entry("x", 0, 0, ("not-an-int",))


# ── check_path_access: argument validation ──────────────────────────────────

def test_check_path_access_rejects_empty_creds():
    with pytest.raises(ValueError, match=r"creds.*non-empty"):
        truenas_os.check_path_access(creds=[], components=[b"/tmp"])


def test_check_path_access_rejects_non_cred_entry():
    with pytest.raises(TypeError, match=r"CredEntry"):
        truenas_os.check_path_access(
            creds=[("alice", 1000, 1000, ())],
            components=[b"/tmp"],
        )


def test_check_path_access_rejects_non_bytes_component():
    cred = truenas_os.create_cred_entry("root", 0, 0, ())
    with pytest.raises(TypeError, match=r"bytes"):
        truenas_os.check_path_access(creds=[cred], components=["/tmp"])


def test_check_path_access_requires_kwargs():
    cred = truenas_os.create_cred_entry("root", 0, 0, ())
    with pytest.raises(TypeError):
        truenas_os.check_path_access([cred], [b"/tmp"])


# ── check_path_access: empty components short-circuit ──────────────────────

def test_check_path_access_empty_components_returns_empty():
    """No components → no forking, empty list. Works even without root."""
    cred = truenas_os.create_cred_entry("nobody", NOBODY_UID, NOBODY_GID, ())
    assert truenas_os.check_path_access(creds=[cred], components=[]) == []


# ── check_path_access: ENOENT handling ──────────────────────────────────────

@NEEDS_ROOT
def test_check_path_access_skips_missing_by_default():
    root = truenas_os.create_cred_entry("root", 0, 0, ())
    result = truenas_os.check_path_access(
        creds=[root], components=[b"/no/such/path"],
    )
    assert result == []


@NEEDS_ROOT
def test_check_path_access_flags_missing_when_required():
    root = truenas_os.create_cred_entry("root", 0, 0, ())
    result = truenas_os.check_path_access(
        creds=[root], components=[b"/no/such/path"], path_must_exist=True,
    )
    assert len(result) == 1
    assert result[0].id_name == "root"
    assert result[0].failing_component == b"/no/such/path"
    assert result[0].errnum == errno.ENOENT


# ── check_path_access: actual permission probes ─────────────────────────────

@NEEDS_ROOT
def test_check_path_access_root_can_traverse_anything():
    root = truenas_os.create_cred_entry("root", 0, 0, ())
    with tempfile.TemporaryDirectory() as base:
        target = pathlib.Path(base) / "inner" / "leaf"
        target.parent.mkdir()
        # Even with mode 0 root traverses (CAP_DAC_OVERRIDE etc.)
        os.chmod(target.parent, 0)
        try:
            failures = truenas_os.check_path_access(
                creds=[root], components=_components_of(target),
            )
            assert failures == []
        finally:
            os.chmod(target.parent, 0o755)


@NEEDS_ROOT
def test_check_path_access_unprivileged_blocked_by_mode_700():
    """A non-root cred is denied at the first mode-700 root-owned ancestor."""
    nobody = truenas_os.create_cred_entry("nobody", NOBODY_UID, NOBODY_GID, ())

    with tempfile.TemporaryDirectory() as base:
        # base is mode 700 by default for TemporaryDirectory
        blocked = pathlib.Path(base) / "deeper" / "leaf"
        blocked.parent.mkdir()
        os.chmod(blocked.parent, 0o755)  # only base blocks

        components = _components_of(blocked)
        failures = truenas_os.check_path_access(
            creds=[nobody], components=components,
        )
        assert failures, "expected denial somewhere"
        # All failures should be EACCES, attributed to nobody
        for f in failures:
            assert f.id_name == "nobody"
            assert f.errnum == errno.EACCES
        # The base directory must be one of the denied components
        assert base.encode() in {f.failing_component for f in failures}


@NEEDS_ROOT
def test_check_path_access_separates_creds():
    """Two creds, only one denied; only that one shows up in the result."""
    nobody = truenas_os.create_cred_entry("nobody", NOBODY_UID, NOBODY_GID, ())
    root   = truenas_os.create_cred_entry("root", 0, 0, ())

    with tempfile.TemporaryDirectory() as base:
        target = pathlib.Path(base) / "leaf"
        components = _components_of(target)
        failures = truenas_os.check_path_access(
            creds=[nobody, root], components=components,
        )
        for f in failures:
            assert f.id_name == "nobody", f"root should not appear: {f!r}"


@NEEDS_ROOT
def test_check_path_access_group_membership_grants_access():
    """A non-root cred whose supplementary groups match a g+x dir succeeds."""
    test_gid = 65530  # arbitrary group id not commonly assigned

    with tempfile.TemporaryDirectory() as base:
        target = pathlib.Path(base) / "shared" / "leaf"
        target.parent.mkdir()
        # Allow base and shared to be traversed via group membership only
        os.chown(base, 0, test_gid)
        os.chown(target.parent, 0, test_gid)
        os.chmod(base, 0o710)              # rwx --x ---
        os.chmod(target.parent, 0o710)

        components = _components_of(target)
        # Without the group, nobody is denied
        outsider = truenas_os.create_cred_entry(
            "outsider", NOBODY_UID, NOBODY_GID, ()
        )
        denied = truenas_os.check_path_access(
            creds=[outsider], components=components,
        )
        assert denied, "outsider should be denied"

        # With the group, the same uid succeeds
        member = truenas_os.create_cred_entry(
            "member", NOBODY_UID, NOBODY_GID, (test_gid,),
        )
        allowed = truenas_os.check_path_access(
            creds=[member], components=components,
        )
        assert allowed == [], f"member should pass, got {allowed!r}"


# ── check_path_access: result-list shape ────────────────────────────────────

@NEEDS_ROOT
def test_check_path_access_returns_access_failure_struct():
    nobody = truenas_os.create_cred_entry("nobody", NOBODY_UID, NOBODY_GID, ())
    with tempfile.TemporaryDirectory() as base:
        target = pathlib.Path(base) / "leaf"
        components = _components_of(target)
        failures = truenas_os.check_path_access(
            creds=[nobody], components=components,
        )
        for f in failures:
            assert isinstance(f, truenas_os.AccessFailure)
            assert isinstance(f.id_name, str)
            assert isinstance(f.failing_component, bytes)
            assert isinstance(f.errnum, int)


# ── check_path_access: replay-inconsistent iterables ────────────────────────

@NEEDS_ROOT
def test_check_path_access_accepts_generator_with_failure():
    """Generator creds must not cause an OOB read when a failure is reported.

    Regression: parse_creds previously consumed the generator and a second
    PySequence_Fast on the same exhausted generator yielded an empty list,
    which build_failure_list then indexed past the end of.
    """
    def gen():
        yield truenas_os.create_cred_entry("alice", NOBODY_UID, NOBODY_GID, ())

    failures = truenas_os.check_path_access(
        creds=gen(),
        components=[b"/no/such/path/from/generator/regression"],
        path_must_exist=True,
    )
    assert len(failures) == 1
    assert failures[0].id_name == "alice"
    assert failures[0].failing_component == (
        b"/no/such/path/from/generator/regression"
    )
    assert failures[0].errnum == errno.ENOENT


@NEEDS_ROOT
def test_check_path_access_accepts_replay_inconsistent_iter():
    """Custom __iter__ returning different counts must not cause OOB.

    Regression: a second PySequence_Fast on a `replay-inconsistent` iterable
    could yield a shorter sequence; the stale `n_creds` bounds check then let
    build_failure_list read past the actual list's allocated items.
    """
    class Replay:
        def __init__(self):
            self.n = 0

        def __iter__(self):
            self.n += 1
            yield truenas_os.create_cred_entry("first", 0, 0, ())
            if self.n == 1:
                yield truenas_os.create_cred_entry(
                    "second", NOBODY_UID, NOBODY_GID, (),
                )

    failures = truenas_os.check_path_access(
        creds=Replay(),
        components=[b"/no/such/path/from/replay/regression"],
        path_must_exist=True,
    )
    # Both creds saw ENOENT during the first iteration the child observed.
    ids = sorted(f.id_name for f in failures)
    assert ids == ["first", "second"], f"got {failures!r}"
    for f in failures:
        assert f.errnum == errno.ENOENT


# ── check_path_access: mode parameter validation ────────────────────────────

def test_check_path_access_rejects_zero_mode():
    """mode=0 (F_OK existence-only) is rejected at the Python boundary."""
    cred = truenas_os.create_cred_entry("root", 0, 0, ())
    with pytest.raises(ValueError, match=r"mode"):
        truenas_os.check_path_access(
            creds=[cred], components=[b"/tmp"], mode=0,
        )


def test_check_path_access_rejects_unknown_mode_bits():
    """Bits outside R_OK | W_OK | X_OK are rejected, not silently dropped."""
    cred = truenas_os.create_cred_entry("root", 0, 0, ())
    with pytest.raises(ValueError, match=r"mode"):
        # 0x10 is well outside the valid mask
        truenas_os.check_path_access(
            creds=[cred], components=[b"/tmp"], mode=0x10,
        )


# ── check_path_access: R/W single-path probes ──────────────────────────────

@NEEDS_ROOT
def test_check_path_access_read_mode_owner_granted():
    """A chmod-0400 file is readable by its owner."""
    owner = truenas_os.create_cred_entry(
        "owner", NOBODY_UID, NOBODY_GID, ()
    )
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        path = tf.name
    try:
        os.chown(path, NOBODY_UID, NOBODY_GID)
        os.chmod(path, 0o400)
        failures = truenas_os.check_path_access(
            creds=[owner], components=[path.encode()], mode=os.R_OK,
        )
        assert failures == [], f"owner should be able to read 0400, got {failures!r}"
    finally:
        os.unlink(path)


@NEEDS_ROOT
def test_check_path_access_write_mode_denied_when_readonly():
    """A chmod-0400 file is not writable by its owner (no W bit)."""
    owner = truenas_os.create_cred_entry(
        "owner", NOBODY_UID, NOBODY_GID, ()
    )
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        path = tf.name
    try:
        os.chown(path, NOBODY_UID, NOBODY_GID)
        os.chmod(path, 0o400)
        failures = truenas_os.check_path_access(
            creds=[owner], components=[path.encode()], mode=os.W_OK,
        )
        assert len(failures) == 1
        assert failures[0].id_name == "owner"
        assert failures[0].errnum == errno.EACCES
    finally:
        os.unlink(path)


@NEEDS_ROOT
def test_check_path_access_combined_mode_requires_all_bits():
    """mode=R_OK|W_OK fails if any bit is denied (chmod 0400 has R but no W)."""
    owner = truenas_os.create_cred_entry(
        "owner", NOBODY_UID, NOBODY_GID, ()
    )
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        path = tf.name
    try:
        os.chown(path, NOBODY_UID, NOBODY_GID)
        os.chmod(path, 0o400)
        failures = truenas_os.check_path_access(
            creds=[owner], components=[path.encode()],
            mode=os.R_OK | os.W_OK,
        )
        # faccessat2 returns failure when any requested bit is denied.
        assert len(failures) == 1
        assert failures[0].errnum == errno.EACCES
    finally:
        os.unlink(path)


@NEEDS_ROOT
def test_check_path_access_default_mode_is_execute_traversal():
    """Omitting mode preserves the original X_OK semantics."""
    nobody = truenas_os.create_cred_entry("nobody", NOBODY_UID, NOBODY_GID, ())
    with tempfile.TemporaryDirectory() as base:
        # base is mode 700 by default — nobody can't traverse it
        components = [base.encode()]
        failures = truenas_os.check_path_access(
            creds=[nobody], components=components,  # mode default = X_OK
        )
        assert len(failures) == 1
        assert failures[0].errnum == errno.EACCES
