// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "open.h"
#include "fhandle.h"
#include "mount.h"
#include "statx.h"
#include "openat2.h"
#include "open_tree.h"
#include "move_mount.h"
#include "mount_setattr.h"
#include "fsmount.h"
#include "umount2.h"
#include "fsiter.h"
#include "truenas_os_state.h"

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

PyDoc_STRVAR(py_open_tree__doc__,
"open_tree(*, path, dir_fd=AT_FDCWD, flags=0)\n"
"--\n\n"
"Open a mount or directory tree.\n\n"
"The open_tree() system call opens a mount or directory tree, returning\n"
"a file descriptor that can be used with move_mount(2) to attach the mount\n"
"to the filesystem tree. With OPEN_TREE_CLONE, it creates a detached clone\n"
"of the mount tree.\n\n"
"All parameters are keyword-only.\n\n"
"Parameters\n"
"----------\n"
"path : str\n"
"    Path to the mount or directory (can be relative to dir_fd)\n"
"dir_fd : int, optional\n"
"    Directory file descriptor, default=AT_FDCWD (current directory)\n"
"flags : int, optional\n"
"    Flags controlling behavior (OPEN_TREE_* and AT_* constants), default=0\n\n"
"Returns\n"
"-------\n"
"fd : int\n"
"    File descriptor representing the mount tree\n\n"
"OPEN_TREE_* flags:\n"
"    OPEN_TREE_CLONE: Create a detached clone of the mount tree\n"
"    OPEN_TREE_CLOEXEC: Set close-on-exec on the file descriptor\n\n"
"AT_* flags (also usable):\n"
"    AT_EMPTY_PATH: Allow empty path (operate on dir_fd itself)\n"
"    AT_NO_AUTOMOUNT: Don't trigger automount\n"
"    AT_RECURSIVE: Clone entire subtree\n"
"    AT_SYMLINK_NOFOLLOW: Don't follow symbolic links\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> # Open a mount tree\n"
">>> mnt_fd = truenas_os.open_tree(path='/mnt/data',\n"
"...                                flags=truenas_os.OPEN_TREE_CLOEXEC)\n\n"
">>> # Clone a mount tree (detached)\n"
">>> clone_fd = truenas_os.open_tree(path='/mnt/data',\n"
"...                                  flags=truenas_os.OPEN_TREE_CLONE |\n"
"...                                        truenas_os.AT_RECURSIVE)\n"
">>> # Move it to a new location\n"
">>> truenas_os.move_mount(from_path='', to_path='/mnt/data-clone',\n"
"...                        from_dirfd=clone_fd,\n"
"...                        flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH)\n"
);

static PyObject *py_open_tree(PyObject *obj,
                               PyObject *args,
                               PyObject *kwargs)
{
	const char *path = NULL;
	int dir_fd = AT_FDCWD;
	unsigned int flags = 0;
	const char *kwnames[] = { "path", "dir_fd", "flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$siI",
	                                 discard_const_p(char *, kwnames),
	                                 &path, &dir_fd, &flags)) {
		return NULL;
	}

	// Validate required keyword-only argument
	if (path == NULL) {
		PyErr_SetString(PyExc_TypeError,
		                "open_tree() missing required keyword-only argument: 'path'");
		return NULL;
	}

	return do_open_tree(dir_fd, path, flags);
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

