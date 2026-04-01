# truenas_pyfilter

High-performance compiled filter-list engine. Pre-compiles filter trees and
query options once so the inner iteration loop runs entirely in C with no
Python frame overhead.

## Requirements

- Python 3.13+

## Constants

String constants for all filter operators and `order_by` prefixes. Use these
instead of raw strings to get typo detection at import time.

```python
import truenas_pyfilter as tf

# Operators
tf.FILTER_OP_EQ            # "="
tf.FILTER_OP_NE            # "!="
tf.FILTER_OP_GT            # ">"
tf.FILTER_OP_GE            # ">="
tf.FILTER_OP_LT            # "<"
tf.FILTER_OP_LE            # "<="
tf.FILTER_OP_REGEX         # "~"
tf.FILTER_OP_IN            # "in"
tf.FILTER_OP_NOT_IN        # "nin"
tf.FILTER_OP_REGEX_IN      # "rin"
tf.FILTER_OP_REGEX_NOT_IN  # "rnin"
tf.FILTER_OP_STARTSWITH    # "^"
tf.FILTER_OP_NOT_STARTSWITH # "!^"
tf.FILTER_OP_ENDSWITH      # "$"
tf.FILTER_OP_NOT_ENDSWITH  # "!$"
tf.FILTER_OP_CI_PREFIX     # "C"  — prepend to any operator for case-insensitive match

# order_by prefixes
tf.FILTER_ORDER_NULLS_FIRST_PREFIX  # "nulls_first:"
tf.FILTER_ORDER_NULLS_LAST_PREFIX   # "nulls_last:"
tf.FILTER_ORDER_REVERSE_PREFIX      # "-"
```

---

## `compile_filters(filters)`

Pre-compile a query-filters list into a `CompiledFilters` object.

```python
filters = truenas_pyfilter.compile_filters([
    ["uid", "=", 1000],
])
```

**Parameters:**
- `filters` (list): Filter list. Each element is either a leaf condition or a
  compound node:
  - **Leaf:** `[field, op, value]` — test one field against a value.
  - **OR node:** `["OR", [branch, ...]]` — match if any branch matches. Each
    branch is itself a filters list (list of leaves/OR nodes), so AND-within-OR
    is expressed by putting multiple conditions in the same branch.
  - Multiple top-level conditions are implicitly AND'd.

  Operators:

  | Operator | Meaning |
  |---|---|
  | `=`, `!=` | equality / inequality |
  | `>`, `>=`, `<`, `<=` | comparison |
  | `~` | regex match |
  | `in`, `nin` | value in / not in list |
  | `rin`, `rnin` | list contains / does not contain value |
  | `^`, `!^` | startswith / not startswith |
  | `$`, `!$` | endswith / not endswith |

  Prefix any operator with `C` for case-insensitive matching (`C=`, `C^`, etc.).
  Use the `FILTER_OP_*` and `FILTER_OP_CI_PREFIX` module constants instead of
  raw strings to avoid typos.

  ```python
  import truenas_pyfilter as tf

  # Simple AND (implicit): locked=False AND ssh_password_enabled=True
  tf.compile_filters([
      ["locked", tf.FILTER_OP_EQ, False],
      ["ssh_password_enabled", tf.FILTER_OP_EQ, True],
  ])

  # OR: name="alice" OR name="bob"
  tf.compile_filters([
      ["OR", [
          ["name", tf.FILTER_OP_EQ, "alice"],
          ["name", tf.FILTER_OP_EQ, "bob"],
      ]]
  ])

  # Mixed: (ssh_password_enabled=True AND twofactor=False) OR enabled=True
  tf.compile_filters([
      ["OR", [
          [
              ["ssh_password_enabled", tf.FILTER_OP_EQ, True],
              ["twofactor_auth_configured", tf.FILTER_OP_EQ, False],
          ],
          ["enabled", tf.FILTER_OP_EQ, True],
      ]]
  ])
  ```

