// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Kernel capability discovery.
 *
 * Two mechanisms, in preference order:
 *
 *   IORING_REGISTER_QUERY (6.18) -- one syscall, needs no ring at all
 *     (fd == -1 routes to io_uring_register_blind() -> io_query(NULL, ...)),
 *     and returns the supported IORING_FEAT_*, IORING_SETUP_*,
 *     IORING_ENTER_* and IOSQE_* masks as bitmaps.
 *
 *   IORING_REGISTER_PROBE -- the pre-6.18 fallback.  Reports which opcodes
 *     exist and nothing else.
 *
 * Neither is sufficient on its own, and the difference matters: a probe
 * reports that an *opcode* exists, not that the opcode accepts a given SQE
 * *flag combination*.  IORING_OP_FGETXATTR has existed since 5.19 but
 * rejected IOSQE_FIXED_FILE with -EBADF until 6.13 (kernel commit
 * dc7e76ba7a60), and no probe can see that.  Capabilities of that shape need
 * a behavioural probe that issues the real combination.
 */

#include <Python.h>
#include "common/includes.h"
#include "probe.h"
#include "truenas_os_state.h"

/* -- docs ----------------------------------------------------------------- */

const char py_uring_query__doc__[] =
"query()\n"
"--\n\n"
"Query kernel io_uring capabilities via IORING_REGISTER_QUERY.\n\n"
"Requires no ring: the underlying register call is made with fd -1, which\n"
"the kernel routes to its ringless handler.\n\n"
"Returns\n"
"-------\n"
"QueryResult or None\n"
"    Supported opcode counts and the IORING_FEAT_*, IORING_SETUP_*,\n"
"    IORING_ENTER_* and IOSQE_* bitmasks. None on kernels older than 6.18,\n"
"    which do not implement the query interface; use supported_ops() there.\n\n"
"Raises\n"
"------\n"
"OSError\n"
"    If the register call failed for any reason other than the interface\n"
"    being absent.\n";

const char py_uring_supported_ops__doc__[] =
"supported_ops()\n"
"--\n\n"
"Return the set of supported IORING_OP_* opcodes via IORING_REGISTER_PROBE.\n\n"
"Note that a probe reports only that an opcode *exists*. It cannot report\n"
"which SQE flag combinations that opcode accepts -- IORING_OP_FGETXATTR has\n"
"existed since 5.19 but rejected IOSQE_FIXED_FILE until 6.13. Capabilities of\n"
"that shape require a behavioural probe instead.\n\n"
"Returns\n"
"-------\n"
"frozenset of int\n"
"    Supported opcode numbers.\n\n"
"Raises\n"
"------\n"
"OSError\n"
"    If io_uring is unavailable or blocked in this environment.\n";

/* -- QueryResult ---------------------------------------------------------- */


static PyStructSequence_Field query_result_fields[] = {
	{"nr_request_opcodes", "Number of supported IORING_OP_* opcodes"},
	{"nr_register_opcodes", "Number of supported IORING_[UN]REGISTER_* opcodes"},
	{"nr_query_opcodes", "Number of available query opcodes"},
	{"feature_flags", "Bitmask of supported IORING_FEAT_* flags"},
	{"ring_setup_flags", "Bitmask of supported IORING_SETUP_* flags"},
	{"enter_flags", "Bitmask of supported IORING_ENTER_* flags"},
	{"sqe_flags", "Bitmask of supported IOSQE_* flags"},
	{NULL}
};

static PyStructSequence_Desc query_result_desc = {
	.name = "truenas_os.uring.QueryResult",
	.doc = "Kernel io_uring capabilities, from IORING_REGISTER_QUERY.\n\n"
	       "Available on Linux 6.18 and later; query() returns None on older "
	       "kernels, where supported_ops() is the only capability source.",
	.fields = query_result_fields,
	.n_in_sequence = 7
};

enum query_result_idx {
	QUERY_NR_REQUEST_OPCODES = 0,
	QUERY_NR_REGISTER_OPCODES,
	QUERY_NR_QUERY_OPCODES,
	QUERY_FEATURE_FLAGS,
	QUERY_RING_SETUP_FLAGS,
	QUERY_ENTER_FLAGS,
	QUERY_SQE_FLAGS,
};

/*
 * Run IORING_REGISTER_QUERY for IO_URING_QUERY_OPCODES.
 *
 * Returns 0 and fills *out on success, or a negative errno.  -EINVAL is the
 * expected answer on kernels below 6.18 (the opcode is >= IORING_REGISTER_LAST,
 * or io_uring_register_blind() does not know it) and is not an error here.
 *
 * Callable with the GIL released; touches no Python state.
 */
