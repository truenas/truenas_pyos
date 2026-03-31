// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef FILTER_LIST_H
#define FILTER_LIST_H

#include <Python.h>
#include "common/includes.h"

/* -- module state ------------------------------------------------------------ */

typedef struct {
    PyObject *casefold_str;  /* interned "casefold" */
    PyObject *re_compile;    /* re.compile callable  */
    PyObject *empty_str;     /* cached ""            */
} fl_state_t;

/* -- filter tree node (opaque; full definition in filter_list.c) ------------- */

typedef struct compiled_filter compiled_filter_t;

/* -- pre-compiled select spec ------------------------------------------------ */

typedef struct {
    PyObject **keys;   /* owned array of PyUnicode path components */
    Py_ssize_t nkeys;
    PyObject *rename;  /* non-NULL: flat output key for [target, rename] specs */
} compiled_select_spec_t;

/* -- pre-compiled order_by directive ----------------------------------------- */

typedef struct {
    PyObject **keys;      /* owned array of PyUnicode path components */
    Py_ssize_t nkeys;
    PyObject *top_key;    /* full field string for null detection (owned) */
    bool reverse;
    int nulls_mode;       /* 0 = none, 1 = nulls_first, 2 = nulls_last */
} compiled_order_spec_t;

/* -- pre-compiled Python object layouts -------------------------------------- */

typedef struct {
    PyObject_HEAD
    compiled_filter_t **filters;
    Py_ssize_t nfilters;
    PyObject *repr_str;
} CompiledFiltersObject;

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
PyObject *apply_select(PyObject *list,
                       compiled_select_spec_t *specs, Py_ssize_t nspecs);
PyObject *apply_order(PyObject *list,
                      compiled_order_spec_t *specs, Py_ssize_t nspecs);
PyObject *apply_options(PyObject *filtered, CompiledOptionsObject *co);

#endif /* FILTER_LIST_H */
