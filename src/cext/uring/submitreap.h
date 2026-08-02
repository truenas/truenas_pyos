// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_SUBMITREAP_H
#define _URING_SUBMITREAP_H

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"

/*
 * The submit and reap mechanics -- the ring's runtime path. The Python-facing
 * stubs (py_uring_ring_submit / reap / cancel) live in uring.c with the method
 * table and do the argument parsing; the backends declared here own the SQ
 * staging, the submit-lock critical section, and turning a CQE into a completion
 * tuple. Split out of uring.c to keep that file to the types, the prep stubs, and
 * the ring lifecycle.
 */

/* A batch this size or smaller stages into an on-stack buffer, no allocator. */
#define URING_SUBMIT_STACK 8

/*
 * One staged op: its pool slot (filled by validation in uring.c) and the
 * token (filled at staging time). The token is captured under the lock
 * before io_uring_submit drops the GIL, so a completion reaped on another thread
 * mid-submit -- which could recycle and re-prep the slot -- can never make us
 * read a different generation for the caller's returned token.
 */
typedef struct {
	uint32_t slot;
	uint64_t token;
} submit_ent_t;

/*
 * Fetch a fresh SQE, or NULL with a BlockingIOError set when the SQ (depth
 * `entries`) is full. No mid-batch flush: submit() stages a whole batch before
 * its single io_uring_submit, so flushing here would break its all-or-nothing
 * contract; a full-SQ batch is rolled back by the transactional revert. Outside a
 * batch (cancel) the SQ is always drained, so this never trips there.
 */
static inline struct io_uring_sqe *
uring_get_sqe(UringObject *self)
{
	struct io_uring_sqe *sqe = io_uring_get_sqe(&self->ring);

	if (sqe == NULL) {
		PyErr_SetString(PyExc_BlockingIOError,
				"submission queue is full: the batch is larger "
				"than the ring's `entries`");
	}
	return sqe;
}

/*
 * Stage a pre-validated batch (ents[0..n), each ents[i].slot a PREPPED pool slot)
 * into the submission queue and fire one io_uring_submit under the submit lock,
 * filling ents[i].token for each. Returns 0 with the ops in flight (a signal that
 * raised during the EINTR retry also returns 0 but leaves an exception set for the
 * caller to propagate), or -1 with an exception set and nothing submitted (ring
 * closed, a slot not PREPPED / duplicated in the batch, or the SQ too small),
 * every staged slot reverted so the handles stay reusable. Defined in submitreap.c.
 */
int uring_submit_batch(UringObject *self, submit_ent_t *ents, Py_ssize_t n,
		       int linked);

/*
 * Convert one completed op into a plain (token, res, result) tuple: build the
 * typed result, recycle the slot, and -- if the op carried a completion
 * callback -- fire it here and set *consumed (the op is then not returned to
 * reap()'s list). Returns a new tuple reference, or NULL with an exception set
 * on an allocation failure. Defined in submitreap.c.
 */
PyObject *reap_one(UringObject *self, uring_op_t *op, int res, bool *consumed);

#endif /* _URING_SUBMITREAP_H */
