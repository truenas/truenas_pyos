// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef FILTER_LIST_H
#define FILTER_LIST_H

#include <Python.h>
#include "common/includes.h"

/*
 * fl_state_t — per-module singleton holding cached Python objects.
 *
 * Initialised once when the extension module is loaded and held for the
 * module's lifetime.  Avoids repeated attribute lookups and string
 * allocations on hot code paths.
 */
typedef struct {
    PyObject *casefold_str;  /* interned "casefold" */
    PyObject *re_compile;    /* re.compile callable  */
    PyObject *empty_str;     /* cached ""            */
} fl_state_t;

/*
 * compiled_filter_t — a single node in a compiled filter tree.
 *
 * Opaque here; the full definition lives in filter_list.c.  Each node
 * represents one [field, op, value] triple or a logical combinator
 * (AND/OR).  Trees are built by compile_filter() and evaluated by
 * filter_list_run() / match_item().
 */
typedef struct compiled_filter compiled_filter_t;

/*
 * compiled_select_spec_t — a single field projection compiled from a
 * select list entry.
 *
 * A select entry is either a plain dotted path string ("a.b.c") or a
 * two-element rename pair (["a.b", "x"]).  At compile time the path is
 * split into a PyUnicode key array (keys/nkeys) and each segment is
 * tested for numeric list-index syntax; the results are cached in
 * key_indices so the per-item traversal loop needs no string parsing.
 *
 * Memory: keys and key_indices are PyMem_RawMalloc'd parallel arrays of
 * length nkeys, owned by this struct.  rename is an owned PyObject ref,
 * or NULL for plain paths.  free_select_specs() releases everything.
 */
typedef struct {
    PyObject **keys;         /* owned array of PyUnicode path components */
    Py_ssize_t *key_indices; /* parallel: >= 0 = list index, -1 = attr name */
    Py_ssize_t nkeys;
    PyObject *rename;        /* non-NULL: flat output key for [target, rename] specs */
} compiled_select_spec_t;

/*
 * compiled_order_spec_t — a single ordering directive compiled from one
 * order_by string (e.g. "nulls_first:-user.score").
 *
 * The directive is parsed at compile time into:
 *   - keys/key_indices/nkeys: the dotted field path, split and
 *     index-tested the same way as compiled_select_spec_t.
 *   - top_key: the unsplit field string (after stripping the nulls and
 *     reverse prefixes), used by partition_nulls() for the fast
 *     top-level dict lookup that separates None values before sorting.
 *   - reverse: true if the "-" prefix was present.
 *   - nulls_mode: controls placement of None / absent-key entries.
 *
 * Memory: same ownership rules as compiled_select_spec_t.  top_key is
 * an additional owned ref.  free_order_specs() releases everything.
 */
typedef struct {
    PyObject **keys;         /* owned array of PyUnicode path components */
    Py_ssize_t *key_indices; /* parallel: >= 0 = list index, -1 = attr name */
    Py_ssize_t nkeys;
    PyObject *top_key;       /* full field string for null detection (owned) */
    bool reverse;
    int nulls_mode;          /* 0 = none, 1 = nulls_first, 2 = nulls_last */
} compiled_order_spec_t;

/*
 * CompiledFiltersObject — Python-visible object wrapping a compiled filter tree.
 *
 * Created by compile_filters() and passed to tnfilter() / match().
 * Holds an array of top-level compiled_filter_t pointers; each may
 * itself be a subtree.  Multiple top-level filters are implicitly AND'd.
 * repr_str caches the __repr__ result (computed lazily, NULL until first
 * use).
 */
typedef struct {
    PyObject_HEAD
    compiled_filter_t **filters;
    Py_ssize_t nfilters;
    PyObject *repr_str;
} CompiledFiltersObject;

/*
 * CompiledOptionsObject — Python-visible object wrapping compiled post-filter
 * options (select, order_by, count, offset, limit).
 *
 * Created by compile_options() and passed to tnfilter().  All string
 * parsing and path splitting happens at compile time; the per-item
 * hot paths in apply_options() operate only on pre-compiled arrays.
 *
 *   shortcircuit — stop after the first match (get=True with no order_by).
 *   count_flag   — return item count instead of the list.
 *   select_specs — field projection specs; NULL / nselect==0 means no projection.
 *   order_specs  — ordering directives applied in reverse spec order (so
 *                  specs[0] is the primary key); NULL / norder==0 means no sort.
 *   offset/limit — applied after ordering; limit==0 means no cap.
 *   repr_str     — lazily cached __repr__.
 */
typedef struct {
    PyObject_HEAD
    bool shortcircuit;
    bool count_flag;
    compiled_select_spec_t *select_specs;
    Py_ssize_t nselect;
    compiled_order_spec_t *order_specs;
    Py_ssize_t norder;
    Py_ssize_t offset;
    Py_ssize_t limit;
    PyObject *repr_str;
} CompiledOptionsObject;

/* -- pre-compiled type objects ----------------------------------------------- */

extern PyTypeObject CompiledFilters_Type;
extern PyTypeObject CompiledOptions_Type;

/* -- internal functions used by truenas_pyfilter.c ----------------------- */

compiled_filter_t *compile_filter(PyObject *f, fl_state_t *state, int depth);
void free_cf_array(compiled_filter_t **arr, Py_ssize_t n);
PyObject *filter_list_run(PyObject *data,
                          compiled_filter_t * const *compiled,
                          Py_ssize_t nfilters, bool shortcircuit,
                          fl_state_t *state);
bool match_item(PyObject *item, compiled_filter_t * const *compiled,
                Py_ssize_t nfilters, fl_state_t *state, bool *matchp);

/* filter_options.c */
void free_select_specs(compiled_select_spec_t *specs, Py_ssize_t n);
void free_order_specs(compiled_order_spec_t *specs, Py_ssize_t n);
int compile_select_specs(PyObject *select_val,
                         compiled_select_spec_t **out_specs, Py_ssize_t *out_n);
int compile_order_specs(PyObject *order_by_val,
                        compiled_order_spec_t **out_specs, Py_ssize_t *out_n);
PyObject *apply_select_item(PyObject *item,
                            compiled_select_spec_t *specs, Py_ssize_t nspecs);
PyObject *apply_select(PyObject *list,
                       compiled_select_spec_t *specs, Py_ssize_t nspecs);
PyObject *apply_order(PyObject *list,
                      compiled_order_spec_t *specs, Py_ssize_t nspecs);
PyObject *apply_options(PyObject *filtered, CompiledOptionsObject *co);

#endif /* FILTER_LIST_H */
