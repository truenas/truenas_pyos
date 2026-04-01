// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * High-performance C implementation of the middlewared filter_list hot path.
 *
 * Design goals
 * ============
 * 1. Zero Python frame pushes in the inner item loop.
 * 2. No NamedTuple allocations per item (the FilterGetResult overhead).
 * 3. Pre-compile all constant work once: op lookup, casefold, regex compile,
 *    path splitting.
 * 4. Fast path for the overwhelmingly common case: flat key in an exact dict.
 *    This reduces to a single PyDict_GetItemWithError + richcmpfunc call.
 *
 * Filter tree nodes
 * =================
 * CF_SIMPLE  - leaf: [name, op, value]
 * CF_OR      - any child matches  (eval short-circuits on first match)
 * CF_AND     - all children match (eval short-circuits on first miss)
 *
 * Path representation
 * ===================
 * Each simple filter pre-splits its name into an array of path_part_t
 * structs at compile time.  At runtime the traversal loop indexes directly
 * into the array - no string operations, no PyObject allocation per level.
 */

#include "filter_list.h"

/* -- operator codes ----------------------------------------------------------- */

typedef enum {
    OP_EQ = 0,
    OP_NE,
    OP_GT,
    OP_GE,
    OP_LT,
    OP_LE,
    OP_RE,   /* regex match (~)                    */
    OP_IN,   /* value contains source (in)          */
    OP_NIN,  /* value does not contain source (nin) */
    OP_RIN,  /* source contains value (rin)         */
    OP_RNIN, /* source does not contain value (rnin)*/
    OP_SW,   /* source.startswith(value) (^)        */
    OP_NSW,  /* not startswith (!^)                 */
    OP_EW,   /* source.endswith(value)  ($)         */
    OP_NEW,  /* not endswith (!$)                   */
} op_code_t;

/* -- pre-split path part ------------------------------------------------------ */

typedef struct {
    PyObject *key;        /* owned PyObject* - used as dict key / attr name */
    const char *key_c;    /* borrowed UTF-8 from key (valid while key alive) */
    bool is_wildcard;     /* key == "*"        */
    bool is_digit;        /* key is all-digits */
    long digit_val;       /* value when is_digit */
} path_part_t;

/* -- compiled simple filter --------------------------------------------------- */

typedef struct {
    path_part_t *parts;  /* owned array: pre-split path components */
    Py_ssize_t nparts;   /* number of parts                        */
    op_code_t op;
    bool ci;             /* case-insensitive flag                  */
    PyObject *value;     /* owned: comparison value                */
    PyObject *value_ci;  /* owned: casefolded value (when ci)      */
    PyObject *re_match;  /* owned: bound pattern.match  (OP_RE)    */
} simple_filter_t;

/* -- compiled filter tree node ------------------------------------------------ */

typedef enum { CF_SIMPLE, CF_OR, CF_AND } cf_type_t;

typedef struct compiled_filter compiled_filter_t;
struct compiled_filter {
    cf_type_t type;
    union {
        simple_filter_t s;
        struct {
            compiled_filter_t **ch;
            Py_ssize_t nch;
        } compound;
    };
};

#define FILTER_MAX_DEPTH 64

/* ===============================================================================
 * Helpers
 * =============================================================================== */

/*
 * Initialise *pp from a PyUnicode segment key (steals the reference).
 * Returns 0 on success, -1 on error (exception set).
 */
static int
init_path_part(path_part_t *pp, PyObject *key)
{
    const char *key_c = PyUnicode_AsUTF8(key);
    char *endp;
    long v;

    if (!key_c)
        return -1;

    pp->key = key; /* steal ref */
    pp->key_c = key_c; /* valid while key alive */
    pp->is_wildcard = (PyUnicode_CompareWithASCIIString(key, "*") == 0);
    pp->is_digit = false;
    pp->digit_val = 0;

    if (!pp->is_wildcard) {
        errno = 0;
        v = strtol(key_c, &endp, 10);
        if (*endp == '\0' && key_c[0] != '\0' && v >= 0 && errno == 0) {
            pp->is_digit = true;
            pp->digit_val = v;
        }
    }

    return 0;
}

