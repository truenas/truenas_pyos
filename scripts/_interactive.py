# SPDX-License-Identifier: LGPL-3.0-or-later

import curses
import dataclasses
import os
import stat
import sys

import truenas_os as t

from ._getfacl import (
    _nfs4_perm_str, _nfs4_flag_str, _nfs4_who_str, _NFS4_TYPE_STR,
    _posix_perm_str, _posix_qualifier, _POSIX_TAG_PREFIX,
    _NFS4_PERM_CHARS, _NFS4_FLAG_CHARS,
)
from ._setfacl import (
    _parse_nfs4_ace, _parse_posix_ace,
    _NFS4InheritedAcls, _get_mount_info, _make_trivial_posix,
    _NFS4_PERM_SETS,
)


_MIN_COLS, _MIN_LINES = 80, 20

# Color pair indices
_CP_TITLE, _CP_SELECTED, _CP_ERROR, _CP_PREVIEW, _CP_WARN = 1, 2, 3, 4, 5

_MODE_NORMAL = 'NORMAL'
_MODE_INPUT = 'INPUT'
_MODE_ACE_FORM = 'ACE_FORM'

# ACE form focus regions
_FR_WHO = 0
_FR_TYPE = 1
_FR_SETS = 2
_FR_BITS = 3
_FR_INHERIT = 4
_FR_COUNT = 5

# Permission sets shown in the form (ordered by preference)
_FORM_PERM_SETS = [
    ('full_set',   _NFS4_PERM_SETS['full_set']),
    ('modify_set', _NFS4_PERM_SETS['modify_set']),
    ('read_set',   _NFS4_PERM_SETS['read_set']),
    ('write_set',  _NFS4_PERM_SETS['write_set']),
]

# Inherit flags shown in the form (audit/access flags omitted)
_FORM_INHERIT_FLAGS = [
    ('file',     t.NFS4Flag.FILE_INHERIT),
    ('dir',      t.NFS4Flag.DIRECTORY_INHERIT),
    ('no_prop',  t.NFS4Flag.NO_PROPAGATE_INHERIT),
    ('inh_only', t.NFS4Flag.INHERIT_ONLY),
]

# Individual permission bits in canonical order
_FORM_PERM_BITS = [bit for bit, _ in _NFS4_PERM_CHARS]
_FORM_PERM_LABELS = [c for _, c in _NFS4_PERM_CHARS]


def _panel_widths(cols):
    """Return (left_w, right_w) with 1-char separator between them."""
    left_w = cols // 2
    right_w = cols - left_w - 1
    return left_w, right_w


def _content_rows(lines):
    """Rows available for scrollable content (total minus 5 fixed rows)."""
    return max(0, lines - 5)


def _ace_str_nfs4(ace):
    """Format NFS4Ace as canonical who:perms:flags:type text (names resolved)."""
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


@dataclasses.dataclass
class _EditContext:
    path: str
    fd: int        # O_RDONLY fd; closed by interactive_edit() after curses exits
    is_dir: bool
    is_nfs4: bool
    aces: list       # list[NFS4Ace] | list[POSIXAce]
    acl_flags: object     # NFS4ACLFlag or None for POSIX
    fs_name: str = ''
    warn: str = ''
    rc: int = 0


