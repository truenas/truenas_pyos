// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Result-mutation passes: select projection, order_by, count, offset, limit.
 *
 * These operate on the already-filtered list produced by filter_list_run()
 * and correspond to the Python-level do_select / do_order / do_count helpers
 * in middlewared/utils/filter_list.py.
 *
 * Select paths and order_by directives are pre-split into PyUnicode key arrays
 * at compile_options() time (compile_select_specs / compile_order_specs), so
 * the per-item hot paths contain no string scanning at all.
 */

#include "filter_list.h"
#include <string.h>

/* ===========================================================================
 * Path splitting (compile-time only)
 *
 * Split a dotted PyUnicode path into an owned array of PyUnicode key
 * components, handling backslash-escaped dots (same convention as
 * split_path in filter_list.c).
 *
 * Examples:
 *   "foo.bar.baz"  ->  ["foo", "bar", "baz"]
 *   "foo\.bar.baz" ->  ["foo.bar", "baz"]
 * =========================================================================== */

static int
opt_split_keys(PyObject *path_obj,
               PyObject ***out_keys, Py_ssize_t **out_indices,
               Py_ssize_t *out_nkeys)
{
    PyObject *dot_str = NULL;
    PyObject *bslash_str = NULL;
    PyObject *pieces = NULL;
    PyObject *seg = NULL, *tmp = NULL;
    PyObject **keys = NULL;
    Py_ssize_t *indices = NULL;
    Py_ssize_t npieces, nkeys, i;
    const char *seg_c;
    char *endp;
    long idx;
    int tm;

    dot_str = PyUnicode_FromString(".");
    if (!dot_str)
        return -1;
    bslash_str = PyUnicode_FromString("\\");
    if (!bslash_str) {
        Py_DECREF(dot_str);
        return -1;
    }

    pieces = PyUnicode_Split(path_obj, dot_str, -1);
    Py_DECREF(dot_str);
    if (!pieces) {
        Py_DECREF(bslash_str);
        return -1;
    }

    npieces = PyList_GET_SIZE(pieces);
    keys = PyMem_RawCalloc((size_t)npieces, sizeof(PyObject *));
    if (!keys) {
        PyErr_NoMemory();
        Py_DECREF(bslash_str);
        Py_DECREF(pieces);
        return -1;
    }
    indices = PyMem_RawMalloc((size_t)npieces * sizeof(Py_ssize_t));
    if (!indices) {
        PyErr_NoMemory();
        PyMem_RawFree(keys);
        Py_DECREF(bslash_str);
        Py_DECREF(pieces);
        return -1;
    }

    nkeys = 0;
    i = 0;
    while (i < npieces) {
        seg = Py_NewRef(PyList_GET_ITEM(pieces, i++));

        while (i < npieces) {
            tm = PyUnicode_Tailmatch(seg, bslash_str, 0, PY_SSIZE_T_MAX, 1);
            if (tm < 0) {
                Py_DECREF(seg);
                goto error;
            }
            if (!tm) break;
            tmp = PyUnicode_Substring(seg, 0, PyUnicode_GET_LENGTH(seg) - 1);
            Py_DECREF(seg);
            if (!tmp) goto error;
            seg = PyUnicode_FromFormat("%U.%U", tmp, PyList_GET_ITEM(pieces, i++));
            Py_DECREF(tmp);
            if (!seg) goto error;
        }

        /* Pre-parse numeric index: ASCII digits only, non-negative, no overflow. */
        seg_c = PyUnicode_AsUTF8(seg);
        if (!seg_c) {
            Py_DECREF(seg);
            goto error;
        }
        errno = 0;
        idx = strtol(seg_c, &endp, 10);
        if (*endp == '\0' && seg_c[0] != '\0' && idx >= 0 &&
            errno == 0 && idx <= (long)PY_SSIZE_T_MAX)
            indices[nkeys] = (Py_ssize_t)idx;
        else
            indices[nkeys] = -1;

        keys[nkeys++] = seg; /* steal ref */
    }

    Py_DECREF(bslash_str);
    Py_DECREF(pieces);
    *out_keys = keys;
    *out_indices = indices;
    *out_nkeys = nkeys;
    return 0;

error:
    for (i = 0; i < nkeys; i++)
        Py_DECREF(keys[i]);
    PyMem_RawFree(keys);
    PyMem_RawFree(indices);
    Py_DECREF(bslash_str);
    Py_DECREF(pieces);
    return -1;
}