/*
 * Split a dotted path (with backslash-escaped dots) into path_part_t components.
 *
 * Strategy: split on every '.' with PyUnicode_Split, then merge consecutive
 * pieces where the left piece ends with '\' - that backslash is the escape
 * character and is stripped.  E.g. "foo\.bar.baz" -> ["foo.bar", "baz"].
 *
 * Returns the number of parts written into *out_parts, or -1 on error.
 * Caller owns the returned array and all PyObject* keys inside it.
 */
static Py_ssize_t
split_path(PyObject *name_obj, path_part_t **out_parts)
{
    PyObject *dot_str = NULL;
    PyObject *bslash_str = NULL;
    PyObject *pieces = NULL;
    PyObject *seg = NULL;
    PyObject *tmp = NULL;
    path_part_t *parts = NULL;
    Py_ssize_t npieces;
    Py_ssize_t nparts;
    Py_ssize_t i;
    int tm;

    dot_str = PyUnicode_FromString(".");
    if (!dot_str)
        return -1;
    bslash_str = PyUnicode_FromString("\\");
    if (!bslash_str) {
        Py_DECREF(dot_str);
        return -1;
    }

    pieces = PyUnicode_Split(name_obj, dot_str, -1);
    Py_DECREF(dot_str);
    if (!pieces) {
        Py_DECREF(bslash_str);
        return -1;
    }

    npieces = PyList_GET_SIZE(pieces);
    parts = PyMem_RawCalloc((size_t)npieces, sizeof(path_part_t));
    if (!parts) {
        PyErr_NoMemory();
        Py_DECREF(bslash_str);
        Py_DECREF(pieces);
        return -1;
    }

    nparts = 0;
    i = 0;
    while (i < npieces) {
        seg = PyList_GET_ITEM(pieces, i++);
        Py_INCREF(seg);

        /* Merge with following pieces while this one ends with '\'.
         * Strip the trailing backslash, re-add the dot, append the next piece. */
        while (i < npieces) {
            tm = PyUnicode_Tailmatch(seg, bslash_str, 0, PY_SSIZE_T_MAX, 1);
            if (tm < 0) {
                Py_DECREF(seg);
                goto error;
            }
            if (!tm)
                break;
            tmp = PyUnicode_Substring(seg, 0, PyUnicode_GET_LENGTH(seg) - 1);
            Py_DECREF(seg);
            if (!tmp)
                goto error;
            seg = PyUnicode_FromFormat("%U.%U", tmp, PyList_GET_ITEM(pieces, i++));
            Py_DECREF(tmp);
            if (!seg)
                goto error;
        }

        if (init_path_part(&parts[nparts], seg) < 0) {
            Py_DECREF(seg); /* init_path_part did not steal on failure */
            goto error;
        }
        nparts++;
    }

    Py_DECREF(bslash_str);
    Py_DECREF(pieces);
    *out_parts = parts;
    return nparts;

error:
    for (i = 0; i < nparts; i++)
        Py_DECREF(parts[i].key);
    PyMem_RawFree(parts);
    Py_DECREF(bslash_str);
    Py_DECREF(pieces);
    return -1;
}

/*
 * Casefold a Python object: str -> str, list/tuple -> list[str], None -> None.
 * Returns a new reference, or NULL on error.
 */
static PyObject *
c_casefold(PyObject *obj, PyObject *casefold_str)
{
    Py_ssize_t n;
    PyObject *result = NULL;
    Py_ssize_t i;
    PyObject *elem = NULL;
    PyObject *folded = NULL;

    if (obj == Py_None) {
        Py_INCREF(Py_None);
        return Py_None;
    }
    if (PyUnicode_Check(obj))
        return PyObject_CallMethodNoArgs(obj, casefold_str);

    if (PyList_Check(obj) || PyTuple_Check(obj)) {
        n = PySequence_Fast_GET_SIZE(obj);
        result = PyList_New(n);
        if (!result)
            return NULL;
        for (i = 0; i < n; i++) {
            elem = PySequence_Fast_GET_ITEM(obj, i);
            folded = PyObject_CallMethodNoArgs(elem, casefold_str);
            if (!folded) {
                Py_DECREF(result);
                return NULL;
            }
            PyList_SET_ITEM(result, i, folded); /* steals ref */
        }
        return result;
    }

    PyErr_Format(PyExc_ValueError,
                 "filter_list: cannot casefold value of type %s",
                 Py_TYPE(obj)->tp_name);
    return NULL;
}

