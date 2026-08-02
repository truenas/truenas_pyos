// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_H
#define _URING_H

#include <Python.h>
#include <linux/openat2.h>
#include "pyuring_common.h"

#define URING_NO_SLOT		0xFFFFFFFFU

/*
 * Most buffers a single vectored read/write may carry -- a deliberate,
 * documented API limitation. The iovec and Py_buffer arrays live inline in
 * the op slot's payload union (keeping the pool zero-malloc-per-op) at ~96 B
 * per vector, so the cap is what sizes the slot; 8 matches the kernel's own
 * UIO_FASTIOV fast path, and a larger transfer splits into multiple
 * operations. The kernel's ceiling is UIO_MAXIOV (1024).
 */
#define URING_RW_IOV_CAP	8

/*
 * The op token the caller sees (submit()'s return, reap()'s first tuple element,
 * and cancel()'s argument) packs two fields into 64 bits:
 *   high 32 bits -- the slot's generation, bumped on every reuse (pool_alloc);
 *   low  32 bits -- the op-slot index + 1 (offset by one so the id is never 0,
 *                   which is reserved as the cancel/drain sentinel).
 * It is stored in each SQE's user_data field and echoed back on the CQE. The
 * generation makes every token unique for the ring's lifetime, so a stale token
 * whose op already reaped no longer equals the token of a new op that reused the
 * slot -- the kernel's exact user_data match then turns cancel(stale) into a
 * harmless no-op instead of an ABA that aborts the live op. reap()/drain()
 * recover the slot by masking the low 32 bits; the generation rides along and is
 * never inspected on our side. These macros are the single home of that layout.
 */
#define SLOT_IDX_TO_OPID(gen, idx) \
	(((uint64_t)(gen) << 32) | ((uint64_t)(idx) + 1))
#define OPID_TO_SLOT_IDX(id)	((uint32_t)((id) - 1))

/*
 * Is `id`'s slot field valid for this ring? Checks only the low 32 bits (slot
 * index + 1) in [1, nr_pool]; the generation (high 32 bits) is deliberately not
 * range-checked -- a stale or foreign generation is syntactically valid and
 * simply fails to match any in-flight op. Any callsite taking a user-supplied id
 * must gate on this before OPID_TO_SLOT_IDX narrows it.
 */
#define OPID_VALID(self, id) \
	((uint32_t)(id) >= 1 && (uint32_t)(id) <= (self)->nr_pool)

/*
 * Op tags -- they tell reap() how to build a completed op's Python result.
 * The values are arbitrary and internal: user_data carries the packed op token,
 * not a tag, so nothing outside this module needs to agree on them.
 */
enum uring_tag {
	URING_TAG_OPEN = 1,
	URING_TAG_READ,
	URING_TAG_WRITE,
	URING_TAG_CLOSE,
	URING_TAG_STATX,
	URING_TAG_FSYNC,
};

/*
 * Op-slot lifecycle. Each pooled slot walks a small state machine:
 *
 *     FREE --prep_*()--> PREPPED --submit()--> INFLIGHT --reap()--> FREE
 *       ^                   |
 *       +----drop handle----+
 *
 * Stages:
 *   FREE      Pooled slot nobody holds; pool_alloc() pops one.
 *   PREPPED   A prep_* filled the slot's standalone SQE and returned a handle;
 *             nothing is in the ring yet.
 *   INFLIGHT  submit() copied the SQE into the ring and fired it. The kernel
 *             owns the op and delivers exactly one CQE (single-shot).
 *   FREE      reap()/drain saw the CQE, released the slot's payloads, and
 *             returned the slot to the pool.
 *
 * A handle dropped while still PREPPED (never submitted) skips INFLIGHT and
 * returns straight to FREE via pool_recycle().
 */
enum uring_op_state {
	URING_OP_FREE = 0,
	URING_OP_PREPPED,
	URING_OP_INFLIGHT,
};

/*
 * Per-op context, one per slot in a pre-allocated pool (a single
 * PyMem_RawCalloc for the whole ring; SLOT_IDX_TO_OPID is the token layout).
 * Every op is single-shot -- exactly one CQE per slot -- so free-on-reap can
 * never double-free or use-after-free.
 *
 * The kernel reads the openat2 path/how, the read/write buffers and the statx
 * path -- and writes the statx result -- with the GIL dropped. The pool is never
 * moved after allocation, so the SQE, the inline open_how, and the statx
 * landing zone stay put across the drop. An op's path is NOT copied inline:
 * the SQE points into the PyBytes the op owns via `path_bytes` (immutable, so
 * its buffer never moves), and a read/write's caller buffers are pinned as
 * Py_buffers -- op_free_payloads releases both kinds. Raw allocation (not
 * PyMem_Malloc): allocating the pool must not require the GIL.
 */
