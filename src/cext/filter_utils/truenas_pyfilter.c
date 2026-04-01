// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "filter_list.h"

#define MODULE_DOC "TrueNAS filter-list functions (C extension)"

/* -- module method implementations -------------------------------------------- */

static PyObject *
py_compile_filters(PyObject *self, PyObject *args, PyObject *kwargs)
{
    PyObject *filters_obj = NULL;
    fl_state_t *state = NULL;
    Py_ssize_t nfilters;
    compiled_filter_t **compiled = NULL;
    Py_ssize_t i;
    PyObject *f = NULL;
    PyObject *repr_str = NULL;
    CompiledFiltersObject *obj = NULL;

    static const char *kwnames[] = { "filters", NULL };

    filters_obj = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O",
                                     discard_const_p(char *, kwnames),
                                     &filters_obj))
        return NULL;

    state = (fl_state_t *)PyModule_GetState(self);
    if (!state) {
        PyErr_SetString(PyExc_RuntimeError, "tnfilter: cannot retrieve module state");
        return NULL;
    }

    repr_str = PyObject_Repr(filters_obj);
    if (!repr_str)
        return NULL;

    nfilters = 0;
    compiled = NULL;

    if (filters_obj != Py_None) {
        nfilters = PySequence_Size(filters_obj);
        if (nfilters < 0) {
            Py_DECREF(repr_str);
            return NULL;
        }
    }

    if (nfilters > 0) {
        compiled = PyMem_RawCalloc((size_t)nfilters, sizeof(compiled_filter_t *));
        if (!compiled) {
            PyErr_NoMemory();
            Py_DECREF(repr_str);
            return NULL;
        }
        for (i = 0; i < nfilters; i++) {
            f = PySequence_GetItem(filters_obj, i);
            if (!f) {
                free_cf_array(compiled, i);
                Py_DECREF(repr_str);
                return NULL;
            }
            compiled[i] = compile_filter(f, state, 0);
            Py_DECREF(f);
            if (!compiled[i]) {
                free_cf_array(compiled, i);
                Py_DECREF(repr_str);
                return NULL;
            }
        }
    }

    obj = PyObject_New(CompiledFiltersObject, &CompiledFilters_Type);
    if (!obj) {
        free_cf_array(compiled, nfilters);
        Py_DECREF(repr_str);
        return NULL;
    }
    obj->filters = compiled;
    obj->nfilters = nfilters;
    obj->repr_str = repr_str; /* steal ref */

    return (PyObject *)obj;
}

static PyObject *
py_compile_options(PyObject *self, PyObject *args, PyObject *kwargs)
{
    int get_val = 0;
    int count_val = 0;
    PyObject *select_val = NULL;
    PyObject *order_by_val = NULL;
    Py_ssize_t offset_val = 0;
    Py_ssize_t limit_val = 0;
    Py_ssize_t oblen;
    PyObject *repr_str = NULL;
    compiled_select_spec_t *select_specs = NULL;
    Py_ssize_t nselect = 0;
    compiled_order_spec_t *order_specs = NULL;
    Py_ssize_t norder = 0;
    CompiledOptionsObject *obj = NULL;

    static const char *kwnames[] = {
        "get", "count", "select", "order_by", "offset", "limit",
        NULL
    };

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$ppOOnn",
                                     discard_const_p(char *, kwnames),
                                     &get_val, &count_val,
                                     &select_val, &order_by_val,
                                     &offset_val, &limit_val))
        return NULL;

    repr_str = kwargs ? PyObject_Repr(kwargs) : PyUnicode_FromString("{}");
    if (!repr_str)
        return NULL;

    /* Compile select specs */
    if (select_val && select_val != Py_None) {
        oblen = PySequence_Size(select_val);
        if (oblen < 0) {
            Py_DECREF(repr_str);
            return NULL;
        }
        if (oblen > 0) {
            if (compile_select_specs(select_val, &select_specs, &nselect) < 0) {
                Py_DECREF(repr_str);
                return NULL;
            }
        }
    }

    /* Compile order_by specs */
    if (order_by_val && order_by_val != Py_None) {
        oblen = PySequence_Size(order_by_val);
        if (oblen < 0) {
            free_select_specs(select_specs, nselect);
            Py_DECREF(repr_str);
            return NULL;
        }
        if (oblen > 0) {
            if (compile_order_specs(order_by_val, &order_specs, &norder) < 0) {
                free_select_specs(select_specs, nselect);
                Py_DECREF(repr_str);
                return NULL;
            }
        }
    }

    obj = PyObject_New(CompiledOptionsObject, &CompiledOptions_Type);
    if (!obj) {
        free_select_specs(select_specs, nselect);
        free_order_specs(order_specs, norder);
        Py_DECREF(repr_str);
        return NULL;
    }
    /* shortcircuit: get=True with no ordering means we stop at the first match */
    obj->shortcircuit = get_val && (norder == 0);
    obj->count_flag = count_val;
    obj->select_specs = select_specs;
    obj->nselect = nselect;
    obj->order_specs = order_specs;
    obj->norder = norder;
    obj->offset = offset_val;
    obj->limit = limit_val;
    obj->repr_str = repr_str;

    return (PyObject *)obj;
}

