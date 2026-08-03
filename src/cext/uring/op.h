// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_OP_H
#define _URING_OP_H

#include "uring.h"

/*
 * The op-slot state machine (enum uring_op_state in uring.h) and the shared
 * argument helpers. Used by the prep stubs in uring.c, the op workers
 * (openclose.c, rw.c, fsync.c, stat.c) and the completion/teardown paths
 * (uring.c, submitreap.c). `static inline` so every TU that uses one gets a
 * definition; unused ones drop with no warning.
 */

/* -- ready check ---------------------------------------------------------- */

/**
 * Reject calls on a closed (or never-initialized) ring.
 *
 * @param self  the ring
 * @return 0 if the ring is usable, -1 with ValueError set otherwise
 */
static inline int
uring_check_ready(UringObject *self)
{
	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Uring is closed");
		return -1;
	}
	return 0;
}

/* -- op-slot pool --------------------------------------------------------- */

/**
 * Release every Python reference an op owns: the completion callback +
 * passthrough, the open/statx path bytes, and a read/write's pinned buffers
 * (exactly views[0, nr) -- uring_op_rw bumps nr per successful pin, so a
 * fully-prepared op and a mid-pin failure unwind the same way). Idempotent;
 * reap_one steals the callback/passthrough first, so a fired callback is
 * never freed here. Requires the GIL.
 *
 * @param op  the op whose payloads to release
 */
static inline void
op_free_payloads(uring_op_t *op)
{
	uint32_t i = 0;

	Py_CLEAR(op->callback);
	Py_CLEAR(op->private_data);
	Py_CLEAR(op->path_bytes);

	if (op->tag == URING_TAG_READ || op->tag == URING_TAG_WRITE) {
		for (i = 0; i < op->u.rw.nr; i++) {
			PyBuffer_Release(&op->u.rw.views[i]);
		}
		op->u.rw.nr = 0;
	}
}

/**
 * Pop a FREE slot and mark it PREPPED: a recycled slot if one exists
 * (already faulted in), else the next pristine slot below pool_hi -- slots
 * past pool_hi stay demand-zero, so RSS tracks peak concurrency, not
 * `entries`. Bumps the slot generation (the token's high 32 bits) and
 * re-initializes the SQE the way io_uring_get_sqe() would, so no prior op's
 * flags leak into the next prep. The payload union is NOT reset: a
 * read/write worker must zero u.rw.nr before its first failure path (see
 * uring_op_rw).
 *
 * @param self  the ring
 * @return the slot index, or URING_NO_SLOT when the pool is exhausted
 *         (the caller raises BlockingIOError)
 */
static inline uint32_t
pool_alloc(UringObject *self)
{
	uring_op_t *op = NULL;
	uint32_t slot = 0;

	if (self->pool_free != URING_NO_SLOT) {
		slot = self->pool_free;
		self->pool_free = self->pool[slot].next_free;
	} else if (self->pool_hi < self->nr_pool) {
		slot = self->pool_hi++;
	} else {
		return URING_NO_SLOT;
	}

	op = &self->pool[slot];
	op->next_free = URING_NO_SLOT;
	op->state = URING_OP_PREPPED;
	op->tag = 0;
	op->callback = NULL;		/* already NULL (calloc / op_free_payloads); */
	op->private_data = NULL;	/* kept explicit so "op owns these" is local */
	op->path_bytes = NULL;
	op->gen++;	/* persists across pool_recycle: consecutive uses of this
			 * slot never share a token */
	io_uring_initialize_sqe(&op->sqe);
	return slot;
}

/**
 * Release an op's payloads and return its slot to the pool free list
 * (PREPPED or reaped INFLIGHT -> FREE). Requires the GIL.
 *
 * @param self  the ring
 * @param slot  the slot to recycle
 */
static inline void
pool_recycle(UringObject *self, uint32_t slot)
{
	uring_op_t *op = &self->pool[slot];

	op_free_payloads(op);
	op->state = URING_OP_FREE;
	op->next_free = self->pool_free;
	self->pool_free = slot;
}

/* -- argument helpers ----------------------------------------------------- */

/*
 * Element count of a true array. A plain sizeof ratio (not Py_ARRAY_LENGTH,
 * whose GCC type-check arm is not an integer constant expression) so it can
 * size a sibling array: the keyword stubs declare
 * `PyObject *slots[URING_NELEM(params)]` and pass the same count to
 * prep_collect, keeping each prep's arity in one place -- its params list.
 */
#define URING_NELEM(arr)	(sizeof(arr) / sizeof((arr)[0]))

/**
 * Map a METH_FASTCALL|METH_KEYWORDS call onto a flat positional slot array by
 * name -- the keyword arm of every keyword-taking prep (the common positional
 * call skips this via the callers' kwnames == NULL fast path). Reads the
 * kwnames tuple in place; nothing is materialised. Which slots are required
 * is the caller's check (prep_collect).
 *
 * @param funcname  method name for error messages
 * @param args      the FASTCALL argument vector (positionals, then keyword
 *                  values)
 * @param nargs     number of positional arguments in args
 * @param kwnames   tuple of keyword names (may be NULL)
 * @param params    positional-order parameter-name list
 * @param nparams   length of params (and of slots)
 * @param slots     out: slots[i] = borrowed argument for parameter i, or NULL
 *                  if not supplied; caller must pre-NULL entries past nargs
 * @return 0, or -1 with TypeError set (too many positionals, duplicate or
 *         unknown keyword)
 */