/* -- operator string -> op_code_t ----------------------------------------------- */

static int
parse_op(const char *s, op_code_t *out_op, bool *out_ci)
{
    *out_ci = false;
    if (s[0] == 'C') {
        *out_ci = true;
        s++;
    }
    if (strcmp(s, "=") == 0)    { *out_op = OP_EQ;   return 0; }
    if (strcmp(s, "!=") == 0)   { *out_op = OP_NE;   return 0; }
    if (strcmp(s, ">") == 0)    { *out_op = OP_GT;   return 0; }
    if (strcmp(s, ">=") == 0)   { *out_op = OP_GE;   return 0; }
    if (strcmp(s, "<") == 0)    { *out_op = OP_LT;   return 0; }
    if (strcmp(s, "<=") == 0)   { *out_op = OP_LE;   return 0; }
    if (strcmp(s, "~") == 0)    { *out_op = OP_RE;   return 0; }
    if (strcmp(s, "in") == 0)   { *out_op = OP_IN;   return 0; }
    if (strcmp(s, "nin") == 0)  { *out_op = OP_NIN;  return 0; }
    if (strcmp(s, "rin") == 0)  { *out_op = OP_RIN;  return 0; }
    if (strcmp(s, "rnin") == 0) { *out_op = OP_RNIN; return 0; }
    if (strcmp(s, "^") == 0)    { *out_op = OP_SW;   return 0; }
    if (strcmp(s, "!^") == 0)   { *out_op = OP_NSW;  return 0; }
    if (strcmp(s, "$") == 0)    { *out_op = OP_EW;   return 0; }
    if (strcmp(s, "!$") == 0)   { *out_op = OP_NEW;  return 0; }
    PyErr_Format(PyExc_ValueError, "filter_list: unknown operator '%s'", s);
    return -1;
}

/* ===============================================================================
 * Filter compilation
 * =============================================================================== */

static void
free_simple(simple_filter_t *sf)
{
    Py_ssize_t i;

    if (sf->parts) {
        for (i = 0; i < sf->nparts; i++)
            Py_XDECREF(sf->parts[i].key);
        PyMem_RawFree(sf->parts);
    }
    Py_XDECREF(sf->value);
    Py_XDECREF(sf->value_ci);
    Py_XDECREF(sf->re_match);
}

static void
free_cf(compiled_filter_t *cf)
{
    Py_ssize_t i;

    if (!cf) return;
    if (cf->type == CF_SIMPLE) {
        free_simple(&cf->s);
    } else {
        for (i = 0; i < cf->compound.nch; i++)
            free_cf(cf->compound.ch[i]);
        PyMem_RawFree(cf->compound.ch);
    }
    PyMem_RawFree(cf);
}

void
free_cf_array(compiled_filter_t **arr, Py_ssize_t n)
{
    Py_ssize_t i;

    if (!arr) return;
    for (i = 0; i < n; i++)
        free_cf(arr[i]);
    PyMem_RawFree(arr);
}

/*
 * Compile a simple [name, op, value] filter into a simple_filter_t.
 * `f` may be a list or tuple of length 3.
 */