static PyObject *
py_tnfilter(PyObject *self, PyObject *args, PyObject *kwargs)
{
    PyObject *data = NULL;
    PyObject *filters_obj = Py_None;
    PyObject *options_obj = Py_None;
    fl_state_t *state = NULL;
    CompiledFiltersObject *cf = NULL;
    CompiledOptionsObject *co = NULL;
    PyObject *filtered = NULL;
    PyObject *result = NULL;

    static const char *kwnames[] = {
        "data", "filters", "options", NULL
    };

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O$O!O!",
                                     discard_const_p(char *, kwnames),
                                     &data,
                                     &CompiledFilters_Type, &filters_obj,
                                     &CompiledOptions_Type, &options_obj))
        return NULL;

    state = (fl_state_t *)PyModule_GetState(self);
    if (!state) {
        PyErr_SetString(PyExc_RuntimeError,
                        "tnfilter: cannot retrieve module state");
        return NULL;
    }

    cf = (CompiledFiltersObject *)filters_obj;
    co = (CompiledOptionsObject *)options_obj;

    filtered = filter_list_run(data, cf->filters, cf->nfilters,
                               co->shortcircuit, state);
    if (!filtered)
        return NULL;

    result = apply_options(filtered, co);
    Py_DECREF(filtered);
    return result;
}

static PyObject *
py_match(PyObject *self, PyObject *args, PyObject *kwargs)
{
    PyObject *item = NULL;
    PyObject *filters_obj = NULL;
    fl_state_t *state = NULL;
    CompiledFiltersObject *cf = NULL;
    bool matched;

    static const char *kwnames[] = { "item", "filters", NULL };

    filters_obj = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O$O!",
                                     discard_const_p(char *, kwnames),
                                     &item,
                                     &CompiledFilters_Type, &filters_obj))
        return NULL;

    state = (fl_state_t *)PyModule_GetState(self);
    if (!state) {
        PyErr_SetString(PyExc_RuntimeError, "match: cannot retrieve module state");
        return NULL;
    }

    cf = (CompiledFiltersObject *)filters_obj;

    if (!match_item(item, cf->filters, cf->nfilters, state, &matched))
        return NULL;

    return PyBool_FromLong(matched);
}

/* -- method table -------------------------------------------------------------- */

PyDoc_STRVAR(match_doc,
"match(item, *, filters: CompiledFilters) -> bool\n"
"--\n\n"
"Test whether a single item matches all compiled filters.\n\n"
"Parameters\n"
"----------\n"
"item : Any\n"
"    The item to test (dict uses fast path; other objects fall back to getattr).\n"
"filters : CompiledFilters\n"
"    Pre-compiled filter tree from compile_filters().\n\n"
"Returns\n"
"-------\n"
"bool\n"
"    True if the item matches all filters, False otherwise.\n"
);

