// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "userns.h"
#include "truenas_os_state.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <linux/sched.h>     /* struct clone_args, CLONE_* (kernel UAPI) */
#include <sys/ioctl.h>
#include <sys/syscall.h>     /* __NR_clone3 */
#include <sys/wait.h>

/* clone3 / pidfd_send_signal syscall numbers — defensive fallbacks if
 * <sys/syscall.h> is older. clone3 is 435 on every arch; pidfd_send_signal
 * is 424 on every arch that uses the generic syscall table. */
#ifndef __NR_clone3
#define __NR_clone3 435
#endif
#ifndef __NR_pidfd_send_signal
#define __NR_pidfd_send_signal 424
#endif

/* PIDFD_GET_USER_NAMESPACE — defined inline (vs <linux/pidfd.h>) to avoid the
 * kernel-vs-glibc <fcntl.h> redefinition conflict that <linux/pidfd.h> drags
 * in via <linux/fcntl.h>. */
#ifndef PIDFS_IOCTL_MAGIC
#define PIDFS_IOCTL_MAGIC 0xFF
#endif
#ifndef PIDFD_GET_USER_NAMESPACE
#define PIDFD_GET_USER_NAMESPACE _IO(PIDFS_IOCTL_MAGIC, 9)
#endif

/* Buffer for the "/proc/<pid>/<file>" paths the parent writes to.
 * "/proc/" (6) + decimal pid (max 7 digits for PID_MAX_LIMIT=4194304)
 * + "/" (1) + longest filename "setgroups" (9) + NUL (1) = 24; 32 has slack. */
#define PROC_PID_FILE_BUFLEN 32

/* String written to /proc/<pid>/setgroups before populating gid_map. The
 * paired length is sizeof(SETGROUPS_DENY) - 1 so the two stay in sync. */
#define SETGROUPS_DENY "deny"


/* ── IdmapMappingEntry PyStructSequence ─────────────────────────────────── */

static PyStructSequence_Field idmap_mapping_entry_fields[] = {
	{"inside",  "Starting ID inside the user namespace"},
	{"outside", "Starting ID in the parent user namespace"},
	{"length",  "Length of the contiguous mapping range"},
	{NULL}
};

static PyStructSequence_Desc idmap_mapping_entry_desc = {
	.name = "truenas_os.IdmapMappingEntry",
	.doc = "A single uid_map / gid_map entry: (inside, outside, length). "
	       "Field ordering matches a /proc/<pid>/uid_map line and "
	       "util-linux's X-mount.idmap=<u|g>:<inside>:<outside>:<length>.",
	.fields = idmap_mapping_entry_fields,
	.n_in_sequence = 3
};

int init_userns_type(PyObject *module)
{
	truenas_os_state_t *state;

	state = get_truenas_os_state(module);
	if (state == NULL) {
		return -1;
	}

	state->IdmapMappingEntryType =
		(PyObject *)PyStructSequence_NewType(&idmap_mapping_entry_desc);
	if (state->IdmapMappingEntryType == NULL) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "IdmapMappingEntry",
	                           state->IdmapMappingEntryType) < 0) {
		return -1;
	}

	return 0;
}


/* ── do_create_idmap_mapping: validating constructor ────────────────────── */

PyObject *do_create_idmap_mapping(unsigned long inside,
                                   unsigned long outside,
                                   unsigned long length)
{
	truenas_os_state_t *state;
	PyObject *entry;

	if (inside > UINT32_MAX) {
		PyErr_SetString(PyExc_ValueError,
		                "inside must be in [0, UINT32_MAX]");
		return NULL;
	}
	if (outside > UINT32_MAX) {
		PyErr_SetString(PyExc_ValueError,
		                "outside must be in [0, UINT32_MAX]");
		return NULL;
	}
	if (length == 0 || length > UINT32_MAX) {
		PyErr_SetString(PyExc_ValueError,
		                "length must be in [1, UINT32_MAX]");
		return NULL;
	}
	if ((uint64_t)inside + (uint64_t)length > (uint64_t)UINT32_MAX + 1) {
		PyErr_SetString(PyExc_ValueError,
		                "inside + length overflows UINT32_MAX");
		return NULL;
	}
	if ((uint64_t)outside + (uint64_t)length > (uint64_t)UINT32_MAX + 1) {
		PyErr_SetString(PyExc_ValueError,
		                "outside + length overflows UINT32_MAX");
		return NULL;
	}

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->IdmapMappingEntryType == NULL) {
		PyErr_SetString(PyExc_SystemError,
		                "IdmapMappingEntry type not initialized");
		return NULL;
	}

	entry = PyStructSequence_New((PyTypeObject *)state->IdmapMappingEntryType);
	if (entry == NULL) {
		return NULL;
	}