PyDoc_STRVAR(py_mount_setattr__doc__,
"mount_setattr(*, path, attr_set=0, attr_clr=0, propagation=0, userns_fd=0, dirfd=AT_FDCWD, flags=0)\n"
"--\n\n"
"Change properties of a mount or mount tree.\n\n"
"The mount_setattr() system call changes the mount properties of a mount\n"
"or an entire mount tree. If path is a relative pathname, then it is\n"
"interpreted relative to the directory referred to by dirfd.\n\n"
"If flags includes AT_RECURSIVE, all mounts in the subtree are affected.\n\n"
"All parameters are keyword-only for safety.\n\n"
"Parameters\n"
"----------\n"
"path : str\n"
"    Path to the mount point (can be relative to dirfd)\n"
"attr_set : int, optional\n"
"    Mount attributes to set (MOUNT_ATTR_* constants), default=0\n"
"attr_clr : int, optional\n"
"    Mount attributes to clear (MOUNT_ATTR_* constants), default=0\n"
"propagation : int, optional\n"
"    Mount propagation type (MS_SHARED, MS_SLAVE, MS_PRIVATE, MS_UNBINDABLE), default=0\n"
"userns_fd : int, optional\n"
"    User namespace file descriptor for MOUNT_ATTR_IDMAP, default=0\n"
"dirfd : int, optional\n"
"    Directory file descriptor, default=AT_FDCWD (current directory)\n"
"flags : int, optional\n"
"    Flags (AT_EMPTY_PATH, AT_RECURSIVE, AT_SYMLINK_NOFOLLOW, etc.), default=0\n\n"
"Returns\n"
"-------\n"
"None\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> # Make a mount read-only\n"
">>> truenas_os.mount_setattr(path='/mnt/data',\n"
"...                           attr_set=truenas_os.MOUNT_ATTR_RDONLY)\n\n"
">>> # Make a mount tree read-only recursively\n"
">>> truenas_os.mount_setattr(path='/mnt/data',\n"
"...                           attr_set=truenas_os.MOUNT_ATTR_RDONLY,\n"
"...                           flags=truenas_os.AT_RECURSIVE)\n\n"
">>> # Remove noexec attribute\n"
">>> truenas_os.mount_setattr(path='/mnt/data',\n"
"...                           attr_clr=truenas_os.MOUNT_ATTR_NOEXEC)\n"
);

static PyObject *py_mount_setattr(PyObject *obj,
                                   PyObject *args,
                                   PyObject *kwargs)
{
	const char *path = NULL;
	int dirfd = AT_FDCWD;
	unsigned int flags = 0;
	struct mount_attr attr = {0};
	const char *kwnames[] = { "path", "attr_set", "attr_clr", "propagation",
	                          "userns_fd", "dirfd", "flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$sKKKKiI",
	                                 discard_const_p(char *, kwnames),
	                                 &path, &attr.attr_set, &attr.attr_clr,
	                                 &attr.propagation, &attr.userns_fd,
	                                 &dirfd, &flags)) {
		return NULL;
	}

	// Validate required keyword-only argument
	if (path == NULL) {
		PyErr_SetString(PyExc_TypeError,
		                "mount_setattr() missing required keyword-only argument: 'path'");
		return NULL;
	}

	return do_mount_setattr(dirfd, path, flags, &attr);
}

PyDoc_STRVAR(py_fsopen__doc__,
"fsopen(*, fs_name, flags=0)\n"
"--\n\n"
"Open a filesystem context for configuration.\n\n"
"The fsopen() system call creates a blank filesystem configuration context\n"
"for the filesystem type specified by fs_name. This context can then be\n"
"configured using fsconfig() before creating a mount with fsmount().\n\n"
"All parameters are keyword-only.\n\n"
"Parameters\n"
"----------\n"
"fs_name : str\n"
"    Filesystem type name (e.g., 'ext4', 'xfs', 'tmpfs')\n"
"flags : int, optional\n"
"    Flags controlling behavior (FSOPEN_* constants), default=0\n\n"
"Returns\n"
"-------\n"
"fd : int\n"
"    File descriptor for the filesystem context\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> # Open a filesystem context for tmpfs\n"
">>> fs_fd = truenas_os.fsopen(fs_name='tmpfs', flags=truenas_os.FSOPEN_CLOEXEC)\n"
);

static PyObject *py_fsopen(PyObject *obj,
                            PyObject *args,
                            PyObject *kwargs)
{
	const char *fs_name = NULL;
	unsigned int flags = 0;
	const char *kwnames[] = { "fs_name", "flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$sI",
	                                 discard_const_p(char *, kwnames),
	                                 &fs_name, &flags)) {
		return NULL;
	}

	// Validate required keyword-only argument
	if (fs_name == NULL) {
		PyErr_SetString(PyExc_TypeError,
		                "fsopen() missing required keyword-only argument: 'fs_name'");
		return NULL;
	}

	return do_fsopen(fs_name, flags);
}