typedef struct {
	uint32_t next_free;		/* pool freelist chain; NO_SLOT at tail */
	uint8_t tag;			/* URING_TAG_* -- selects the `u` arm below */
	uint8_t state;			/* enum uring_op_state */
	uint32_t gen;			/* bumped on every reuse; the op id's high 32 bits */

	/*
	 * Optional per-op completion callback + opaque passthrough (owned refs,
	 * NULL when none). Set at prep; fired once at reap (which steals them);
	 * cleared on every other teardown path via op_free_payloads.
	 */
	PyObject *callback;
	PyObject *private_data;

	/*
	 * The op's path (open/statx only; owned ref, NULL otherwise): the
	 * FSConverter-produced PyBytes whose NUL-terminated internal buffer the
	 * SQE's addr field points at. Bytes objects are immutable and their
	 * buffer never moves, so holding this ref until the op is released is
	 * all the lifetime the kernel needs -- it getname()s its own copy at
	 * submission. Cleared via op_free_payloads.
	 */
	PyObject *path_bytes;

	/*
	 * Op-type-specific args. An op keeps one tag for its whole lifetime, so
	 * an open's how, a read/write's pinned buffers, and a statx's result
	 * never coexist -- they share storage. Everything here is inline in the
	 * pre-allocated slot; op_free_payloads() releases only a read/write's
	 * pinned caller buffers. A close or fsync op uses no arm.
	 */
	union {
		struct {			/* URING_TAG_OPEN */
			/* the kernel reads `how` off-GIL at submission */
			struct open_how how;
		} open;
		struct {			/* URING_TAG_READ / URING_TAG_WRITE */
			/*
			 * Caller buffers, each pinned for the whole in-flight
			 * window. Holding a Py_buffer (views[i].obj is the
			 * strong ref) is what stops a bytearray being resized
			 * out from under the kernel: bytearray's bf_getbuffer
			 * bumps ob_exports and resize then fails with
			 * BufferError. The iovec array the kernel reads at
			 * submission points into these views; it lives here so
			 * it never moves while the SQE is staged.
			 */
			unsigned int nr;	/* pinned views in [0, nr); 0 = no pins */
			struct iovec iov[URING_RW_IOV_CAP];
			Py_buffer views[URING_RW_IOV_CAP];
		} rw;
		struct {			/* URING_TAG_STATX */
			/* result landing zone, inline in the slot; kernel writes off-GIL. */
			struct statx stx;
		} statx;
	} u;

	/*
	 * A standalone SQE the prep_* fills. submit() memcpy's it into the real
	 * SQ ring, so a prepared-but-unsubmitted op consumes no SQ entry.
	 */
	struct io_uring_sqe sqe;
} uring_op_t;

/* Opaque handle returned by prep_* and consumed by submit(). Defined in
 * uring.c; forward-declared here for the UringObject freelist head. */
typedef struct UringOpObject UringOpObject;

typedef struct UringObject {
	PyObject_HEAD

	struct io_uring ring;
	bool ring_ready;
	int ring_fd;

	uint32_t inflight;

	/* Pre-allocated op-slot pool. */
	uring_op_t *pool;
	uint32_t nr_pool;		/* == entries */
	uint32_t pool_free;		/* recycled-slot LIFO head; NO_SLOT when empty */
	uint32_t pool_hi;		/* next never-used slot; slots >= this are pristine */

	/* Dead-handle freelist (float-style: refcount-0 blocks, resurrected). */
	UringOpObject *handle_free;
	uint32_t nr_handle_free;

	bool closed;
	bool reaping;			/* inside reap()'s drain: forbids re-entrant reap/close */

	/*
	 * Serializes the SQ producers (submit/cancel, and drain during close) so
	 * multiple threads may submit concurrently while a single thread reaps. The
	 * SQ ring is single-producer; everything else (pool, inflight, handles, the
	 * single-consumer CQ) is already GIL-serialized, so this lock only guards
	 * the SQ. PyMutex releases the GIL while parked, so acquiring it across the
	 * GIL-dropping io_uring_submit cannot deadlock. Zero == unlocked, so
	 * tp_alloc's zero-init needs no explicit setup or teardown.
	 */
	PyMutex submit_lock;
} UringObject;

extern PyTypeObject UringType;

int init_uring_types(PyObject *module);

#endif /* _URING_H */