#define SET_FIELD(idx, value) do {                       \
	PyObject *v = (value);                           \
	if (v == NULL) {                                 \
		Py_DECREF(entry);                        \
		return NULL;                             \
	}                                                \
	PyStructSequence_SET_ITEM(entry, idx, v);        \
} while (0)

	SET_FIELD(0, PyLong_FromUnsignedLong(inside));
	SET_FIELD(1, PyLong_FromUnsignedLong(outside));
	SET_FIELD(2, PyLong_FromUnsignedLong(length));

#undef SET_FIELD

	return entry;
}


/* ── Map list parsing → C array, formatted text ─────────────────────────── */

struct map_entry {
	uint32_t inside;
	uint32_t outside;
	uint32_t length;
};

/*
 * Validate that `seq` is a sequence of IdmapMappingEntry instances and
 * extract them into a caller-PyMem_RawFree'd `*out` C array. Sets a Python
 * exception and returns -1 on error.
 */
static int parse_map_list(PyObject *seq, struct map_entry **out,
                          Py_ssize_t *out_n, const char *which)
{
	PyObject *fast;
	truenas_os_state_t *state;
	PyTypeObject *entry_type;
	Py_ssize_t n;
	Py_ssize_t i;
	struct map_entry *arr;

	*out = NULL;
	*out_n = 0;

	fast = PySequence_Fast(seq, "expected a sequence");
	if (fast == NULL) {
		return -1;
	}

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->IdmapMappingEntryType == NULL) {
		PyErr_SetString(PyExc_SystemError,
		                "IdmapMappingEntry type not initialized");
		Py_DECREF(fast);
		return -1;
	}
	entry_type = (PyTypeObject *)state->IdmapMappingEntryType;

	n = PySequence_Fast_GET_SIZE(fast);
	if (n == 0) {
		Py_DECREF(fast);
		return 0;
	}

	arr = PyMem_RawCalloc((size_t)n, sizeof(*arr));
	if (arr == NULL) {
		Py_DECREF(fast);
		PyErr_NoMemory();
		return -1;
	}

	for (i = 0; i < n; i++) {
		PyObject *item;
		PyObject *p_inside;
		PyObject *p_outside;
		PyObject *p_length;
		unsigned long inside;
		unsigned long outside;
		unsigned long length;

		item = PySequence_Fast_GET_ITEM(fast, i);
		if (!PyObject_TypeCheck(item, entry_type)) {
			PyErr_Format(PyExc_TypeError,
			             "%s[%zd] must be an IdmapMappingEntry "
			             "(use truenas_os.create_idmap_mapping)",
			             which, i);
			PyMem_RawFree(arr);
			Py_DECREF(fast);
			return -1;
		}

		p_inside  = PyStructSequence_GET_ITEM(item, 0);
		p_outside = PyStructSequence_GET_ITEM(item, 1);
		p_length  = PyStructSequence_GET_ITEM(item, 2);

		inside  = PyLong_AsUnsignedLong(p_inside);
		outside = PyLong_AsUnsignedLong(p_outside);
		length  = PyLong_AsUnsignedLong(p_length);
		if (PyErr_Occurred()) {
			PyMem_RawFree(arr);
			Py_DECREF(fast);
			return -1;
		}

		arr[i].inside  = (uint32_t)inside;
		arr[i].outside = (uint32_t)outside;
		arr[i].length  = (uint32_t)length;
	}

	Py_DECREF(fast);
	*out = arr;
	*out_n = n;
	return 0;
}

/*
 * Format a C array of map entries as /proc/<pid>/uid_map text:
 *   "<inside> <outside> <length>\n" per entry.
 * Returns a PyMem_RawMalloc'd buffer (caller PyMem_RawFrees) plus its
 * length via *out_len. Sets a Python exception and returns NULL on alloc
 * failure.
 */