PyDoc_STRVAR(py_fsconfig__doc__,
"fsconfig(*, fs_fd, cmd, key=None, value=None, aux=0)\n"
"--\n\n"
"Configure a filesystem context.\n\n"
"The fsconfig() system call is used to configure a filesystem context\n"
"created by fsopen(). It can set options, provide a source device, and\n"
"trigger filesystem creation or reconfiguration.\n\n"
"All parameters are keyword-only.\n\n"
"Parameters\n"
"----------\n"
"fs_fd : int\n"
"    File descriptor from fsopen()\n"
"cmd : int\n"
"    Configuration command (FSCONFIG_* constants)\n"
"key : str, optional\n"
"    Option name (for SET_FLAG, SET_STRING, SET_PATH, etc.)\n"
"value : str or bytes, optional\n"
"    Option value (for SET_STRING, SET_BINARY, SET_PATH, etc.)\n"
"aux : int, optional\n"
"    Auxiliary parameter (for SET_FD), default=0\n\n"
"Returns\n"
"-------\n"
"None\n\n"
"FSCONFIG_* commands:\n"
"    FSCONFIG_SET_FLAG: Set a flag option (key only, no value)\n"
"    FSCONFIG_SET_STRING: Set a string-valued option\n"
"    FSCONFIG_SET_BINARY: Set a binary blob option\n"
"    FSCONFIG_SET_PATH: Set an option from a file path\n"
"    FSCONFIG_SET_PATH_EMPTY: Set from an empty path\n"
"    FSCONFIG_SET_FD: Set from a file descriptor\n"
"    FSCONFIG_CMD_CREATE: Create the filesystem\n"
"    FSCONFIG_CMD_RECONFIGURE: Reconfigure the filesystem\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> fs_fd = truenas_os.fsopen(fs_name='tmpfs', flags=truenas_os.FSOPEN_CLOEXEC)\n"
">>> # Set size option\n"
">>> truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_SET_STRING,\n"
"...                      key='size', value='1G')\n"
">>> # Create the filesystem\n"
">>> truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_CMD_CREATE)\n"
);

static PyObject *py_fsconfig(PyObject *obj,
                              PyObject *args,
                              PyObject *kwargs)
{
	int fs_fd = -1;
	unsigned int cmd = 0;
	const char *key = NULL;
	PyObject *value_obj = NULL;
	const char *value_str = NULL;
	const void *value_ptr = NULL;
	Py_ssize_t value_len = 0;
	int aux = 0;
	const char *kwnames[] = { "fs_fd", "cmd", "key", "value", "aux", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$iIsOi",
	                                 discard_const_p(char *, kwnames),
	                                 &fs_fd, &cmd, &key, &value_obj, &aux)) {
		return NULL;
	}

	// Validate required keyword-only arguments
	// We need a better way to check if cmd was provided, so let's adjust the logic
	// For now, we'll require fs_fd to be >= 0
	if (fs_fd < 0) {
		PyErr_SetString(PyExc_TypeError,
		                "fsconfig() missing required keyword-only argument: 'fs_fd'");
		return NULL;
	}

	// Handle value parameter based on its type
	if (value_obj != NULL && value_obj != Py_None) {
		if (PyUnicode_Check(value_obj)) {
			value_str = PyUnicode_AsUTF8(value_obj);
			if (value_str == NULL) {
				return NULL;
			}
			value_ptr = value_str;
		} else if (PyBytes_Check(value_obj)) {
			if (PyBytes_AsStringAndSize(value_obj, (char **)&value_ptr, &value_len) < 0) {
				return NULL;
			}
		} else if (PyLong_Check(value_obj)) {
			aux = PyLong_AsLong(value_obj);
			if (aux == -1 && PyErr_Occurred()) {
				return NULL;
			}
			value_ptr = NULL;
		} else {
			PyErr_SetString(PyExc_TypeError,
			                "value must be str, bytes, int, or None");
			return NULL;
		}
	}

	return do_fsconfig(fs_fd, cmd, key, value_ptr, aux);
}

