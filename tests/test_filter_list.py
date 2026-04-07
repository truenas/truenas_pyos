# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Tests for the truenas_utils C extension filter_list implementation.

Covers tnfilter(), compile_filters(), and compile_options() across:
  - All comparison, string, and membership operators (including CI variants)
  - Compound filters: multi-filter AND, OR, OR-with-AND conjunctions, nested OR
  - Path traversal: flat keys, dotted nesting, array indexing, wildcards, escaped dots
  - None / missing-key semantics
  - Non-dict traversal: datetime leaf (.$date pattern), inconsistent list contents
  - compile_options shortcircuit logic
  - Type checking and error cases
  - Repr of compiled objects

Tests marked with ``ref_`` compare against an inline pure-Python reference to
guard against regressions where both old and new code might silently agree on a
wrong answer.  Other tests assert concrete expected values.

Run with:
    python3 -m pytest tests/test_filter_list.py -v
"""
from __future__ import annotations

import dataclasses
import datetime
import operator
import re

import pytest

from typing import NamedTuple

from truenas_pyfilter import (
    CompiledFilters,
    CompiledOptions,
    compile_filters,
    compile_options,
    tnfilter,
    match,
)

# ── Convenience wrapper ───────────────────────────────────────────────────────


def fl(data, filters, **co_kwargs):
    """Compile filters + options and run tnfilter. Returns the result list."""
    cf = compile_filters(filters or [])
    co = compile_options(**co_kwargs)
    return tnfilter(data, filters=cf, options=co)


# ── Pure-Python reference implementation ─────────────────────────────────────
# Used to cross-check C results for complex cases.  Intentionally minimal and
# independent of the C extension.

_UNDEF = object()


def _partition(s):
    rv = ""
    while True:
        left, sep, right = s.partition(".")
        if not sep:
            return rv + left, right
        if left[-1] == "\\":
            rv += left[:-1] + sep
            s = right
        else:
            return rv + left, right


def _casefold(obj):
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.casefold()
    if isinstance(obj, (list, tuple)):
        return [x.casefold() for x in obj]
    raise ValueError(f"cannot casefold {type(obj)}")


_OPMAP = {
    "=": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "~": lambda x, y: re.match(y, x or ""),
    "in": lambda x, y: operator.contains(y, x),
    "nin": lambda x, y: x is not None and not operator.contains(y, x),
    "rin": lambda x, y: x is not None and operator.contains(x, y),
    "rnin": lambda x, y: x is not None and not operator.contains(x, y),
    "^": lambda x, y: x is not None and x.startswith(y),
    "!^": lambda x, y: x is not None and not x.startswith(y),
    "$": lambda x, y: x is not None and x.endswith(y),
    "!$": lambda x, y: x is not None and not x.endswith(y),
}


def _get_ref(obj, path):
    """Python reference for path traversal.  Returns the value or _UNDEF."""
    if "." not in path:
        if isinstance(obj, dict):
            return obj.get(path, _UNDEF)
        if isinstance(obj, (list, tuple)):
            if path == "*":
                return ("*wildcard*", obj)
            if path.isdigit():
                idx = int(path)
                return obj[idx] if idx < len(obj) else None
        return obj  # non-dict/list: return as-is (matches C behaviour post-fix)

    right = path
    cur = obj
    while right:
        left, right = _partition(right)
        if isinstance(cur, dict):
            cur = cur.get(left, _UNDEF)
            if cur is _UNDEF:
                return _UNDEF
        elif isinstance(cur, (list, tuple)):
            if left == "*":
                return ("*wildcard*", cur, right)
            if left.isdigit():
                idx = int(left)
                cur = cur[idx] if idx < len(cur) else None
            else:
                return _UNDEF
        else:
            # Non-dict/list: use value as-is (Python get_impl semantics)
            return cur
    return cur


def _filterop_ref(item, name, op, value):
    raw = _get_ref(item, name)
    if raw is _UNDEF:
        return False
    if isinstance(raw, tuple) and raw[0] == "*wildcard*":
        seq = raw[1]
        rest = raw[2] if len(raw) == 3 else ""
        sub_name = rest if rest else ""
        return any(
            _filterop_ref(entry, sub_name, op, value) if sub_name
            else _filterop_ref({"_": entry}, "_", op, value)
            for entry in seq
        )
    source = raw
    ci = op and op[0] == "C"
    bare_op = op[1:] if ci else op
    fn = _OPMAP[bare_op]
    if ci:
        return bool(fn(_casefold(source), _casefold(value)))
    return bool(fn(source, value))


def _eval_ref(item, f):
    if len(f) == 2:
        _, branches = f
        for branch in branches:
            if isinstance(branch[0], list):
                hit = all(_eval_ref(item, b) for b in branch)
            else:
                hit = _eval_ref(item, branch)
            if hit:
                return True
        return False
    name, op, value = f
    return _filterop_ref(item, name, op, value)


def filter_list_ref(data, filters):
    """Pure-Python reference filter_list (no select/order/offset/limit)."""
    if not filters:
        return list(data)
    return [item for item in data if all(_eval_ref(item, f) for f in filters)]


# ── Shared datasets ───────────────────────────────────────────────────────────

BASIC = [
    {"id": 1, "name": "alice", "score": 100, "active": True,  "tags": ["a", "b"]},
    {"id": 2, "name": "bob",   "score": 85,  "active": True,  "tags": ["b", "c"]},
    {"id": 3, "name": "carol", "score": 90,  "active": False, "tags": ["a", "c"]},
    {"id": 4, "name": "dave",  "score": 70,  "active": False, "tags": ["d"]},
    {"id": 5, "name": "eve",   "score": 95,  "active": True,  "tags": ["a", "b", "c"]},
]

NULLS = [
    {"id": 1, "value": "alpha",  "num": 10},
    {"id": 2, "value": None,     "num": 20},
    {"id": 3, "value": "beta",   "num": 30},
    {"id": 4,                    "num": 40},  # 'value' key absent
]

NESTED = [
    {"id": 1, "user": {"name": "alice", "role": "admin"}, "dept": {"name": "eng"}},
    {"id": 2, "user": {"name": "bob",   "role": "user"},  "dept": {"name": "hr"}},
    {"id": 3, "user": {"name": "carol", "role": "admin"}, "dept": {"name": "eng"}},
    {"id": 4, "user": {"name": "dave",  "role": "user"},  "dept": {"name": "fin"}},
]

WITH_CASE = [
    {"foo": "foo",  "number": 1},
    {"foo": "Foo",  "number": 2},
    {"foo": "foO_", "number": 3},
    {"foo": "bar",  "number": 4},
]

WITH_LISTODICTS = [
    {"foo": "foo",  "list": [{"number": 1}, {"number": 2}]},
    {"foo": "Foo",  "list": [{"number": 2}, {"number": 3}]},
    {"foo": "foO_", "list": [{"number": 3}]},
    {"foo": "bar",  "list": [{"number": 0}]},
]

WITH_DEEP_LISTS = [
    {"foo": "foo", "list": [
        {"list2": [{"number": 1}, {"number": 2}]},
        {"list2": [{"number": 3}]},
    ]},
    {"foo": "bar", "list": [
        {"list2": [{"number": 2}, {"number": 4}]},
    ]},
]

INCONSISTENT = [
    {"foo": "foo",  "list": [{"number": 1}, "canary"]},
    {"foo": "Foo",  "list": [1, {"number": 3}]},
    {"foo": "foO_", "list": [{"number": 3}, ("bob", 1)]},
    {"foo": "bar",  "list": [{"number": 0}]},
    {"foo": "bar",  "list": "whointheirrightmindwoulddothis"},
    {"foo": "bar"},
    {"foo": "bar",  "list": None},
    {"foo": "bar",  "list": 42},
    "canary",  # top-level non-dict item
]

_UTC = datetime.timezone.utc
SAMPLE_AUDIT = [
    {"audit_id": "a", "timestamp": datetime.datetime(2023, 12, 18, 16, 10, 30, tzinfo=_UTC), "event": "AUTH"},
    {"audit_id": "b", "timestamp": datetime.datetime(2023, 12, 18, 16, 10, 33, tzinfo=_UTC), "event": "AUTH"},
    {"audit_id": "c", "timestamp": datetime.datetime(2023, 12, 18, 16, 15, 35, tzinfo=_UTC), "event": "METHOD"},
    {"audit_id": "d", "timestamp": datetime.datetime(2023, 12, 18, 16, 15, 55, tzinfo=_UTC), "event": "METHOD"},
    {"audit_id": "e", "timestamp": datetime.datetime(2023, 12, 18, 16, 21, 25, tzinfo=_UTC), "event": "AUTH"},
]

COMPLEX_DATA = [
    {
        "timestamp": "2022-11-10T07:40:17",
        "type": "Authentication",
        "Authentication": {
            "status": "NT_STATUS_NO_SUCH_USER",
            "clientAccount": "awalker325@outlook.com",
            "version": {"major": 1, "minor": 2},
        },
    },
    {
        "timestamp": "2023-01-24T12:37:39",
        "type": "Authentication",
        "Authentication": {
            "status": "NT_STATUS_OK",
            "clientAccount": "joiner",
            "version": {"major": 1, "minor": 3},
        },
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# Comparison operators
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("filters,expected_ids", [
    ([["name", "=", "alice"]],   {1}),
    ([["id",   "=", 3]],         {3}),
    ([["active", "=", True]],    {1, 2, 5}),
    ([["active", "=", False]],   {3, 4}),
    ([["score", ">",  90]],      {1, 5}),
    ([["score", ">=", 90]],      {1, 3, 5}),
    ([["score", "<",  90]],      {2, 4}),
    ([["score", "<=", 90]],      {2, 3, 4}),
    ([["name",  "!=", "alice"]], {2, 3, 4, 5}),
])
def test_comparison_op(filters, expected_ids):
    assert {r["id"] for r in fl(BASIC, filters)} == expected_ids


def test_eq_none_value():
    # value=None matches the row with value=None; missing key does not match
    result = fl(NULLS, [["value", "=", None]])
    assert result == [NULLS[1]]
    assert NULLS[3] not in result


def test_ne_none_value():
    # ne None: non-None strings match; None source and missing key do not
    result = fl(NULLS, [["value", "!=", None]])
    ids = {r["id"] for r in result}
    assert 1 in ids and 3 in ids  # "alpha" and "beta" are both != None
    assert 2 not in ids            # None != None is False


def test_eq_missing_key():
    assert fl(BASIC, [["nonexistent", "=", "x"]]) == []


# ═════════════════════════════════════════════════════════════════════════════
# String operators
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("op,value,expected_names", [
    ("^",  "a",      {"alice"}),
    ("^",  "b",      {"bob"}),
    ("!^", "a",      {"bob", "carol", "dave", "eve"}),
    ("$",  "e",      {"alice", "dave", "eve"}),
    ("!$", "e",      {"bob", "carol"}),
    ("~",  "^a",     {"alice"}),
    ("~",  ".*o.*",  {"bob", "carol"}),
    ("~",  "^alice$", {"alice"}),
    ("~",  r"^[ace]", {"alice", "carol", "eve"}),
])
def test_string_op(op, value, expected_names):
    assert {r["name"] for r in fl(BASIC, [["name", op, value]])} == expected_names


def test_string_op_none_source():
    # None source: ^ and $ return no match; !^ also returns no match
    assert fl(NULLS, [["value", "^", "al"]]) == [NULLS[0]]
    assert fl(NULLS, [["value", "$", "ha"]]) == [NULLS[0]]
    # !^ with None → False (None has no startswith); only non-None strings match
    assert {r["id"] for r in fl(NULLS, [["value", "!^", "z"]])} == {1, 3}


def test_regex_ci_flag_in_pattern():
    assert fl(BASIC, [["name", "~", "(?i)ALICE"]]) == [BASIC[0]]


def test_regex_none_source():
    # None source is matched against "": "^$" matches, non-empty pattern does not
    assert NULLS[1] in fl(NULLS, [["value", "~", "^$"]])
    assert NULLS[1] not in fl(NULLS, [["value", "~", "alpha"]])
    assert NULLS[0] in fl(NULLS, [["value", "~", "alpha"]])  # "alpha" matches itself


# ═════════════════════════════════════════════════════════════════════════════
# Membership operators
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("filters,expected_ids", [
    ([["id",   "in",  [1, 3, 5]]],     {1, 3, 5}),
    ([["name", "in",  ["alice", "eve"]]], {1, 5}),
    ([["id",   "nin", [1, 2]]],          {3, 4, 5}),
    ([["tags", "rin", "a"]],             {1, 3, 5}),
    ([["tags", "rnin", "a"]],            {2, 4}),
])
def test_membership_op(filters, expected_ids):
    assert {r["id"] for r in fl(BASIC, filters)} == expected_ids


def test_in_none_in_list():
    # None IS in the list — should match
    assert {r["id"] for r in fl(NULLS, [["value", "in", [None, "alpha"]]])} == {1, 2}


def test_in_none_not_in_list():
    assert {r["id"] for r in fl(NULLS, [["value", "in", ["alpha", "beta"]]])} == {1, 3}


def test_nin_none_source_no_match():
    # nin: None source → always no match; only non-None values not in the list match
    result = fl(NULLS, [["value", "nin", ["alpha"]]])
    ids = {r["id"] for r in result}
    assert ids == {3}          # "beta" is not in ["alpha"]
    assert 2 not in ids        # None nin [...] → False


def test_rin_none_source_no_match():
    # rin: None source → no match; only non-None sources that contain the value match
    result = fl(NULLS, [["value", "rin", "a"]])
    ids = {r["id"] for r in result}
    assert ids == {1, 3}       # "alpha" and "beta" both contain "a"
    assert 2 not in ids        # None → no match


def test_rnin_none_source_no_match():
    # rnin: None source → no match; only non-None sources that lack the value match
    result = fl(NULLS, [["value", "rnin", "x"]])
    ids = {r["id"] for r in result}
    assert ids == {1, 3}       # "alpha" and "beta" lack "x"
    assert 2 not in ids        # None → no match


def test_in_string_containment():
    # "in" with a string container checks substring containment
    result = fl(BASIC, [["name", "in", "alice bob"]])
    assert {r["name"] for r in result} == {"alice", "bob"}


# ═════════════════════════════════════════════════════════════════════════════
# Case-insensitive operators
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("op,value,expected_foos", [
    ("C=",    "ALICE",  {"alice"}),
    ("C!=",   "ALICE",  {"bob", "carol", "dave", "eve"}),
    ("C^",    "F",      {"foo", "Foo", "foO_"}),
    ("C!^",   "F",      {"bar"}),
    ("C$",    "oo",     {"foo", "Foo"}),
    ("C!$",   "O",      {"foO_", "bar"}),
])
def test_ci_op_on_basic(op, value, expected_foos):
    # Run C= and C!= on BASIC.name; others on WITH_CASE.foo
    if op in ("C=", "C!="):
        result = fl(BASIC, [["name", op, value]])
        assert {r["name"] for r in result} == expected_foos
    else:
        result = fl(WITH_CASE, [["foo", op, value]])
        assert {r["foo"] for r in result} == expected_foos


@pytest.mark.parametrize("op,value,expected_count", [
    ("Cin",   "foo", 2),   # casefold(src) in casefold("foo"): "foo"→"foo"⊂"foo", "Foo"→"foo"⊂"foo"
    ("Crin",  "foo", 3),   # casefold(src) contains "foo": foo,Foo,foO_ all fold to contain "foo"
    ("Cnin",  "foo", 2),   # negation of Cin
    ("Crnin", "foo", 1),   # negation of Crin: only "bar"
])
def test_ci_membership_op(op, value, expected_count):
    assert len(fl(WITH_CASE, [["foo", op, value]])) == expected_count


def test_ci_complex_nested():
    result = fl(COMPLEX_DATA, [["Authentication.clientAccount", "C=", "JOINER"]])
    assert len(result) == 1
    assert result[0]["Authentication"]["clientAccount"] == "joiner"


def test_ci_with_none_value():
    result = fl(NULLS, [["value", "C=", "ALPHA"]])
    assert len(result) == 1
    assert result[0]["id"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# Compound filters (multi-filter AND, OR, conjunctions)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("filters,expected_ids", [
    # Multi-filter AND
    ([["active", "=", True], ["score", ">", 90]],   {1, 5}),
    ([["active", "=", True], ["score", ">", 200]],  set()),
    ([["id", ">", 0],        ["score", ">", 0]],    {1, 2, 3, 4, 5}),
    # Simple OR
    ([["OR", [["id", "=", 1], ["id", "=", 3]]]],    {1, 3}),
    ([["OR", [["id", "=", 1], ["id", "=", 999]]]],  {1}),
    ([["OR", [["score", ">", 80], ["score", "<", 80]]]], {1, 2, 3, 4, 5}),
])
def test_compound_filter(filters, expected_ids):
    assert {r["id"] for r in fl(BASIC, filters)} == expected_ids


def test_or_with_and_conjunction():
    result = fl(BASIC, [["OR", [
        [["name", "=", "alice"], ["score", "=", 100]],  # AND: both true → alice
        ["id", "=", 2],
    ]]])
    assert {r["id"] for r in result} == {1, 2}


def test_or_with_and_conjunction_partial_miss():
    result = fl(BASIC, [["OR", [
        [["name", "=", "alice"], ["score", "=", 85]],  # AND: fails (alice has 100)
        ["id", "=", 2],
    ]]])
    assert {r["id"] for r in result} == {2}


def test_nested_or():
    result = fl(BASIC, [["OR", [
        ["OR", [["id", "=", 1], ["id", "=", 2]]],
        ["id", "=", 3],
    ]]])
    assert {r["id"] for r in result} == {1, 2, 3}


def test_nested_or_deep():
    result = fl(BASIC, [["OR", [
        ["OR", [
            ["OR", [["id", "=", 1], ["id", "=", 2]]],
            ["id", "=", 3],
        ]],
        ["id", "=", 4],
    ]]])
    assert {r["id"] for r in result} == {1, 2, 3, 4}


def test_or_combined_with_outer_and():
    result = fl(BASIC, [
        ["OR", [["id", "=", 1], ["id", "=", 2], ["id", "=", 3]]],
        ["active", "=", True],
    ])
    assert {r["id"] for r in result} == {1, 2}  # carol (id=3) is inactive


# ═════════════════════════════════════════════════════════════════════════════
# Empty filters and empty data
# ═════════════════════════════════════════════════════════════════════════════

def test_empty_filters_returns_all():
    assert fl(BASIC, []) == BASIC


def test_empty_filters_on_empty_data():
    assert fl([], []) == []


def test_nonempty_filters_on_empty_data():
    assert fl([], [["id", "=", 1]]) == []


def test_empty_filters_generator():
    assert fl((x for x in BASIC), []) == BASIC


def test_filters_on_generator():
    assert fl((x for x in BASIC), [["id", "=", 1]]) == [BASIC[0]]


# ═════════════════════════════════════════════════════════════════════════════
# Path traversal
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("filters,dataset,expected_ids", [
    ([["id", "=", 1]],                              NESTED,       {1}),
    ([["user.name", "=", "alice"]],                 NESTED,       {1}),
    ([["user.role", "=", "admin"]],                 NESTED,       {1, 3}),
    ([["user.role", "=", "admin"], ["dept.name", "=", "eng"]], NESTED, {1, 3}),
    ([["user.nonexistent", "=", "x"]],              NESTED,       set()),
])
def test_path_traversal(filters, dataset, expected_ids):
    assert {r["id"] for r in fl(dataset, filters)} == expected_ids


def test_deeply_nested():
    assert len(fl(COMPLEX_DATA, [["Authentication.version.major", "=", 1]])) == 2
    assert len(fl(COMPLEX_DATA, [["Authentication.version.minor", "=", 3]])) == 1


def test_nested_key_is_none():
    data = [{"a": {"b": None}}, {"a": {"b": "x"}}]
    assert fl(data, [["a.b", "=", None]]) == [data[0]]
    assert fl(data, [["a.b", "=", "x"]]) == [data[1]]


def test_array_index():
    data = [{"items": ["first", "second", "third"]}]
    assert fl(data, [["items.0", "=", "first"]]) == [data[0]]
    assert fl(data, [["items.1", "=", "second"]]) == [data[0]]
    assert fl(data, [["items.2", "=", "third"]]) == [data[0]]


def test_array_index_out_of_bounds():
    data = [{"items": ["only"]}]
    assert fl(data, [["items.5", "=", "anything"]]) == []


def test_escaped_dot_in_key():
    data = [{"foo.bar": 42, "foo": {"bar": 99}}]
    assert fl(data, [["foo\\.bar", "=", 42]]) == [data[0]]
    assert fl(data, [["foo\\.bar", "=", 99]]) == []   # does NOT traverse foo→bar


def test_nested_or_with_nested_paths():
    result = fl(NESTED, [["OR", [
        ["user.role", "=", "admin"],
        ["dept.name", "=", "hr"],
    ]]])
    assert {r["id"] for r in result} == {1, 2, 3}


# ═════════════════════════════════════════════════════════════════════════════
# Wildcard path (*)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("filters,expected_len", [
    ([["list.*.number", "=", 3]],   2),
    ([["list.*.number", "=", 99]],  0),
    ([["list.*.number", ">=", 0]],  4),
])
def test_wildcard_basic(filters, expected_len):
    assert len(fl(WITH_LISTODICTS, filters)) == expected_len


def test_wildcard_or():
    result = fl(WITH_LISTODICTS, [["OR", [
        ["list.*.number", "=", 1],
        ["list.*.number", "=", 3],
    ]]])
    assert len(result) == 3  # foo (has 1), Foo (has 3), foO_ (has 3)


def test_wildcard_deeply_nested():
    assert len(fl(WITH_DEEP_LISTS, [["list.*.list2.*.number", "=", 2]])) == 2
    assert len(fl(WITH_DEEP_LISTS, [["list.*.list2.*.number", "=", 4]])) == 1


def test_wildcard_empty_list():
    data = [{"items": []}, {"items": [{"v": 1}]}]
    result = fl(data, [["items.*.v", "=", 1]])
    assert result == [data[1]]


def test_wildcard_combined_with_and():
    result = fl(WITH_LISTODICTS, [
        ["list.*.number", ">=", 2],
        ["foo", "C^", "f"],
    ])
    assert len(result) == 3  # foo (has 2), Foo (has 2,3), foO_ (has 3); all start with f


def test_wildcard_inconsistent_list_items():
    # Non-dict items (strings, ints, tuples) at wildcard level are skipped; no errors raised
    result = fl(INCONSISTENT, [["list.*.number", "=", 3]])
    assert len(result) == 2                         # foO_ and Foo rows match
    assert all(isinstance(item, dict) for item in result)  # canary string not in results


def test_wildcard_with_none_list():
    data = [{"items": None}, {"items": [{"v": 1}]}]
    assert fl(data, [["items.*.v", "=", 1]]) == [data[1]]


# ═════════════════════════════════════════════════════════════════════════════
# None / missing key semantics
# ═════════════════════════════════════════════════════════════════════════════

def test_missing_key_not_equal_none():
    # A missing key is undefined, not None; must NOT match '= None'
    result = fl(NULLS, [["value", "=", None]])
    assert result == [NULLS[1]]   # only the explicit-None row matches


def test_missing_key_all_ops_no_match():
    # Missing key (NULLS[3]) never produces a match.
    # Comparison ops (>, <, >=, <=) and 'in' raise TypeError when source is None
    # (NULLS[1]), so they are excluded here.
    for op in ["=", "^", "$", "nin", "rin", "rnin"]:
        result = fl(NULLS, [["value", op, "anything"]])
        assert NULLS[3] not in result, f"op={op} should not match missing key"


def test_regex_none_source_matches_empty_pattern():
    # None source → matched against "" → "^$" matches
    assert NULLS[1] in fl(NULLS, [["value", "~", "^$"]])


def test_regex_none_source_no_match_nonempty_pattern():
    # None source → "alpha" does not match ""
    result = fl(NULLS, [["value", "~", "alpha"]])
    assert NULLS[1] not in result
    assert NULLS[0] in result    # "alpha" matches itself — positive check


# ═════════════════════════════════════════════════════════════════════════════
# Non-dict traversal (.$date, inconsistent data)
# ═════════════════════════════════════════════════════════════════════════════
# Python get_impl semantics: when traversal hits a non-dict/non-list value,
# the current value is used as the leaf (remaining path ignored).

_TS_CUT = datetime.datetime(2023, 12, 18, 16, 15, 35, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize("op,expected_ids", [
    (">",  {"d", "e"}),
    (">=", {"c", "d", "e"}),
    ("<",  {"a", "b"}),
    ("=",  {"c"}),
])
def test_datetime_leaf(op, expected_ids):
    # timestamp.$date traverses to a datetime object; $date attr not found →
    # C clears AttributeError and uses the datetime itself as the leaf value.
    result = fl(SAMPLE_AUDIT, [["timestamp.$date", op, _TS_CUT]])
    assert {r["audit_id"] for r in result} == expected_ids


def test_datetime_leaf_combined_range():
    ts_lo = datetime.datetime(2023, 12, 18, 16, 10, 33, tzinfo=datetime.timezone.utc)
    ts_hi = datetime.datetime(2023, 12, 18, 16, 15, 55, tzinfo=datetime.timezone.utc)
    result = fl(SAMPLE_AUDIT, [
        ["timestamp.$date", ">", ts_lo],
        ["timestamp.$date", "<", ts_hi],
    ])
    assert len(result) == 1
    assert result[0]["audit_id"] == "c"


def test_non_dict_top_level_item_no_error():
    data = [{"id": 1}, "bare-string", {"id": 3}]
    assert fl(data, [["id", "=", 1]]) == [{"id": 1}]


def test_non_dict_in_wildcard_list_no_error():
    data = [{"items": [{"v": 1}, "string", 42, None, {"v": 2}]}]
    assert fl(data, [["items.*.v", "=", 1]]) == [data[0]]


def test_string_as_intermediate_value():
    # Path a.b where a="hello" — no 'b' attr → leaf is "hello"
    data = [{"a": "hello"}, {"a": {"b": "hello"}}]
    assert len(fl(data, [["a.b", "=", "hello"]])) == 2


def test_int_as_intermediate_value():
    # Path a.b where a=42 — no 'b' attr → leaf is 42
    data = [{"a": 42}, {"a": {"b": 42}}]
    assert len(fl(data, [["a.b", "=", 42]])) == 2


# ═════════════════════════════════════════════════════════════════════════════
# compile_options: shortcircuit and accepted kwargs
# ═════════════════════════════════════════════════════════════════════════════

def test_get_true_returns_first_match():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(get=True)
    result = tnfilter(BASIC, filters=cf, options=co)
    assert len(result) == 1
    assert result[0]["id"] == 1   # alice is the first active item


def test_get_true_empty_data_returns_empty_list():
    cf = compile_filters([["id", "=", 1]])
    co = compile_options(get=True)
    assert tnfilter([], filters=cf, options=co) == []


def test_get_true_no_match_returns_empty_list():
    cf = compile_filters([["id", "=", 9999]])
    co = compile_options(get=True)
    assert tnfilter(BASIC, filters=cf, options=co) == []


def test_get_true_with_nonempty_order_by_disables_shortcircuit():
    # order_by=["-id"] → shortcircuit disabled → all matches returned
    cf = compile_filters([["active", "=", True]])
    co = compile_options(get=True, order_by=["-id"])
    assert len(tnfilter(BASIC, filters=cf, options=co)) == 3


def test_get_true_with_empty_order_by_still_shortcircuits():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(get=True, order_by=[])
    assert len(tnfilter(BASIC, filters=cf, options=co)) == 1


def test_get_false_returns_all_matches():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(get=False)
    assert len(tnfilter(BASIC, filters=cf, options=co)) == 3


def test_count_returns_integer():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(count=True)
    result = tnfilter(BASIC, filters=cf, options=co)
    assert result == 3


def test_offset_limit_applied():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(offset=1, limit=1)
    result = tnfilter(BASIC, filters=cf, options=co)
    assert len(result) == 1
    assert result[0]["id"] == 2  # bob is second active item


def test_select_projects_fields():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(select=["id", "name"])
    result = tnfilter(BASIC, filters=cf, options=co)
    assert "score" not in result[0]
    assert result[0] == {"id": 1, "name": "alice"}


def test_order_by_ascending():
    co = compile_options(order_by=["score"])
    result = tnfilter(BASIC, filters=compile_filters([]), options=co)
    scores = [r["score"] for r in result]
    assert scores == sorted(scores)


def test_order_by_descending():
    co = compile_options(order_by=["-score"])
    result = tnfilter(BASIC, filters=compile_filters([]), options=co)
    scores = [r["score"] for r in result]
    assert scores == sorted(scores, reverse=True)


def test_order_by_nested_path():
    co = compile_options(order_by=["user.name"])
    result = tnfilter(NESTED, filters=compile_filters([]), options=co)
    names = [r["user"]["name"] for r in result]
    assert names == sorted(names)


def test_order_by_nulls_first():
    co = compile_options(order_by=["nulls_first:value"])
    result = tnfilter(NULLS, filters=compile_filters([]), options=co)
    # None and missing-key entries come first
    assert result[0]["id"] in (2, 4)
    assert result[1]["id"] in (2, 4)


def test_order_by_nulls_last():
    co = compile_options(order_by=["nulls_last:value"])
    result = tnfilter(NULLS, filters=compile_filters([]), options=co)
    assert result[-1]["id"] in (2, 4)
    assert result[-2]["id"] in (2, 4)


def test_order_by_nulls_first_reverse():
    co = compile_options(order_by=["nulls_first:-value"])
    result = tnfilter(NULLS, filters=compile_filters([]), options=co)
    non_nulls = [r for r in result if r.get("value") is not None and "value" in r]
    values = [r["value"] for r in non_nulls]
    assert values == sorted(values, reverse=True)


def test_order_by_multi_key_primary():
    # order_by=["score", "name"]: score is primary, name breaks ties.
    data = [
        {"id": 1, "score": 2, "name": "charlie"},
        {"id": 2, "score": 1, "name": "bob"},
        {"id": 3, "score": 2, "name": "alice"},
        {"id": 4, "score": 1, "name": "dave"},
    ]
    co = compile_options(order_by=["score", "name"])
    result = tnfilter(data, filters=compile_filters([]), options=co)
    assert [(r["score"], r["name"]) for r in result] == [
        (1, "bob"),
        (1, "dave"),
        (2, "alice"),
        (2, "charlie"),
    ]


def test_order_by_multi_key_tiebreaker_desc():
    # order_by=["score", "-name"]: score ascending, ties broken by name descending.
    data = [
        {"id": 1, "score": 2, "name": "alice"},
        {"id": 2, "score": 1, "name": "bob"},
        {"id": 3, "score": 2, "name": "charlie"},
        {"id": 4, "score": 1, "name": "dave"},
    ]
    co = compile_options(order_by=["score", "-name"])
    result = tnfilter(data, filters=compile_filters([]), options=co)
    assert [(r["score"], r["name"]) for r in result] == [
        (1, "dave"),
        (1, "bob"),
        (2, "charlie"),
        (2, "alice"),
    ]


def test_order_by_desc_primary_equal_asc_tiebreaker():
    # Regression: when the primary (descending) key is equal for all rows,
    # the ascending tie-breaker must still determine final order.
    # Previously PyList_Reverse would flip equal-keyed pairs and undo the
    # tie-breaking established by lower-priority passes.
    data = [
        {"id": 1, "gid": 0, "builtin": True, "group": "wheel"},
        {"id": 43, "gid": 0, "builtin": True, "group": "root"},
    ]
    cf = compile_filters([])
    co = compile_options(order_by=["-builtin", "gid", "group"])
    result = tnfilter(data, filters=cf, options=co)
    assert [r["group"] for r in result] == ["root", "wheel"]


def test_order_by_desc_primary_equal_desc_tiebreaker():
    # Same regression, but with a descending tie-breaker.
    data = [
        {"id": 1, "gid": 0, "builtin": True, "group": "root"},
        {"id": 43, "gid": 0, "builtin": True, "group": "wheel"},
    ]
    cf = compile_filters([])
    co = compile_options(order_by=["-builtin", "gid", "-group"])
    result = tnfilter(data, filters=cf, options=co)
    assert [r["group"] for r in result] == ["wheel", "root"]


def test_order_by_desc_primary_mixed_asc_tiebreaker():
    # When primary key values differ, descending order must be correct AND
    # equal-primary-key rows must still be broken by the tie-breaker.
    data = [
        {"id": 1, "score": 1, "name": "charlie"},
        {"id": 2, "score": 2, "name": "bob"},
        {"id": 3, "score": 2, "name": "alice"},
        {"id": 4, "score": 1, "name": "dave"},
    ]
    co = compile_options(order_by=["-score", "name"])
    result = tnfilter(data, filters=compile_filters([]), options=co)
    assert [(r["score"], r["name"]) for r in result] == [
        (2, "alice"),
        (2, "bob"),
        (1, "charlie"),
        (1, "dave"),
    ]


def test_order_by_desc_mid_list():
    # Regression: descending key in the middle of order_by must not disturb
    # the ordering set by lower-priority keys, and the higher-priority key
    # must still govern primary grouping.
    # order_by=["gid", "-builtin", "group"]:
    #   gid ascending → within each gid, builtin descending (True before False)
    #   → within equal builtin, group ascending.
    data = [
        {"gid": 0, "builtin": True,  "group": "wheel"},
        {"gid": 0, "builtin": False, "group": "staff"},
        {"gid": 0, "builtin": True,  "group": "root"},
        {"gid": 1, "builtin": True,  "group": "sudo"},
    ]
    co = compile_options(order_by=["gid", "-builtin", "group"])
    result = tnfilter(data, filters=compile_filters([]), options=co)
    assert [(r["gid"], r["builtin"], r["group"]) for r in result] == [
        (0, True,  "root"),
        (0, True,  "wheel"),
        (0, False, "staff"),
        (1, True,  "sudo"),
    ]


def test_select_rename():
    co = compile_options(select=[["id", "user_id"], "name"])
    result = tnfilter(BASIC[:1], filters=compile_filters([]), options=co)
    assert result[0] == {"user_id": 1, "name": "alice"}


def test_select_nested_path():
    co = compile_options(select=["user.name"])
    result = tnfilter(NESTED[:1], filters=compile_filters([]), options=co)
    assert result[0] == {"user": {"name": "alice"}}


def test_select_missing_key_skipped():
    co = compile_options(select=["id", "nonexistent"])
    result = tnfilter(BASIC[:1], filters=compile_filters([]), options=co)
    assert result[0] == {"id": 1}


def test_count_no_filters():
    co = compile_options(count=True)
    assert tnfilter(BASIC, filters=compile_filters([]), options=co) == 5


def test_offset_only():
    co = compile_options(offset=2)
    result = tnfilter(BASIC, filters=compile_filters([]), options=co)
    assert len(result) == 3
    assert result[0]["id"] == 3


def test_limit_only():
    co = compile_options(limit=2)
    result = tnfilter(BASIC, filters=compile_filters([]), options=co)
    assert len(result) == 2
    assert result[0]["id"] == 1


def test_offset_beyond_end():
    co = compile_options(offset=100)
    result = tnfilter(BASIC, filters=compile_filters([]), options=co)
    assert result == []


def test_select_then_order_by():
    co = compile_options(select=["id", "score"], order_by=["-score"])
    result = tnfilter(BASIC, filters=compile_filters([]), options=co)
    assert "name" not in result[0]
    scores = [r["score"] for r in result]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("bad_kwarg", [
    {"extra": {"foo": "bar"}},
    {"force_sql_filters": True},
    {"relationships": True},
])
def test_unknown_kwarg_raises_type_error(bad_kwarg):
    with pytest.raises(TypeError):
        compile_options(**bad_kwarg)


@pytest.mark.parametrize("bad_select", [
    [['foobar.stuff.more_stuff']],           # too few items
    [['foobar.stuff.more_stuff', 'cat', 'dog']],  # too many items
])
def test_select_as_list_wrong_length_raises(bad_select):
    with pytest.raises(ValueError) as ve:
        compile_options(select=bad_select)
    assert 'select as list may only contain two parameters' in str(ve.value)


def test_select_as_list_non_string_first_item_raises():
    with pytest.raises(ValueError) as ve:
        compile_options(select=[[1, 'cat']])
    assert 'first item must be a string' in str(ve.value)


def test_select_as_list_non_string_second_item_raises():
    with pytest.raises(ValueError) as ve:
        compile_options(select=[['cat', 1]])
    assert 'second item must be a string' in str(ve.value)


# ═════════════════════════════════════════════════════════════════════════════
# compile_filters: edge cases
# ═════════════════════════════════════════════════════════════════════════════

def test_compile_filters_empty_list():
    cf = compile_filters([])
    co = compile_options()
    assert tnfilter(BASIC, filters=cf, options=co) == BASIC


def test_compile_filters_none():
    cf = compile_filters(None)
    co = compile_options()
    assert tnfilter(BASIC, filters=cf, options=co) == BASIC


def test_compile_filters_invalid_operator_raises():
    with pytest.raises(ValueError):
        compile_filters([["id", "??", 1]])


def test_compile_filters_c_tilde_accepted():
    # C~ is parsed as case-insensitive regex at the C API level (not rejected here)
    assert isinstance(compile_filters([["name", "C~", "alice"]]), CompiledFilters)


def test_compile_filters_single():
    cf = compile_filters([["id", "=", 1]])
    assert tnfilter(BASIC, filters=cf, options=compile_options()) == [BASIC[0]]


def test_compile_filters_many():
    filters = [["id", "!=", i] for i in range(2, 6)]
    cf = compile_filters(filters)
    assert tnfilter(BASIC, filters=cf, options=compile_options()) == [BASIC[0]]


def test_precompiled_filters_reusable():
    cf = compile_filters([["active", "=", True]])
    co = compile_options()
    assert tnfilter(BASIC, filters=cf, options=co) == tnfilter(BASIC, filters=cf, options=co)


def test_precompiled_options_reusable():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(get=True)
    assert tnfilter(BASIC, filters=cf, options=co) == tnfilter(BASIC, filters=cf, options=co)


# ═════════════════════════════════════════════════════════════════════════════
# Type checking and tnfilter argument validation
# ═════════════════════════════════════════════════════════════════════════════

def test_tnfilter_requires_compiled_filters():
    with pytest.raises(TypeError):
        tnfilter(BASIC, filters=[], options=compile_options())


def test_tnfilter_requires_compiled_options():
    with pytest.raises(TypeError):
        tnfilter(BASIC, filters=compile_filters([]), options={})


def test_tnfilter_filters_positional_raises():
    with pytest.raises(TypeError):
        tnfilter(BASIC, compile_filters([]), compile_options())


def test_tnfilter_data_must_be_iterable():
    with pytest.raises(TypeError):
        tnfilter(42, filters=compile_filters([]), options=compile_options())


@pytest.mark.parametrize("bad_filters", [
    [["id", "NOTANOP", 1]],
    [["just_one"]],
])
def test_compile_filters_bad_input_raises(bad_filters):
    with pytest.raises((ValueError, SystemError, Exception)):
        compile_filters(bad_filters)


# ═════════════════════════════════════════════════════════════════════════════
# Repr of compiled objects
# ═════════════════════════════════════════════════════════════════════════════

def test_compiled_filters_repr_empty():
    assert repr(compile_filters([])) == "CompiledFilters([])"


def test_compiled_filters_repr_with_filter():
    assert repr(compile_filters([["id", "=", 1]])) == "CompiledFilters([['id', '=', 1]])"


def test_compiled_filters_repr_shows_original():
    filters = [["name", "^", "alice"], ["score", ">", 90]]
    r = repr(compile_filters(filters))
    assert "CompiledFilters(" in r
    assert "alice" in r


def test_compiled_options_repr_empty():
    assert repr(compile_options()).startswith("CompiledOptions(")


def test_compiled_options_repr_with_args():
    # Repr with kwargs must differ from the no-kwargs repr
    r_default = repr(compile_options())
    r_with_args = repr(compile_options(get=True, count=False))
    assert r_with_args.startswith("CompiledOptions(")
    assert r_with_args != r_default


def test_types_are_correct():
    assert isinstance(compile_filters([]), CompiledFilters)
    assert isinstance(compile_options(), CompiledOptions)


# ═════════════════════════════════════════════════════════════════════════════
# Cross-check against the pure-Python reference implementation
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("filters", [
    [["active", "=", True]],
    [["score", ">", 85]],
    [["name", "^", "a"]],
    [["name", "~", ".*o.*"]],
    [["tags", "rin", "a"]],
    [["active", "=", True], ["score", ">=", 90]],
    [["OR", [["id", "=", 1], ["id", "=", 3]]]],
    [["OR", [["id", "=", 1], ["active", "=", False]]]],
    [["OR", [[["active", "=", True], ["score", ">", 90]], ["id", "=", 4]]]],
    [["name", "C=", "ALICE"]],
    [["name", "C^", "A"]],
    [["name", "Crin", "li"]],
])
def test_basic_vs_ref(filters):
    assert fl(BASIC, filters) == filter_list_ref(BASIC, filters)


@pytest.mark.parametrize("filters", [
    [["value", "=", None]],
    [["value", "!=", None]],
    [["value", "^", "al"]],
    [["value", "nin", ["alpha"]]],
    [["value", "rin", "a"]],
    [["value", "rnin", "x"]],
])
def test_nulls_vs_ref(filters):
    assert fl(NULLS, filters) == filter_list_ref(NULLS, filters)


@pytest.mark.parametrize("filters", [
    [["user.name", "=", "alice"]],
    [["user.role", "=", "admin"]],
    [["user.role", "=", "admin"], ["dept.name", "=", "eng"]],
    [["Authentication.status", "=", "NT_STATUS_OK"]],
    [["Authentication.version.minor", "=", 3]],
])
def test_nested_vs_ref(filters):
    dataset = NESTED if "user" in filters[0][0] else COMPLEX_DATA
    assert fl(dataset, filters) == filter_list_ref(dataset, filters)


@pytest.mark.parametrize("filters", [
    [["list.*.number", "=", 3]],
    [["list.*.number", ">=", 2]],
    [["list.*.number", "in", [1, 3]]],
])
def test_wildcard_vs_ref(filters):
    assert fl(WITH_LISTODICTS, filters) == filter_list_ref(WITH_LISTODICTS, filters)


_MW_DATA = [
    {"foo": "foo1", "number": 1, "list": [1]},
    {"foo": "foo2", "number": 2, "list": [2]},
    {"foo": "_foo_", "number": 3, "list": [3]},
]

_MW_DATA_NULL = [
    {"foo": "foo1", "number": 1, "list": [1]},
    {"foo": "foo2", "number": 2, "list": [2]},
    {"foo": "_foo_", "number": 3, "list": [3]},
    {"foo": None,   "number": 4, "list": [4]},
    {"number": 5, "list": [5]},
]

_MW_CASE = [
    {"foo": "foo",  "number": 1, "list": [1]},
    {"foo": "Foo",  "number": 2, "list": [2]},
    {"foo": "foO_", "number": 3, "list": [3]},
    {"foo": "bar",  "number": 3, "list": [3]},
]

_MW_LISTODICTS = [
    {"foo": "foo",  "list": [{"number": 1}, {"number": 2}]},
    {"foo": "Foo",  "list": [{"number": 2}, {"number": 3}]},
    {"foo": "foO_", "list": [{"number": 3}]},
    {"foo": "bar",  "list": [{"number": 0}]},
]

_MW_INCONSISTENT = [
    {"foo": "foo",  "list": [{"number": 1}, "canary"]},
    {"foo": "Foo",  "list": [1, {"number": 3}]},
    {"foo": "foO_", "list": [{"number": 3}, ("bob", 1)]},
    {"foo": "bar",  "list": [{"number": 0}]},
    {"foo": "bar",  "list": "whointheirrightmindwoulddothis"},
    {"foo": "bar"},
    {"foo": "bar",  "list": None},
    {"foo": "bar",  "list": 42},
    "canary",
]

_MW_DEEP = [
    {"foo": "foo", "list": [{"list2": [{"number": 1}, "canary"]}, {"list2": [{"number": 2}, "canary"]}]},
    {"foo": "Foo", "list": [{"list2": [{"number": 3}, "canary"]}, {"list2": [{"number": 2}, "canary"]}]},
]

_MW_TS = datetime.timezone.utc
_MW_AUDIT = [
    {"audit_id": "d89cd1ba", "timestamp": datetime.datetime(2023, 12, 18, 16, 10, 30, tzinfo=_MW_TS),
     "service": "MIDDLEWARE", "event": "AUTHENTICATION"},
    {"audit_id": "d53dcd53", "timestamp": datetime.datetime(2023, 12, 18, 16, 10, 33, tzinfo=_MW_TS),
     "service": "MIDDLEWARE", "event": "AUTHENTICATION"},
    {"audit_id": "3f63e567", "timestamp": datetime.datetime(2023, 12, 18, 16, 15, 35, tzinfo=_MW_TS),
     "service": "MIDDLEWARE", "event": "METHOD_CALL"},
    {"audit_id": "e617db35", "timestamp": datetime.datetime(2023, 12, 18, 16, 15, 55, tzinfo=_MW_TS),
     "service": "MIDDLEWARE", "event": "METHOD_CALL"},
    {"audit_id": "7fd9f725", "timestamp": datetime.datetime(2023, 12, 18, 16, 21, 25, tzinfo=_MW_TS),
     "service": "MIDDLEWARE", "event": "AUTHENTICATION"},
]


@pytest.mark.parametrize("data,filters,expected_len", [
    (_MW_DATA, [["foo",    "=",  "foo1"]],         1),
    (_MW_DATA, [["foo",    "^",  "foo"]],           2),
    (_MW_DATA, [["foo",    "$",  "_"]],             1),
    (_MW_DATA, [["foo",    "~",  "^foo"]],          2),
    (_MW_DATA, [["foo",    "~",  ".*foo.*"]],       3),
    (_MW_DATA, [["number", ">",  1]],               2),
    (_MW_DATA, [["number", ">=", 1]],               3),
    (_MW_DATA, [["number", "<",  3]],               2),
    (_MW_DATA, [["number", "<=", 3]],               3),
    (_MW_DATA, [["number", "in", [1, 3]]],          2),
    (_MW_DATA, [["number", "nin", [1, 3]]],         1),
    (_MW_DATA, [["list",   "rin", 1]],              1),
    (_MW_DATA, [["list",   "rnin", 1]],             2),
    # casefold
    (_MW_DATA,  [["foo", "C=",    "Foo1"]],         1),
    (_MW_CASE,  [["foo", "C^",    "F"]],            3),
    (_MW_CASE,  [["foo", "C!^",   "F"]],            1),
    (_MW_CASE,  [["foo", "C$",    "foo"]],          2),
    (_MW_CASE,  [["foo", "C!$",   "O"]],            2),
    (_MW_CASE,  [["foo", "Cin",   "foo"]],          2),
    (_MW_CASE,  [["foo", "Crin",  "foo"]],          3),
    (_MW_CASE,  [["foo", "Cnin",  "foo"]],          2),
    (_MW_CASE,  [["foo", "Crnin", "foo"]],          1),
    # nested / wildcard
    (COMPLEX_DATA,    [["Authentication.status",        "=", "NT_STATUS_OK"]], 1),
    (COMPLEX_DATA,    [["Authentication.clientAccount", "C=", "JOINER"]],      1),
    (_MW_LISTODICTS,  [["list.*.number", "=", 3]],                             2),
    (_MW_INCONSISTENT, [["list.*.number", "=", 3]],                            2),
    (_MW_DEEP,        [["list.*.list2.*.number", "=", 2]],                     2),
    # null / missing
    (_MW_DATA_NULL,   [["foo", "=", None]],                                    1),
    (_MW_DATA_NULL,   [["canary", "in", "canary2"]],                           0),
    (_MW_DATA_NULL,   [["foo", "~", "(?i)Foo1"]],                              1),
])
def test_comprehensive_filter_cases(data, filters, expected_len):
    assert len(fl(data, filters)) == expected_len


def test_mw_or_one_branch():
    assert len(fl(_MW_DATA, [["OR", [["number", "=", 1], ["number", "=", 200]]]])) == 1


def test_mw_or_two_branches():
    assert len(fl(_MW_DATA, [["OR", [["number", "=", 1], ["number", "=", 2]]]])) == 2


def test_mw_or_with_and_conjunction():
    assert len(fl(_MW_DATA, [["OR", [
        [["number", "=", 1], ["foo", "=", "foo1"]],
        ["number", "=", 2],
    ]]])) == 2
    assert len(fl(_MW_DATA, [["OR", [
        [["number", "=", 1], ["foo", "=", "foo2"]],
        ["number", "=", 2],
    ]]])) == 1


def test_mw_or_nesting():
    assert len(fl(_MW_DATA, [["OR", [
        ["OR", [["number", "=", 1], ["foo", "=", "canary"]]],
        ["number", "=", 2],
    ]]])) == 2
    assert len(fl(_MW_DATA, [["OR", [
        ["OR", [["number", "=", "canary"], ["foo", "=", "canary"]]],
        ["number", "=", 2],
    ]]])) == 1
    assert len(fl(_MW_DATA, [["OR", [
        ["OR", [
            ["OR", [["number", "=", 1], ["number", "=", "canary"]]],
            ["foo", "=", "canary"],
        ]],
        ["number", "=", 2],
    ]]])) == 2


def test_mw_timestamp_range():
    ts_cut = datetime.datetime(2023, 12, 18, 16, 15, 35, tzinfo=_MW_TS)
    assert len(fl(_MW_AUDIT, [["timestamp.$date", ">",  ts_cut]])) == 2
    assert len(fl(_MW_AUDIT, [["timestamp.$date", ">=", ts_cut]])) == 3
    assert len(fl(_MW_AUDIT, [["timestamp.$date", "<",  ts_cut]])) == 2


# ═════════════════════════════════════════════════════════════════════════════
# NamedTuple support (getattr fallback path)
# ═════════════════════════════════════════════════════════════════════════════

class _User(NamedTuple):
    id: int
    name: str
    active: bool
    score: float


class _Address(NamedTuple):
    city: str
    zip: str


class _UserWithAddr(NamedTuple):
    id: int
    name: str
    address: _Address


_NT_USERS = [
    _User(1, "alice", True,  9.5),
    _User(2, "bob",   False, 4.0),
    _User(3, "carol", True,  7.2),
    _User(4, "dave",  False, 7.2),
]


def test_nt_eq():
    assert fl(_NT_USERS, [["name", "=", "alice"]]) == [_NT_USERS[0]]


def test_nt_ne():
    assert fl(_NT_USERS, [["active", "!=", True]]) == [_NT_USERS[1], _NT_USERS[3]]


def test_nt_gt():
    assert fl(_NT_USERS, [["score", ">", 7.2]]) == [_NT_USERS[0]]


def test_nt_in():
    assert fl(_NT_USERS, [["name", "in", ["alice", "carol"]]]) == [
        _NT_USERS[0], _NT_USERS[2]
    ]


def test_nt_startswith():
    result = fl(_NT_USERS, [["name", "^", "a"]])
    assert result == [_NT_USERS[0]]


def test_nt_ci_eq():
    assert fl(_NT_USERS, [["name", "C=", "ALICE"]]) == [_NT_USERS[0]]


def test_nt_compound_and():
    # active=True AND score >= 7.2
    result = fl(_NT_USERS, [["active", "=", True], ["score", ">=", 7.2]])
    assert result == [_NT_USERS[0], _NT_USERS[2]]


def test_nt_compound_or():
    result = fl(_NT_USERS, [["OR", [["name", "=", "alice"], ["name", "=", "dave"]]]])
    assert result == [_NT_USERS[0], _NT_USERS[3]]


def test_nt_missing_attr_no_match():
    # NamedTuple has no "email" field — should not match, no error
    assert fl(_NT_USERS, [["email", "=", "x"]]) == []


def test_nt_order_by():
    cf = compile_filters([])
    co = compile_options(order_by=["score"])
    result = tnfilter(_NT_USERS, filters=cf, options=co)
    assert [r.score for r in result] == [4.0, 7.2, 7.2, 9.5]


def test_nt_order_by_desc():
    cf = compile_filters([])
    co = compile_options(order_by=["-name"])
    result = tnfilter(_NT_USERS, filters=cf, options=co)
    assert [r.name for r in result] == ["dave", "carol", "bob", "alice"]


def test_nt_select():
    # select projects into dicts; NamedTuple fields become dict keys
    cf = compile_filters([["active", "=", True]])
    co = compile_options(select=["id", "name"])
    result = tnfilter(_NT_USERS, filters=cf, options=co)
    assert result == [{"id": 1, "name": "alice"}, {"id": 3, "name": "carol"}]


def test_nt_limit_offset():
    cf = compile_filters([])
    co = compile_options(order_by=["id"], offset=1, limit=2)
    result = tnfilter(_NT_USERS, filters=cf, options=co)
    assert [r.id for r in result] == [2, 3]


def test_nt_count():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(count=True)
    assert tnfilter(_NT_USERS, filters=cf, options=co) == 2


def test_nt_match_true():
    cf = compile_filters([["active", "=", True], ["score", ">", 5.0]])
    assert match(_NT_USERS[0], filters=cf) is _NT_USERS[0]


def test_nt_match_false():
    cf = compile_filters([["active", "=", True]])
    assert match(_NT_USERS[1], filters=cf) is None


def test_nt_nested_namedtuple():
    # Dotted path traverses into a nested NamedTuple via getattr
    data = [
        _UserWithAddr(1, "alice", _Address("Springfield", "62701")),
        _UserWithAddr(2, "bob",   _Address("Shelbyville", "62702")),
    ]
    result = fl(data, [["address.city", "=", "Springfield"]])
    assert len(result) == 1
    assert result[0].name == "alice"


def test_nt_mixed_with_dicts():
    # Lists may contain both dicts and NamedTuples; both paths must work
    data = [
        {"id": 1, "name": "alice"},
        _User(2, "bob", False, 4.0),
        {"id": 3, "name": "carol"},
        _User(4, "dave", True, 8.0),
    ]
    result = fl(data, [["name", "^", "a"]])
    assert len(result) == 1
    assert result[0]["name"] == "alice"


# ═════════════════════════════════════════════════════════════════════════════
# dataclass support (getattr fallback path, non-tuple)
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class _UserDC:
    id: int
    name: str
    active: bool
    score: float


@dataclasses.dataclass
class _AddressDC:
    city: str
    zip: str


@dataclasses.dataclass
class _UserWithAddrDC:
    id: int
    name: str
    address: _AddressDC


_DC_USERS = [
    _UserDC(1, "alice", True,  9.5),
    _UserDC(2, "bob",   False, 4.0),
    _UserDC(3, "carol", True,  7.2),
    _UserDC(4, "dave",  False, 7.2),
]


def test_dc_eq():
    assert fl(_DC_USERS, [["name", "=", "alice"]]) == [_DC_USERS[0]]


def test_dc_ne():
    assert fl(_DC_USERS, [["active", "!=", True]]) == [_DC_USERS[1], _DC_USERS[3]]


def test_dc_gt():
    assert fl(_DC_USERS, [["score", ">", 7.2]]) == [_DC_USERS[0]]


def test_dc_in():
    assert fl(_DC_USERS, [["name", "in", ["alice", "carol"]]]) == [
        _DC_USERS[0], _DC_USERS[2]
    ]


def test_dc_startswith():
    assert fl(_DC_USERS, [["name", "^", "a"]]) == [_DC_USERS[0]]


def test_dc_ci_eq():
    assert fl(_DC_USERS, [["name", "C=", "ALICE"]]) == [_DC_USERS[0]]


def test_dc_compound_and():
    result = fl(_DC_USERS, [["active", "=", True], ["score", ">=", 7.2]])
    assert result == [_DC_USERS[0], _DC_USERS[2]]


def test_dc_compound_or():
    result = fl(_DC_USERS, [["OR", [["name", "=", "alice"], ["name", "=", "dave"]]]])
    assert result == [_DC_USERS[0], _DC_USERS[3]]


def test_dc_missing_attr_no_match():
    assert fl(_DC_USERS, [["email", "=", "x"]]) == []


def test_dc_order_by():
    cf = compile_filters([])
    co = compile_options(order_by=["score"])
    result = tnfilter(_DC_USERS, filters=cf, options=co)
    assert [r.score for r in result] == [4.0, 7.2, 7.2, 9.5]


def test_dc_order_by_desc():
    cf = compile_filters([])
    co = compile_options(order_by=["-name"])
    result = tnfilter(_DC_USERS, filters=cf, options=co)
    assert [r.name for r in result] == ["dave", "carol", "bob", "alice"]


def test_dc_select():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(select=["id", "name"])
    result = tnfilter(_DC_USERS, filters=cf, options=co)
    assert result == [{"id": 1, "name": "alice"}, {"id": 3, "name": "carol"}]


def test_dc_limit_offset():
    cf = compile_filters([])
    co = compile_options(order_by=["id"], offset=1, limit=2)
    result = tnfilter(_DC_USERS, filters=cf, options=co)
    assert [r.id for r in result] == [2, 3]


def test_dc_count():
    cf = compile_filters([["active", "=", True]])
    co = compile_options(count=True)
    assert tnfilter(_DC_USERS, filters=cf, options=co) == 2


def test_dc_match_true():
    cf = compile_filters([["active", "=", True], ["score", ">", 5.0]])
    assert match(_DC_USERS[0], filters=cf) is _DC_USERS[0]


def test_dc_match_false():
    cf = compile_filters([["active", "=", True]])
    assert match(_DC_USERS[1], filters=cf) is None


def test_dc_nested_dataclass():
    data = [
        _UserWithAddrDC(1, "alice", _AddressDC("Springfield", "62701")),
        _UserWithAddrDC(2, "bob",   _AddressDC("Shelbyville", "62702")),
    ]
    result = fl(data, [["address.city", "=", "Springfield"]])
    assert len(result) == 1
    assert result[0].name == "alice"


def test_dc_mixed_with_dicts():
    data = [
        {"id": 1, "name": "alice"},
        _UserDC(2, "bob", False, 4.0),
        {"id": 3, "name": "carol"},
        _UserDC(4, "dave", True, 8.0),
    ]
    result = fl(data, [["name", "^", "a"]])
    assert len(result) == 1
    assert result[0]["name"] == "alice"


# ═════════════════════════════════════════════════════════════════════════════
# match() return-value semantics (options / select support)
# ═════════════════════════════════════════════════════════════════════════════

def test_match_no_match_returns_none():
    cf = compile_filters([["id", "=", 99]])
    assert match(BASIC[0], filters=cf) is None


def test_match_no_options_returns_original():
    cf = compile_filters([["id", "=", 1]])
    item = BASIC[0]
    assert match(item, filters=cf) is item


def test_match_options_none_returns_original():
    cf = compile_filters([["id", "=", 1]])
    item = BASIC[0]
    assert match(item, filters=cf, options=None) is item


def test_match_options_without_select_returns_original():
    cf = compile_filters([["id", "=", 1]])
    co = compile_options(order_by=["name"])
    item = BASIC[0]
    assert match(item, filters=cf, options=co) is item


def test_match_select_returns_projected_dict():
    cf = compile_filters([["id", "=", 1]])
    co = compile_options(select=["id", "name"])
    result = match(BASIC[0], filters=cf, options=co)
    assert result == {"id": 1, "name": "alice"}
    assert result is not BASIC[0]


def test_match_select_no_match_returns_none():
    cf = compile_filters([["id", "=", 99]])
    co = compile_options(select=["id", "name"])
    assert match(BASIC[0], filters=cf, options=co) is None


def test_match_select_rename():
    cf = compile_filters([["id", "=", 1]])
    co = compile_options(select=[["name", "username"]])
    result = match(BASIC[0], filters=cf, options=co)
    assert result == {"username": "alice"}


def test_match_select_nested_path():
    item = {"id": 1, "user": {"name": "alice", "role": "admin"}}
    cf = compile_filters([["id", "=", 1]])
    co = compile_options(select=["user.name"])
    result = match(item, filters=cf, options=co)
    assert result == {"user": {"name": "alice"}}
