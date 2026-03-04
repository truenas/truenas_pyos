# SPDX-License-Identifier: LGPL-3.0-or-later

import curses
import curses.ascii
import dataclasses
import enum
import grp
import os
import pwd
import stat
import sys

import truenas_os as t

from ._getfacl import (
    _nfs4_perm_str, _nfs4_flag_str, _nfs4_who_str, _NFS4_TYPE_STR,
    _posix_perm_str, _posix_qualifier, _POSIX_TAG_PREFIX,
    _NFS4_PERM_CHARS, _NFS4_FLAG_CHARS,
    _name_of_uid, _name_of_gid,
)
from ._setfacl import (
    _parse_nfs4_ace, _parse_posix_ace,
    _NFS4InheritedAcls, _get_mount_info, _make_trivial_posix,
    _NFS4_PERM_SETS,
    _resolve_uid, _resolve_gid,
)


_MIN_COLS, _MIN_LINES = 80, 20

# Total fixed chrome: title bar + panel headers + separator + help bar + status bar
_LAYOUT_FIXED_ROWS = 5

# Color pair indices
_CP_TITLE, _CP_SELECTED, _CP_ERROR, _CP_PREVIEW, _CP_WARN = 1, 2, 3, 4, 5

_MODE_NORMAL = 'NORMAL'
_MODE_INPUT = 'INPUT'
_MODE_ACE_FORM = 'ACE_FORM'

# Key code aliases
_KEY_ESC = curses.ascii.ESC
_KEY_CTRL_C = curses.ascii.ETX
_KEY_CTRL_T = 20
_PRINTABLE_FIRST = 0x20
_PRINTABLE_LAST = 0x7E
_BACKSPACE_KEYS = frozenset({curses.KEY_BACKSPACE, curses.ascii.DEL, curses.ascii.BS})
_ENTER_KEYS = frozenset({curses.KEY_ENTER, curses.ascii.NL, curses.ascii.CR})

# ACE form layout — column offsets for TYPE, SETS, BITS, INHERIT regions
_FORM_ALLOW_COL = 8
_FORM_ALLOW_TEXT_COL = 12
_FORM_DENY_COL = 20
_FORM_DENY_TEXT_COL = 24

_FORM_SETS_INDENT = 2
_FORM_SETS_COLS = 2
_FORM_SETS_STRIDE = 16
_FORM_SETS_NAME_W = 10

_FORM_BITS_COLS = 7
_FORM_BITS_STRIDE = 4

_FORM_INHERIT_INDENT = 2
_FORM_INHERIT_COLS = 2
_FORM_INHERIT_STRIDE = 14
_FORM_INHERIT_NAME_W = 9

# NFS4 who-type radio grid (5 options, 2 columns)
_FORM_WHO_COLS = 2
_FORM_WHO_STRIDE = 14
_FORM_WHO_LABELS = ('owner@', 'group@', 'everyone@', 'user', 'group')

# POSIX structured editor — base-entry rows and perm bits
_POSIX_BASE_ROWS = (
    (t.POSIXTag.USER_OBJ, 'User (owner)'),
    (t.POSIXTag.GROUP_OBJ, 'Group (owner)'),
    (t.POSIXTag.MASK, 'Mask'),
    (t.POSIXTag.OTHER, 'Other'),
)
_POSIX_PERM_BITS = (
    (t.POSIXPerm.READ, 'r'),
    (t.POSIXPerm.WRITE, 'w'),
    (t.POSIXPerm.EXECUTE, 'x'),
)

# POSIX editor checkbox layout
# Row format: '{indicator} {label:<16}[r][w][x]'
# indicator(1) + space(1) + label(16) = 18 chars before first '['
_POSIX_CHK_COL = 18
_POSIX_CHK_STRIDE = 3

# POSIX named-entry form checkbox layout
_POSIX_NAMED_CHK_COL = 10
_POSIX_NAMED_CHK_STRIDE = 7


class _FormRegion(enum.IntEnum):
    """Focusable regions of the NFS4 ACE structured form."""
    WHO = 0
    TYPE = 1
    SETS = 2
    BITS = 3
    INHERIT = 4


class _NFS4WhoChoice(enum.IntEnum):
    """Radio options for the NFS4 WHO field."""
    OWNER = 0
    GROUP = 1
    EVERYONE = 2
    USER = 3
    GROUP_NAMED = 4


class _PosixNamedRegion(enum.IntEnum):
    """Focusable regions of the POSIX named-entry add/edit form."""
    TYPE = 0
    ID = 1
    ACCESS = 2
    DEFAULT = 3


# Permission sets shown in the form
_FORM_PERM_SETS = [
    ('full_set', _NFS4_PERM_SETS['full_set']),
    ('modify_set', _NFS4_PERM_SETS['modify_set']),
    ('read_set', _NFS4_PERM_SETS['read_set']),
    ('write_set', _NFS4_PERM_SETS['write_set']),
]

# Inherit flags shown in the form
_FORM_INHERIT_FLAGS = [
    ('file', t.NFS4Flag.FILE_INHERIT),
    ('dir', t.NFS4Flag.DIRECTORY_INHERIT),
    ('no_prop', t.NFS4Flag.NO_PROPAGATE_INHERIT),
    ('inh_only', t.NFS4Flag.INHERIT_ONLY),
]

# Individual permission bits in canonical order
_FORM_PERM_BITS = [bit for bit, _ in _NFS4_PERM_CHARS]
_FORM_PERM_LABELS = [c for _, c in _NFS4_PERM_CHARS]


@dataclasses.dataclass
class _InputState:
    """Transient state for one text-input session (legacy)."""
    prompt: str = ''
    buf: str = ''
    caret: int = 0
    edit_idx: int | None = None


@dataclasses.dataclass
class _FormState:
    """Transient state for one NFS4 ACE form session."""
    who_type: _NFS4WhoChoice = _NFS4WhoChoice.OWNER
    who_id: str = ''
    who_id_caret: int = 0
    who_numeric: bool = False
    who_in_id: bool = False
    allow: bool = True
    mask: t.NFS4Perm = dataclasses.field(default_factory=lambda: t.NFS4Perm(0))
    inh_flags: t.NFS4Flag = dataclasses.field(default_factory=lambda: t.NFS4Flag(0))
    region: _FormRegion = _FormRegion.WHO
    cursor: int = 0
    edit_idx: int | None = None


@dataclasses.dataclass
class _PosixNamedState:
    """Transient state for the POSIX named-entry add/edit sub-form."""
    is_group: bool = False
    id_str: str = ''
    id_caret: int = 0
    id_numeric: bool = False
    access_perms: t.POSIXPerm = dataclasses.field(default_factory=lambda: t.POSIXPerm(0))
    default_perms: t.POSIXPerm = dataclasses.field(default_factory=lambda: t.POSIXPerm(0))
    has_default: bool = False
    region: _PosixNamedRegion = _PosixNamedRegion.TYPE
    cursor: int = 0
    edit_id: int | None = None


@dataclasses.dataclass
class _EditContext:
    path: str
    fd: int
    is_dir: bool
    is_nfs4: bool
    aces: list[t.NFS4Ace] | list[t.POSIXAce]
    acl_flags: t.NFS4ACLFlag | None
    fs_name: str = ''
    warn: str = ''
    rc: int = 0


# ── module-level helpers ──────────────────────────────────────────────────────

def _panel_widths(cols):
    """Return (left_w, right_w) with 1-char separator between them."""
    left_w = cols // 2
    right_w = cols - left_w - 1
    return left_w, right_w


def _content_rows(lines):
    """Rows available for scrollable content (total minus fixed chrome)."""
    return max(0, lines - _LAYOUT_FIXED_ROWS)


def _ace_str_nfs4(ace):
    """Format NFS4Ace as canonical who:perms:flags:type text."""
    who = _nfs4_who_str(ace, numeric=False)
    perms = _nfs4_perm_str(ace.access_mask)
    flags = _nfs4_flag_str(ace.ace_flags)
    atype = _NFS4_TYPE_STR.get(ace.ace_type, str(int(ace.ace_type)))
    return f'{who}:{perms}:{flags}:{atype}'


def _ace_str_posix(ace):
    """Format POSIXAce as canonical [default:]tag:qualifier:perms text."""
    tag = _POSIX_TAG_PREFIX[ace.tag]
    qual = _posix_qualifier(ace, numeric=False)
    perms = _posix_perm_str(ace.perms)
    prefix = 'default:' if ace.default else ''
    return f'{prefix}{tag}:{qual}:{perms}'


