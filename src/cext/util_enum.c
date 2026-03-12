// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "util_enum.h"

PyObject *
table_to_dict(const py_intenum_tbl_t *tbl, size_t n)
{
	PyObject *dict;
	PyObject *v;
	size_t i;

	dict = PyDict_New();
	if (dict == NULL)
		return NULL;

	for (i = 0; i < n; i++) {
		v = PyLong_FromLong(tbl[i].val);
		if (v == NULL || PyDict_SetItemString(dict, tbl[i].name, v) < 0) {
			Py_XDECREF(v);
			Py_DECREF(dict);
			return NULL;
		}
		Py_DECREF(v);
	}
	return dict;
}

int
add_enum(PyObject *module,
         PyObject *enum_type,
         const char *class_name,
         const py_intenum_tbl_t *tbl,
         size_t n,
         PyObject *kwargs,
         PyObject **penum_out)
{
	PyObject *name = NULL;
	PyObject *attrs = NULL;
	PyObject *args = NULL;
	PyObject *enum_obj = NULL;
	int ret = -1;

	name = PyUnicode_FromString(class_name);
	if (name == NULL)
		goto out;

	attrs = table_to_dict(tbl, n);
	if (attrs == NULL)
		goto out;

	args = PyTuple_Pack(2, name, attrs);
	if (args == NULL)
		goto out;

	enum_obj = PyObject_Call(enum_type, args, kwargs);
	if (enum_obj == NULL)
		goto out;

	if (PyModule_AddObjectRef(module, class_name, enum_obj) < 0)
		goto out;

	ret = 0;
	if (penum_out != NULL)
		*penum_out = enum_obj;
	else
		Py_DECREF(enum_obj);
	enum_obj = NULL;  /* ownership transferred or released */

out:
	Py_XDECREF(name);
	Py_XDECREF(attrs);
	Py_XDECREF(args);
	Py_XDECREF(enum_obj);
	return ret;
}