static char *format_map_text(const struct map_entry *e, Py_ssize_t n,
                              size_t *out_len)
{
	/* Each entry: up to 10 digits per uint32 × 3 + 2 separators + '\n' = 35. */
	const size_t max_per_entry = 40;
	size_t cap;
	size_t off;
	Py_ssize_t i;
	char *buf;

	cap = (size_t)n * max_per_entry + 1;
	buf = PyMem_RawMalloc(cap);
	if (buf == NULL) {
		PyErr_NoMemory();
		return NULL;
	}

	off = 0;
	for (i = 0; i < n; i++) {
		int k;

		k = snprintf(buf + off, cap - off,
		             "%u %u %u\n",
		             e[i].inside, e[i].outside, e[i].length);
		if (k < 0 || (size_t)k >= cap - off) {
			PyMem_RawFree(buf);
			PyErr_SetString(PyExc_RuntimeError,
			                "format_map_text: snprintf overflow");
			return NULL;
		}
		off += (size_t)k;
	}
	buf[off] = '\0';
	*out_len = off;
	return buf;
}


/* ── Userns construction ────────────────────────────────────────────────── */

/*
 * Open `p`, write `n` bytes from `d`, close. Returns 0 on success, -1 with
 * errno set on error.
 */
static int write_path(const char *p, const char *d, size_t n)
{
	int fd;
	int e;
	ssize_t k;

	fd = open(p, O_WRONLY | O_CLOEXEC);
	if (fd < 0) {
		return -1;
	}
	while (n > 0) {
		k = write(fd, d, n);
		if (k < 0) {
			if (errno == EINTR) {
				continue;
			}
			e = errno;
			close(fd);
			errno = e;
			return -1;
		}
		d += k;
		n -= (size_t)k;
	}
	return close(fd);
}

/*
 * Returns owning userns fd on success, -1 with errno set on failure.
 * Safe to call from within Py_BEGIN_ALLOW_THREADS — no Python state touched.
 *
 * Plain fork-style clone3 (no CLONE_VM) — the child gets its own MM via
 * CoW page tables, so libc is safe in both halves. The parent must do
 * the privileged /proc map writes itself (kernel/user_namespace.c
 * requires CAP_SETUID in the parent userns, which the new-userns child
 * does not have).
 *
 * CLONE_CLEAR_SIGHAND resets the child's signal handlers to SIG_DFL so
 * inherited Python handlers cannot run in the child during the brief
 * window before the parent SIGKILLs it.
 *
 * Per-call cost is dominated by the page-table duplication for the
 * parent's VSZ (~few-tens of ms for a multi-GiB Python interpreter).
 * The Python wrapper in truenas_os_pyutils.namespace caches the
 * resulting fd keyed on (uid_map, gid_map) so callers paying that cost
 * do so at most once per distinct map for the life of the process.
 */
