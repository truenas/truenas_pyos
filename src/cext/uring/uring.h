// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_H
#define _URING_H

#include <Python.h>
#include <linux/openat2.h>
#include "pyuring_common.h"

#define URING_NO_SLOT		0xFFFFFFFFU

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
	URING_TAG_INSTALL,
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
 * returns straight to FREE via pool_release().
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
 * The kernel reads the openat2 path/how, the read/write buffer and the statx
 * path -- and writes the statx result -- with the GIL dropped. The pool is never
 * moved after allocation, so the inline paths, the SQE, and the statx landing
 * zone stay put across the drop; a read/write's caller buffer is pinned
 * separately (op_free_payloads). Raw allocation (not PyMem_Malloc): allocating
 * the pool must not require the GIL.
 */
typedef struct {
	uint32_t next_free;		/* pool freelist chain; NO_SLOT at tail */
	uint8_t tag;			/* URING_TAG_* -- selects the `u` arm below */
	uint8_t state;			/* enum uring_op_state */

	uint32_t file_slot;		/* fixed-file slot targeted, or NO_SLOT */
	bool owns_slot;			/* open reserved it; release if reclaimed */
	bool counted_file;		/* charged files[file_slot].live at prep; undo once */

	uint32_t gen;			/* bumped on every reuse; the op id's high 32 bits */

	/*
	 * Optional per-op completion callback + opaque passthrough (owned refs,
	 * NULL when none). Set at prep; fired once at reap (which steals them);
	 * cleared on every other teardown path via op_free_payloads.
	 */
	PyObject *callback;
	PyObject *private_data;

	/*
	 * Op-type-specific args. An op keeps one tag for its whole lifetime, so
	 * an open's path/how, a read/write's pinned buffer, and a statx's path +
	 * result never coexist -- they share storage. Everything here is inline in
	 * the pre-allocated slot except a read/write's pinned caller buffer, which
	 * is the only arm op_free_payloads() has to release. A close op uses no arm.
	 */
	union {
		struct {			/* URING_TAG_OPEN */
			/*
			 * openat2 path (NUL-terminated, < PATH_MAX), inline in
			 * the slot; the kernel reads it (and `how`) off-GIL.
			 */
			char path[PATH_MAX];
			struct open_how how;
		} open;
		struct {			/* URING_TAG_READ / URING_TAG_WRITE */
			/*
			 * Caller buffer, pinned for the whole in-flight window.
			 * Holding a Py_buffer is what stops a bytearray being
			 * resized out from under the kernel: bytearray's
			 * bf_getbuffer bumps ob_exports and resize then fails
			 * with BufferError.
			 */
			PyObject *buf_obj;	/* strong; NULL when the arm holds no pin */
			Py_buffer view;		/* valid iff buf_obj != NULL */
		} rw;
		struct {			/* URING_TAG_STATX */
			/* statx path (NUL-terminated, < PATH_MAX), inline; kernel reads off-GIL. */
			char path[PATH_MAX];
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

typedef struct {
	bool in_use;
	bool close_pending;		/* a close is prepared or in flight on this slot */
	uint32_t next_free;
	uint32_t live;			/* live (prepared + in-flight) ops on this slot */
} uring_file_t;

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

	/* Fixed-file table. */
	uring_file_t *files;
	uint32_t nr_files;
	uint32_t file_free;

	/* Dead-handle freelist (float-style: refcount-0 blocks, resurrected). */
	UringOpObject *handle_free;
	uint32_t nr_handle_free;

	bool closed;
	bool reaping;			/* inside reap()'s drain: forbids re-entrant reap/close */

	/*
	 * Serializes the SQ producers (submit/cancel, and drain during close) so
	 * multiple threads may submit concurrently while a single thread reaps. The
	 * SQ ring is single-producer; everything else (pool, file table, inflight,
	 * handles, the single-consumer CQ) is already GIL-serialized, so this lock
	 * only guards the SQ. PyMutex releases the GIL while parked, so acquiring it
	 * across the GIL-dropping io_uring_submit cannot deadlock. Zero == unlocked,
	 * so tp_alloc's zero-init needs no explicit setup or teardown.
	 */
	PyMutex submit_lock;
} UringObject;

extern PyTypeObject UringType;

int init_uring_types(PyObject *module);

#endif /* _URING_H */
