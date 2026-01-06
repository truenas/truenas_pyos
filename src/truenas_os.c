// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "open.h"
#include "fhandle.h"
#include "mount.h"
#include "statx.h"
#include "openat2.h"
#include "move_mount.h"

#define MODULE_DOC "TrueNAS OS module"

PyDoc_STRVAR(py_openmnt__doc__,
"open_mount_by_id()\n"
"--\n\n"
"Open the mountpoint for a mounted filesystem by its given mount_id.\n"
"This is useful for cases where a file handle must be converted into a \n"
"Usable file descriptor, but no open is available for the required \n"
"mounted filesystem."
"Parameters\n"
"----------\n"
"mount_id : str\n"
"    Mount ID for the mounted filesystem. See mountinfo documentation \n"
"    in the manpage for proc(5).\n"
"flags : str, optional, default=os.O_DIRECTORY\n"
"    open(2) flags with which to open mountpoint. See documentation for \n"
"    os.open() for list of open(2) flags and their meanings \n"
"Returns\n"
"-------\n"
"fd : int\n"
"    opened file descriptor for mountpoint\n"
);

static PyObject *py_openmnt(PyObject *obj,
			    PyObject *args,
			    PyObject *kwargs)
{
	uint64_t mount_id;
	int flags = O_DIRECTORY;
	const char *kwnames [] = { "mount_id", "flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs,
					 "K|i",
					 discard_const_p(char *, kwnames),
					 &mount_id, &flags)) {
		return NULL;
	}

	return py_open_mount_id(mount_id, flags);
}

PyDoc_STRVAR(py_listmount__doc__,
"listmount(mnt_id=LSMT_ROOT, last_mnt_id=0, reverse=False)\n"
"--\n\n"
"List mount IDs under a given mount point.\n\n"
"The listmount() system call returns a list of mount IDs for mounts that\n"
"are children of the specified mount ID.  This can be used to traverse\n"
"the mount tree.\n\n"
"This function automatically handles pagination to return all mount IDs.\n\n"
"Parameters\n"
"----------\n"
"mnt_id : int, optional\n"
"    Mount ID to list children of. Defaults to root mount (LSMT_ROOT).\n"
"last_mnt_id : int, optional\n"
"    Last mount ID returned (for pagination), default=0\n"
"reverse : bool, optional\n"
"    List mounts in reverse order (newest first), default=False\n\n"
"Returns\n"
"-------\n"
"list of int\n"
"    List of mount IDs. Empty list if no child mounts.\n"
);

static PyObject *py_listmount(PyObject *obj,
			      PyObject *args,
			      PyObject *kwargs)
{
	uint64_t mnt_id = LSMT_ROOT;
	uint64_t last_mnt_id = 0;
	int reverse = 0;
	const char *kwnames[] = { "mnt_id", "last_mnt_id", "reverse", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|KKp",
					 discard_const_p(char *, kwnames),
					 &mnt_id, &last_mnt_id, &reverse)) {
		return NULL;
	}

	return do_listmount(mnt_id, last_mnt_id, reverse);
}

PyDoc_STRVAR(py_statmount__doc__,
"statmount(mnt_id, mask=STATMOUNT_MNT_BASIC|STATMOUNT_SB_BASIC)\n"
"--\n\n"
"Get detailed information about a mount.\n\n"
"The statmount() system call returns information about the mount point\n"
"identified by mnt_id. The information returned is controlled by the mask\n"
"parameter, which specifies which fields to retrieve.\n\n"
"Parameters\n"
"----------\n"
"mnt_id : int\n"
"    Mount ID to query\n"
"mask : int, optional\n"
"    Mask of fields to retrieve (STATMOUNT_* constants).\n"
"    Default is STATMOUNT_MNT_BASIC | STATMOUNT_SB_BASIC\n\n"
"Returns\n"
"-------\n"
"StatmountResult\n"
"    Named tuple with mount information. Fields not requested will be None.\n"
);