PyDoc_STRVAR(tnfilter_doc,
"tnfilter(data: Iterable, *, filters: CompiledFilters, options: CompiledOptions) -> list\n"
"--\n\n"
"Filter an iterable using pre-compiled C-level filters.\n\n"
"Both `filters` and `options` must be objects previously returned by\n"
"compile_filters() and compile_options() respectively.\n\n"
"Parameters\n"
"----------\n"
"data : Iterable\n"
"    Items to filter (dicts use fast path; other objects fall back to getattr).\n"
"filters : CompiledFilters\n"
"    Pre-compiled filter tree from compile_filters().\n"
"options : CompiledOptions\n"
"    Pre-compiled options from compile_options().\n\n"
"Returns\n"
"-------\n"
"list\n"
"    Items (in original order) that matched all filters.\n"
);

PyDoc_STRVAR(compile_filters_doc,
"compile_filters(filters: list) -> CompiledFilters\n"
"--\n\n"
"Pre-compile a query-filters list into a CompiledFilters object.\n\n"
"The returned object can be passed directly to tnfilter(), skipping\n"
"per-call compilation overhead.\n\n"
"Parameters\n"
"----------\n"
"filters : list\n"
"    Standard middlewared query-filters: [name, op, value] leaves and\n"
"    ['OR', [branch, ...]] nodes.\n\n"
"Returns\n"
"-------\n"
"CompiledFilters\n"
"    Compiled filter tree.  repr() shows the original filters list.\n"
);

PyDoc_STRVAR(compile_options_doc,
"compile_options(*, get: bool = False, count: bool = False,\n"
"                select: list[str | list] | None = None,\n"
"                order_by: list[str] | None = None,\n"
"                offset: int = 0, limit: int = 0) -> CompiledOptions\n"
"--\n\n"
"Pre-parse query-options into a CompiledOptions object.\n\n"
"The returned object can be passed directly to tnfilter(), skipping\n"
"per-call options parsing overhead.\n\n"
"Parameters\n"
"----------\n"
"get : bool\n"
"    Return first match only (enables shortcircuit when order_by is empty).\n"
"count : bool\n"
"    Return the count of matched items instead of the items themselves.\n"
"select : list[str | list] | None\n"
"    Fields to project from each result entry.\n"
"order_by : list[str] | None\n"
"    Ordering directives.  Non-empty disables shortcircuit even when get=True.\n"
"offset : int\n"
"    Skip the first N matched items.\n"
"limit : int\n"
"    Cap results at N items.\n\n"
"Returns\n"
"-------\n"
"CompiledOptions\n"
"    Parsed options.  repr() shows the kwargs as passed.\n"
);

static PyMethodDef truenas_pyfilter_methods[] = {
    {
        .ml_name = "match",
        .ml_meth = (PyCFunction)(void(*)(void))py_match,
        .ml_flags = METH_VARARGS | METH_KEYWORDS,
        .ml_doc = match_doc,
    },
    {
        .ml_name = "tnfilter",
        .ml_meth = (PyCFunction)(void(*)(void))py_tnfilter,
        .ml_flags = METH_VARARGS | METH_KEYWORDS,
        .ml_doc = tnfilter_doc,
    },
    {
        .ml_name = "compile_filters",
        .ml_meth = (PyCFunction)(void(*)(void))py_compile_filters,
        .ml_flags = METH_VARARGS | METH_KEYWORDS,
        .ml_doc = compile_filters_doc,
    },
    {
        .ml_name = "compile_options",
        .ml_meth = (PyCFunction)(void(*)(void))py_compile_options,
        .ml_flags = METH_VARARGS | METH_KEYWORDS,
        .ml_doc = compile_options_doc,
    },
    { .ml_name = NULL },
};

/* -- module GC support --------------------------------------------------------- */

static int
truenas_pyfilter_traverse(PyObject *m, visitproc visit, void *arg)
{
    fl_state_t *state = (fl_state_t *)PyModule_GetState(m);
    if (!state) return 0;
    Py_VISIT(state->casefold_str);
    Py_VISIT(state->re_compile);
    Py_VISIT(state->empty_str);
    return 0;
}