/* ===========================================================================
 * Compile-time: build compiled_select_spec_t and compiled_order_spec_t arrays
 * =========================================================================== */

void
free_select_specs(compiled_select_spec_t *specs, Py_ssize_t n)
{
    Py_ssize_t i, j;

    if (!specs) return;
    for (i = 0; i < n; i++) {
        for (j = 0; j < specs[i].nkeys; j++)
            Py_DECREF(specs[i].keys[j]);
        PyMem_RawFree(specs[i].keys);
        PyMem_RawFree(specs[i].key_indices);
        Py_XDECREF(specs[i].rename);
    }
    PyMem_RawFree(specs);
}

void
free_order_specs(compiled_order_spec_t *specs, Py_ssize_t n)
{
    Py_ssize_t i, j;

    if (!specs) return;
    for (i = 0; i < n; i++) {
        for (j = 0; j < specs[i].nkeys; j++)
            Py_DECREF(specs[i].keys[j]);
        PyMem_RawFree(specs[i].keys);
        PyMem_RawFree(specs[i].key_indices);
        Py_XDECREF(specs[i].top_key);
    }
    PyMem_RawFree(specs);
}

int
compile_select_specs(PyObject *select_val,
                     compiled_select_spec_t **out_specs, Py_ssize_t *out_n)
{
    Py_ssize_t nspecs;
    compiled_select_spec_t *specs = NULL;
    Py_ssize_t si;
    PyObject *spec = NULL, *target = NULL, *rename = NULL;
    int owned;

    nspecs = PySequence_Size(select_val);
    if (nspecs < 0)
        return -1;

    specs = PyMem_RawCalloc((size_t)nspecs, sizeof(*specs));
    if (!specs) {
        PyErr_NoMemory();
        return -1;
    }

    for (si = 0; si < nspecs; si++) {
        spec = PySequence_GetItem(select_val, si);
        if (!spec)
            goto error;

        owned = 0;
        if (PyUnicode_Check(spec)) {
            target = spec; /* borrowed from select_val */
            rename = NULL;
        } else if (PyList_Check(spec) || PyTuple_Check(spec)) {
            if (PySequence_Size(spec) != 2) {
                Py_DECREF(spec);
                PyErr_SetString(PyExc_ValueError,
                    "filter_list: select spec list must be [target, new_name]");
                goto error;
            }
            target = PySequence_GetItem(spec, 0);
            rename = PySequence_GetItem(spec, 1);
            owned = 1;
            if (!target || !rename) {
                Py_XDECREF(target);
                Py_XDECREF(rename);
                Py_DECREF(spec);
                goto error;
            }
        } else {
            Py_DECREF(spec);
            PyErr_SetString(PyExc_TypeError,
                "filter_list: select spec must be str or [str, str]");
            goto error;
        }

        if (opt_split_keys(target, &specs[si].keys, &specs[si].key_indices, &specs[si].nkeys) < 0) {
            if (owned) {
                Py_DECREF(target);
                Py_XDECREF(rename);
            }
            Py_DECREF(spec);
            goto error;
        }

        specs[si].rename = rename; /* steal ref, or NULL */
        if (owned)
            Py_DECREF(target);
        Py_DECREF(spec);
    }

    *out_specs = specs;
    *out_n = nspecs;
    return 0;

error:
    free_select_specs(specs, si);
    return -1;
}

#define NULLS_FIRST_PFX  "nulls_first:"
#define NULLS_LAST_PFX   "nulls_last:"
#define NULLS_FIRST_LEN  12
#define NULLS_LAST_LEN   11