PyDoc_STRVAR(py_fsmount__doc__,
"fsmount(*, fs_fd, flags=0, attr_flags=0)\n"
"--\n\n"
"Create a mount object from a configured filesystem context.\n\n"
"The fsmount() system call takes a filesystem context created by fsopen()\n"
"and configured with fsconfig(), and creates a mount object. This mount\n"
"can then be attached to the filesystem tree using move_mount().\n\n"
"All parameters are keyword-only.\n\n"
"Parameters\n"
"----------\n"
"fs_fd : int\n"
"    File descriptor from fsopen() (after configuration with fsconfig())\n"
"flags : int, optional\n"
"    Mount flags (FSMOUNT_* constants), default=0\n"
"attr_flags : int, optional\n"
"    Mount attribute flags (MOUNT_ATTR_* constants), default=0\n\n"
"Returns\n"
"-------\n"
"fd : int\n"
"    File descriptor for the mount object\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> import os\n"
">>> # Create and configure filesystem\n"
">>> fs_fd = truenas_os.fsopen(fs_name='tmpfs', flags=truenas_os.FSOPEN_CLOEXEC)\n"
">>> truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_SET_STRING,\n"
"...                      key='size', value='1G')\n"
">>> truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_CMD_CREATE)\n"
">>> # Create mount object\n"
">>> mnt_fd = truenas_os.fsmount(fs_fd=fs_fd, flags=truenas_os.FSMOUNT_CLOEXEC,\n"
"...                              attr_flags=truenas_os.MOUNT_ATTR_RDONLY)\n"
">>> os.close(fs_fd)\n"
">>> # Attach to filesystem tree\n"
">>> truenas_os.move_mount(from_path='', to_path='/mnt/test',\n"
"...                        from_dirfd=mnt_fd,\n"
"...                        flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH)\n"
">>> os.close(mnt_fd)\n"
);

static PyObject *py_fsmount(PyObject *obj,
                             PyObject *args,
                             PyObject *kwargs)
{
	int fs_fd = -1;
	unsigned int flags = 0;
	unsigned int attr_flags = 0;
	const char *kwnames[] = { "fs_fd", "flags", "attr_flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$iII",
	                                 discard_const_p(char *, kwnames),
	                                 &fs_fd, &flags, &attr_flags)) {
		return NULL;
	}

	// Validate required keyword-only argument
	if (fs_fd < 0) {
		PyErr_SetString(PyExc_TypeError,
		                "fsmount() missing required keyword-only argument: 'fs_fd'");
		return NULL;
	}

	return do_fsmount(fs_fd, flags, attr_flags);
}

PyDoc_STRVAR(py_umount2__doc__,
"umount2(*, target, flags=0)\n"
"--\n\n"
"Unmount a filesystem.\n\n"
"The umount2() system call unmounts the filesystem mounted at the specified\n"
"target. The flags parameter controls the unmount behavior, allowing for\n"
"forced unmounts, lazy unmounts, or expiration of mount points.\n\n"
"All parameters are keyword-only.\n\n"
"Parameters\n"
"----------\n"
"target : str\n"
"    Path to the mount point to unmount\n"
"flags : int, optional\n"
"    Unmount flags (MNT_* and UMOUNT_* constants), default=0\n\n"
"Returns\n"
"-------\n"
"None\n\n"
"MNT_* and UMOUNT_* flags:\n"
"    MNT_FORCE: Force unmount even if busy (may cause data loss)\n"
"    MNT_DETACH: Lazy unmount - detach filesystem from hierarchy now,\n"
"                clean up references when no longer busy\n"
"    MNT_EXPIRE: Mark mount point as expired. If not busy, unmount it.\n"
"                Repeated calls will unmount an expired mount.\n"
"    UMOUNT_NOFOLLOW: Don't dereference target if it is a symbolic link\n\n"
"Examples\n"
"--------\n"
">>> import truenas_os\n"
">>> # Normal unmount\n"
">>> truenas_os.umount2(target='/mnt/data')\n\n"
">>> # Lazy unmount (useful when filesystem is busy)\n"
">>> truenas_os.umount2(target='/mnt/data',\n"
"...                     flags=truenas_os.MNT_DETACH)\n\n"
">>> # Force unmount (may cause data loss - use with caution)\n"
">>> truenas_os.umount2(target='/mnt/data',\n"
"...                     flags=truenas_os.MNT_FORCE)\n\n"
">>> # Don't follow symbolic links\n"
">>> truenas_os.umount2(target='/mnt/link',\n"
"...                     flags=truenas_os.UMOUNT_NOFOLLOW)\n"
);