static int
truenas_pyfilter_clear(PyObject *m)
{
    fl_state_t *state = (fl_state_t *)PyModule_GetState(m);
    if (!state) return 0;
    Py_CLEAR(state->casefold_str);
    Py_CLEAR(state->re_compile);
    Py_CLEAR(state->empty_str);
    return 0;
}

/* -- module definition --------------------------------------------------------- */

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    .m_name = "truenas_pyfilter",
    .m_doc = MODULE_DOC,
    .m_size = sizeof(fl_state_t),
    .m_methods = truenas_pyfilter_methods,
    .m_traverse = truenas_pyfilter_traverse,
    .m_clear = truenas_pyfilter_clear,
};

PyMODINIT_FUNC PyInit_truenas_pyfilter(void);

PyMODINIT_FUNC
PyInit_truenas_pyfilter(void)
{
    PyObject *m = NULL;
    PyObject *re_mod = NULL;
    fl_state_t *state = NULL;

    if (PyType_Ready(&CompiledFilters_Type) < 0)
        return NULL;
    if (PyType_Ready(&CompiledOptions_Type) < 0)
        return NULL;

    m = PyModule_Create(&moduledef);
    if (!m)
        return NULL;

    state = (fl_state_t *)PyModule_GetState(m);

    /* Cache interned "casefold" string */
    state->casefold_str = PyUnicode_InternFromString("casefold");
    if (!state->casefold_str)
        goto fail;

    /* Cache re.compile */
    re_mod = PyImport_ImportModule("re");
    if (!re_mod)
        goto fail;
    state->re_compile = PyObject_GetAttrString(re_mod, "compile");
    Py_DECREF(re_mod);
    if (!state->re_compile)
        goto fail;

    /* Cache empty string for regex None-source handling */
    state->empty_str = PyUnicode_FromStringAndSize("", 0);
    if (!state->empty_str)
        goto fail;

    /* Register pre-compiled types as module attributes */
    if (PyModule_AddType(m, &CompiledFilters_Type) < 0)
        goto fail;
    if (PyModule_AddType(m, &CompiledOptions_Type) < 0)
        goto fail;

    /* order_by prefix constants */
#define ADD_STR(name, val) \
    if (PyModule_AddStringConstant(m, name, val) < 0) goto fail
    ADD_STR("FILTER_ORDER_NULLS_FIRST_PREFIX", "nulls_first:");
    ADD_STR("FILTER_ORDER_NULLS_LAST_PREFIX",  "nulls_last:");
    ADD_STR("FILTER_ORDER_REVERSE_PREFIX",     "-");

    /* filter operator constants */
    ADD_STR("FILTER_OP_EQ",              "=");
    ADD_STR("FILTER_OP_NE",              "!=");
    ADD_STR("FILTER_OP_GT",              ">");
    ADD_STR("FILTER_OP_GE",              ">=");
    ADD_STR("FILTER_OP_LT",              "<");
    ADD_STR("FILTER_OP_LE",              "<=");
    ADD_STR("FILTER_OP_REGEX",           "~");
    ADD_STR("FILTER_OP_IN",              "in");
    ADD_STR("FILTER_OP_NOT_IN",          "nin");
    ADD_STR("FILTER_OP_REGEX_IN",        "rin");
    ADD_STR("FILTER_OP_REGEX_NOT_IN",    "rnin");
    ADD_STR("FILTER_OP_STARTSWITH",      "^");
    ADD_STR("FILTER_OP_NOT_STARTSWITH",  "!^");
    ADD_STR("FILTER_OP_ENDSWITH",        "$");
    ADD_STR("FILTER_OP_NOT_ENDSWITH",    "!$");
    ADD_STR("FILTER_OP_CI_PREFIX",       "C");
#undef ADD_STR

    return m;

fail:
    Py_DECREF(m);
    return NULL;
}