**Returns:** `CompiledFilters` — opaque compiled tree, pass directly to
`tnfilter()`. `repr()` shows the original filters list.

---

## `compile_options(**kwargs)`

Pre-parse query options into a `CompiledOptions` object.

```python
options = truenas_pyfilter.compile_options(
    order_by=["name"],
    select=["id", "name"],
    limit=100,
    offset=0,
)
```

**Parameters:**
- `get` (bool): Return first match only; enables short-circuit when
  `order_by` is empty. Default: `False`.
- `count` (bool): Return the count of matched items instead of the items.
  Default: `False`.
- `select` (list[str | list] | None): Fields to project from each result
  entry. Each element is a dotted field path or `[src_path, dest_name]`
  for renaming. Default: `None` (return full items). When `select` is
  specified the output list always contains `dict` items, regardless of
  the input item type.
- `order_by` (list[str] | None): Ordering directives. Prefixes may be
  combined in the order shown:
  - `nulls_first:` — place `None`/absent values before non-`None` values.
  - `nulls_last:` — place `None`/absent values after non-`None` values.
  - `-` — descending sort.

  Example: `"nulls_first:-expiretime"` sorts descending with nulls first.
  When no nulls prefix is given, `None` values are passed to Python's sort
  and will raise `TypeError` if the field can be `None`. Non-empty disables
  short-circuit even when `get=True`. Default: `None`.
  Use the `FILTER_ORDER_*` module constants instead of raw prefix strings.
- `offset` (int): Skip the first N matched items. Default: `0`.
- `limit` (int): Cap results at N items (`0` = no limit). Default: `0`.

**Returns:** `CompiledOptions` — opaque options object, pass directly to
`tnfilter()`. `repr()` shows the kwargs as passed.

---

## `match(item, *, filters)`

Test whether a single item matches all compiled filters.

```python
filters = truenas_pyfilter.compile_filters([["uid", "=", 1000]])

truenas_pyfilter.match({"uid": 1000, "name": "alice"}, filters=filters)  # True
truenas_pyfilter.match({"uid": 1001, "name": "bob"},   filters=filters)  # False
```

**Parameters:**
- `item` (Any): The item to test. Dicts use the fast path; other objects fall
  back to `getattr`.
- `filters` (CompiledFilters): Pre-compiled filter tree from
  `compile_filters()`. **Keyword-only.**

**Returns:** `bool` — `True` if the item matches all filters, `False` otherwise.

---

## `tnfilter(data, *, filters, options)`

Filter an iterable using pre-compiled filters and options.

```python
import truenas_pyfilter

records = [
    {"id": 1, "name": "alice", "uid": 1000},
    {"id": 2, "name": "bob",   "uid": 1001},
    {"id": 3, "name": "carol", "uid": 1000},
]

filters = truenas_pyfilter.compile_filters([["uid", "=", 1000]])
options = truenas_pyfilter.compile_options(order_by=["name"])
results = truenas_pyfilter.tnfilter(records, filters=filters, options=options)
# [{"id": 1, "name": "alice", "uid": 1000},
#  {"id": 3, "name": "carol", "uid": 1000}]
```

**Parameters:**
- `data` (Iterable): Items to filter. Dicts use a fast path; other objects
  fall back to `getattr`.
- `filters` (CompiledFilters): Pre-compiled filter tree from
  `compile_filters()`. **Keyword-only.**
- `options` (CompiledOptions): Pre-compiled options from
  `compile_options()`. **Keyword-only.**

**Returns:** `list` — items that matched all filters, with options applied.

### Field paths

Dotted notation traverses nested dicts: `"a.b.c"`. Escape a literal dot
with a backslash: `"a\\.b"`. Use integer strings for list indexing:
`"items.0.name"`. Use `*` as a wildcard to match any key or list element:
`"tags.*.value"`.