static compiled_filter_t *
compile_simple(PyObject *f, fl_state_t *state)
{
    PyObject *name_obj = NULL;
    PyObject *op_obj = NULL;
    PyObject *value_obj = NULL;
    const char *op_str = NULL;
    op_code_t op;
    bool ci;
    compiled_filter_t *cf = NULL;
    simple_filter_t *sf = NULL;
    PyObject *pattern = NULL;

    name_obj = PySequence_GetItem(f, 0);
    if (!name_obj)
        return NULL;
    op_obj = PySequence_GetItem(f, 1);
    if (!op_obj) {
        Py_DECREF(name_obj);
        return NULL;
    }
    value_obj = PySequence_GetItem(f, 2);
    if (!value_obj) {
        Py_DECREF(name_obj);
        Py_DECREF(op_obj);
        return NULL;
    }

    op_str = PyUnicode_AsUTF8(op_obj);
    Py_DECREF(op_obj);
    if (!op_str) {
        Py_DECREF(name_obj);
        Py_DECREF(value_obj);
        return NULL;
    }

    if (parse_op(op_str, &op, &ci) < 0) {
        Py_DECREF(name_obj);
        Py_DECREF(value_obj);
        return NULL;
    }

    cf = PyMem_RawCalloc(1, sizeof(*cf));
    if (!cf) {
        PyErr_NoMemory();
        Py_DECREF(name_obj);
        Py_DECREF(value_obj);
        return NULL;
    }
    cf->type = CF_SIMPLE;
    sf = &cf->s;
    sf->op = op;
    sf->ci = ci;
    sf->value = value_obj; /* steal ref */

    /* Pre-split the dotted path. */
    sf->nparts = split_path(name_obj, &sf->parts);
    Py_DECREF(name_obj);
    if (sf->nparts < 0) {
        free_cf(cf);
        return NULL;
    }

    /* Pre-fold comparison value for CI operators */
    if (ci) {
        sf->value_ci = c_casefold(sf->value, state->casefold_str);
        if (!sf->value_ci) {
            free_cf(cf);
            return NULL;
        }
    }

    /* Pre-compile regex and cache the bound .match method */
    if (op == OP_RE) {
        pattern = PyObject_CallOneArg(state->re_compile, sf->value);
        if (!pattern) {
            free_cf(cf);
            return NULL;
        }
        sf->re_match = PyObject_GetAttrString(pattern, "match");
        Py_DECREF(pattern);
        if (!sf->re_match) {
            free_cf(cf);
            return NULL;
        }
    }

    return cf;
}

/*
 * Compile a conjunction (AND) branch from within an OR filter.
 *
 * A conjunction branch is a list/tuple of sub-filters where the first element
 * is itself a list/tuple (signalling AND semantics).  Every sub-filter must
 * match for the branch to match.
 *
 * Returns a new CF_AND node, or NULL on error (exception set).
 */
static compiled_filter_t *
compile_and_branch(PyObject *branch, fl_state_t *state, int depth)
{
    Py_ssize_t nsubs;
    compiled_filter_t *and_cf = NULL;
    Py_ssize_t j;
    PyObject *sub = NULL;

    nsubs = PySequence_Size(branch);
    if (nsubs < 0)
        return NULL;

    and_cf = PyMem_RawCalloc(1, sizeof(*and_cf));
    if (!and_cf) {
        PyErr_NoMemory();
        return NULL;
    }
    and_cf->type = CF_AND;
    and_cf->compound.nch = 0;
    and_cf->compound.ch = PyMem_RawCalloc((size_t)nsubs, sizeof(compiled_filter_t *));
    if (!and_cf->compound.ch) {
        PyErr_NoMemory();
        PyMem_RawFree(and_cf);
        return NULL;
    }

    for (j = 0; j < nsubs; j++) {
        sub = PySequence_GetItem(branch, j);
        if (!sub) {
            free_cf(and_cf);
            return NULL;
        }
        and_cf->compound.ch[j] = compile_filter(sub, state, depth + 1);
        Py_DECREF(sub);
        if (!and_cf->compound.ch[j]) {
            /* Record how many children were compiled so free_cf releases them. */
            and_cf->compound.nch = j;
            free_cf(and_cf);
            return NULL;
        }
        and_cf->compound.nch = j + 1;
    }

    return and_cf;
}

/*
 * Recursively compile a filter spec `f` into a compiled_filter_t tree.
 *
 * `f` is either:
 *   len-3 sequence  -> simple leaf filter:  [name, op, value]
 *   len-2 sequence  -> OR node:             ['OR', [branch, ...]]
 *
 * Within an OR node each branch is either:
 *   - a simple/OR filter  when branch[0] is a str  (recurse into compile_filter)
 *   - a conjunction (AND) when branch[0] is a list/tuple (delegate to compile_and_branch)
 */
