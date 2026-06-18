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

## `compile_filters(filters, *, model=None)`

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

- `model` (type | None): Pass the pydantic model class here when the compiled
  filter will be run (via `tnfilter`/`match`) against instances of that model.
  **Keyword-only.** This is **required** to filter pydantic models: a compiled
  filter without `model` raises `TypeError` if handed a model instance. Filter
  field paths written as aliases are resolved to attribute names. Must be a
  pydantic model class, else `TypeError`. Resolves **filter** paths only — pass
  the same `model=` to `compile_options` to resolve `order_by`/`select` aliases.

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
  specified the output list contains `dict` items, regardless of the input
  item type — unless `model` is also given, in which case each projected dict
  is passed to `model.model_construct()` and the output contains (partial)
  model instances instead.
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
- `model` (type | None): Pass the pydantic model class when the options will
  be applied to instances of that model. **Keyword-only.** The `select` and
  `order_by` field paths are then resolved from field **alias** to attribute
  name at compile time — the same contract `compile_filters` applies to filter
  paths (unknown field on a strict model raises `ValueError`; `extra='allow'`
  leaves it unchanged). It also turns `select` projections into model instances
  via `model.model_construct()` (see `select`). Must be a pydantic model class,
  else `TypeError`. Default: `None`.

**Returns:** `CompiledOptions` — opaque options object, pass directly to
`tnfilter()`. `repr()` shows the kwargs as passed.

---

## `match(item, *, filters, options=None)`

Test whether a single item matches all compiled filters, optionally projecting
matched items via `select`.

```python
filters = truenas_pyfilter.compile_filters([["uid", "=", 1000]])

# Basic usage — returns the original item or None
truenas_pyfilter.match({"uid": 1000, "name": "alice"}, filters=filters)
# {"uid": 1000, "name": "alice"}

truenas_pyfilter.match({"uid": 1001, "name": "bob"}, filters=filters)
# None

# With select — returns a projected dict on match
options = truenas_pyfilter.compile_options(select=["name"])
truenas_pyfilter.match({"uid": 1000, "name": "alice"}, filters=filters, options=options)
# {"name": "alice"}
```

**Parameters:**
- `item` (Any): The item to test. Dicts use the fast path; other objects fall
  back to `getattr`.
- `filters` (CompiledFilters): Pre-compiled filter tree from
  `compile_filters()`. **Keyword-only.**
- `options` (CompiledOptions | None): Pre-compiled options from
  `compile_options()`. Only the `select` field is applied; `order_by`, `count`,
  `offset`, and `limit` are ignored for single-item matching.
  **Keyword-only. Default: `None`.**

**Returns:**

| Condition | Return value |
|---|---|
| Item does not match | `None` |
| Item matches, no `select` in options | Original `item` (unchanged) |
| Item matches, `select` in options | New projected `dict` (or a `model_construct` instance when `options` carry a `model`) |

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

### Item types and field access

Each path component is resolved against the current value by type:

- **dict** — `dict[key]`.
- **list / tuple** — integer index or `*` wildcard; a non-numeric component
  falls back to `getattr` (supports `NamedTuple` fields).
- **any other object** — `getattr(obj, name)`. This makes dataclasses,
  `NamedTuple`s and arbitrary objects filterable with no per-item conversion.

A missing dict key or attribute means "no match" for that filter.

**Pydantic models** are filtered directly, including `computed_field`
properties, `extra='allow'` fields, `PrivateAttr`s, and unset fields. The
filter **must** have been compiled with `model=` (see
`compile_filters`); passing a model instance to a filter compiled without it
raises `TypeError`. To filter by field **alias** pass `model=` to
`compile_filters` (which resolves aliases at compile time); pass the same
`model=` to `compile_options` to resolve `order_by`/`select` aliases.