static inline int
map_kwargs(const char *funcname, PyObject *const *args, Py_ssize_t nargs,
	   PyObject *kwnames, const char *const *params, Py_ssize_t nparams,
	   PyObject **slots)
{
	Py_ssize_t i = 0;
	Py_ssize_t k = 0;
	Py_ssize_t nkw = 0;

	if (nargs > nparams) {
		PyErr_Format(PyExc_TypeError,
			     "%s() takes at most %zd positional arguments",
			     funcname, nparams);
		return -1;
	}
	for (i = 0; i < nargs; i++) {
		slots[i] = args[i];
	}
	if (kwnames == NULL) {
		return 0;
	}
	nkw = PyTuple_GET_SIZE(kwnames);
	for (k = 0; k < nkw; k++) {
		PyObject *name = PyTuple_GET_ITEM(kwnames, k);	/* borrowed */

		for (i = 0; i < nparams; i++) {
			if (PyUnicode_CompareWithASCIIString(name, params[i]) == 0) {
				if (slots[i] != NULL) {
					PyErr_Format(PyExc_TypeError,
						     "%s() got multiple values for "
						     "argument '%s'", funcname,
						     params[i]);
					return -1;
				}
				slots[i] = args[nargs + k];
				break;
			}
		}
		if (i == nparams) {
			PyErr_Format(PyExc_TypeError,
				     "%s() got an unexpected keyword argument "
				     "'%U'", funcname, name);
			return -1;
		}
	}
	return 0;
}

/* -- op workers ----------------------------------------------------------- */

/**
 * Allocate an opaque UringOp handle for a prepared slot. Defined in uring.c
 * with the UringOp type; the op workers call it last, and on failure they
 * recycle the slot themselves.
 *
 * @param self  the ring (a strong ref is stored on the handle)
 * @param slot  the PREPPED slot the handle names
 * @return a new handle, or NULL with an exception set
 */
PyObject *handle_new(UringObject *self, uint32_t slot);

/*
 * Op workers -- each builds one prepared operation from already-parsed C
 * arguments: pool_alloc, fill the slot and its SQE, handle_new. The method
 * stubs in uring.c do the Python-level parsing and call these. All take an
 * optional callback/private_data pair (borrowed; owned refs stored on the
 * op) and return a new UringOp handle, or NULL with an exception set.
 */

/**
 * Prepare an openat2(2). The completion result is the new process fd.
 *
 * @param self        the ring
 * @param dirfd       directory fd the path resolves against
 * @param path_bytes  borrowed PyUnicode_FSConverter result (NUL-terminated
 *                    bytes, no embedded NUL); the op stores an owned ref and
 *                    the SQE points at its buffer
 * @param flags       open_how flags (O_*)
 * @param mode        open_how mode
 * @param resolve     open_how resolve (RESOLVE_*)
 */
PyObject *uring_op_openat2(UringObject *self, int dirfd, PyObject *path_bytes,
			   int flags, int mode, unsigned long long resolve,
			   PyObject *callback, PyObject *private_data);

/**
 * Prepare a close(2) of @p fd. The completion result is None.
 */
PyObject *uring_op_close(UringObject *self, int fd,
			 PyObject *callback, PyObject *private_data);

/**
 * Prepare a vectored preadv2(2)/pwritev2(2). Pins every item of @p buffers
 * (PyBUF_WRITABLE for a read, PyBUF_SIMPLE for a write) into the slot's
 * inline Py_buffer/iovec arrays until the completion reaps. The completion
 * result is the int byte count.
 *
 * @param buffers   sequence of buffer objects, at most URING_RW_IOV_CAP
 * @param offset    file offset
 * @param flags     per-IO RWF_* flags (kernel-validated at issue)
 * @param is_write  false = preadv2, true = pwritev2
 */
PyObject *uring_op_rw(UringObject *self, int fd, PyObject *buffers,
		      unsigned long long offset, int flags, bool is_write,
		      PyObject *callback, PyObject *private_data);

/**
 * Prepare an fsync(2)/fdatasync(2) of @p fd. The completion result is None.
 *
 * @param datasync  nonzero sets IORING_FSYNC_DATASYNC
 * @param offset    start of the byte range to sync
 * @param length    range length; offset == length == 0 syncs the whole file
 */
PyObject *uring_op_fsync(UringObject *self, int fd, int datasync,
			 unsigned long long offset, unsigned int length,
			 PyObject *callback, PyObject *private_data);

/**
 * Prepare a statx(2) of @p path_bytes relative to @p dirfd (same borrowed
 * FSConverter contract as uring_op_openat2). The completion result is a
 * truenas_os StatxResult built from the slot's inline landing zone.
 *
 * @param flags  AT_* flags
 * @param mask   STATX_* mask
 */
PyObject *uring_op_statx(UringObject *self, int dirfd, PyObject *path_bytes,
			 int flags, unsigned int mask,
			 PyObject *callback, PyObject *private_data);

#endif /* _URING_OP_H */