static int clone_and_collect_userns_fd(const char *uid_text, size_t ul,
                                        const char *gid_text, size_t gl,
                                        const char **stage_out)
{
	int pidfd;
	int userns_fd;
	int err;
	pid_t pid;
	char path[PROC_PID_FILE_BUFLEN];
	struct clone_args ca;

	pidfd = -1;
	userns_fd = -1;
	err = 0;
	pid = -1;
	*stage_out = NULL;

	ca = (struct clone_args){
		.flags = CLONE_NEWUSER | CLONE_PIDFD | CLONE_CLEAR_SIGHAND,
		.pidfd = (uintptr_t)&pidfd,
		.exit_signal = SIGCHLD,
	};
	pid = (pid_t)syscall(__NR_clone3, &ca, sizeof ca);
	if (pid < 0) {
		*stage_out = "clone3(CLONE_NEWUSER|CLONE_PIDFD|CLONE_CLEAR_SIGHAND)";
		err = errno;
		goto out;
	}
	if (pid == 0) {
		/* CHILD — own MM via CoW, libc safe. Block until the parent
		 * SIGKILLs us (which is uncatchable and bypasses pause()). */
		for (;;) {
			pause();
		}
		_exit(0);  /* unreachable */
	}

	/* PARENT — runs concurrently with child; child is blocked in pause(). */
	snprintf(path, sizeof path, "/proc/%d/setgroups", pid);
	if (write_path(path, SETGROUPS_DENY, sizeof(SETGROUPS_DENY) - 1) < 0) {
		*stage_out = "write /proc/<pid>/setgroups";
		err = errno;
		goto cleanup_child;
	}
	snprintf(path, sizeof path, "/proc/%d/uid_map", pid);
	if (write_path(path, uid_text, ul) < 0) {
		*stage_out = "write /proc/<pid>/uid_map";
		err = errno;
		goto cleanup_child;
	}
	snprintf(path, sizeof path, "/proc/%d/gid_map", pid);
	if (write_path(path, gid_text, gl) < 0) {
		*stage_out = "write /proc/<pid>/gid_map";
		err = errno;
		goto cleanup_child;
	}

	/*
	 * Third arg must be 0 — the kernel's pidfd_ioctl (fs/pidfs.c) rejects
	 * with -EINVAL if arg is non-zero for PIDFD_GET_*_NAMESPACE commands.
	 * Without an explicit 0, glibc's variadic ioctl() passes whatever's in
	 * the third-arg register, which is non-deterministic across call sites.
	 */
	userns_fd = ioctl(pidfd, PIDFD_GET_USER_NAMESPACE, 0);
	if (userns_fd < 0) {
		*stage_out = "ioctl(pidfd, PIDFD_GET_USER_NAMESPACE)";
		err = errno;
		/* fall through to cleanup */
	}

cleanup_child:
	/* pidfd_send_signal is PID-reuse-safe vs raw kill(2) — the pidfd is
	 * bound to the original task identity, so a wrap-around PID can't be
	 * targeted by accident. SIGKILL cannot be masked, caught or blocked,
	 * so it bypasses the child's pause-loop unconditionally. */
	syscall(__NR_pidfd_send_signal, pidfd, SIGKILL, (void *)NULL, 0u);
	while (waitpid(pid, NULL, 0) < 0 && errno == EINTR) {
		/* retry */
	}

out:
	if (pidfd >= 0) {
		close(pidfd);
	}
	if (err) {
		if (userns_fd >= 0) {
			close(userns_fd);
		}
		errno = err;
		return -1;
	}
	return userns_fd;
}


/* ── do_create_idmap_userns: full Python-facing entry point ─────────────── */

PyObject *do_create_idmap_userns(PyObject *uid_seq, PyObject *gid_seq)
{
	struct map_entry *uid_arr;
	struct map_entry *gid_arr;
	Py_ssize_t n_uid;
	Py_ssize_t n_gid;
	char *uid_text;
	char *gid_text;
	size_t ul;
	size_t gl;
	int fd;
	int saved_errno;
	const char *stage;
	PyObject *result;

	uid_arr = NULL;
	gid_arr = NULL;
	n_uid = 0;
	n_gid = 0;
	uid_text = NULL;
	gid_text = NULL;
	ul = 0;
	gl = 0;
	fd = -1;
	saved_errno = 0;
	stage = NULL;
	result = NULL;

	if (parse_map_list(uid_seq, &uid_arr, &n_uid, "uid_map") < 0) {
		goto cleanup;
	}
	if (parse_map_list(gid_seq, &gid_arr, &n_gid, "gid_map") < 0) {
		goto cleanup;
	}
	if (n_uid == 0 || n_gid == 0) {
		PyErr_SetString(PyExc_ValueError,
		                "create_idmap_userns: uid_map and gid_map "
		                "must be non-empty");
		goto cleanup;
	}

	uid_text = format_map_text(uid_arr, n_uid, &ul);
	if (uid_text == NULL) {
		goto cleanup;
	}
	gid_text = format_map_text(gid_arr, n_gid, &gl);
	if (gid_text == NULL) {
		goto cleanup;
	}

	Py_BEGIN_ALLOW_THREADS
	fd = clone_and_collect_userns_fd(uid_text, ul, gid_text, gl, &stage);
	saved_errno = errno;
	Py_END_ALLOW_THREADS

	if (fd < 0) {
		errno = saved_errno;
		PyErr_SetFromErrnoWithFilename(PyExc_OSError,
		                                stage ? stage : "<unknown stage>");
		goto cleanup;
	}

	result = PyLong_FromLong(fd);
	if (result == NULL) {
		close(fd);
	}

cleanup:
	PyMem_RawFree(uid_arr);
	PyMem_RawFree(gid_arr);
	PyMem_RawFree(uid_text);
	PyMem_RawFree(gid_text);
	return result;
}