int
compile_order_specs(PyObject *order_by_val,
                    compiled_order_spec_t **out_specs, Py_ssize_t *out_n)
{
    Py_ssize_t nspecs;
    compiled_order_spec_t *specs = NULL;
    Py_ssize_t di;
    PyObject *item = NULL;
    const char *d = NULL, *field = NULL;
    PyObject *field_obj = NULL;
    int nulls_mode;
    bool reverse;

    nspecs = PySequence_Size(order_by_val);
    if (nspecs < 0)
        return -1;

    specs = PyMem_RawCalloc((size_t)nspecs, sizeof(*specs));
    if (!specs) {
        PyErr_NoMemory();
        return -1;
    }

    for (di = 0; di < nspecs; di++) {
        item = PySequence_GetItem(order_by_val, di);
        if (!item)
            goto error;

        d = PyUnicode_AsUTF8(item);
        if (!d) {
            Py_DECREF(item);
            goto error;
        }

        nulls_mode = 0;
        reverse = false;
        field = d;

        if (strncmp(d, NULLS_FIRST_PFX, NULLS_FIRST_LEN) == 0) {
            nulls_mode = 1;
            field = d + NULLS_FIRST_LEN;
        } else if (strncmp(d, NULLS_LAST_PFX, NULLS_LAST_LEN) == 0) {
            nulls_mode = 2;
            field = d + NULLS_LAST_LEN;
        }

        if (field[0] == '-') {
            reverse = true;
            field++;
        }

        if (field[0] == '\0') {
            Py_DECREF(item);
            PyErr_SetString(PyExc_ValueError,
                            "filter_list: order_by field name is empty");
            goto error;
        }

        field_obj = PyUnicode_FromString(field);
        Py_DECREF(item);
        if (!field_obj)
            goto error;

        if (opt_split_keys(field_obj, &specs[di].keys, &specs[di].key_indices, &specs[di].nkeys) < 0) {
            Py_DECREF(field_obj);
            goto error;
        }

        specs[di].top_key = field_obj; /* steal ref */
        specs[di].reverse = reverse;
        specs[di].nulls_mode = nulls_mode;
    }

    *out_specs = specs;
    *out_n = nspecs;
    return 0;

error:
    free_order_specs(specs, di);
    return -1;
}

/* ===========================================================================
 * Runtime value traversal helpers
 * =========================================================================== */

/*
 * Traverse nested dicts/lists following a pre-split key array.
 * Used for order_by sort-key extraction.
 *
 * Returns a new owned reference to the leaf value on success, or a new
 * owned reference to Py_None when the path is absent (*found set to 0).
 * Returns NULL only when an exception has been set.
 *
 * cur is kept as an owned reference throughout the loop so that mixed
 * paths (e.g. getattr then dict-lookup) do not leak the intermediate
 * owned ref when ownership changes mid-path.
 */
static PyObject *
opt_traverse_keys(PyObject *item, PyObject **keys, Py_ssize_t *key_indices,
                  Py_ssize_t nkeys, int *found)
{
    PyObject *cur = Py_NewRef(item); /* always an owned reference */
    PyObject *val = NULL;
    Py_ssize_t n, i;

    *found = 1;

    for (i = 0; i < nkeys; i++) {
        if (PyDict_CheckExact(cur)) {
            val = PyDict_GetItemWithError(cur, keys[i]);
            Py_DECREF(cur);
            if (!val) {
                if (PyErr_Occurred())
                    return NULL;
                *found = 0;
                Py_RETURN_NONE;
            }
            cur = Py_NewRef(val);
        } else if (PyList_Check(cur) || PyTuple_Check(cur)) {
            if (key_indices[i] >= 0) {
                /* Numeric index: pre-parsed at compile time */
                n = PySequence_Fast_GET_SIZE(cur);
                val = (key_indices[i] < n)
                      ? PySequence_Fast_GET_ITEM(cur, key_indices[i])
                      : Py_None;
                Py_SETREF(cur, Py_NewRef(val));
            } else {
                /* Non-numeric key: try getattr for NamedTuple support */
                val = PyObject_GetAttr(cur, keys[i]);
                Py_DECREF(cur);
                if (!val) {
                    if (!PyErr_ExceptionMatches(PyExc_AttributeError))
                        return NULL;
                    PyErr_Clear();
                    *found = 0;
                    Py_RETURN_NONE;
                }
                cur = val;
            }
        } else {
            /* Non-dict, non-sequence: try getattr for dataclasses and
             * other custom objects.  Missing attribute → absent. */
            val = PyObject_GetAttr(cur, keys[i]);
            Py_DECREF(cur);
            if (!val) {
                if (!PyErr_ExceptionMatches(PyExc_AttributeError))
                    return NULL;
                PyErr_Clear();
                *found = 0;
                Py_RETURN_NONE;
            }
            cur = val;
        }
    }

    return cur;
}

/*
 * Traverse dict-only path following a pre-split key array.
 * Used for select projection.
 *
 * List/tuple mid-path raises ValueError.
 * Non-dict non-list returns *found=0 (path absent).
 *
 * Returns a new owned reference to the leaf value on success, or a new
 * owned reference to Py_None when the path is absent (*found set to 0).
 * Returns NULL only when an exception has been set.
 *
 * cur is kept as an owned reference throughout the loop so that mixed
 * paths (e.g. getattr then dict-lookup) do not leak intermediate owned refs.
 */