compiled_filter_t *
compile_filter(PyObject *f, fl_state_t *state, int depth)
{
    Py_ssize_t flen;
    PyObject *tag = NULL;
    PyObject *branches = NULL;
    Py_ssize_t nbranches;
    compiled_filter_t *cf = NULL;
    Py_ssize_t i;
    PyObject *branch = NULL;
    PyObject *branch0 = NULL;
    int is_conjunction;
    compiled_filter_t *child = NULL;

    if (depth > FILTER_MAX_DEPTH) {
        PyErr_SetString(PyExc_RecursionError,
                        "filter_list: maximum filter nesting depth exceeded");
        return NULL;
    }

    flen = PySequence_Size(f);
    if (flen < 0)
        return NULL;

    /* len-3 -> simple leaf */
    if (flen == 3)
        return compile_simple(f, state);

    if (flen != 2) {
        PyErr_Format(PyExc_ValueError,
                     "filter_list: invalid filter length %zd", flen);
        return NULL;
    }

    /* len-2 -> OR node: validate f[0] == "OR" before trusting f[1] */
    tag = PySequence_GetItem(f, 0);
    if (!tag)
        return NULL;
    if (!PyUnicode_Check(tag) ||
        PyUnicode_CompareWithASCIIString(tag, "OR") != 0) {
        Py_DECREF(tag);
        PyErr_SetString(PyExc_ValueError,
                        "filter_list: len-2 filter must start with \"OR\"");
        return NULL;
    }
    Py_DECREF(tag);

    branches = PySequence_GetItem(f, 1);
    if (!branches)
        return NULL;

    nbranches = PySequence_Size(branches);
    if (nbranches < 0) {
        Py_DECREF(branches);
        return NULL;
    }

    cf = PyMem_RawCalloc(1, sizeof(*cf));
    if (!cf) {
        PyErr_NoMemory();
        Py_DECREF(branches);
        return NULL;
    }
    cf->type = CF_OR;
    cf->compound.nch = 0;
    cf->compound.ch = PyMem_RawCalloc((size_t)nbranches, sizeof(compiled_filter_t *));
    if (!cf->compound.ch) {
        PyErr_NoMemory();
        PyMem_RawFree(cf);
        Py_DECREF(branches);
        return NULL;
    }

    for (i = 0; i < nbranches; i++) {
        branch = PySequence_GetItem(branches, i);
        if (!branch)
            goto fail;

        /*
         * Peek at branch[0]: a list/tuple signals a conjunction (AND node),
         * anything else (a str) signals a simple or nested OR filter.
         */
        branch0 = PySequence_GetItem(branch, 0);
        if (!branch0) {
            Py_DECREF(branch);
            goto fail;
        }
        is_conjunction = PyList_Check(branch0) || PyTuple_Check(branch0);
        Py_DECREF(branch0);

        if (is_conjunction)
            child = compile_and_branch(branch, state, depth);
        else
            child = compile_filter(branch, state, depth + 1);

        Py_DECREF(branch);

        if (!child)
            goto fail;

        cf->compound.ch[cf->compound.nch++] = child;
    }

    Py_DECREF(branches);
    return cf;

fail:
    Py_DECREF(branches);
    free_cf(cf);
    return NULL;
}

/* ===============================================================================
 * Filter evaluation
 * =============================================================================== */

/*
 * Apply the operator in `sf` to a concrete (already-retrieved) value `val`.
 *
 * Returns 1 (match), 0 (no match), -1 (error).
 * `val` is a borrowed reference; this function does not consume it.
 */