def _grid_nav(cursor, ch, cols, count):
    """Move cursor in a row-major grid; return (new_cursor, went_prev, went_next)."""
    col = cursor % cols
    row = cursor // cols
    last_row = (count - 1) // cols

    if ch == curses.KEY_LEFT:
        if col > 0:
            cursor -= 1
    elif ch == curses.KEY_RIGHT:
        if col < cols - 1 and cursor < count - 1:
            cursor += 1
    elif ch == curses.KEY_UP:
        if row > 0:
            cursor -= cols
        else:
            return cursor, True, False
    elif ch == curses.KEY_DOWN:
        if row < last_row:
            cursor = min(cursor + cols, count - 1)
        else:
            return cursor, False, True

    return cursor, False, False


# ── editor class ──────────────────────────────────────────────────────────────

class AclEditor:

    def __init__(self, stdscr, ctx):
        self._scr = stdscr
        self._ctx = ctx
        self._cursor = 0
        self._scroll = 0
        self._mode = _MODE_NORMAL
        self._status = ''
        self._error = ''
        self._inp = _InputState()
        self._frm = _FormState()
        self._saved = True
        self._posix_cursor = 0
        self._posix_named = None

    # ── public entry ──────────────────────────────────────────────────────────

    def run(self):
        """Initialize curses, run the event loop, return an exit code."""
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(_CP_ERROR, curses.COLOR_RED, -1)
            curses.init_pair(_CP_PREVIEW, curses.COLOR_CYAN, -1)
            curses.init_pair(_CP_WARN, curses.COLOR_RED, -1)
        self._scr.keypad(True)
        self._draw()

        while True:
            try:
                ch = self._scr.getch()
            except KeyboardInterrupt:
                if self._mode == _MODE_INPUT:
                    self._input_cancel()
                elif self._mode == _MODE_ACE_FORM:
                    if not self._ctx.is_nfs4:
                        self._posix_named_cancel()
                    else:
                        self._form_cancel()
                else:
                    return 0
                self._draw()
                continue

            result = self._handle_key(ch)
            if result is not None:
                return result

    # ── key dispatch ──────────────────────────────────────────────────────────

    def _handle_key(self, ch):
        if ch == curses.KEY_RESIZE:
            return self._handle_resize()
        if self._mode == _MODE_NORMAL:
            return self._handle_normal_key(ch)
        if self._mode == _MODE_INPUT:
            return self._handle_input_key(ch)
        return self._handle_ace_form_key(ch)

    def _handle_normal_key(self, ch):
        if not self._ctx.is_nfs4:
            return self._handle_posix_key(ch)

        lines, cols = self._scr.getmaxyx()
        content = _content_rows(lines)
        n = len(self._ctx.aces)

        self._error = ''
        self._status = ''

        if ch in (curses.KEY_UP, ord('k')):
            if self._cursor > 0:
                self._cursor -= 1
                if self._cursor < self._scroll:
                    self._scroll = self._cursor

        elif ch in (curses.KEY_DOWN, ord('j')):
            if self._cursor < n - 1:
                self._cursor += 1
                if self._cursor >= self._scroll + content:
                    self._scroll = self._cursor - content + 1

        elif ch == ord('a'):
            self._do_add()

        elif ch == ord('d'):
            self._do_delete()

        elif ch in (ord('e'), *_ENTER_KEYS):
            self._do_edit()

        elif ch in (ord('s'), ord('w')):
            result = self._do_save()
            if result is not None:
                return result

        elif ch in (ord('q'), _KEY_ESC):
            return 0

        elif ch == _KEY_CTRL_C:
            return 0

        self._draw()
        return None

    def _handle_input_key(self, ch):
        if ch in (_KEY_ESC, _KEY_CTRL_C):
            self._input_cancel()
            self._draw()
            return None

        if ch in _ENTER_KEYS:
            self._input_finish()
            self._draw()
            return None

        if ch == curses.KEY_LEFT:
            if self._inp.caret > 0:
                self._inp.caret -= 1
        elif ch == curses.KEY_RIGHT:
            if self._inp.caret < len(self._inp.buf):
                self._inp.caret += 1
        elif ch == curses.KEY_HOME:
            self._inp.caret = 0
        elif ch == curses.KEY_END:
            self._inp.caret = len(self._inp.buf)
        elif ch in _BACKSPACE_KEYS:
            if self._inp.caret > 0:
                b = self._inp.buf
                self._inp.buf = b[:self._inp.caret - 1] + b[self._inp.caret:]
                self._inp.caret -= 1
        elif ch == curses.KEY_DC:
            if self._inp.caret < len(self._inp.buf):
                b = self._inp.buf
                self._inp.buf = b[:self._inp.caret] + b[self._inp.caret + 1:]
        elif _PRINTABLE_FIRST <= ch <= _PRINTABLE_LAST:
            b = self._inp.buf
            self._inp.buf = b[:self._inp.caret] + chr(ch) + b[self._inp.caret:]
            self._inp.caret += 1

        self._draw()
        return None

    def _handle_ace_form_key(self, ch):
        if not self._ctx.is_nfs4:
            return self._handle_posix_named_key(ch)

        if ch in (_KEY_ESC, _KEY_CTRL_C):
            if self._frm.region == _FormRegion.WHO and self._frm.who_in_id:
                self._frm.who_in_id = False
                self._draw()
                return None
            self._form_cancel()
            self._draw()
            return None

        if ch in _ENTER_KEYS:
            if self._frm.region == _FormRegion.WHO and self._frm.who_in_id:
                self._form_key_who(ch)
            else:
                self._form_finish()
            self._draw()
            return None

        if ch == curses.KEY_BTAB:
            self._frm.region = _FormRegion((int(self._frm.region) - 1) % len(_FormRegion))
            self._frm.cursor = 0
            self._frm.who_in_id = False
            self._draw()
            return None

        if ch == ord('\t'):
            self._frm.region = _FormRegion((int(self._frm.region) + 1) % len(_FormRegion))
            self._frm.cursor = 0
            self._frm.who_in_id = False
            self._draw()
            return None

        r = self._frm.region
        if r == _FormRegion.WHO:
            self._form_key_who(ch)
        elif r == _FormRegion.TYPE:
            self._form_key_type(ch)
        elif r == _FormRegion.SETS:
            self._form_key_sets(ch)
        elif r == _FormRegion.BITS:
            self._form_key_bits(ch)
        elif r == _FormRegion.INHERIT:
            self._form_key_inherit(ch)

        self._draw()
        return None

    # ── NFS4 form key handlers per region ────────────────────────────────────

    def _form_key_who(self, ch):
        if self._frm.who_in_id:
            if ch in (curses.KEY_UP, _KEY_ESC):
                self._frm.who_in_id = False
            elif ch in (*_ENTER_KEYS, curses.KEY_DOWN):
                if self._validate_who_id():
                    self._frm.who_in_id = False
                    if ch in _ENTER_KEYS:
                        self._frm.region = _FormRegion.TYPE
                        self._frm.cursor = 0
            elif ch == curses.KEY_LEFT:
                if self._frm.who_id_caret > 0:
                    self._frm.who_id_caret -= 1
            elif ch == curses.KEY_RIGHT:
                if self._frm.who_id_caret < len(self._frm.who_id):
                    self._frm.who_id_caret += 1
            elif ch == curses.KEY_HOME:
                self._frm.who_id_caret = 0
            elif ch == curses.KEY_END:
                self._frm.who_id_caret = len(self._frm.who_id)
            elif ch in _BACKSPACE_KEYS:
                if self._frm.who_id_caret > 0:
                    s = self._frm.who_id
                    self._frm.who_id = s[:self._frm.who_id_caret - 1] + s[self._frm.who_id_caret:]
                    self._frm.who_id_caret -= 1
            elif ch == curses.KEY_DC:
                if self._frm.who_id_caret < len(self._frm.who_id):
                    s = self._frm.who_id
                    self._frm.who_id = s[:self._frm.who_id_caret] + s[self._frm.who_id_caret + 1:]
            elif ch == _KEY_CTRL_T:
                self._who_toggle_numeric()
            elif _PRINTABLE_FIRST <= ch <= _PRINTABLE_LAST:
                s = self._frm.who_id
                self._frm.who_id = s[:self._frm.who_id_caret] + chr(ch) + s[self._frm.who_id_caret:]
                self._frm.who_id_caret += 1
        else:
            new_cur, went_prev, went_next = _grid_nav(
                self._frm.cursor, ch, _FORM_WHO_COLS, len(_NFS4WhoChoice))
            if went_prev:
                self._frm.region = _FormRegion.INHERIT
                self._frm.cursor = 0
            elif went_next:
                if self._frm.who_type in (_NFS4WhoChoice.USER, _NFS4WhoChoice.GROUP_NAMED):
                    self._frm.who_in_id = True
                else:
                    self._frm.region = _FormRegion.TYPE
                    self._frm.cursor = 0
            else:
                self._frm.cursor = new_cur
                self._frm.who_type = _NFS4WhoChoice(new_cur)

    def _validate_who_id(self):
        s = self._frm.who_id.strip()
        if not s:
            self._error = 'Identifier cannot be empty'
            return False
        if self._frm.who_numeric:
            try:
                int(s)
                return True
            except ValueError:
                self._error = 'Numeric ID must be an integer'
                return False
        kind = 'user' if self._frm.who_type == _NFS4WhoChoice.USER else 'group'
        try:
            (pwd.getpwnam if kind == 'user' else grp.getgrnam)(s)
            return True
        except KeyError:
            self._error = f'Unknown {kind}: {s!r}'
            return False

    def _who_toggle_numeric(self):
        s = self._frm.who_id.strip()
        if not self._frm.who_numeric:
            if s:
                try:
                    if self._frm.who_type == _NFS4WhoChoice.USER:
                        new_s = str(_resolve_uid(s))
                    else:
                        new_s = str(_resolve_gid(s))
                    self._frm.who_id = new_s
                    self._frm.who_id_caret = len(new_s)
                except ValueError as e:
                    self._error = str(e)
                    return
            self._frm.who_numeric = True
        else:
            if s:
                try:
                    n = int(s)
                    if self._frm.who_type == _NFS4WhoChoice.USER:
                        new_s = _name_of_uid(n, False)
                    else:
                        new_s = _name_of_gid(n, False)
                    self._frm.who_id = new_s
                    self._frm.who_id_caret = len(new_s)
                except (ValueError, KeyError):
                    self._error = f'Cannot resolve id: {s!r}'
                    return
            self._frm.who_numeric = False

    def _form_key_type(self, ch):
        if ch == curses.KEY_LEFT:
            self._frm.cursor = 0
            self._frm.allow = True
        elif ch == curses.KEY_RIGHT:
            self._frm.cursor = 1
            self._frm.allow = False
        elif ch == ord(' '):
            self._frm.allow = not self._frm.allow
            self._frm.cursor = 0 if self._frm.allow else 1
        elif ch == curses.KEY_UP:
            self._frm.region = _FormRegion.WHO
            self._frm.cursor = 0
        elif ch == curses.KEY_DOWN:
            self._frm.region = _FormRegion.SETS
            self._frm.cursor = 0

    def _form_key_sets(self, ch):
        if ch == ord(' '):
            _, set_bits = _FORM_PERM_SETS[self._frm.cursor]
            if (int(self._frm.mask) & int(set_bits)) == int(set_bits):
                self._frm.mask = t.NFS4Perm(int(self._frm.mask) & ~int(set_bits))
            else:
                self._frm.mask = t.NFS4Perm(int(self._frm.mask) | int(set_bits))
            return

        new_cur, went_prev, went_next = _grid_nav(
            self._frm.cursor, ch, _FORM_SETS_COLS, len(_FORM_PERM_SETS))
        if went_prev:
            self._frm.region = _FormRegion.TYPE
            self._frm.cursor = 0
        elif went_next:
            self._frm.region = _FormRegion.BITS
            self._frm.cursor = 0
        else:
            self._frm.cursor = new_cur

    def _form_key_bits(self, ch):
        if ch == ord(' '):
            bit = _FORM_PERM_BITS[self._frm.cursor]
            if self._frm.mask & bit:
                self._frm.mask = t.NFS4Perm(int(self._frm.mask) & ~int(bit))
            else:
                self._frm.mask = t.NFS4Perm(int(self._frm.mask) | int(bit))
            return

        new_cur, went_prev, went_next = _grid_nav(
            self._frm.cursor, ch, _FORM_BITS_COLS, len(_FORM_PERM_BITS))
        if went_prev:
            self._frm.region = _FormRegion.SETS
            self._frm.cursor = _FORM_SETS_COLS
        elif went_next:
            self._frm.region = _FormRegion.INHERIT
            self._frm.cursor = 0
        else:
            self._frm.cursor = new_cur

    def _form_key_inherit(self, ch):
        if ch == ord(' '):
            _, flag = _FORM_INHERIT_FLAGS[self._frm.cursor]
            if self._frm.inh_flags & flag:
                self._frm.inh_flags = t.NFS4Flag(int(self._frm.inh_flags) & ~int(flag))
            else:
                self._frm.inh_flags = t.NFS4Flag(int(self._frm.inh_flags) | int(flag))
            return

        new_cur, went_prev, went_next = _grid_nav(
            self._frm.cursor, ch, _FORM_INHERIT_COLS, len(_FORM_INHERIT_FLAGS))
        if went_prev:
            self._frm.region = _FormRegion.BITS
            self._frm.cursor = _FORM_BITS_COLS
        elif went_next:
            self._frm.region = _FormRegion.WHO
            self._frm.cursor = 0
        else:
            self._frm.cursor = new_cur

    # ── POSIX normal-mode key handler ─────────────────────────────────────────

    def _handle_posix_key(self, ch):
        named_base = 24 if self._ctx.is_dir else 12
        entries = self._posix_named_entries()
        n_named = len(entries)
        c = self._posix_cursor

        self._error = ''
        self._status = ''

        if ch in (curses.KEY_UP, ord('k')):
            if c > 0 and c < 12:
                row, col = c // 3, c % 3
                if row > 0:
                    self._posix_cursor = (row - 1) * 3 + col
            elif c >= 12 and c < 24 and self._ctx.is_dir:
                row = (c - 12) // 3
                col = (c - 12) % 3
                if row > 0:
                    self._posix_cursor = 12 + (row - 1) * 3 + col
                else:
                    self._posix_cursor = 9 + col
            elif c >= named_base:
                k = c - named_base
                if k == 0:
                    self._posix_cursor = 21 if self._ctx.is_dir else 9
                else:
                    self._posix_cursor = c - 1

        elif ch in (curses.KEY_DOWN, ord('j')):
            if c < 12:
                row, col = c // 3, c % 3
                if row < 3:
                    self._posix_cursor = (row + 1) * 3 + col
                elif self._ctx.is_dir:
                    self._posix_cursor = 12 + col
                elif n_named > 0:
                    self._posix_cursor = named_base
            elif c >= 12 and c < 24 and self._ctx.is_dir:
                row = (c - 12) // 3
                col = (c - 12) % 3
                if row < 3:
                    self._posix_cursor = 12 + (row + 1) * 3 + col
                elif n_named > 0:
                    self._posix_cursor = named_base
            elif c >= named_base:
                k = c - named_base
                if k < n_named - 1:
                    self._posix_cursor = c + 1

        elif ch == curses.KEY_LEFT:
            if c < 12:
                if c % 3 > 0:
                    self._posix_cursor -= 1
            elif c >= 12 and c < 24 and self._ctx.is_dir:
                if (c - 12) % 3 > 0:
                    self._posix_cursor -= 1

        elif ch == curses.KEY_RIGHT:
            if c < 12:
                if c % 3 < 2:
                    self._posix_cursor += 1
            elif c >= 12 and c < 24 and self._ctx.is_dir:
                if (c - 12) % 3 < 2:
                    self._posix_cursor += 1

        elif ch == ord(' '):
            self._posix_toggle()

        elif ch == ord('a'):
            self._posix_named_start(None)

        elif ch == ord('d'):
            if c >= named_base and n_named > 0:
                k = c - named_base
                if k < n_named:
                    self._posix_delete_named(k)

        elif ch in (ord('e'), *_ENTER_KEYS):
            if c >= named_base and n_named > 0:
                k = c - named_base
                if k < n_named:
                    self._posix_named_start(k)

        elif ch in (ord('s'), ord('w')):
            result = self._do_save()
            if result is not None:
                return result

        elif ch in (ord('q'), _KEY_ESC, _KEY_CTRL_C):
            return 0

        self._draw()
        return None

    def _posix_toggle(self):
        c = self._posix_cursor

        if c < 12:
            ri, ci = c // 3, c % 3
            tag = _POSIX_BASE_ROWS[ri][0]
            bit, _ = _POSIX_PERM_BITS[ci]
            is_default = False
        elif c < 24 and self._ctx.is_dir:
            ri, ci = (c - 12) // 3, (c - 12) % 3
            tag = _POSIX_BASE_ROWS[ri][0]
            bit, _ = _POSIX_PERM_BITS[ci]
            is_default = True
        else:
            return

        ace_idx = None
        for i, ace in enumerate(self._ctx.aces):
            if ace.tag == tag and ace.default == is_default:
                ace_idx = i
                break

        prefix = 'default:' if is_default else ''
        tag_str = _POSIX_TAG_PREFIX[tag]

        if ace_idx is None:
            new_perm = t.POSIXPerm(int(bit))
        else:
            old_perm = self._ctx.aces[ace_idx].perms
            if old_perm & bit:
                new_perm = t.POSIXPerm(int(old_perm) & ~int(bit))
            else:
                new_perm = t.POSIXPerm(int(old_perm) | int(bit))

        perm_str = _posix_perm_str(new_perm)
        text = f'{prefix}{tag_str}::{perm_str}'
        try:
            new_ace = _parse_posix_ace(text)
        except ValueError as e:
            self._error = str(e)
            return

        if ace_idx is not None:
            self._ctx.aces[ace_idx] = new_ace
        else:
            self._ctx.aces.append(new_ace)
        self._saved = False

    def _posix_find_ace(self, tag, is_default):
        for ace in self._ctx.aces:
            if ace.tag == tag and ace.default == is_default:
                return ace
        return None

    def _posix_named_entries(self):
        seen = {}
        for ace in self._ctx.aces:
            if ace.tag not in (t.POSIXTag.USER, t.POSIXTag.GROUP):
                continue
            is_group = (ace.tag == t.POSIXTag.GROUP)
            key = (ace.id, is_group)
            if key not in seen:
                seen[key] = [None, None]
            if ace.default:
                seen[key][1] = ace.perms
            else:
                seen[key][0] = ace.perms
        return [(k[0], k[1], v[0], v[1]) for k, v in seen.items()]

    def _posix_delete_named(self, k):
        entries = self._posix_named_entries()
        if k >= len(entries):
            return
        entry_id, is_group, _, _ = entries[k]
        tag = t.POSIXTag.GROUP if is_group else t.POSIXTag.USER
        self._ctx.aces = [
            ace for ace in self._ctx.aces
            if not (ace.tag == tag and ace.id == entry_id)
        ]
        named_base = 24 if self._ctx.is_dir else 12
        n_named = len(self._posix_named_entries())
        if n_named == 0:
            self._posix_cursor = 21 if self._ctx.is_dir else 9
        elif self._posix_cursor >= named_base + n_named:
            self._posix_cursor = named_base + n_named - 1
        self._saved = False
        self._status = 'Named entry deleted'

    # ── POSIX named-entry form ────────────────────────────────────────────────

    def _posix_named_start(self, entry_k):
        self._mode = _MODE_ACE_FORM
        self._error = ''

        if entry_k is None:
            self._posix_named = _PosixNamedState()
        else:
            entries = self._posix_named_entries()
            if entry_k >= len(entries):
                self._posix_named = _PosixNamedState()
            else:
                entry_id, is_group, access_perm, default_perm = entries[entry_k]
                name = (_name_of_gid(entry_id, False) if is_group
                        else _name_of_uid(entry_id, False))
                self._posix_named = _PosixNamedState(
                    is_group=is_group,
                    id_str=name,
                    id_caret=len(name),
                    id_numeric=False,
                    access_perms=(access_perm if access_perm is not None else t.POSIXPerm(0)),
                    default_perms=(default_perm if default_perm is not None else t.POSIXPerm(0)),
                    has_default=(default_perm is not None),
                    region=_PosixNamedRegion.TYPE,
                    cursor=1 if is_group else 0,
                    edit_id=entry_id,
                )

    def _posix_named_finish(self):
        ns = self._posix_named
        if not self._validate_posix_id():
            return

        id_str = ns.id_str.strip()
        tag_str = 'group' if ns.is_group else 'user'

        if ns.edit_id is not None:
            tag = t.POSIXTag.GROUP if ns.is_group else t.POSIXTag.USER
            self._ctx.aces = [
                ace for ace in self._ctx.aces
                if not (ace.tag == tag and ace.id == ns.edit_id)
            ]

        access_perm_str = _posix_perm_str(ns.access_perms)
        text = f'{tag_str}:{id_str}:{access_perm_str}'
        try:
            ace = _parse_posix_ace(text)
            self._ctx.aces.append(ace)
        except ValueError as e:
            self._error = f'Parse error: {e}'
            return

        if ns.has_default and self._ctx.is_dir:
            default_perm_str = _posix_perm_str(ns.default_perms)
            text = f'default:{tag_str}:{id_str}:{default_perm_str}'
            try:
                ace = _parse_posix_ace(text)
                self._ctx.aces.append(ace)
            except ValueError as e:
                self._error = f'Parse error: {e}'
                return

        self._saved = False
        self._status = 'Entry updated' if ns.edit_id is not None else 'Entry added'
        self._posix_named_cancel()

    def _posix_named_cancel(self):
        self._mode = _MODE_NORMAL
        self._posix_named = None

    def _validate_posix_id(self):
        ns = self._posix_named
        s = ns.id_str.strip()
        if not s:
            self._error = 'Identifier cannot be empty'
            return False
        if ns.id_numeric:
            try:
                int(s)
                return True
            except ValueError:
                self._error = 'Numeric ID must be an integer'
                return False
        kind = 'group' if ns.is_group else 'user'
        try:
            (grp.getgrnam if ns.is_group else pwd.getpwnam)(s)
            return True
        except KeyError:
            self._error = f'Unknown {kind}: {s!r}'
            return False

    def _posix_toggle_numeric(self):
        ns = self._posix_named
        s = ns.id_str.strip()
        if not ns.id_numeric:
            if s:
                try:
                    new_s = (str(_resolve_gid(s)) if ns.is_group else str(_resolve_uid(s)))
                    ns.id_str = new_s
                    ns.id_caret = len(new_s)
                except ValueError as e:
                    self._error = str(e)
                    return
            ns.id_numeric = True
        else:
            if s:
                try:
                    n = int(s)
                    new_s = (_name_of_gid(n, False) if ns.is_group else _name_of_uid(n, False))
                    ns.id_str = new_s
                    ns.id_caret = len(new_s)
                except (ValueError, KeyError):
                    self._error = f'Cannot resolve id: {s!r}'
                    return
            ns.id_numeric = False

    def _handle_posix_named_key(self, ch):
        ns = self._posix_named

        if ch in (_KEY_ESC, _KEY_CTRL_C):
            self._posix_named_cancel()
            self._draw()
            return None

        n_regions = len(_PosixNamedRegion) if self._ctx.is_dir else len(_PosixNamedRegion) - 1

        if ch in _ENTER_KEYS:
            if ns.region == _PosixNamedRegion.ID:
                if not self._validate_posix_id():
                    self._draw()
                    return None
            self._posix_named_finish()
            self._draw()
            return None

        if ch == ord('\t'):
            ns.region = _PosixNamedRegion((int(ns.region) + 1) % n_regions)
            ns.cursor = 0
            self._draw()
            return None

        if ch == curses.KEY_BTAB:
            ns.region = _PosixNamedRegion((int(ns.region) - 1) % n_regions)
            ns.cursor = 0
            self._draw()
            return None

        r = ns.region
        if r == _PosixNamedRegion.TYPE:
            if ch == curses.KEY_LEFT:
                ns.cursor = 0
                ns.is_group = False
            elif ch == curses.KEY_RIGHT:
                ns.cursor = 1
                ns.is_group = True
            elif ch == ord(' '):
                ns.is_group = not ns.is_group
                ns.cursor = 1 if ns.is_group else 0

        elif r == _PosixNamedRegion.ID:
            if ch == curses.KEY_LEFT:
                if ns.id_caret > 0:
                    ns.id_caret -= 1
            elif ch == curses.KEY_RIGHT:
                if ns.id_caret < len(ns.id_str):
                    ns.id_caret += 1
            elif ch == curses.KEY_HOME:
                ns.id_caret = 0
            elif ch == curses.KEY_END:
                ns.id_caret = len(ns.id_str)
            elif ch in _BACKSPACE_KEYS:
                if ns.id_caret > 0:
                    s = ns.id_str
                    ns.id_str = s[:ns.id_caret - 1] + s[ns.id_caret:]
                    ns.id_caret -= 1
            elif ch == curses.KEY_DC:
                if ns.id_caret < len(ns.id_str):
                    s = ns.id_str
                    ns.id_str = s[:ns.id_caret] + s[ns.id_caret + 1:]
            elif ch == _KEY_CTRL_T:
                self._posix_toggle_numeric()
            elif _PRINTABLE_FIRST <= ch <= _PRINTABLE_LAST:
                s = ns.id_str
                ns.id_str = s[:ns.id_caret] + chr(ch) + s[ns.id_caret:]
                ns.id_caret += 1

        elif r == _PosixNamedRegion.ACCESS:
            if ch == curses.KEY_LEFT:
                if ns.cursor > 0:
                    ns.cursor -= 1
            elif ch == curses.KEY_RIGHT:
                if ns.cursor < 2:
                    ns.cursor += 1
            elif ch == ord(' '):
                bit, _ = _POSIX_PERM_BITS[ns.cursor]
                if ns.access_perms & bit:
                    ns.access_perms = t.POSIXPerm(int(ns.access_perms) & ~int(bit))
                else:
                    ns.access_perms = t.POSIXPerm(int(ns.access_perms) | int(bit))

        elif r == _PosixNamedRegion.DEFAULT:
            if ch == curses.KEY_LEFT:
                if ns.cursor > 0:
                    ns.cursor -= 1
            elif ch == curses.KEY_RIGHT:
                if ns.cursor < 3:
                    ns.cursor += 1
            elif ch == ord(' '):
                if ns.cursor < 3:
                    bit, _ = _POSIX_PERM_BITS[ns.cursor]
                    if ns.default_perms & bit:
                        ns.default_perms = t.POSIXPerm(int(ns.default_perms) & ~int(bit))
                    else:
                        ns.default_perms = t.POSIXPerm(int(ns.default_perms) | int(bit))
                else:
                    ns.has_default = not ns.has_default

        self._draw()
        return None

    # ── normal-mode actions ───────────────────────────────────────────────────

    def _do_add(self):
        self._form_start(None, None)

    def _do_delete(self):
        n = len(self._ctx.aces)
        if n == 0:
            self._error = 'No entries to delete'
            return
        idx = self._cursor
        del self._ctx.aces[idx]
        n -= 1
        if n == 0:
            self._cursor = 0
            self._scroll = 0
        else:
            self._cursor = min(idx, n - 1)
            if self._cursor < self._scroll:
                self._scroll = self._cursor
        self._status = f'Entry {idx} deleted'
        self._saved = False

    def _do_edit(self):
        if not self._ctx.aces:
            self._error = 'No entries to edit'
            return
        self._form_start(self._ctx.aces[self._cursor], self._cursor)

    def _do_save(self):
        try:
            acl = self._build_acl()
            t.validate_acl(self._ctx.fd, acl)
            t.fsetacl(self._ctx.fd, acl)
        except (ValueError, OSError) as e:
            self._error = f'Save failed: {e}'
            return None
        self._saved = True
        self._status = 'Saved.'
        if self._ctx.is_dir:
            return self._prompt_recursive(acl)
        return None

    # ── recursive application ─────────────────────────────────────────────────

    def _prompt_recursive(self, acl):
        lines, cols = self._scr.getmaxyx()
        msg = 'Apply recursively? [y/N] '
        try:
            attr = (curses.color_pair(_CP_TITLE) if curses.has_colors() else curses.A_REVERSE)
            self._scr.addnstr(lines - 1, 0, (msg + ' ' * cols)[:cols - 1], cols - 1, attr)
        except curses.error:
            pass
        self._scr.refresh()
        curses.curs_set(0)
        ch = self._scr.getch()
        if ch in (ord('y'), ord('Y')):
            return self._apply_recursive(acl)
        self._status = 'Not applied recursively.'
        self._draw()
        return None

    def _apply_recursive(self, acl):
        lines, cols = self._scr.getmaxyx()
        left_w, right_w = _panel_widths(cols)
        right_col = left_w + 1
        content_start = 3

        try:
            mountpoint, fs_name, rel_path = _get_mount_info(self._ctx.path)
        except OSError as e:
            self._error = f'Recursive failed: {e}'
            self._draw()
            return None

        nfs4_inh = (_NFS4InheritedAcls.from_root(acl) if self._ctx.is_nfs4 else None)

        count = [0]
        errors = [0]

        def _progress_cb(dir_stack, state, _private):
            count[0] = state.cnt
            self._draw_progress_panel(right_col, right_w, content_start,
                                      state.current_directory, state.cnt)
            self._scr.refresh()

        try:
            with t.iter_filesystem_contents(
                    mountpoint, fs_name, relative_path=rel_path,
                    reporting_callback=_progress_cb,
                    reporting_increment=50) as it:
                for item in it:
                    if item.islnk:
                        continue
                    try:
                        if nfs4_inh is not None:
                            child_acl = nfs4_inh.pick(len(it.dir_stack()), item.isdir)
                        else:
                            child_acl = acl.generate_inherited_acl(is_dir=item.isdir)
                        t.fsetacl(item.fd, child_acl)
                    except (OSError, ValueError):
                        errors[0] += 1
        except OSError as e:
            self._error = f'Recursive error: {e}'

        self._draw_done_panel(right_col, right_w, content_start, count[0], errors[0])
        self._scr.refresh()
        self._scr.getch()
        self._status = (f'Applied to {count[0]} item(s)'
                        + (f', {errors[0]} error(s).' if errors[0] else '.'))
        self._draw()
        return None

    def _draw_progress_panel(self, col, width, row, current_dir, cnt):
        lines, _ = self._scr.getmaxyx()
        content = _content_rows(lines)
        for r in range(row, row + content):
            try:
                self._scr.addnstr(r, col, ' ' * width, width)
            except curses.error:
                pass
        try:
            self._scr.addnstr(row, col, 'Applying recursively...', width)
            self._scr.addnstr(row + 1, col, f'Items: {cnt}', width)
            self._scr.addnstr(row + 2, col, f'Dir: {current_dir}'[:width], width)
        except curses.error:
            pass

    def _draw_done_panel(self, col, width, row, cnt, errors):
        lines, _ = self._scr.getmaxyx()
        content = _content_rows(lines)
        for r in range(row, row + content):
            try:
                self._scr.addnstr(r, col, ' ' * width, width)
            except curses.error:
                pass
        try:
            self._scr.addnstr(row, col, 'Done!', width)
            self._scr.addnstr(row + 1, col, f'{cnt} item(s) processed.', width)
            if errors:
                attr = (curses.color_pair(_CP_ERROR) if curses.has_colors() else 0)
                self._scr.addnstr(row + 2, col, f'{errors} error(s) occurred.', width, attr)
            self._scr.addnstr(row + 3, col, 'Press any key to continue.', width)
        except curses.error:
            pass

    # ── text input-mode helpers (legacy) ──────────────────────────────────────

    def _input_start(self, prompt, prefill, edit_idx):
        self._mode = _MODE_INPUT
        self._inp = _InputState(prompt=prompt, buf=prefill, caret=len(prefill), edit_idx=edit_idx)
        self._error = ''

    def _input_finish(self):
        text = self._inp.buf.strip()
        try:
            ace = _parse_posix_ace(text)
        except ValueError as e:
            self._error = f'Parse error: {e}'
            return

        if self._inp.edit_idx is not None:
            self._ctx.aces[self._inp.edit_idx] = ace
            self._status = f'Entry {self._inp.edit_idx} updated'
        else:
            self._ctx.aces.append(ace)
            self._cursor = len(self._ctx.aces) - 1
            lines, _ = self._scr.getmaxyx()
            content = _content_rows(lines)
            if self._cursor >= self._scroll + content:
                self._scroll = self._cursor - content + 1
            self._status = 'Entry added'

        self._saved = False
        self._input_cancel()

    def _input_cancel(self):
        self._mode = _MODE_NORMAL
        self._inp = _InputState()

    # ── NFS4 ACE form helpers ─────────────────────────────────────────────────

    def _form_start(self, ace, edit_idx):
        self._mode = _MODE_ACE_FORM
        self._error = ''

        if ace is None:
            self._frm = _FormState(
                who_type=_NFS4WhoChoice.OWNER,
                who_id='',
                who_id_caret=0,
                who_numeric=False,
                who_in_id=False,
                allow=True,
                mask=t.NFS4Perm(int(_NFS4_PERM_SETS['modify_set'])),
                inh_flags=t.NFS4Flag(0),
                region=_FormRegion.WHO,
                cursor=0,
                edit_idx=None,
            )
        else:
            who_str = _nfs4_who_str(ace, numeric=False)
            match who_str:
                case 'owner@':
                    who_type, who_id = _NFS4WhoChoice.OWNER, ''
                case 'group@':
                    who_type, who_id = _NFS4WhoChoice.GROUP, ''
                case 'everyone@':
                    who_type, who_id = _NFS4WhoChoice.EVERYONE, ''
                case s if s.startswith('user:'):
                    who_type, who_id = _NFS4WhoChoice.USER, s[5:]
                case s:
                    who_type, who_id = _NFS4WhoChoice.GROUP_NAMED, s[6:]
            editable = ~(int(t.NFS4Flag.IDENTIFIER_GROUP) | int(t.NFS4Flag.INHERITED))
            self._frm = _FormState(
                who_type=who_type,
                who_id=who_id,
                who_id_caret=len(who_id),
                who_numeric=False,
                who_in_id=False,
                allow=(ace.ace_type == t.NFS4AceType.ALLOW),
                mask=ace.access_mask,
                inh_flags=t.NFS4Flag(int(ace.ace_flags) & editable),
                region=_FormRegion.WHO,
                cursor=int(who_type),
                edit_idx=edit_idx,
            )

    def _form_finish(self):
        if self._frm.who_type in (_NFS4WhoChoice.USER, _NFS4WhoChoice.GROUP_NAMED):
            if not self._validate_who_id():
                return

        match self._frm.who_type:
            case _NFS4WhoChoice.OWNER:
                who = 'owner@'
            case _NFS4WhoChoice.GROUP:
                who = 'group@'
            case _NFS4WhoChoice.EVERYONE:
                who = 'everyone@'
            case _NFS4WhoChoice.USER:
                who = f'user:{self._frm.who_id.strip()}'
            case _NFS4WhoChoice.GROUP_NAMED:
                who = f'group:{self._frm.who_id.strip()}'
            case _:
                who = 'owner@'

        if not who:
            self._error = 'Who field is empty'
            return

        perm_str = _nfs4_perm_str(self._frm.mask)
        inh_only_mask = (t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT |
                         t.NFS4Flag.NO_PROPAGATE_INHERIT | t.NFS4Flag.INHERIT_ONLY)
        flag_val = t.NFS4Flag(int(self._frm.inh_flags) & int(inh_only_mask))
        flag_str = ''.join(
            c if flag_val & bit else '-'
            for bit, c in _NFS4_FLAG_CHARS
            if bit in (t.NFS4Flag.FILE_INHERIT, t.NFS4Flag.DIRECTORY_INHERIT,
                       t.NFS4Flag.NO_PROPAGATE_INHERIT, t.NFS4Flag.INHERIT_ONLY,
                       t.NFS4Flag.SUCCESSFUL_ACCESS, t.NFS4Flag.FAILED_ACCESS,
                       t.NFS4Flag.INHERITED)
        )
        type_str = 'allow' if self._frm.allow else 'deny'
        text = f'{who}:{perm_str}:{flag_str}:{type_str}'

        try:
            ace = _parse_nfs4_ace(text)
        except ValueError as e:
            self._error = f'Parse error: {e}'
            return

        if self._frm.edit_idx is not None:
            self._ctx.aces[self._frm.edit_idx] = ace
            self._status = f'Entry {self._frm.edit_idx} updated'
        else:
            self._ctx.aces.append(ace)
            self._cursor = len(self._ctx.aces) - 1
            lines, _ = self._scr.getmaxyx()
            content = _content_rows(lines)
            if self._cursor >= self._scroll + content:
                self._scroll = self._cursor - content + 1
            self._status = 'Entry added'

        self._saved = False
        self._form_cancel()

    def _form_cancel(self):
        self._mode = _MODE_NORMAL
        self._frm = _FormState()
        self._posix_named = None

    # ── ACL helpers ───────────────────────────────────────────────────────────

    def _build_acl(self):
        if self._ctx.is_nfs4:
            return t.NFS4ACL.from_aces(self._ctx.aces, self._ctx.acl_flags)
        return t.POSIXACL.from_aces(self._ctx.aces)

    def _ace_str(self, i):
        ace = self._ctx.aces[i]
        return _ace_str_nfs4(ace) if self._ctx.is_nfs4 else _ace_str_posix(ace)

    def _preview_lines(self):
        if not self._ctx.is_dir:
            return ['Not a directory', '(no inheritance preview)']
        try:
            acl = self._build_acl()
        except Exception as e:
            return [f'[Build error: {e}]']

        lines = []
        for label, is_dir in (('Child dirs:', True), ('Child files:', False)):
            lines.append(label)
            try:
                inh = acl.generate_inherited_acl(is_dir=is_dir)
                for ace in inh.aces:
                    lines.append('  ' + _ace_str_nfs4(ace))
            except Exception as e:
                lines.append(f'  [Preview error: {e}]')
            lines.append('')
        return lines

    # ── NFS4 ACE form drawing ─────────────────────────────────────────────────

    def _draw_ace_form(self, right_col, right_w, content, has_color):
        sel_attr = curses.color_pair(_CP_SELECTED) if has_color else curses.A_REVERSE
        prev_attr = curses.color_pair(_CP_PREVIEW) if has_color else 0

        def put(row, text, attr=0):
            try:
                self._scr.addnstr(3 + row, right_col, text[:right_w], right_w, attr)
            except curses.error:
                pass

        def put_at(row, col, text, attr=0):
            try:
                self._scr.addnstr(3 + row, right_col + col, text[:right_w - col], right_w - col, attr)
            except curses.error:
                pass

        row = 0

        who_focused = (self._frm.region == _FormRegion.WHO)
        put(row, ' Who type:', prev_attr)
        row += 1

        num_who_rows = (len(_NFS4WhoChoice) + _FORM_WHO_COLS - 1) // _FORM_WHO_COLS
        for wi in range(len(_NFS4WhoChoice)):
            grid_row = wi // _FORM_WHO_COLS
            grid_col = wi % _FORM_WHO_COLS
            disp_row = row + grid_row
            col_off = 2 + grid_col * _FORM_WHO_STRIDE
            is_selected = (self._frm.who_type == _NFS4WhoChoice(wi))
            radio_mark = '(*)' if is_selected else '( )'
            label = _FORM_WHO_LABELS[wi]
            is_focused = who_focused and self._frm.cursor == wi and not self._frm.who_in_id
            attr = sel_attr if is_focused else 0
            put_at(disp_row, col_off, f'{radio_mark} {label}', attr)

        row += num_who_rows

        if self._frm.who_type in (_NFS4WhoChoice.USER, _NFS4WhoChoice.GROUP_NAMED):
            id_focused = who_focused and self._frm.who_in_id
            box_w = max(8, right_w - 26)
            id_disp = self._frm.who_id[:box_w]
            id_box = id_disp.ljust(box_w)
            mode_str = '(id)' if self._frm.who_numeric else '(name)'
            put(row, f' Identifier: [{id_box}] {mode_str}')
            if id_focused:
                caret = min(self._frm.who_id_caret, box_w - 1)
                char = id_box[caret] if caret < len(id_box) else ' '
                put_at(row, 14 + caret, char, sel_attr)
            row += 1

        row += 1

        type_focused = (self._frm.region == _FormRegion.TYPE)
        allow_mark = '(*)' if self._frm.allow else '( )'
        deny_mark = '( )' if self._frm.allow else '(*)'
        allow_attr = sel_attr if (type_focused and self._frm.cursor == 0) else 0
        deny_attr = sel_attr if (type_focused and self._frm.cursor == 1) else 0
        put(row, ' Type:  ')
        put_at(row, _FORM_ALLOW_COL, allow_mark, allow_attr)
        put_at(row, _FORM_ALLOW_TEXT_COL, ' allow  ')
        put_at(row, _FORM_DENY_COL, deny_mark, deny_attr)
        put_at(row, _FORM_DENY_TEXT_COL, ' deny')

        row += 1
        put(row, '')
        row += 1

        put(row, ' Permissions:', prev_attr)
        row += 1

        sets_focused = (self._frm.region == _FormRegion.SETS)
        for si, (sname, sbits) in enumerate(_FORM_PERM_SETS):
            col_in_row = si % _FORM_SETS_COLS
            display_row = row + (si // _FORM_SETS_COLS)
            checked = (int(self._frm.mask) & int(sbits)) == int(sbits)
            box = '[x]' if checked else '[ ]'
            focused = sets_focused and self._frm.cursor == si
            attr = sel_attr if focused else 0
            label = f'{box} {sname:<{_FORM_SETS_NAME_W}}'
            col_offset = _FORM_SETS_INDENT + col_in_row * _FORM_SETS_STRIDE
            put_at(display_row, col_offset, label, attr)

        row += len(_FORM_PERM_SETS) // _FORM_SETS_COLS
        put(row, '')
        row += 1

        bits_focused = (self._frm.region == _FormRegion.BITS)
        bit_rows = (len(_FORM_PERM_BITS) + _FORM_BITS_COLS - 1) // _FORM_BITS_COLS
        for bit_row in range(bit_rows):
            label_str = ' '
            for bi in range(_FORM_BITS_COLS):
                idx = bit_row * _FORM_BITS_COLS + bi
                label_str += f' {_FORM_PERM_LABELS[idx]}  '
            put(row, label_str[:right_w])
            row += 1
            for bi in range(_FORM_BITS_COLS):
                idx = bit_row * _FORM_BITS_COLS + bi
                bit = _FORM_PERM_BITS[idx]
                checked = bool(self._frm.mask & bit)
                box = '[x]' if checked else '[ ]'
                focused = bits_focused and self._frm.cursor == idx
                attr = sel_attr if focused else 0
                put_at(row, bi * _FORM_BITS_STRIDE, box, attr)
            row += 1

        put(row, '')
        row += 1

        put(row, ' Inherit:', prev_attr)
        row += 1

        inh_focused = (self._frm.region == _FormRegion.INHERIT)
        for fi, (fname, flag) in enumerate(_FORM_INHERIT_FLAGS):
            col_in_row = fi % _FORM_INHERIT_COLS
            display_row = row + (fi // _FORM_INHERIT_COLS)
            checked = bool(self._frm.inh_flags & flag)
            box = '[x]' if checked else '[ ]'
            focused = inh_focused and self._frm.cursor == fi
            attr = sel_attr if focused else 0
            label = f'{box} {fname:<{_FORM_INHERIT_NAME_W}}'
            col_offset = _FORM_INHERIT_INDENT + col_in_row * _FORM_INHERIT_STRIDE
            put_at(display_row, col_offset, label, attr)

    # ── POSIX structured editor drawing ──────────────────────────────────────

    def _draw_posix_editor(self, right_col, right_w, content, has_color):
        sel_attr = curses.color_pair(_CP_SELECTED) if has_color else curses.A_REVERSE
        prev_attr = curses.color_pair(_CP_PREVIEW) if has_color else 0

        named_base = 24 if self._ctx.is_dir else 12
        entries = self._posix_named_entries()

        def put(row, text, attr=0):
            if row >= content:
                return
            try:
                self._scr.addnstr(3 + row, right_col, text[:right_w], right_w, attr)
            except curses.error:
                pass

        def put_at(row, col, text, attr=0):
            if row >= content or col >= right_w:
                return
            try:
                self._scr.addnstr(3 + row, right_col + col, text[:right_w - col], right_w - col, attr)
            except curses.error:
                pass

        row = 0

        put(row, ' Access ACL       r  w  x', prev_attr)
        row += 1

        for ri, (tag, label) in enumerate(_POSIX_BASE_ROWS):
            base_c = ri * 3
            cursor_on_row = (self._posix_cursor >= base_c
                             and self._posix_cursor < base_c + 3
                             and self._posix_cursor < 12)
            indicator = '>' if cursor_on_row else ' '
            label_str = f'{label}:'
            put(row, f'{indicator} {label_str:<16}')
            ace = self._posix_find_ace(tag, False)
            perms = ace.perms if ace else t.POSIXPerm(0)
            for ci, (bit, _) in enumerate(_POSIX_PERM_BITS):
                checked = bool(perms & bit)
                box = '[x]' if checked else '[ ]'
                on_cell = cursor_on_row and (self._posix_cursor - base_c) == ci
                attr = sel_attr if on_cell else 0
                put_at(row, _POSIX_CHK_COL + ci * _POSIX_CHK_STRIDE, box, attr)
            row += 1

        row += 1

        if self._ctx.is_dir:
            put(row, ' Default ACL      r  w  x', prev_attr)
            row += 1

            for ri, (tag, label) in enumerate(_POSIX_BASE_ROWS):
                base_c = 12 + ri * 3
                cursor_on_row = (self._posix_cursor >= base_c
                                 and self._posix_cursor < base_c + 3)
                indicator = '>' if cursor_on_row else ' '
                label_str = f'{label}:'
                put(row, f'{indicator} {label_str:<16}')
                ace = self._posix_find_ace(tag, True)
                perms = ace.perms if ace else t.POSIXPerm(0)
                for ci, (bit, _) in enumerate(_POSIX_PERM_BITS):
                    checked = bool(perms & bit)
                    box = '[x]' if checked else '[ ]'
                    on_cell = cursor_on_row and (self._posix_cursor - base_c) == ci
                    attr = sel_attr if on_cell else 0
                    put_at(row, _POSIX_CHK_COL + ci * _POSIX_CHK_STRIDE, box, attr)
                row += 1

            row += 1

        put(row, ' Named entries:', prev_attr)
        row += 1

        if not entries:
            put(row, '  (none)')
            row += 1
        else:
            for k, (entry_id, is_group, access_perm, default_perm) in enumerate(entries):
                cursor_on = (self._posix_cursor == named_base + k)
                indicator = '>' if cursor_on else ' '
                name = (_name_of_gid(entry_id, False) if is_group
                        else _name_of_uid(entry_id, False))
                tag_str = 'grp' if is_group else 'user'
                a_str = _posix_perm_str(access_perm) if access_perm is not None else '---'
                d_str = _posix_perm_str(default_perm) if default_perm is not None else '---'
                line = f'{indicator} {name}({tag_str}):  {a_str} / {d_str}'
                attr = sel_attr if cursor_on else 0
                put(row, line, attr)
                row += 1

        put(row, ' [a]dd  [d]el  [e]dit')

    # ── POSIX named-entry form drawing ────────────────────────────────────────

    def _draw_posix_named_form(self, right_col, right_w, content, has_color):
        sel_attr = curses.color_pair(_CP_SELECTED) if has_color else curses.A_REVERSE
        prev_attr = curses.color_pair(_CP_PREVIEW) if has_color else 0
        ns = self._posix_named

        def put(row, text, attr=0):
            if row >= content:
                return
            try:
                self._scr.addnstr(3 + row, right_col, text[:right_w], right_w, attr)
            except curses.error:
                pass

        def put_at(row, col, text, attr=0):
            if row >= content or col >= right_w:
                return
            try:
                self._scr.addnstr(3 + row, right_col + col, text[:right_w - col], right_w - col, attr)
            except curses.error:
                pass

        row = 0

        title = 'Edit named entry:' if ns.edit_id is not None else 'Add named entry:'
        put(row, f' {title}', prev_attr)
        row += 1
        put(row, '')
        row += 1

        type_focused = (ns.region == _PosixNamedRegion.TYPE)
        user_mark = '(*)' if not ns.is_group else '( )'
        group_mark = '(*)' if ns.is_group else '( )'
        user_attr = sel_attr if (type_focused and ns.cursor == 0) else 0
        group_attr = sel_attr if (type_focused and ns.cursor == 1) else 0
        put(row, ' Type:  ')
        put_at(row, 8, user_mark, user_attr)
        put_at(row, 12, ' user  ')
        put_at(row, 19, group_mark, group_attr)
        put_at(row, 23, ' group')
        row += 1

        id_focused = (ns.region == _PosixNamedRegion.ID)
        box_w = max(8, right_w - 25)
        id_disp = ns.id_str[:box_w]
        id_box = id_disp.ljust(box_w)
        mode_str = '(id)' if ns.id_numeric else '(name)'
        put(row, f' Identifier: [{id_box}] {mode_str}')
        if id_focused:
            caret = min(ns.id_caret, box_w - 1)
            char = id_box[caret] if caret < len(id_box) else ' '
            put_at(row, 14 + caret, char, sel_attr)
        row += 1

        put(row, '')
        row += 1

        access_focused = (ns.region == _PosixNamedRegion.ACCESS)
        put(row, ' Access:  ')
        for ci, (bit, label) in enumerate(_POSIX_PERM_BITS):
            checked = bool(ns.access_perms & bit)
            box = '[x]' if checked else '[ ]'
            on_cell = access_focused and ns.cursor == ci
            cell_attr = sel_attr if on_cell else 0
            col = _POSIX_NAMED_CHK_COL + ci * _POSIX_NAMED_CHK_STRIDE
            put_at(row, col, box, cell_attr)
            put_at(row, col + 3, f' {label}  ')
        row += 1

        if self._ctx.is_dir:
            default_focused = (ns.region == _PosixNamedRegion.DEFAULT)
            put(row, ' Default: ')
            for ci, (bit, label) in enumerate(_POSIX_PERM_BITS):
                checked = bool(ns.default_perms & bit)
                box = '[x]' if checked else '[ ]'
                on_cell = default_focused and ns.cursor == ci
                cell_attr = sel_attr if on_cell else 0
                col = _POSIX_NAMED_CHK_COL + ci * _POSIX_NAMED_CHK_STRIDE
                put_at(row, col, box, cell_attr)
                put_at(row, col + 3, f' {label}  ')
            row += 1

            hd_box = '[x]' if ns.has_default else '[ ]'
            hd_focused = (ns.region == _PosixNamedRegion.DEFAULT and ns.cursor == 3)
            hd_attr = sel_attr if hd_focused else 0
            put(row, '          ')
            put_at(row, _POSIX_NAMED_CHK_COL, hd_box, hd_attr)
            put_at(row, _POSIX_NAMED_CHK_COL + 4, ' create default ACE')

    # ── drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        lines, cols = self._scr.getmaxyx()

        if lines < _MIN_LINES or cols < _MIN_COLS:
            self._scr.clear()
            msg = f'Terminal too small (min {_MIN_COLS}x{_MIN_LINES})'
            try:
                self._scr.addnstr(0, 0, msg, cols)
            except curses.error:
                pass
            self._scr.refresh()
            return

        self._scr.erase()
        left_w, right_w = _panel_widths(cols)
        right_col = left_w + 1
        content = _content_rows(lines)
        has_color = curses.has_colors()

        modified = '' if self._saved else ' [modified]'
        left_part = f' ACL Editor: {self._ctx.path}{modified}'
        right_part = (f' {self._ctx.fs_name} ' if self._ctx.fs_name else '')
        available = cols - 1
        if len(left_part) + len(right_part) <= available:
            title_pad = left_part + ' ' * (available - len(left_part) - len(right_part)) + right_part
        else:
            title_pad = (left_part + ' ' * available)[:available]
        try:
            attr = curses.color_pair(_CP_TITLE) if has_color else curses.A_REVERSE
            self._scr.addnstr(0, 0, title_pad, cols - 1, attr)
        except curses.error:
            pass

        if self._mode == _MODE_ACE_FORM and self._ctx.is_nfs4:
            right_header = ' Edit NFS4 ACE'
        elif self._mode == _MODE_ACE_FORM:
            right_header = ' Edit POSIX Entry'
        elif not self._ctx.is_nfs4:
            right_header = ' POSIX ACL'
        else:
            right_header = ' Inheritance Preview'

        try:
            self._scr.addnstr(1, 0, ' ACL Entries', left_w)
            self._scr.addnstr(1, left_w, '\u2502', 1)
            self._scr.addnstr(1, right_col, right_header, right_w)
        except curses.error:
            pass

        try:
            self._scr.addnstr(2, 0, '\u2500' * (cols - 1), cols - 1)
        except curses.error:
            pass

        n = len(self._ctx.aces)
        for i in range(content):
            scr_row = 3 + i
            ace_idx = self._scroll + i
            is_sel = (ace_idx == self._cursor) and self._ctx.is_nfs4

            if ace_idx < n:
                prefix = '> ' if is_sel else '  '
                text = prefix + self._ace_str(ace_idx)
                if is_sel:
                    attr = (curses.color_pair(_CP_SELECTED) if has_color else curses.A_STANDOUT)
                    try:
                        self._scr.addnstr(scr_row, 0, text, left_w, attr)
                    except curses.error:
                        pass
                else:
                    try:
                        self._scr.addnstr(scr_row, 0, text, left_w)
                    except curses.error:
                        pass

            try:
                self._scr.addnstr(scr_row, left_w, '\u2502', 1)
            except curses.error:
                pass

        if self._mode == _MODE_ACE_FORM:
            if self._ctx.is_nfs4:
                self._draw_ace_form(right_col, right_w, content, has_color)
            else:
                self._draw_posix_named_form(right_col, right_w, content, has_color)
        elif not self._ctx.is_nfs4:
            self._draw_posix_editor(right_col, right_w, content, has_color)
        else:
            preview = self._preview_lines()
            attr_p = curses.color_pair(_CP_PREVIEW) if has_color else 0
            for i, pline in enumerate(preview):
                if i >= content:
                    break
                try:
                    self._scr.addnstr(3 + i, right_col, pline, right_w, attr_p)
                except curses.error:
                    pass

        help_row = lines - 2
        if self._mode == _MODE_ACE_FORM and self._ctx.is_nfs4:
            help_text = ' Tab:next  Arrows:nav  Space:toggle  Enter:apply  Esc:cancel  Ctrl-T:id/name'
        elif self._mode == _MODE_ACE_FORM:
            help_text = ' Tab:region  Space:toggle  Ctrl-T:id/name  Enter:apply  Esc:cancel'
        elif self._mode == _MODE_NORMAL and not self._ctx.is_nfs4:
            help_text = ' Arrows:nav  Space:toggle  [a]dd [d]el [e]dit  [s]ave [q]uit'
        elif self._mode == _MODE_NORMAL:
            help_text = ' [a]dd [d]el [e]dit [s]ave [q]uit'
        else:
            help_text = ' Enter:confirm  Esc:cancel  \u2190\u2192:move  BS:delete'
        help_pad = (help_text + ' ' * cols)[:cols - 1]
        try:
            attr = curses.color_pair(_CP_TITLE) if has_color else curses.A_REVERSE
            self._scr.addnstr(help_row, 0, help_pad, cols - 1, attr)
        except curses.error:
            pass

        status_row = lines - 1
        if self._mode == _MODE_INPUT:
            input_line = self._inp.prompt + self._inp.buf
            try:
                self._scr.addnstr(status_row, 0, input_line, cols - 1)
            except curses.error:
                pass
            caret_col = min(len(self._inp.prompt) + self._inp.caret, cols - 1)
            try:
                curses.curs_set(1)
                self._scr.move(status_row, caret_col)
            except curses.error:
                pass
        elif self._error:
            attr = curses.color_pair(_CP_ERROR) if has_color else 0
            try:
                self._scr.addnstr(status_row, 0, self._error, cols - 1, attr)
            except curses.error:
                pass
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        else:
            if self._status:
                try:
                    self._scr.addnstr(status_row, 0, self._status, cols - 1)
                except curses.error:
                    pass
            elif self._ctx.warn:
                attr = (curses.color_pair(_CP_WARN) | curses.A_BOLD) if has_color else 0
                try:
                    self._scr.addnstr(status_row, 0, self._ctx.warn, cols - 1, attr)
                except curses.error:
                    pass
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        self._scr.refresh()

    def _handle_resize(self):
        lines, cols = self._scr.getmaxyx()
        if lines < _MIN_LINES or cols < _MIN_COLS:
            self._scr.clear()
            msg = f'Terminal too small (min {_MIN_COLS}x{_MIN_LINES})'
            try:
                self._scr.addnstr(0, 0, msg, cols)
            except curses.error:
                pass
            self._scr.refresh()
            return None

        content = _content_rows(lines)
        if self._cursor >= self._scroll + content:
            self._scroll = max(0, self._cursor - content + 1)
        elif self._cursor < self._scroll:
            self._scroll = self._cursor
        self._draw()
        return None


# ── public entry point ────────────────────────────────────────────────────────

def interactive_edit(path):
    """Open *path*, launch the curses ACL editor, return exit code."""
    if not sys.stdout.isatty():
        print('truenas_setfacl: -e requires an interactive terminal', file=sys.stderr)
        return 1

    try:
        ts = os.get_terminal_size()
        if ts.columns < _MIN_COLS or ts.lines < _MIN_LINES:
            print(
                f'truenas_setfacl: terminal too small '
                f'(need {_MIN_COLS}x{_MIN_LINES}, '
                f'got {ts.columns}x{ts.lines})',
                file=sys.stderr,
            )
            return 1
    except OSError:
        pass

    try:
        fd = t.openat2(path, flags=os.O_RDONLY, resolve=t.RESOLVE_NO_SYMLINKS)
    except OSError as e:
        print(f'truenas_setfacl: {path}: {e}', file=sys.stderr)
        return 1

    try:
        acl = t.fgetacl(fd)
        st = os.fstat(fd)
        is_dir = stat.S_ISDIR(st.st_mode)
        is_nfs4 = isinstance(acl, t.NFS4ACL)

        if is_nfs4:
            aces = list(acl.aces)
            acl_flags = acl.acl_flags
        else:
            if not acl.aces:
                acl = t.POSIXACL.from_aces(
                    list(_make_trivial_posix(st.st_mode).aces) + list(acl.default_aces)
                )
            aces = list(acl.aces) + list(acl.default_aces)
            acl_flags = None

        try:
            _, fs_name, _ = _get_mount_info(path)
        except OSError:
            fs_name = ''

        abs_path = os.path.realpath(path)
        if abs_path == '/mnt' or abs_path.startswith('/mnt/'):
            warn = ''
        else:
            warn = 'Warning: permissions changes outside of /mnt are not supported'

        ctx = _EditContext(
            path=path,
            fd=fd,
            is_dir=is_dir,
            is_nfs4=is_nfs4,
            aces=aces,
            acl_flags=acl_flags,
            fs_name=fs_name,
            warn=warn,
        )

        rc = curses.wrapper(lambda stdscr: AclEditor(stdscr, ctx).run())
        return rc if rc is not None else 0

    except OSError as e:
        print(f'truenas_setfacl: {path}: {e}', file=sys.stderr)
        return 1
    finally:
        os.close(fd)