static PyObject *py_statmount(PyObject *obj,
			      PyObject *args,
			      PyObject *kwargs)
{
	uint64_t mnt_id;
	uint64_t mask = STATMOUNT_MNT_BASIC | STATMOUNT_SB_BASIC;
	const char *kwnames[] = { "mnt_id", "mask", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "K|K",
					 discard_const_p(char *, kwnames),
					 &mnt_id, &mask)) {
		return NULL;
	}

	return do_statmount(mnt_id, mask);
}

PyDoc_STRVAR(py_iter_mount__doc__,
"iter_mount(mnt_id=LSMT_ROOT, last_mnt_id=0, reverse=False, statmount_flags=STATMOUNT_MNT_BASIC|STATMOUNT_SB_BASIC)\n"
"--\n\n"
"Create an iterator over mount information.\n\n"
"Returns an iterator that yields StatmountResult objects for each mount\n"
"under the specified mount ID. This combines listmount(2) and statmount(2)\n"
"syscalls into a single iterator interface, efficiently fetching mount IDs\n"
"in batches.\n\n"
"Parameters\n"
"----------\n"
"mnt_id : int, optional\n"
"    Mount ID to list children of. Defaults to root mount (LSMT_ROOT).\n"
"last_mnt_id : int, optional\n"
"    Last mount ID returned (for pagination), default=0\n"
"reverse : bool, optional\n"
"    List mounts in reverse order (newest first), default=False\n"
"statmount_flags : int, optional\n"
"    Mask of fields to retrieve for each mount (STATMOUNT_* constants).\n"
"    Default is STATMOUNT_MNT_BASIC | STATMOUNT_SB_BASIC\n\n"
"Returns\n"
"-------\n"
"iterator\n"
"    Iterator that yields StatmountResult objects\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> # Iterate over all mounts from root\n"
">>> for mount_info in truenas_os.iter_mount():\n"
"...     print(f\"Mount ID: {mount_info.mnt_id}, Type: {mount_info.fs_type}\")\n\n"
">>> # Get detailed info for all mounts\n"
">>> flags = (truenas_os.STATMOUNT_MNT_BASIC | \n"
"...          truenas_os.STATMOUNT_SB_BASIC |\n"
"...          truenas_os.STATMOUNT_MNT_ROOT |\n"
"...          truenas_os.STATMOUNT_MNT_POINT)\n"
">>> for mount_info in truenas_os.iter_mount(statmount_flags=flags):\n"
"...     print(f\"{mount_info.mnt_point}: {mount_info.fs_type}\")\n"
);

static PyObject *py_iter_mount(PyObject *obj,
			       PyObject *args,
			       PyObject *kwargs)
{
	return create_mount_iterator(args, kwargs);
}

PyDoc_STRVAR(py_statx__doc__,
"statx(dirfd, path, flags=0, mask=STATX_BASIC_STATS|STATX_BTIME)\n"
"--\n\n"
"Get extended file attributes.\n\n"
"The statx() system call returns detailed information about a file,\n"
"including fields not available in traditional stat() such as creation\n"
"time (birth time), mount ID, and atomic write capabilities.\n\n"
"Parameters\n"
"----------\n"
"dirfd : int\n"
"    Directory file descriptor (use AT_FDCWD for current directory)\n"
"path : str\n"
"    Path to the file (can be relative to dirfd)\n"
"flags : int, optional\n"
"    Flags controlling the behavior (AT_* constants), default=0\n"
"mask : int, optional\n"
"    Mask of fields to retrieve (STATX_* constants).\n"
"    Default is STATX_BASIC_STATS | STATX_BTIME\n\n"
"Returns\n"
"-------\n"
"StatxResult\n"
"    Named tuple with extended file attributes.\n"
);

static PyObject *py_statx(PyObject *obj,
			  PyObject *args,
			  PyObject *kwargs)
{
	const char *path;
	int dirfd = AT_FDCWD;
	int flags = 0;
	unsigned int mask = STATX_BASIC_STATS | STATX_BTIME;
	const char *kwnames[] = { "path", "dir_fd", "flags", "mask", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "s|iiI",
					 discard_const_p(char *, kwnames),
					 &path, &dirfd, &flags, &mask)) {
		return NULL;
	}

	return do_statx(dirfd, path, flags, mask);
}

