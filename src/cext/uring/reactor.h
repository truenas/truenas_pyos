// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_REACTOR_H
#define _URING_REACTOR_H

#include <Python.h>
#include "common/includes.h"
#include "truenas_os_state.h"

/*
 * user_data layout: tag(8) | slot(24) | generation(32).
 *
 * The generation is what makes a completion for a recycled slot inert: a slot
 * is freed and its generation bumped *before* the waiter is settled, so a
 * callback may immediately reuse the slot while a late or cancelled CQE for
 * the previous occupant is still in flight.
 */
#define URING_UD(tag, slot, gen)					\
	(((uint64_t)(uint8_t)(tag)) |					\
	 (((uint64_t)(slot) & 0xFFFFFFULL) << 8) |			\
	 (((uint64_t)(gen)) << 32))

#define URING_UD_TAG(ud)	((uint8_t)((ud) & 0xFFULL))
#define URING_UD_SLOT(ud)	((uint32_t)(((ud) >> 8) & 0xFFFFFFULL))
#define URING_UD_GEN(ud)	((uint32_t)((ud) >> 32))

#define URING_MAX_OPS		0xFFFFFFU	/* 24-bit slot field */
#define URING_NO_SLOT		0xFFFFFFFFU

/*
 * Op tags. Values match the Rust implementation's fs tag domain
 * (fs-reactor design section 13) so both implementations describe a
 * completion the same way in traces.
 */
enum uring_tag {
	URING_TAG_OPEN		= 0x80,
	URING_TAG_READ		= 0x81,
	URING_TAG_WRITE		= 0x82,
	URING_TAG_FSYNC		= 0x83,
	URING_TAG_STATX		= 0x84,
	URING_TAG_CLOSE		= 0x85,
	URING_TAG_CANCEL	= 0x9E,
};

enum uring_op_state {
	URING_OP_FREE = 0,
	URING_OP_INFLIGHT,
	/*
	 * The waiter is gone (Future cancelled or reactor tearing down) but
	 * the kernel may still write into this entry's buffers. Everything
	 * stays allocated until the CQE reaps.
	 */
	URING_OP_ORPHANED,
};

typedef struct {
	uint8_t tag;
	uint8_t state;
	uint32_t gen;
	uint32_t next_free;

	PyObject *future;		/* strong; NULL once orphaned */

	/*
	 * Caller-supplied buffer, pinned for the whole in-flight window.
	 * Holding a Py_buffer is what stops a bytearray being resized out
	 * from under the kernel: bytearray's bf_getbuffer bumps ob_exports
	 * and resize then fails with BufferError.
	 */
	PyObject *buf_obj;		/* strong; NULL if none */
	Py_buffer view;
	bool has_view;

	/*
	 * Kernel-visible payloads. PyMem_RawMalloc, never PyMem_Malloc: the
	 * kernel writes into these while the GIL is not held, and the Mem and
	 * object domains both require the GIL.
	 */
	char *path;
	struct open_how *how;
	struct statx *stx;

	uint32_t file_slot;		/* fixed-file slot targeted, or NO_SLOT */
	bool owns_slot;			/* open reserved it; release if it fails */
} uring_op_t;

typedef struct {
	bool in_use;
	bool close_pending;
	uint32_t gen;
	uint32_t next_free;
	uint32_t ops;			/* in-flight ops against this slot */
} uring_file_t;

typedef struct {
	PyObject_HEAD

	struct io_uring ring;
	bool ring_ready;
	int ring_fd;

	uring_op_t *ops;
	uint32_t nr_ops;
	uint32_t op_free;
	uint32_t inflight;

	uring_file_t *files;
	uint32_t nr_files;
	uint32_t file_free;

	PyObject *loop;			/* strong while attached */
	PyObject *reap_cb;		/* bound self._reap, held while attached */
	bool attached;
	bool closed;
} ReactorObject;

typedef struct {
	PyObject_HEAD
	PyObject *reactor;		/* strong */
	unsigned int id;		/* 1..65535; 0 means unregistered */
} PersonalityObject;

typedef struct {
	PyObject_HEAD
	PyObject *reactor;		/* strong */
	uint32_t slot;
	uint32_t gen;
	bool closed;
} FixedFileObject;

extern PyTypeObject ReactorType;
extern PyTypeObject PersonalityType;
extern PyTypeObject FixedFileType;

/* -- shared between reactor.c and ops.c ----------------------------------- */

/* Allocate an op slot. Returns URING_NO_SLOT when the table is full. */
uint32_t uring_op_alloc(ReactorObject *self, uint8_t tag);

/* Release an op slot and bump its generation. Frees owned payloads. */
void uring_op_release(ReactorObject *self, uint32_t slot);

/* Obtain an SQE, flushing once if the ring is momentarily full. */
struct io_uring_sqe *uring_get_sqe(ReactorObject *self);

/* Reserve / release a fixed-file slot. */
uint32_t uring_file_alloc(ReactorObject *self);
void uring_file_release(ReactorObject *self, uint32_t slot);

/* Validate that a reactor is usable for submission. Returns 0 or -1. */
int uring_check_ready(ReactorObject *self);

/* Raise OSError from a negative CQE result or errno value. */
PyObject *uring_set_error(int err);

/* Allocate a Future from the attached loop. */
PyObject *uring_create_future(PyObject *loop);

int init_reactor_types(PyObject *module);

#endif /* _URING_REACTOR_H */
