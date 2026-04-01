// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef UTIL_ENUM_H
#define UTIL_ENUM_H

#include <Python.h>
#include <stddef.h>

typedef struct {
	const char *name;
	long val;
} py_intenum_tbl_t;

#define TABLE_SIZE(t) (sizeof(t) / sizeof((t)[0]))

/*
 * Build a {name: value, ...} PyDict from tbl[0..n-1].
 * Returns a new reference, or NULL with an exception set on failure.
 */
PyObject *table_to_dict(const py_intenum_tbl_t *tbl, size_t n);

/*
 * Construct an IntEnum or IntFlag subclass named class_name, add it to
 * module, and optionally store a new reference in *penum_out.
 *
 * enum_type  – the IntEnum or IntFlag type object from the enum module
 * tbl/n      – name→value table and its length
 * kwargs     – keyword arguments forwarded to the enum constructor; may be NULL
 * penum_out  – if non-NULL, receives a new reference to the created enum
 *
 * Returns 0 on success, -1 with an exception set on failure.
 */
int add_enum(PyObject *module,
             PyObject *enum_type,
             const char *class_name,
             const py_intenum_tbl_t *tbl,
             size_t n,
             PyObject *kwargs,
             PyObject **penum_out);

#endif /* UTIL_ENUM_H */