PyDoc_STRVAR(py_openat2__doc__,
"openat2(path, flags, dir_fd=AT_FDCWD, mode=0, resolve=0)\n"
"--\n\n"
"Extended openat with path resolution control.\n\n"
"The openat2() system call is an extension of openat(2) and provides\n"
"additional control over path resolution through the resolve parameter.\n\n"
"Parameters\n"
"----------\n"
"path : str\n"
"    Path to the file (can be relative to dir_fd)\n"
"flags : int\n"
"    File creation and status flags (O_* constants from os module)\n"
"dir_fd : int, optional\n"
"    Directory file descriptor, default=AT_FDCWD (current directory)\n"
"mode : int, optional\n"
"    File mode (permissions) for O_CREAT/O_TMPFILE, default=0\n"
"resolve : int, optional\n"
"    Path resolution flags (RESOLVE_* constants), default=0\n\n"
"Returns\n"
"-------\n"
"fd : int\n"
"    File descriptor for the opened file\n\n"
"RESOLVE_* flags:\n"
"    RESOLVE_NO_XDEV: Block mount-point crossings\n"
"    RESOLVE_NO_MAGICLINKS: Block traversal through procfs magic-links\n"
"    RESOLVE_NO_SYMLINKS: Block traversal through all symlinks\n"
"    RESOLVE_BENEATH: Block escaping the dir_fd (no .. or absolute paths)\n"
"    RESOLVE_IN_ROOT: Scope all jumps to / and .. inside dir_fd\n"
"    RESOLVE_CACHED: Only complete if resolution can use cached lookup\n"
);

static PyObject *py_openat2(PyObject *obj,
			    PyObject *args,
			    PyObject *kwargs)
{
	const char *path;
	uint64_t flags;
	int dir_fd = AT_FDCWD;
	uint64_t mode = 0;
	uint64_t resolve = 0;
	const char *kwnames[] = { "path", "flags", "dir_fd", "mode", "resolve", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "sK|iKK",
					 discard_const_p(char *, kwnames),
					 &path, &flags, &dir_fd, &mode, &resolve)) {
		return NULL;
	}

	return do_openat2(dir_fd, path, flags, mode, resolve);
}

PyDoc_STRVAR(py_move_mount__doc__,
"move_mount(*, from_path, to_path, from_dirfd=AT_FDCWD, to_dirfd=AT_FDCWD, flags=0)\n"
"--\n\n"
"Move a mount from one place to another.\n\n"
"The move_mount() system call moves a mount from one place to another;\n"
"it can also be used to attach an unattached mount created by fsmount(2)\n"
"or open_tree(2).\n\n"
"All parameters are keyword-only for safety.\n\n"
"Parameters\n"
"----------\n"
"from_path : str\n"
"    Source path (can be relative to from_dirfd)\n"
"to_path : str\n"
"    Destination path (can be relative to to_dirfd)\n"
"from_dirfd : int, optional\n"
"    Directory file descriptor for source, default=AT_FDCWD (current directory)\n"
"to_dirfd : int, optional\n"
"    Directory file descriptor for destination, default=AT_FDCWD (current directory)\n"
"flags : int, optional\n"
"    Movement flags (MOVE_MOUNT_* constants), default=0\n\n"
"Returns\n"
"-------\n"
"None\n\n"
"MOVE_MOUNT_* flags:\n"
"    MOVE_MOUNT_F_SYMLINKS: Follow symlinks on from path\n"
"    MOVE_MOUNT_F_AUTOMOUNTS: Follow automounts on from path\n"
"    MOVE_MOUNT_F_EMPTY_PATH: Empty from path permitted\n"
"    MOVE_MOUNT_T_SYMLINKS: Follow symlinks on to path\n"
"    MOVE_MOUNT_T_AUTOMOUNTS: Follow automounts on to path\n"
"    MOVE_MOUNT_T_EMPTY_PATH: Empty to path permitted\n"
"    MOVE_MOUNT_SET_GROUP: Set sharing group instead\n"
"    MOVE_MOUNT_BENEATH: Mount beneath top mount\n"
);

