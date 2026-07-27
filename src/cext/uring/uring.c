// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * truenas_os.uring -- io_uring filesystem reactor for asyncio.
 *
 * Lives inside the truenas_os shared object rather than beside it, so that
 * completions can call truenas_os internals -- statx_to_pyobject() above all
 * -- directly, with no capsule indirection and no duplicated StatxResult
 * field packing.  It is a submodule only for namespace hygiene: the reactor
 * surface is large enough that it would crowd truenas_os's top level.
 *
 * truenas_os is a plain extension module, not a package, so `import
 * truenas_os.uring` works only because init_uring_submodule() registers the
 * dotted name in sys.modules.  The import system consults sys.modules before
 * it would ever look for a __path__ that does not exist.
 */

#include <Python.h>
#include "common/includes.h"
#include "truenas_os_state.h"
#include "uring.h"
#include "probe.h"
#include "anchor.h"
#include "reactor.h"

#define URING_MODULE_NAME "truenas_os.uring"

#define URING_MODULE_DOC \
	"io_uring filesystem reactor.\n\n" \
	"Provides asyncio-integrated filesystem I/O with per-request credential\n" \
	"override via registered io_uring personalities, so a privileged process\n" \
	"can perform operations as an authenticated user with the kernel's own\n" \
	"DAC and LSM checks applied.\n\n" \
	"Directory enumeration is not available here: io_uring has no getdents\n" \
	"opcode. Use truenas_os.iter_filesystem_contents() for that."

static PyMethodDef uring_methods[] = {
	{
		.ml_name = "query",
		.ml_meth = (PyCFunction)py_uring_query,
		.ml_flags = METH_NOARGS,
		.ml_doc = py_uring_query__doc__
	},
	{
		.ml_name = "supported_ops",
		.ml_meth = (PyCFunction)py_uring_supported_ops,
		.ml_flags = METH_NOARGS,
		.ml_doc = py_uring_supported_ops__doc__
	},
	{ .ml_name = NULL },
};

static struct PyModuleDef uring_moduledef = {
	PyModuleDef_HEAD_INIT,
	.m_name = URING_MODULE_NAME,
	.m_doc = URING_MODULE_DOC,
	/*
	 * State lives in the parent's truenas_os_state_t, reached through
	 * get_truenas_os_state(NULL).  A submodule created by hand never goes
	 * through _PyImport_FixupExtensionObject, so PyState_FindModule could
	 * not find it -- it must not own state of its own.
	 */
	.m_size = -1,
	.m_methods = uring_methods,
};

int
init_uring_submodule(PyObject *parent)
{
	truenas_os_state_t *state = NULL;
	PyObject *module = NULL;
	PyObject *modules = NULL;

	state = get_truenas_os_state(parent);
	if (state == NULL) {
		return -1;
	}

	module = PyModule_Create(&uring_moduledef);
	if (module == NULL) {
		return -1;
	}

	if (init_probe_types(module, state) < 0) {
		goto fail;
	}

	if (init_anchor_type(module) < 0) {
		goto fail;
	}

	if (init_reactor_types(module) < 0) {
		goto fail;
	}

	/*
	 * Register the dotted name before exposing the attribute, so a
	 * partially initialised submodule is never reachable by either route.
	 */
	modules = PyImport_GetModuleDict();	/* borrowed */
	if (modules == NULL) {
		PyErr_SetString(PyExc_SystemError, "sys.modules is unavailable");
		goto fail;
	}

	if (PyDict_SetItemString(modules, URING_MODULE_NAME, module) < 0) {
		goto fail;
	}

	if (PyModule_AddObjectRef(parent, "uring", module) < 0) {
		(void)PyDict_DelItemString(modules, URING_MODULE_NAME);
		goto fail;
	}

	/* Parent state keeps a reference for internal lookups. */
	state->uring_module = Py_NewRef(module);

	Py_DECREF(module);
	return 0;

fail:
	Py_DECREF(module);
	return -1;
}
