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
 * staging, the submit-lock critical section, and turning a CQE into a
 * completion tuple.
 */

/* A batch this size or smaller stages into an on-stack buffer, no allocator. */
#define URING_SUBMIT_STACK 8

/*
 * One staged op: its pool slot (filled by validation in uring.c) and the token
 * (filled at staging time, under the submit lock and before io_uring_submit
 * drops the GIL -- a completion reaped mid-submit could otherwise recycle the
 * slot and change the generation under us).
 */
typedef struct {
	uint32_t slot;
	uint64_t token;
} submit_ent_t;

/**
 * Fetch a fresh SQE. No mid-batch flush: submit() stages a whole batch before
 * its single io_uring_submit, so a full SQ here means the batch does not fit
 * and is rolled back by the caller's transactional revert.
 *
 * @param self  the ring (submit lock held)
 * @return the SQE, or NULL with BlockingIOError set when the SQ (depth
 *         `entries`) is full
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

/**
 * Stage a pre-validated batch into the submission queue and fire one
 * io_uring_submit, all under the submit lock. All-or-nothing: on any staging
 * failure every staged slot is reverted to PREPPED and the queued SQEs are
 * discarded, so the handles stay reusable. Defined in submitreap.c.
 *
 * @param self    the ring
 * @param ents    the batch; each ents[i].slot must be a PREPPED pool slot.
 *                ents[i].token is filled at staging time
 * @param n       batch length
 * @param linked  nonzero chains all but the last SQE with IOSQE_IO_LINK
 * @return 0 with the ops in flight (also 0, but with an exception set, when a
 *         signal handler raised during the EINTR retry -- the ops are already
 *         committed); -1 with an exception set and nothing submitted (ring
 *         closed, a slot not PREPPED / duplicated in the batch, or the SQ too
 *         small)
 */
int uring_submit_batch(UringObject *self, submit_ent_t *ents, Py_ssize_t n,
		       int linked);

/**
 * Convert one completed op into a (token, res, result) tuple (layout: enum
 * uring_completion_field), recycle its slot, and -- if the op carried a
 * completion callback -- fire it and set *consumed so the caller does not
 * also return the tuple. Defined in submitreap.c.
 *
 * @param self      the ring
 * @param op        the completed op (its CQE has been seen)
 * @param res       the CQE's raw res
 * @param consumed  out: true when a callback took the completion
 * @return a new tuple reference, or NULL with an exception set on an
 *         allocation failure
 */
PyObject *reap_one(UringObject *self, uring_op_t *op, int res, bool *consumed);

#endif /* _URING_SUBMITREAP_H */