static PyObject *py_umount2(PyObject *obj,
                             PyObject *args,
                             PyObject *kwargs)
{
	const char *target = NULL;
	int flags = 0;
	const char *kwnames[] = { "target", "flags", NULL };

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$si",
	                                 discard_const_p(char *, kwnames),
	                                 &target, &flags)) {
		return NULL;
	}

	// Validate required keyword-only argument
	if (target == NULL) {
		PyErr_SetString(PyExc_TypeError,
		                "umount2() missing required keyword-only argument: 'target'");
		return NULL;
	}

	return do_umount2(target, flags);
}

PyDoc_STRVAR(py_iter_filesystem_contents__doc__,
"iter_filesystem_contents(mountpoint, filesystem_name, relative_path=None, /,\n"
"                         btime_cutoff=0, cnt=0, cnt_bytes=0,\n"
"                         file_open_flags=0, reporting_increment=1000,\n"
"                         reporting_callback=None, reporting_private_data=None,\n"
"                         dir_stack=None)\n"
"--\n\n"
"Iterate over all files and directories in a filesystem.\n"
"Provides secure iteration using openat2 and statx, preventing symlink attacks\n"
"and ensuring iteration stays within filesystem boundaries.\n"
"Parameters\n"
"----------\n"
"mountpoint : str\n"
"    Absolute path where the filesystem is mounted\n"
"filesystem_name : str\n"
"    Filesystem source name to verify (e.g., 'tank/dataset')\n"
"relative_path : str, optional\n"
"    Subdirectory path relative to mountpoint. If None, iterates from root\n"
"btime_cutoff : int, optional, default=0\n"
"    Unix timestamp for filtering files by birth time. Files newer than this\n"
"    timestamp are skipped. Set to 0 to disable filtering\n"
"cnt : int, optional, default=0\n"
"    Running count of items yielded. Updated during iteration\n"
"cnt_bytes : int, optional, default=0\n"
"    Running count of total bytes. Updated during iteration\n"
"file_open_flags : int, optional, default=0\n"
"    Flags to use when opening files. O_NOFOLLOW is always added automatically\n"
"reporting_increment : int, optional, default=1000\n"
"    Call reporting_callback every N items processed. Set to 0 to disable\n"
"reporting_callback : callable, optional\n"
"    Function to call with (dir_stack, state, reporting_private_data) every reporting_increment items.\n"
"    The dir_stack parameter is a tuple of (path, inode) tuples representing the current directory stack.\n"
"    The state parameter is a FilesystemIterState object with current iteration statistics\n"
"reporting_private_data : object, optional\n"
"    User data to pass to reporting_callback\n"
"dir_stack : tuple, optional\n"
"    Directory stack from a previous iteration to resume from. Should be a tuple of\n"
"    (path, inode) tuples obtained from a previous iterator's dir_stack() method.\n"
"    If provided, the iterator will attempt to restore to that position in the tree.\n"
"    Raises IteratorRestoreError if restoration fails.\n"
"Returns\n"
"-------\n"
"iterator : FilesystemIterator\n"
"    Iterator yielding IterInstance objects for each file and directory\n"
);

/*
 * Python wrapper for iter_filesystem_contents
 */
