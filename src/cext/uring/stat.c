// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_statx -- the file-metadata worker.
 *
 * Takes already-parsed C arguments from the prep_statx stub in uring.c and
 * builds one statx operation: pop an op slot, copy the path and allocate a
 * struct statx landing zone, fill the SQE, and hand back an opaque UringOp
 * handle. reap() turns the completed struct statx into a truenas_os StatxResult
 * via statx_to_pyobject() (op_build_result in uring.c). The op-slot state
 * machine is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_statx(UringObject *self, int dirfd, const char *path,
	       Py_ssize_t path_len, int flags, unsigned int mask,
	       PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	uint32_t slot = URING_NO_SLOT;

	if (path_len >= PATH_MAX) {
		PyErr_SetString(PyExc_ValueError,
				"path too long (must be < PATH_MAX)");
		return NULL;
	}

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = URING_TAG_STATX;

	/*
	 * Both the path and the struct statx result are inline in the slot -- no
	 * allocation. The kernel fully overwrites stx on completion (statx zeroes
	 * fields it does not fill), so stale bytes from a prior op never leak.
	 */
	memcpy(op->u.statx.path, path, (size_t)path_len + 1);

	io_uring_prep_statx(&op->sqe, dirfd, op->u.statx.path, flags, mask,
			    &op->u.statx.stx);

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_release(self, slot);
		return NULL;
	}
	return handle;
}