static int
apply_op(const simple_filter_t *sf, PyObject *val, fl_state_t *state)
{
    PyObject *source = val;
    PyObject *cmp_val = sf->value;
    PyObject *tmp_fold = NULL; /* non-NULL -> we must Py_DECREF before return */
    int result;
    PyObject *arg = NULL;
    PyObject *res = NULL;

    /* Casefold source for CI operators (value was pre-folded at compile time) */
    if (sf->ci && val != Py_None) {
        tmp_fold = c_casefold(val, state->casefold_str);
        if (!tmp_fold)
            return -1;
        source = tmp_fold;
        cmp_val = sf->value_ci;
    } else if (sf->ci) {
        /* val is None: casefold(None) = None; use pre-folded value */
        cmp_val = sf->value_ci;
    }

    switch (sf->op) {
    case OP_EQ:
        result = PyObject_RichCompareBool(source, cmp_val, Py_EQ);
        break;
    case OP_NE:
        result = PyObject_RichCompareBool(source, cmp_val, Py_NE);
        break;
    case OP_GT:
        result = PyObject_RichCompareBool(source, cmp_val, Py_GT);
        break;
    case OP_GE:
        result = PyObject_RichCompareBool(source, cmp_val, Py_GE);
        break;
    case OP_LT:
        result = PyObject_RichCompareBool(source, cmp_val, Py_LT);
        break;
    case OP_LE:
        result = PyObject_RichCompareBool(source, cmp_val, Py_LE);
        break;

    case OP_RE:
        /* Regex: match on source (or "" if None).  CI would have folded both. */
        arg = (source == Py_None) ? state->empty_str : source;
        res = PyObject_CallOneArg(sf->re_match, arg);
        if (!res) {
            result = -1;
            break;
        }
        result = (res != Py_None) && PyObject_IsTrue(res);
        Py_DECREF(res);
        break;

    case OP_IN:
        /* x in y: filter value (y) contains source (x) */
        result = PySequence_Contains(cmp_val, source);
        break;

    case OP_NIN:
        if (val == Py_None) {
            result = 0;
            break;
        }
        result = PySequence_Contains(cmp_val, source);
        if (result == 1)
            result = 0;
        else if (result == 0)
            result = 1;
        break;

    case OP_RIN:
        /* y in x: source (x) contains filter value (y) */
        if (val == Py_None) {
            result = 0;
            break;
        }
        result = PySequence_Contains(source, cmp_val);
        break;

    case OP_RNIN:
        if (val == Py_None) {
            result = 0;
            break;
        }
        result = PySequence_Contains(source, cmp_val);
        if (result == 1)
            result = 0;
        else if (result == 0)
            result = 1;
        break;

    case OP_SW:
        if (val == Py_None) {
            result = 0;
            break;
        }
        /* PyUnicode_Tailmatch: direction -1 = startswith */
        result = PyUnicode_Tailmatch(source, cmp_val, 0, PY_SSIZE_T_MAX, -1);
        break;
    case OP_NSW:
        if (val == Py_None) {
            result = 0;
            break;
        }
        result = PyUnicode_Tailmatch(source, cmp_val, 0, PY_SSIZE_T_MAX, -1);
        if (result == 1)
            result = 0;
        else if (result == 0)
            result = 1;
        break;
    case OP_EW:
        if (val == Py_None) {
            result = 0;
            break;
        }
        /* direction 1 = endswith */
        result = PyUnicode_Tailmatch(source, cmp_val, 0, PY_SSIZE_T_MAX, 1);
        break;
    case OP_NEW:
        if (val == Py_None) {
            result = 0;
            break;
        }
        result = PyUnicode_Tailmatch(source, cmp_val, 0, PY_SSIZE_T_MAX, 1);
        if (result == 1)
            result = 0;
        else if (result == 0)
            result = 1;
        break;

    default:
        PyErr_SetString(PyExc_ValueError, "filter_list: unknown op code");
        result = -1;
        break;
    }

    Py_XDECREF(tmp_fold);
    return result;
}

/*
 * Evaluate a simple filter against `item`, starting path traversal at
 * parts[start].
 *
 * Returns 1 (match), 0 (no match), -1 (error).
 *
 * Ownership model
 * ---------------
 * We traverse the object graph borrowing references from dicts/lists (which
 * are kept alive by `item`, which is kept alive by the outer loop).  Whenever
 * we obtain a new ref (getattr, or sequence-index from an owned container) we
 * track it in `cur_owned` and release it when we no longer need it.  The final
 * value passed to apply_op is always a borrowed ref (from the dict/list chain)
 * or an owned ref that we release after apply_op returns.
 */