static PyObject *
py_iter_filesystem_contents(PyObject *self, PyObject *args, PyObject *kwargs)
{
	const char *mountpoint;
	const char *relative_path = NULL;
	const char *filesystem_name;
	iter_state_t state = {0};

	/* New reporting parameters */
	size_t reporting_cb_increment = 1000;
	PyObject *reporting_cb = NULL;
	PyObject *reporting_cb_private_data = NULL;
	PyObject *dir_stack = NULL;

	static char *kwlist[] = {
		"mountpoint", "filesystem_name", "relative_path",
		"btime_cutoff", "cnt", "cnt_bytes", "file_open_flags",
		"reporting_increment", "reporting_callback", "reporting_private_data",
		"dir_stack",
		NULL
	};

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "ss|zLKKiKOOO:iter_filesystem_contents", kwlist,
					  &mountpoint, &filesystem_name, &relative_path,
					  &state.btime_cutoff, &state.cnt, &state.cnt_bytes,
					  &state.file_open_flags,
					  &reporting_cb_increment, &reporting_cb, &reporting_cb_private_data,
					  &dir_stack)) {
		return NULL;
	}

	return create_filesystem_iterator(mountpoint, relative_path, filesystem_name, &state,
	                                  reporting_cb_increment, reporting_cb, reporting_cb_private_data,
	                                  dir_stack);
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
		.ml_name = "open_tree",
		.ml_meth = (PyCFunction)py_open_tree,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_open_tree__doc__
	},
	{
		.ml_name = "move_mount",
		.ml_meth = (PyCFunction)py_move_mount,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_move_mount__doc__
	},
	{
		.ml_name = "mount_setattr",
		.ml_meth = (PyCFunction)py_mount_setattr,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_mount_setattr__doc__
	},
	{
		.ml_name = "fsopen",
		.ml_meth = (PyCFunction)py_fsopen,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_fsopen__doc__
	},
	{
		.ml_name = "fsconfig",
		.ml_meth = (PyCFunction)py_fsconfig,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_fsconfig__doc__
	},
	{
		.ml_name = "fsmount",
		.ml_meth = (PyCFunction)py_fsmount,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_fsmount__doc__
	},
	{
		.ml_name = "umount2",
		.ml_meth = (PyCFunction)py_umount2,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_umount2__doc__
	},
	{
		.ml_name = "iter_filesystem_contents",
		.ml_meth = (PyCFunction)py_iter_filesystem_contents,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_iter_filesystem_contents__doc__
	},
	{ .ml_name = NULL }
};

static struct PyModuleDef moduledef = {
	PyModuleDef_HEAD_INIT,
	.m_name = "truenas_os",
	.m_doc = MODULE_DOC,
	.m_size = sizeof(truenas_os_state_t),
	.m_methods = truenas_os_methods,
};

/* Get module state */
truenas_os_state_t *
get_truenas_os_state(PyObject *module)
{
	void *state;

	if (module == NULL) {
		module = PyState_FindModule(&moduledef);
		if (module == NULL) {
			return NULL;
		}
	}

	state = PyModule_GetState(module);
	return (truenas_os_state_t *)state;
}

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

	// Initialize open_tree constants
	if (init_open_tree_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize move_mount constants
	if (init_move_mount_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize mount_setattr constants
	if (init_mount_setattr_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize fsmount constants
	if (init_fsmount_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize umount2 constants
	if (init_umount2_constants(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Initialize filesystem iterator types
	if (init_iter_types(m) < 0) {
		Py_DECREF(m);
		return NULL;
	}

	// Create and add IteratorRestoreError exception
	truenas_os_state_t *state = get_truenas_os_state(m);
	if (state == NULL) {
		Py_DECREF(m);
		return NULL;
	}

	state->IteratorRestoreError = PyErr_NewExceptionWithDoc(
		"truenas_os.IteratorRestoreError",
		"Exception raised when iterator cannot be restored to previous state.\n\n"
		"Attributes\n"
		"----------\n"
		"depth : int\n"
		"    The directory stack depth at which restoration failed",
		NULL, NULL);

	if (state->IteratorRestoreError == NULL) {
		Py_DECREF(m);
		return NULL;
	}

	if (PyModule_AddObjectRef(m, "IteratorRestoreError", state->IteratorRestoreError) < 0) {
		Py_DECREF(state->IteratorRestoreError);
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