static int
uring_query_opcodes(struct io_uring_query_opcode *out)
{
	struct io_uring_query_hdr hdr;
	int ret = 0;

	memset(&hdr, 0, sizeof(hdr));
	memset(out, 0, sizeof(*out));

	/*
	 * The kernel rejects the entry unless __resv is all zero and result is
	 * zero, so hdr must be zero-initialised rather than merely assigned.
	 */
	hdr.query_op = IO_URING_QUERY_OPCODES;
	hdr.query_data = (__u64)(uintptr_t)out;
	hdr.size = sizeof(*out);

	ret = io_uring_register_query(&hdr);
	if (ret < 0) {
		return ret;
	}

	/* Per-entry status is carried in hdr.result, not the syscall return. */
	if (hdr.result < 0) {
		return hdr.result;
	}

	return 0;
}

PyObject *
py_uring_query(PyObject *module, PyObject *Py_UNUSED(ignored))
{
	truenas_os_state_t *state = NULL;
	struct io_uring_query_opcode q;
	PyObject *result = NULL;
	PyObject *tmp = NULL;
	int ret = 0;

	state = get_truenas_os_state(NULL);
	if (state == NULL) {
		return NULL;
	}

	Py_BEGIN_ALLOW_THREADS
	ret = uring_query_opcodes(&q);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		/*
		 * Absence of the query interface is a fact about the kernel,
		 * not a failure: report it as None and let the caller fall
		 * back to supported_ops().  Anything else is a real error.
		 */
		if (ret == -EINVAL || ret == -EOPNOTSUPP || ret == -ENOSYS) {
			Py_RETURN_NONE;
		}
		errno = -ret;
		return PyErr_SetFromErrno(PyExc_OSError);
	}

	result = PyStructSequence_New((PyTypeObject *)state->QueryResultType);
	if (result == NULL) {
		return NULL;
	}

	#define SET_FIELD(idx, value) do {				\
		tmp = (value);						\
		if (tmp == NULL) {					\
			Py_DECREF(result);				\
			return NULL;					\
		}							\
		PyStructSequence_SET_ITEM(result, idx, tmp);		\
	} while (0)

	SET_FIELD(QUERY_NR_REQUEST_OPCODES, PyLong_FromUnsignedLong(q.nr_request_opcodes));
	SET_FIELD(QUERY_NR_REGISTER_OPCODES, PyLong_FromUnsignedLong(q.nr_register_opcodes));
	SET_FIELD(QUERY_NR_QUERY_OPCODES, PyLong_FromUnsignedLong(q.nr_query_opcodes));
	SET_FIELD(QUERY_FEATURE_FLAGS, PyLong_FromUnsignedLongLong(q.feature_flags));
	SET_FIELD(QUERY_RING_SETUP_FLAGS, PyLong_FromUnsignedLongLong(q.ring_setup_flags));
	SET_FIELD(QUERY_ENTER_FLAGS, PyLong_FromUnsignedLongLong(q.enter_flags));
	SET_FIELD(QUERY_SQE_FLAGS, PyLong_FromUnsignedLongLong(q.sqe_flags));

	#undef SET_FIELD

	return result;
}

/* -- opcode probe --------------------------------------------------------- */

PyObject *
py_uring_supported_ops(PyObject *Py_UNUSED(module), PyObject *Py_UNUSED(ignored))
{
	struct io_uring_probe *probe = NULL;
	PyObject *ops = NULL;
	PyObject *frozen = NULL;
	PyObject *num = NULL;
	unsigned int op = 0;

	/*
	 * io_uring_get_probe() sets up and tears down a throwaway ring, so it
	 * can fail for environmental reasons -- kernel.io_uring_disabled, or a
	 * seccomp policy that blocks io_uring_setup, both common in
	 * containers.  liburing gives us no errno here, so report the
	 * condition rather than inventing one.
	 */
	Py_BEGIN_ALLOW_THREADS
	probe = io_uring_get_probe();
	Py_END_ALLOW_THREADS

	if (probe == NULL) {
		PyErr_SetString(PyExc_OSError,
				"IORING_REGISTER_PROBE failed: io_uring is "
				"unavailable or blocked in this environment");
		return NULL;
	}

	ops = PySet_New(NULL);
	if (ops == NULL) {
		io_uring_free_probe(probe);
		return NULL;
	}

	for (op = 0; op < IORING_OP_LAST; op++) {
		if (!io_uring_opcode_supported(probe, op)) {
			continue;
		}

		num = PyLong_FromUnsignedLong(op);
		if (num == NULL) {
			goto fail;
		}
		if (PySet_Add(ops, num) < 0) {
			Py_DECREF(num);
			goto fail;
		}
		Py_DECREF(num);
	}

	io_uring_free_probe(probe);
	probe = NULL;

	frozen = PyFrozenSet_New(ops);
	Py_DECREF(ops);
	return frozen;

fail:
	io_uring_free_probe(probe);
	Py_DECREF(ops);
	return NULL;
}

/* -- constants ------------------------------------------------------------ */

typedef struct {
	const char *name;
	unsigned long long value;
} uring_const_t;