static int
eval_simple_from(PyObject *item, const simple_filter_t *sf,
                 Py_ssize_t start, fl_state_t *state)
{
    Py_ssize_t nparts = sf->nparts;
    PyObject *val = NULL;
    PyObject *cur = item;
    PyObject *cur_owned = NULL; /* non-NULL: we own cur and must release it */
    Py_ssize_t i;
    const path_part_t *pp = NULL;
    Py_ssize_t n;
    Py_ssize_t j;
    PyObject *entry = NULL;
    int result;
    Py_ssize_t slen;
    PyObject *v = NULL;

    /* -- ultra-fast path: single-level exact-dict lookup ---------------------- */
    if (start == 0 && nparts == 1 && PyDict_CheckExact(item)) {
        val = PyDict_GetItemWithError(item, sf->parts[0].key);
        if (!val)
            return PyErr_Occurred() ? -1 : 0; /* missing key -> no match */
        return apply_op(sf, val, state); /* val borrowed from item */
    }

    /* -- general path: traverse parts[start:] ---------------------------------- */
    for (i = start; i < nparts; i++) {
        pp = &sf->parts[i];

        if (PyDict_CheckExact(cur)) {
            v = PyDict_GetItemWithError(cur, pp->key);
            if (!v) {
                Py_XDECREF(cur_owned);
                return PyErr_Occurred() ? -1 : 0;
            }
            /*
             * v is borrowed from cur.  If we own cur, incref v so it stays
             * alive when we release cur below.
             */
            if (cur_owned)
                Py_SETREF(cur_owned, Py_NewRef(v));
            cur = v;

        } else if (PyList_Check(cur) || PyTuple_Check(cur)) {

            if (pp->is_wildcard) {
                /* Wildcard: recurse over each element with remaining path.
                 * Incref each entry before recursing: eval_simple_from may
                 * call Python code (getattr, casefold, regex) that mutates
                 * cur, invalidating the borrowed ob_item[] pointer. */
                n = PySequence_Fast_GET_SIZE(cur);
                result = 0;
                for (j = 0; j < n && result == 0; j++) {
                    entry = PySequence_Fast_GET_ITEM(cur, j);
                    Py_INCREF(entry);
                    result = eval_simple_from(entry, sf, i + 1, state);
                    Py_DECREF(entry);
                }
                Py_XDECREF(cur_owned);
                return result;
            }

            if (pp->is_digit) {
                slen = PySequence_Fast_GET_SIZE(cur);
                v = (pp->digit_val < slen)
                    ? PySequence_Fast_GET_ITEM(cur, pp->digit_val)
                    : Py_None;
                /* v borrowed from cur; same ownership dance as dict case */
                if (cur_owned)
                    Py_SETREF(cur_owned, Py_NewRef(v));
                cur = v;
            } else {
                /*
                 * Non-numeric, non-wildcard component on a sequence.
                 * Try getattr to support NamedTuple and other tuple
                 * subclasses that expose named fields as attributes.
                 * Fall through to a descriptive ValueError only when
                 * the attribute genuinely does not exist.
                 */
                v = PyObject_GetAttr(cur, pp->key);
                if (!v) {
                    if (!PyErr_ExceptionMatches(PyExc_AttributeError)) {
                        Py_XDECREF(cur_owned);
                        return -1;
                    }
                    /* Missing named field: mirror dict missing-key → no match */
                    PyErr_Clear();
                    Py_XDECREF(cur_owned);
                    return 0;
                }
                Py_XSETREF(cur_owned, v);
                cur = v;
            }

        } else {
            /*
             * Non-dict, non-list: try getattr for custom objects (NamedTuple,
             * dataclass, etc.).  If the attribute does not exist, mirror
             * Python get_impl semantics: treat the current value as the leaf
             * and apply the operator directly, ignoring remaining path parts.
             */
            v = PyObject_GetAttr(cur, pp->key);
            if (!v) {
                if (!PyErr_ExceptionMatches(PyExc_AttributeError)) {
                    Py_XDECREF(cur_owned);
                    return -1;
                }
                PyErr_Clear();
                result = apply_op(sf, cur, state);
                Py_XDECREF(cur_owned);
                return result;
            }
            Py_XDECREF(cur_owned);
            cur = v;
            cur_owned = v;
        }
    }

    /* Apply the operator to the final value */
    result = apply_op(sf, cur, state);
    Py_XDECREF(cur_owned);
    return result;
}

/*
 * Evaluate a compiled filter tree node against `item`.
 * Returns 1 (match), 0 (no match), -1 (error).
 */
static int
eval_filter(PyObject *item, const compiled_filter_t *cf, fl_state_t *state,
            int depth)
{
    Py_ssize_t i;
    int r;

    if (depth > FILTER_MAX_DEPTH) {
        PyErr_SetString(PyExc_RecursionError,
                        "filter_list: maximum filter nesting depth exceeded");
        return -1;
    }

    switch (cf->type) {
    case CF_SIMPLE:
        return eval_simple_from(item, &cf->s, 0, state);

    case CF_OR:
        for (i = 0; i < cf->compound.nch; i++) {
            r = eval_filter(item, cf->compound.ch[i], state, depth + 1);
            if (r != 0)
                return r; /* match or error: short-circuit */
        }
        return 0;

    case CF_AND:
        for (i = 0; i < cf->compound.nch; i++) {
            r = eval_filter(item, cf->compound.ch[i], state, depth + 1);
            if (r != 1)
                return r; /* no-match or error: short-circuit */
        }
        return 1;
    }
    /* unreachable */
    PyErr_SetString(PyExc_RuntimeError, "filter_list: unknown cf_type");
    return -1;
}