static PyObject *
opt_select_traverse(PyObject *item, PyObject **keys, Py_ssize_t nkeys, int *found)
{
    PyObject *cur = Py_NewRef(item); /* always an owned reference */
    PyObject *val = NULL;
    Py_ssize_t i;

    *found = 1;

    for (i = 0; i < nkeys; i++) {
        if (PyList_Check(cur) || PyTuple_Check(cur)) {
            /*
             * Try getattr to support NamedTuple and other tuple subclasses.
             * Plain lists never have named attributes — keep the ValueError.
             */
            int was_tuple = PyTuple_Check(cur);
            val = PyObject_GetAttr(cur, keys[i]);
            Py_DECREF(cur);
            if (!val) {
                if (!PyErr_ExceptionMatches(PyExc_AttributeError))
                    return NULL;
                PyErr_Clear();
                if (was_tuple) {
                    /* NamedTuple missing field: treat as absent */
                    *found = 0;
                    Py_RETURN_NONE;
                }
                PyErr_SetString(PyExc_ValueError,
                                "filter_list: selecting by list index is not supported");
                return NULL;
            }
            cur = val;
            continue;
        }
        if (!PyDict_CheckExact(cur)) {
            /* Non-dict, non-sequence: try getattr for dataclasses and
             * other custom objects.  Missing attribute → absent. */
            val = PyObject_GetAttr(cur, keys[i]);
            Py_DECREF(cur);
            if (!val) {
                if (!PyErr_ExceptionMatches(PyExc_AttributeError))
                    return NULL;
                PyErr_Clear();
                *found = 0;
                Py_RETURN_NONE;
            }
            cur = val;
            continue;
        }
        val = PyDict_GetItemWithError(cur, keys[i]);
        Py_DECREF(cur);
        if (!val) {
            if (PyErr_Occurred())
                return NULL;
            *found = 0;
            Py_RETURN_NONE;
        }
        cur = Py_NewRef(val);
    }

    return cur;
}

/* ===========================================================================
 * select implementation
 * =========================================================================== */

#define SELECT_MAX_DEPTH 64

/*
 * Apply one select spec to item, writing the result into entry.
 * If spec->rename is set, stores value at that flat key.
 * Otherwise reconstructs the nested dict path in entry.
 * Returns 0 on success, -1 on error (exception set).
 */
static int
opt_select_apply_spec(PyObject *item, PyObject *entry,
                      const compiled_select_spec_t *spec)
{
    PyObject *value = NULL;
    PyObject *obj = NULL, *sub = NULL;
    Py_ssize_t ki;
    int found, rv;

    value = opt_select_traverse(item, spec->keys, spec->nkeys, &found);
    if (!value)
        return -1;
    if (!found) {
        rv = 0; /* path absent - skip */
        goto done;
    }

    if (spec->rename) {
        rv = PyDict_SetItem(entry, spec->rename, value);
        goto done;
    }

    /* No rename: reconstruct nested dict structure in output. */
    obj = entry;
    for (ki = 0; ki < spec->nkeys - 1; ki++) {
        sub = PyDict_GetItemWithError(obj, spec->keys[ki]);
        if (!sub) {
            if (PyErr_Occurred()) {
                rv = -1;
                goto done;
            }
            sub = PyDict_New();
            if (!sub) {
                rv = -1;
                goto done;
            }
            rv = PyDict_SetItem(obj, spec->keys[ki], sub);
            Py_DECREF(sub);
            if (rv < 0)
                goto done;
            sub = PyDict_GetItemWithError(obj, spec->keys[ki]);
            if (!sub) {
                rv = PyErr_Occurred() ? -1 : 0;
                goto done;
            }
        }
        obj = sub;
    }
    rv = PyDict_SetItem(obj, spec->keys[spec->nkeys - 1], value);

done:
    Py_DECREF(value);
    return rv;
}

/*
 * Apply select projection to a single item.
 * Returns a new dict, or NULL on error (exception set).
 */
static PyObject *
opt_select_item(PyObject *item, compiled_select_spec_t *specs, Py_ssize_t nspecs)
{
    PyObject *entry;
    Py_ssize_t si;

    entry = PyDict_New();
    if (!entry)
        return NULL;

    for (si = 0; si < nspecs; si++) {
        if (opt_select_apply_spec(item, entry, &specs[si]) < 0) {
            Py_DECREF(entry);
            return NULL;
        }
    }

    return entry;
}