class AclEditor:

    def __init__(self, stdscr, ctx: _EditContext):
        self._scr = stdscr
        self._ctx = ctx
        self._cursor = 0
        self._scroll = 0
        self._mode = _MODE_NORMAL
        self._status = ''
        self._error = ''
        # Text input mode state (POSIX ACEs)
        self._input_buf = ''
        self._input_caret = 0
        self._input_prompt = ''
        self._input_edit_idx = None
        # ACE form state (NFS4 ACEs)
        self._form_who = ''
        self._form_who_caret = 0
        self._form_allow = True
        self._form_mask = t.NFS4Perm(0)
        self._form_inh_flags = t.NFS4Flag(0)
        self._form_region = _FR_WHO
        self._form_cursor = 0
        self._form_edit_idx = None
        # Start clean
        self._saved = True

    # ── public entry ──────────────────────────────────────────────────────────

    def run(self) -> int:
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

        elif ch in (ord('e'), curses.KEY_ENTER, 10, 13):
            self._do_edit()

        elif ch in (ord('s'), ord('w')):
            result = self._do_save()
            if result is not None:
                return result

        elif ch in (ord('q'), 27):
            return 0

        elif ch == 3:
            return 0

        self._draw()
        return None

    def _handle_input_key(self, ch):
        """Text input handler for POSIX ACE editing."""
        if ch in (27, 3):
            self._input_cancel()
            self._draw()
            return None

        if ch in (curses.KEY_ENTER, 10, 13):
            self._input_finish()
            self._draw()
            return None

        if ch == curses.KEY_LEFT:
            if self._input_caret > 0:
                self._input_caret -= 1

        elif ch == curses.KEY_RIGHT:
            if self._input_caret < len(self._input_buf):
                self._input_caret += 1

        elif ch == curses.KEY_HOME:
            self._input_caret = 0

        elif ch == curses.KEY_END:
            self._input_caret = len(self._input_buf)

        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if self._input_caret > 0:
                b = self._input_buf
                self._input_buf = b[:self._input_caret - 1] + b[self._input_caret:]
                self._input_caret -= 1

        elif ch == curses.KEY_DC:
            if self._input_caret < len(self._input_buf):
                b = self._input_buf
                self._input_buf = b[:self._input_caret] + b[self._input_caret + 1:]

        elif 32 <= ch <= 126:
            b = self._input_buf
            self._input_buf = b[:self._input_caret] + chr(ch) + b[self._input_caret:]
            self._input_caret += 1

        self._draw()
        return None

    def _handle_ace_form_key(self, ch):
        """Key handler for the NFS4 ACE structured form."""
        if ch in (27, 3):
            self._form_cancel()
            self._draw()
            return None

        if ch in (curses.KEY_ENTER, 10, 13):
            self._form_finish()
            self._draw()
            return None

        if ch == curses.KEY_BTAB:
            self._form_region = (self._form_region - 1) % _FR_COUNT
            self._form_cursor = 0
            self._draw()
            return None

        if ch == ord('\t'):
            self._form_region = (self._form_region + 1) % _FR_COUNT
            self._form_cursor = 0
            self._draw()
            return None

        r = self._form_region

        if r == _FR_WHO:
            self._form_key_who(ch)
        elif r == _FR_TYPE:
            self._form_key_type(ch)
        elif r == _FR_SETS:
            self._form_key_sets(ch)
        elif r == _FR_BITS:
            self._form_key_bits(ch)
        elif r == _FR_INHERIT:
            self._form_key_inherit(ch)

        self._draw()
        return None

    # ── form key handlers per region ─────────────────────────────────────────

    def _form_key_who(self, ch):
        if ch == curses.KEY_LEFT:
            if self._form_who_caret > 0:
                self._form_who_caret -= 1
        elif ch == curses.KEY_RIGHT:
            if self._form_who_caret < len(self._form_who):
                self._form_who_caret += 1
        elif ch == curses.KEY_HOME:
            self._form_who_caret = 0
        elif ch == curses.KEY_END:
            self._form_who_caret = len(self._form_who)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if self._form_who_caret > 0:
                w = self._form_who
                self._form_who = w[:self._form_who_caret - 1] + w[self._form_who_caret:]
                self._form_who_caret -= 1
        elif ch == curses.KEY_DC:
            if self._form_who_caret < len(self._form_who):
                w = self._form_who
                self._form_who = w[:self._form_who_caret] + w[self._form_who_caret + 1:]
        elif ch in (curses.KEY_DOWN,):
            self._form_region = _FR_TYPE
            self._form_cursor = 0
        elif ch in (curses.KEY_UP,):
            self._form_region = _FR_INHERIT
            self._form_cursor = 0
        elif 32 <= ch <= 126:
            w = self._form_who
            self._form_who = w[:self._form_who_caret] + chr(ch) + w[self._form_who_caret:]
            self._form_who_caret += 1

    def _form_key_type(self, ch):
        if ch in (curses.KEY_LEFT,):
            self._form_cursor = 0
            self._form_allow = True
        elif ch in (curses.KEY_RIGHT,):
            self._form_cursor = 1
            self._form_allow = False
        elif ch == ord(' '):
            self._form_allow = not self._form_allow
            self._form_cursor = 0 if self._form_allow else 1
        elif ch == curses.KEY_UP:
            self._form_region = _FR_WHO
            self._form_cursor = 0
        elif ch == curses.KEY_DOWN:
            self._form_region = _FR_SETS
            self._form_cursor = 0

    def _form_key_sets(self, ch):
        c = self._form_cursor
        if ch == curses.KEY_LEFT:
            if c % 2 == 1:
                self._form_cursor -= 1
        elif ch == curses.KEY_RIGHT:
            if c % 2 == 0 and c < 3:
                self._form_cursor += 1
        elif ch == curses.KEY_UP:
            if c >= 2:
                self._form_cursor -= 2
            else:
                self._form_region = _FR_TYPE
                self._form_cursor = 0
        elif ch == curses.KEY_DOWN:
            if c <= 1:
                self._form_cursor += 2
            else:
                self._form_region = _FR_BITS
                self._form_cursor = 0
        elif ch == ord(' '):
            _, set_bits = _FORM_PERM_SETS[self._form_cursor]
            if (int(self._form_mask) & int(set_bits)) == int(set_bits):
                self._form_mask = t.NFS4Perm(int(self._form_mask) & ~int(set_bits))
            else:
                self._form_mask = t.NFS4Perm(int(self._form_mask) | int(set_bits))

    def _form_key_bits(self, ch):
        c = self._form_cursor
        if ch == curses.KEY_LEFT:
            if c % 7 > 0:
                self._form_cursor -= 1
        elif ch == curses.KEY_RIGHT:
            if c % 7 < 6 and c < 13:
                self._form_cursor += 1
        elif ch == curses.KEY_UP:
            if c >= 7:
                self._form_cursor -= 7
            else:
                self._form_region = _FR_SETS
                self._form_cursor = 2  # bottom row of sets
        elif ch == curses.KEY_DOWN:
            if c < 7:
                self._form_cursor += 7
            else:
                self._form_region = _FR_INHERIT
                self._form_cursor = 0
        elif ch == ord(' '):
            bit = _FORM_PERM_BITS[self._form_cursor]
            if self._form_mask & bit:
                self._form_mask = t.NFS4Perm(int(self._form_mask) & ~int(bit))
            else:
                self._form_mask = t.NFS4Perm(int(self._form_mask) | int(bit))

    def _form_key_inherit(self, ch):
        c = self._form_cursor
        if ch == curses.KEY_LEFT:
            if c % 2 == 1:
                self._form_cursor -= 1
        elif ch == curses.KEY_RIGHT:
            if c % 2 == 0 and c < 3:
                self._form_cursor += 1
        elif ch == curses.KEY_UP:
            if c >= 2:
                self._form_cursor -= 2
            else:
                self._form_region = _FR_BITS
                self._form_cursor = 7  # top of bits row 1
        elif ch == curses.KEY_DOWN:
            if c <= 1:
                self._form_cursor += 2
            else:
                self._form_region = _FR_WHO
                self._form_cursor = 0
        elif ch == ord(' '):
            _, flag = _FORM_INHERIT_FLAGS[self._form_cursor]
            if self._form_inh_flags & flag:
                self._form_inh_flags = t.NFS4Flag(int(self._form_inh_flags) & ~int(flag))
            else:
                self._form_inh_flags = t.NFS4Flag(int(self._form_inh_flags) | int(flag))

    # ── normal-mode actions ───────────────────────────────────────────────────

    def _do_add(self):
        if self._ctx.is_nfs4:
            self._form_start(None, None)
        else:
            self._input_start('Add POSIX ACE ([default:]tag:qualifier:perms): ', '', None)

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
        idx = self._cursor
        if self._ctx.is_nfs4:
            self._form_start(self._ctx.aces[idx], idx)
        else:
            prefill = self._ace_str(idx)
            self._input_start('Edit POSIX ACE: ', prefill, idx)

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
            attr = (curses.color_pair(_CP_TITLE) if curses.has_colors()
                    else curses.A_REVERSE)
            self._scr.addnstr(lines - 1, 0,
                              (msg + ' ' * cols)[:cols - 1],
                              cols - 1, attr)
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

        nfs4_inh = (
            _NFS4InheritedAcls.from_root(acl) if self._ctx.is_nfs4 else None
        )

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
                            child_acl = nfs4_inh.pick(
                                len(it.dir_stack()), item.isdir)
                        else:
                            child_acl = acl.generate_inherited_acl(
                                is_dir=item.isdir)
                        t.fsetacl(item.fd, child_acl)
                    except (OSError, ValueError):
                        errors[0] += 1
        except OSError as e:
            self._error = f'Recursive error: {e}'

        self._draw_done_panel(right_col, right_w, content_start,
                              count[0], errors[0])
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
            self._scr.addnstr(row + 2, col,
                              f'Dir: {current_dir}'[:width], width)
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
                attr = (curses.color_pair(_CP_ERROR) if curses.has_colors()
                        else 0)
                self._scr.addnstr(row + 2, col,
                                  f'{errors} error(s) occurred.', width, attr)
            self._scr.addnstr(row + 3, col, 'Press any key to continue.', width)
        except curses.error:
            pass

    # ── text input-mode helpers (POSIX) ───────────────────────────────────────

    def _input_start(self, prompt, prefill, edit_idx):
        self._mode = _MODE_INPUT
        self._input_prompt = prompt
        self._input_buf = prefill
        self._input_caret = len(prefill)
        self._input_edit_idx = edit_idx
        self._error = ''

    def _input_finish(self):
        text = self._input_buf.strip()
        try:
            ace = _parse_posix_ace(text)
        except ValueError as e:
            self._error = f'Parse error: {e}'
            return

        if self._input_edit_idx is not None:
            self._ctx.aces[self._input_edit_idx] = ace
            self._status = f'Entry {self._input_edit_idx} updated'
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
        self._input_buf = ''
        self._input_caret = 0
        self._input_prompt = ''
        self._input_edit_idx = None

    # ── ACE form helpers (NFS4) ───────────────────────────────────────────────

    def _form_start(self, ace, edit_idx):
        """Enter ACE form mode, pre-filling from *ace* or using defaults."""
        self._mode = _MODE_ACE_FORM
        self._form_edit_idx = edit_idx
        self._form_region = _FR_WHO
        self._form_cursor = 0
        self._error = ''

        if ace is None:
            self._form_who = 'owner@'
            self._form_who_caret = len(self._form_who)
            self._form_allow = True
            self._form_mask = t.NFS4Perm(int(_NFS4_PERM_SETS['modify_set']))
            self._form_inh_flags = t.NFS4Flag(0)
        else:
            self._form_who = _nfs4_who_str(ace, numeric=False)
            self._form_who_caret = len(self._form_who)
            self._form_allow = (ace.ace_type == t.NFS4AceType.ALLOW)
            self._form_mask = ace.access_mask
            # Strip non-editable flags (IDENTIFIER_GROUP set by who, INHERITED by kernel)
            editable = ~(int(t.NFS4Flag.IDENTIFIER_GROUP) | int(t.NFS4Flag.INHERITED))
            self._form_inh_flags = t.NFS4Flag(int(ace.ace_flags) & editable)

    def _form_finish(self):
        """Build NFS4Ace from form state and add/replace in the ACE list."""
        who = self._form_who.strip()
        if not who:
            self._error = 'Who field is empty'
            return

        perm_str = _nfs4_perm_str(self._form_mask)
        # Only include editable inherit flags in the flag string; omit INHERITED
        inh_only_mask = (t.NFS4Flag.FILE_INHERIT | t.NFS4Flag.DIRECTORY_INHERIT |
                         t.NFS4Flag.NO_PROPAGATE_INHERIT | t.NFS4Flag.INHERIT_ONLY)
        flag_val = t.NFS4Flag(int(self._form_inh_flags) & int(inh_only_mask))
        flag_str = ''.join(
            c if flag_val & bit else '-'
            for bit, c in _NFS4_FLAG_CHARS
            if bit in (t.NFS4Flag.FILE_INHERIT, t.NFS4Flag.DIRECTORY_INHERIT,
                       t.NFS4Flag.NO_PROPAGATE_INHERIT, t.NFS4Flag.INHERIT_ONLY,
                       t.NFS4Flag.SUCCESSFUL_ACCESS, t.NFS4Flag.FAILED_ACCESS,
                       t.NFS4Flag.INHERITED)
        )
        type_str = 'allow' if self._form_allow else 'deny'
        text = f'{who}:{perm_str}:{flag_str}:{type_str}'

        try:
            ace = _parse_nfs4_ace(text)
        except ValueError as e:
            self._error = f'Parse error: {e}'
            return

        if self._form_edit_idx is not None:
            self._ctx.aces[self._form_edit_idx] = ace
            self._status = f'Entry {self._form_edit_idx} updated'
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
        self._form_edit_idx = None

    # ── ACL helpers ───────────────────────────────────────────────────────────

    def _build_acl(self):
        if self._ctx.is_nfs4:
            return t.NFS4ACL.from_aces(self._ctx.aces, self._ctx.acl_flags)
        return t.POSIXACL.from_aces(self._ctx.aces)

    def _ace_str(self, i):
        ace = self._ctx.aces[i]
        return _ace_str_nfs4(ace) if self._ctx.is_nfs4 else _ace_str_posix(ace)

    def _preview_lines(self):
        """Generate lines for the right panel (inheritance preview)."""
        if not self._ctx.is_dir:
            return ['Not a directory', '(no inheritance preview)']
        try:
            acl = self._build_acl()
        except Exception as e:
            return [f'[Build error: {e}]']

        lines = []
        if self._ctx.is_nfs4:
            for label, is_dir in (('Child dirs:', True), ('Child files:', False)):
                lines.append(label)
                try:
                    inh = acl.generate_inherited_acl(is_dir=is_dir)
                    for ace in inh.aces:
                        lines.append('  ' + _ace_str_nfs4(ace))
                except Exception as e:
                    lines.append(f'  [Preview error: {e}]')
                lines.append('')
        else:
            lines.append('Inherited ACL:')
            try:
                inh = acl.generate_inherited_acl(is_dir=True)
                for ace in inh.aces:
                    lines.append('  ' + _ace_str_posix(ace))
                for ace in inh.default_aces:
                    lines.append('  ' + _ace_str_posix(ace))
            except Exception as e:
                lines.append(f'  [Preview error: {e}]')
        return lines

    # ── ACE form drawing ──────────────────────────────────────────────────────

    def _draw_ace_form(self, right_col, right_w, content, has_color):
        """Render the NFS4 ACE structured form into the right panel."""
        sel_attr = curses.color_pair(_CP_SELECTED) if has_color else curses.A_REVERSE
        prev_attr = curses.color_pair(_CP_PREVIEW) if has_color else 0

        def put(row, text, attr=0):
            try:
                self._scr.addnstr(3 + row, right_col, text[:right_w], right_w, attr)
            except curses.error:
                pass

        def put_at(row, col, text, attr=0):
            abs_col = right_col + col
            try:
                self._scr.addnstr(3 + row, abs_col, text[:right_w - col], right_w - col, attr)
            except curses.error:
                pass

        row = 0

        # ── Who field ──
        who_focused = (self._form_region == _FR_WHO)
        box_w = max(10, right_w - 8)
        who_disp = self._form_who[:box_w]
        who_box = who_disp.ljust(box_w)
        put(row, f' Who:  [{who_box}]')
        # Show caret inside who box
        if who_focused:
            caret = min(self._form_who_caret, box_w - 1)
            char = who_box[caret] if caret < len(who_box) else ' '
            put_at(row, 8 + caret, char, sel_attr)

        row += 1

        # ── Type field ──
        type_focused = (self._form_region == _FR_TYPE)
        allow_mark = '(*)' if self._form_allow else '( )'
        deny_mark = '( )' if self._form_allow else '(*)'
        allow_attr = sel_attr if (type_focused and self._form_cursor == 0) else 0
        deny_attr = sel_attr if (type_focused and self._form_cursor == 1) else 0
        put(row, ' Type:  ')
        put_at(row, 8, allow_mark, allow_attr)
        put_at(row, 12, ' allow  ')
        put_at(row, 20, deny_mark, deny_attr)
        put_at(row, 24, ' deny')

        row += 1
        put(row, '')
        row += 1

        # ── Permission sets ──
        put(row, ' Permissions:', prev_attr)
        row += 1

        sets_focused = (self._form_region == _FR_SETS)
        for si, (sname, sbits) in enumerate(_FORM_PERM_SETS):
            col_in_row = si % 2
            display_row = row + (si // 2)
            checked = (int(self._form_mask) & int(sbits)) == int(sbits)
            box = '[x]' if checked else '[ ]'
            focused = sets_focused and self._form_cursor == si
            attr = sel_attr if focused else 0
            label = f'{box} {sname:<10}'
            col_offset = 2 + col_in_row * 16
            put_at(display_row, col_offset, label, attr)

        row += 2
        put(row, '')
        row += 1

        # ── Individual bits ──
        bits_focused = (self._form_region == _FR_BITS)
        for bit_row in range(2):
            # Label row
            label_str = ' '
            for bi in range(7):
                idx = bit_row * 7 + bi
                label_str += f' {_FORM_PERM_LABELS[idx]}  '
            put(row, label_str[:right_w])
            row += 1
            # Checkbox row
            check_str = ''
            for bi in range(7):
                idx = bit_row * 7 + bi
                bit = _FORM_PERM_BITS[idx]
                checked = bool(self._form_mask & bit)
                box = '[x]' if checked else '[ ]'
                focused = bits_focused and self._form_cursor == idx
                if focused:
                    put_at(row, bi * 4, box, sel_attr)
                else:
                    check_str += box + ' '
            if not bits_focused or not any(
                self._form_cursor == bit_row * 7 + bi for bi in range(7)
            ):
                put(row, check_str[:right_w])
            else:
                # Draw non-focused boxes without highlight
                for bi in range(7):
                    idx = bit_row * 7 + bi
                    bit = _FORM_PERM_BITS[idx]
                    checked = bool(self._form_mask & bit)
                    box = '[x]' if checked else '[ ]'
                    focused = bits_focused and self._form_cursor == idx
                    attr = sel_attr if focused else 0
                    put_at(row, bi * 4, box, attr)
            row += 1

        put(row, '')
        row += 1

        # ── Inherit flags ──
        put(row, ' Inherit:', prev_attr)
        row += 1

        inh_focused = (self._form_region == _FR_INHERIT)
        for fi, (fname, flag) in enumerate(_FORM_INHERIT_FLAGS):
            col_in_row = fi % 2
            display_row = row + (fi // 2)
            checked = bool(self._form_inh_flags & flag)
            box = '[x]' if checked else '[ ]'
            focused = inh_focused and self._form_cursor == fi
            attr = sel_attr if focused else 0
            label = f'{box} {fname:<9}'
            col_offset = 2 + col_in_row * 14
            put_at(display_row, col_offset, label, attr)

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

        # ── Row 0: title bar ──────────────────────────────────────────────────
        modified = '' if self._saved else ' [modified]'
        left_part = f' ACL Editor: {self._ctx.path}{modified}'
        right_part = (f' {self._ctx.fs_name} ' if self._ctx.fs_name else '')
        available = cols - 1
        if len(left_part) + len(right_part) <= available:
            title_pad = (left_part
                         + ' ' * (available - len(left_part) - len(right_part))
                         + right_part)
        else:
            title_pad = (left_part + ' ' * available)[:available]
        try:
            attr = curses.color_pair(_CP_TITLE) if has_color else curses.A_REVERSE
            self._scr.addnstr(0, 0, title_pad, cols - 1, attr)
        except curses.error:
            pass

        # ── Row 1: panel headers ──────────────────────────────────────────────
        right_header = ' Edit NFS4 ACE' if self._mode == _MODE_ACE_FORM else ' Inheritance Preview'
        try:
            self._scr.addnstr(1, 0, ' ACL Entries', left_w)
            self._scr.addnstr(1, left_w, '\u2502', 1)
            self._scr.addnstr(1, right_col, right_header, right_w)
        except curses.error:
            pass

        # ── Row 2: horizontal separator ───────────────────────────────────────
        try:
            self._scr.addnstr(2, 0, '\u2500' * (cols - 1), cols - 1)
        except curses.error:
            pass

        # ── Rows 3…N-3: ACE list (left) ──────────────────────────────────────
        n = len(self._ctx.aces)
        for i in range(content):
            row = 3 + i
            ace_idx = self._scroll + i
            is_sel = (ace_idx == self._cursor)

            if ace_idx < n:
                prefix = '> ' if is_sel else '  '
                text = prefix + self._ace_str(ace_idx)
                if is_sel:
                    attr = (curses.color_pair(_CP_SELECTED) if has_color
                            else curses.A_STANDOUT)
                    try:
                        self._scr.addnstr(row, 0, text, left_w, attr)
                    except curses.error:
                        pass
                else:
                    try:
                        self._scr.addnstr(row, 0, text, left_w)
                    except curses.error:
                        pass

            try:
                self._scr.addnstr(row, left_w, '\u2502', 1)
            except curses.error:
                pass

        # ── Right panel: form or preview ──────────────────────────────────────
        if self._mode == _MODE_ACE_FORM:
            self._draw_ace_form(right_col, right_w, content, has_color)
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

        # ── Row N-2: help bar ─────────────────────────────────────────────────
        help_row = lines - 2
        if self._mode == _MODE_ACE_FORM:
            help_text = ' Tab:next  Arrows:nav  Space:toggle  Enter:apply  Esc:cancel'
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

        # ── Row N-1: status / error / input line ──────────────────────────────
        status_row = lines - 1
        if self._mode == _MODE_INPUT:
            input_line = self._input_prompt + self._input_buf
            try:
                self._scr.addnstr(status_row, 0, input_line, cols - 1)
            except curses.error:
                pass
            caret_col = min(len(self._input_prompt) + self._input_caret, cols - 1)
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

def interactive_edit(path: str) -> int:
    """Open *path*, launch the curses ACL editor, return exit code."""
    if not sys.stdout.isatty():
        print('truenas_setfacl: -e requires an interactive terminal',
              file=sys.stderr)
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
                    list(_make_trivial_posix(st.st_mode).aces) +
                    list(acl.default_aces)
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