static PyObject *py_move_mount(PyObject *obj,
			       PyObject *args,
			       PyObject *kwargs)
{
	const char *from_path = NULL;
	const char *to_path = NULL;
	int from_dirfd = AT_FDCWD;
	int to_dirfd = AT_FDCWD;
	unsigned int flags = 0;
	const char *kwnames[] = { "from_path", "to_path", "from_dirfd",
	                          "to_dirfd", "flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$ssiiI",
					 discard_const_p(char *, kwnames),
					 &from_path, &to_path,
					 &from_dirfd, &to_dirfd, &flags)) {
		return NULL;
	}

	// Validate required keyword-only arguments
	if (from_path == NULL) {
		PyErr_SetString(PyExc_TypeError,
				"move_mount() missing required keyword-only argument: 'from_path'");
		return NULL;
	}
	if (to_path == NULL) {
		PyErr_SetString(PyExc_TypeError,
				"move_mount() missing required keyword-only argument: 'to_path'");
		return NULL;
	}

	return do_move_mount(from_dirfd, from_path, to_dirfd, to_path, flags);
}

static PyMethodDef truenas_os_methods[] = {
	{
		.ml_name = "open_mount_by_id",
		.ml_meth = (PyCFunction)py_openmnt,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_openmnt__doc__
        },
	{
		.ml_name = "listmount",
		.ml_meth = (PyCFunction)py_listmount,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_listmount__doc__
	},
	{
		.ml_name = "statmount",
		.ml_meth = (PyCFunction)py_statmount,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_statmount__doc__
	},
	{
		.ml_name = "iter_mount",
		.ml_meth = (PyCFunction)py_iter_mount,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_iter_mount__doc__
	},
	{
		.ml_name = "statx",
		.ml_meth = (PyCFunction)py_statx,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_statx__doc__
	},
	{
		.ml_name = "openat2",
		.ml_meth = (PyCFunction)py_openat2,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_openat2__doc__
	},
	{
		.ml_name = "move_mount",
		.ml_meth = (PyCFunction)py_move_mount,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_move_mount__doc__
	},
	{ .ml_name = NULL }
};

static struct PyModuleDef moduledef = {
	PyModuleDef_HEAD_INIT,
	.m_name = "truenas_os",
	.m_doc = MODULE_DOC,
	.m_size = -1,
	.m_methods = truenas_os_methods,
};

PyObject* module_init(void)
{
	PyObject *m = NULL;
	m = PyModule_Create(&moduledef);
	if (m == NULL) {
		fprintf(stderr, "failed to initalize module\n");
		return NULL;
	}

	if (PyType_Ready(&PyFhandle) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	if (PyModule_AddObject(m, "fhandle", (PyObject *)&PyFhandle) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize mount types and constants
	if (init_mount_types(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize mount iterator type
	if (init_mount_iter_type(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize statx types and constants
	if (init_statx_types(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize openat2 constants
	if (init_openat2_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize move_mount constants
	if (init_move_mount_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Add file handle flag constants
	PyModule_AddIntConstant(m, "FH_AT_SYMLINK_FOLLOW", FH_AT_SYMLINK_FOLLOW);
	PyModule_AddIntConstant(m, "FH_AT_EMPTY_PATH", FH_AT_EMPTY_PATH);
	PyModule_AddIntConstant(m, "FH_AT_HANDLE_FID", FH_AT_HANDLE_FID);
	PyModule_AddIntConstant(m, "FH_AT_HANDLE_CONNECTABLE", FH_AT_HANDLE_CONNECTABLE);

	return m;
}

PyMODINIT_FUNC PyInit_truenas_os(void)
{
	return module_init();
}