/*
 * Apply select to every item in list.
 * Returns a new list of projected dicts, or NULL on error (exception set).
 */
PyObject *
apply_select(PyObject *list, compiled_select_spec_t *specs, Py_ssize_t nspecs)
{
    Py_ssize_t n = PyList_GET_SIZE(list);
    PyObject *result = NULL;
    PyObject *projected = NULL;
    Py_ssize_t i;

    result = PyList_New(n);
    if (!result)
        return NULL;

    for (i = 0; i < n; i++) {
        projected = opt_select_item(PyList_GET_ITEM(list, i), specs, nspecs);
        if (!projected) {
            Py_DECREF(result);
            return NULL;
        }
        PyList_SET_ITEM(result, i, projected);
    }

    return result;
}

/* ===========================================================================
 * order_by implementation
 *
 * Null detection uses spec->top_key (the full field string) for a simple
 * top-level dict lookup, matching the Python reference behaviour.
 * Sort-key extraction uses opt_traverse_keys over the pre-split key array.
 *
 * Sort algorithm: build (sort_key, original_index) tuples, hand to
 * PyList_Sort() - Python's own timsort - then reconstruct the result list.
 * =========================================================================== */

/*
 * Partition list into (nulls, non_nulls) via a top-level dict lookup for
 * spec->top_key being absent or None.
 * Returns 0 on success, -1 on error (exception set).
 */
static int
partition_nulls(PyObject *list, PyObject *top_key,
                PyObject *nulls, PyObject *non_nulls)
{
    Py_ssize_t n = PyList_GET_SIZE(list);
    PyObject *item = NULL, *val = NULL, *bucket = NULL;
    Py_ssize_t i;

    for (i = 0; i < n; i++) {
        item = PyList_GET_ITEM(list, i);
        val = NULL;
        if (PyDict_CheckExact(item)) {
            val = PyDict_GetItemWithError(item, top_key);
            if (!val && PyErr_Occurred())
                return -1;
        }
        bucket = (!val || val == Py_None) ? nulls : non_nulls;
        if (PyList_Append(bucket, item) < 0)
            return -1;
    }
    return 0;
}

/*
 * Build a list of (sort_key, original_index) tuples from list.
 * Returns a new list, or NULL on error (exception set).
 */
static PyObject *
build_sort_pairs(PyObject *list, compiled_order_spec_t *spec)
{
    Py_ssize_t m = PyList_GET_SIZE(list);
    PyObject *pairs = NULL;
    PyObject *raw = NULL, *idx = NULL, *pair = NULL;
    Py_ssize_t i;
    int found;

    pairs = PyList_New(m);
    if (!pairs)
        return NULL;

    for (i = 0; i < m; i++) {
        raw = opt_traverse_keys(PyList_GET_ITEM(list, i),
                                spec->keys, spec->key_indices, spec->nkeys, &found);
        if (!raw) {
            Py_DECREF(pairs);
            return NULL;
        }

        idx = PyLong_FromSsize_t(i);
        if (!idx) {
            Py_DECREF(raw);
            Py_DECREF(pairs);
            return NULL;
        }

        pair = PyTuple_New(2);
        if (!pair) {
            Py_DECREF(raw);
            Py_DECREF(idx);
            Py_DECREF(pairs);
            return NULL;
        }

        PyTuple_SET_ITEM(pair, 0, raw);
        PyTuple_SET_ITEM(pair, 1, idx);
        PyList_SET_ITEM(pairs, i, pair);
    }

    return pairs;
}

/*
 * Reconstruct a list by indexing source in the order given by sorted pairs.
 * Returns a new list, or NULL on error (exception set).
 */
static PyObject *
reconstruct_from_pairs(PyObject *source, PyObject *pairs)
{
    Py_ssize_t m = PyList_GET_SIZE(pairs);
    PyObject *result = NULL;
    PyObject *item = NULL;
    Py_ssize_t i, orig;

    result = PyList_New(m);
    if (!result)
        return NULL;

    for (i = 0; i < m; i++) {
        orig = PyLong_AsSsize_t(PyTuple_GET_ITEM(PyList_GET_ITEM(pairs, i), 1));
        if (orig < 0 && PyErr_Occurred()) {
            Py_DECREF(result);
            return NULL;
        }
        item = PyList_GET_ITEM(source, orig);
        PyList_SET_ITEM(result, i, Py_NewRef(item));
    }

    return result;
}