static const uring_const_t uring_constants[] = {
	/* Opcodes used by this module (fs-reactor design table). */
	{"IORING_OP_NOP", IORING_OP_NOP},
	{"IORING_OP_READ", IORING_OP_READ},
	{"IORING_OP_WRITE", IORING_OP_WRITE},
	{"IORING_OP_READV", IORING_OP_READV},
	{"IORING_OP_WRITEV", IORING_OP_WRITEV},
	{"IORING_OP_FSYNC", IORING_OP_FSYNC},
	{"IORING_OP_FALLOCATE", IORING_OP_FALLOCATE},
	{"IORING_OP_CLOSE", IORING_OP_CLOSE},
	{"IORING_OP_STATX", IORING_OP_STATX},
	{"IORING_OP_OPENAT2", IORING_OP_OPENAT2},
	{"IORING_OP_ASYNC_CANCEL", IORING_OP_ASYNC_CANCEL},
	{"IORING_OP_RENAMEAT", IORING_OP_RENAMEAT},
	{"IORING_OP_UNLINKAT", IORING_OP_UNLINKAT},
	{"IORING_OP_MKDIRAT", IORING_OP_MKDIRAT},
	{"IORING_OP_SYMLINKAT", IORING_OP_SYMLINKAT},
	{"IORING_OP_LINKAT", IORING_OP_LINKAT},
	{"IORING_OP_FSETXATTR", IORING_OP_FSETXATTR},
	{"IORING_OP_FGETXATTR", IORING_OP_FGETXATTR},
	{"IORING_OP_FTRUNCATE", IORING_OP_FTRUNCATE},
	{"IORING_OP_FIXED_FD_INSTALL", IORING_OP_FIXED_FD_INSTALL},
	{"IORING_OP_LAST", IORING_OP_LAST},

	/* Setup flags. SINGLE_ISSUER / DEFER_TASKRUN are exposed so tests can
	 * pin the -EEXIST register gate; the reactor itself must never set
	 * them (fs-reactor design section 6.3). */
	{"IORING_SETUP_IOPOLL", IORING_SETUP_IOPOLL},
	{"IORING_SETUP_SQPOLL", IORING_SETUP_SQPOLL},
	{"IORING_SETUP_CQSIZE", IORING_SETUP_CQSIZE},
	{"IORING_SETUP_CLAMP", IORING_SETUP_CLAMP},
	{"IORING_SETUP_COOP_TASKRUN", IORING_SETUP_COOP_TASKRUN},
	{"IORING_SETUP_SINGLE_ISSUER", IORING_SETUP_SINGLE_ISSUER},
	{"IORING_SETUP_DEFER_TASKRUN", IORING_SETUP_DEFER_TASKRUN},

	/* Feature flags. */
	{"IORING_FEAT_CUR_PERSONALITY", IORING_FEAT_CUR_PERSONALITY},
	{"IORING_FEAT_FAST_POLL", IORING_FEAT_FAST_POLL},
	{"IORING_FEAT_RSRC_TAGS", IORING_FEAT_RSRC_TAGS},
	{"IORING_FEAT_REG_REG_RING", IORING_FEAT_REG_REG_RING},

	/* SQE flags. There is no IOSQE_PERSONALITY: the personality is a
	 * u16 field at SQE offset 42, not a flag. */
	{"IOSQE_FIXED_FILE", 1U << IOSQE_FIXED_FILE_BIT},
	{"IOSQE_IO_LINK", 1U << IOSQE_IO_LINK_BIT},
	{"IOSQE_IO_HARDLINK", 1U << IOSQE_IO_HARDLINK_BIT},
	{"IOSQE_ASYNC", 1U << IOSQE_ASYNC_BIT},
	{"IOSQE_CQE_SKIP_SUCCESS", 1U << IOSQE_CQE_SKIP_SUCCESS_BIT},

	/* Register opcodes this module issues directly. */
	{"IORING_REGISTER_PERSONALITY", IORING_REGISTER_PERSONALITY},
	{"IORING_UNREGISTER_PERSONALITY", IORING_UNREGISTER_PERSONALITY},

	/* The personality id space: XA_FLAGS_ALLOC1 means 0 is never
	 * allocated, so 0 unambiguously means "ring-owner ambient creds". */
	{"URING_PERSONALITY_MAX", 65535},

	{NULL, 0}
};

int
init_probe_types(PyObject *module, truenas_os_state_t *state)
{
	PyObject *type = NULL;
	const uring_const_t *entry = NULL;

	type = (PyObject *)PyStructSequence_NewType(&query_result_desc);
	if (type == NULL) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "QueryResult", type) < 0) {
		Py_DECREF(type);
		return -1;
	}

	/* Module state owns the reference the module namespace does not. */
	state->QueryResultType = type;

	for (entry = uring_constants; entry->name != NULL; entry++) {
		if (PyModule_AddIntConstant(module, entry->name,
					    (long long)entry->value) < 0) {
			return -1;
		}
	}

	return 0;
}