/* ===============================================================================
 * Internal filter_list implementation
 * =============================================================================== */

/*
 * Pure evaluation loop: iterate `data`, append items matching all `compiled`
 * filters to a new list, and return it.  Does not own or free `compiled`.
 */
PyObject *
filter_list_run(PyObject *data, compiled_filter_t * const *compiled,
                Py_ssize_t nfilters, bool shortcircuit, fl_state_t *state)
{
    PyObject *result = NULL;
    PyObject *iter = NULL;
    PyObject *item = NULL;
    Py_ssize_t i;
    int match;
    int r;

    result = PyList_New(0);
    if (!result)
        return NULL;

    iter = PyObject_GetIter(data);
    if (!iter) {
        Py_DECREF(result);
        return NULL;
    }

    while ((item = PyIter_Next(iter)) != NULL) {
        match = 1;

        for (i = 0; i < nfilters; i++) {
            r = eval_filter(item, compiled[i], state, 0);
            if (r < 0) {
                Py_DECREF(item);
                Py_DECREF(iter);
                Py_DECREF(result);
                return NULL;
            }
            if (!r) {
                match = 0;
                break;
            }
        }

        if (match) {
            if (PyList_Append(result, item) < 0) {
                Py_DECREF(item);
                Py_DECREF(iter);
                Py_DECREF(result);
                return NULL;
            }
            if (shortcircuit) {
                Py_DECREF(item);
                break;
            }
        }

        Py_DECREF(item);
    }

    Py_DECREF(iter);

    if (PyErr_Occurred()) {
        Py_DECREF(result);
        return NULL;
    }

    return result;
}

/* ===============================================================================
 * match_item: check whether a single item matches all compiled filters
 * =============================================================================== */

bool
match_item(PyObject *item, compiled_filter_t * const *compiled,
           Py_ssize_t nfilters, fl_state_t *state, bool *matchp)
{
    Py_ssize_t i;
    int r;

    for (i = 0; i < nfilters; i++) {
        r = eval_filter(item, compiled[i], state, 0);
        if (r < 0)
            return false;  /* exception already set */
        if (!r) {
            *matchp = false;
            return true;
        }
    }
    *matchp = true;
    return true;
}

/* ===============================================================================
 * CompiledFilters Python type
 * =============================================================================== */

static void
compiled_filters_dealloc(CompiledFiltersObject *self)
{
    free_cf_array(self->filters, self->nfilters);
    Py_XDECREF(self->repr_str);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
compiled_filters_repr(CompiledFiltersObject *self)
{
    return PyUnicode_FromFormat("CompiledFilters(%U)", self->repr_str);
}

PyTypeObject CompiledFilters_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "truenas_pyfilter.CompiledFilters",
    .tp_basicsize = sizeof(CompiledFiltersObject),
    .tp_dealloc = (destructor)compiled_filters_dealloc,
    .tp_repr = (reprfunc)compiled_filters_repr,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = PyDoc_STR("Pre-compiled filter tree for use with tnfilter()."),
};

/* ===============================================================================
 * CompiledOptions Python type
 * =============================================================================== */

static void
compiled_options_dealloc(CompiledOptionsObject *self)
{
    free_select_specs(self->select_specs, self->nselect);
    free_order_specs(self->order_specs, self->norder);
    Py_XDECREF(self->repr_str);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
compiled_options_repr(CompiledOptionsObject *self)
{
    return PyUnicode_FromFormat("CompiledOptions(%U)", self->repr_str);
}

PyTypeObject CompiledOptions_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "truenas_pyfilter.CompiledOptions",
    .tp_basicsize = sizeof(CompiledOptionsObject),
    .tp_dealloc = (destructor)compiled_options_dealloc,
    .tp_repr = (reprfunc)compiled_options_repr,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = PyDoc_STR("Pre-compiled options for use with tnfilter()."),
};