/*
 * Sort list by a pre-compiled order spec.
 * Returns a new sorted list, or NULL on error (exception set).
 */
static PyObject *
sort_by_spec(PyObject *list, compiled_order_spec_t *spec)
{
    Py_ssize_t n = PyList_GET_SIZE(list);
    PyObject *nulls = NULL;
    PyObject *non_nulls = NULL;
    PyObject *pairs = NULL, *sorted_non = NULL;
    PyObject *first = NULL, *second = NULL, *result = NULL;

    if (n <= 1)
        return Py_NewRef(list);

    if (spec->nulls_mode != 0) {
        nulls = PyList_New(0);
        non_nulls = PyList_New(0);
        if (!nulls || !non_nulls)
            goto fail;
        if (partition_nulls(list, spec->top_key, nulls, non_nulls) < 0)
            goto fail;
    } else {
        non_nulls = Py_NewRef(list);
    }

    pairs = build_sort_pairs(non_nulls, spec);
    if (!pairs)
        goto fail;

    if (PyList_Sort(pairs) < 0) {
        Py_DECREF(pairs);
        goto fail;
    }
    if (spec->reverse)
        PyList_Reverse(pairs);

    sorted_non = reconstruct_from_pairs(non_nulls, pairs);
    Py_DECREF(pairs);
    Py_CLEAR(non_nulls);
    if (!sorted_non)
        goto fail;

    if (spec->nulls_mode == 0)
        return sorted_non;

    first = (spec->nulls_mode == 1) ? nulls : sorted_non;
    second = (spec->nulls_mode == 1) ? sorted_non : nulls;
    result = PySequence_Concat(first, second);
    Py_DECREF(nulls);
    Py_DECREF(sorted_non);
    return result;

fail:
    Py_XDECREF(nulls);
    Py_XDECREF(non_nulls);
    return NULL;
}

PyObject *
apply_order(PyObject *list, compiled_order_spec_t *specs, Py_ssize_t nspecs)
{
    PyObject *rv = NULL;
    PyObject *sorted = NULL;
    Py_ssize_t di;

    if (nspecs == 0)
        return Py_NewRef(list);

    rv = Py_NewRef(list);

    /*
     * Iterate in reverse so that specs[0] is the primary (most significant)
     * sort key.  Timsort is stable: each pass preserves the order established
     * by later passes, so applying specs[nspecs-1] first and specs[0] last
     * means ties at every level are broken by the next spec in the original
     * order_by list.
     */
    for (di = nspecs - 1; di >= 0; di--) {
        sorted = sort_by_spec(rv, &specs[di]);
        Py_DECREF(rv);
        if (!sorted)
            return NULL;
        rv = sorted;
    }

    return rv;
}

/* ===========================================================================
 * apply_options
 *
 * Post-filter pipeline: select -> count -> order -> offset -> limit.
 * Order matches Python's filter_list().
 * =========================================================================== */

PyObject *
apply_options(PyObject *filtered, CompiledOptionsObject *co)
{
    PyObject *rv = NULL;
    PyObject *tmp = NULL;
    Py_ssize_t n, start, end;

    if (co->nselect > 0) {
        rv = apply_select(filtered, co->select_specs, co->nselect);
        if (!rv)
            return NULL;
    } else {
        rv = Py_NewRef(filtered);
    }

    if (co->count_flag) {
        n = PyList_GET_SIZE(rv);
        Py_DECREF(rv);
        return PyLong_FromSsize_t(n);
    }

    if (co->norder > 0) {
        tmp = apply_order(rv, co->order_specs, co->norder);
        Py_DECREF(rv);
        if (!tmp)
            return NULL;
        rv = tmp;
    }

    if (co->offset > 0) {
        n = PyList_GET_SIZE(rv);
        start = (co->offset < n) ? co->offset : n;
        tmp = PyList_GetSlice(rv, start, n);
        Py_DECREF(rv);
        if (!tmp)
            return NULL;
        rv = tmp;
    }

    if (co->limit > 0) {
        n = PyList_GET_SIZE(rv);
        end = (co->limit < n) ? co->limit : n;
        tmp = PyList_GetSlice(rv, 0, end);
        Py_DECREF(rv);
        if (!tmp)
            return NULL;
        rv = tmp;
    }

    return rv;
}
