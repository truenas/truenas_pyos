// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "acl_check.h"
#include "truenas_os_state.h"

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <signal.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <linux/sched.h>     /* struct clone_args, CLONE_* */
#include <sys/syscall.h>     /* __NR_clone3, __NR_faccessat2 */
#include <sys/wait.h>

#ifndef __NR_clone3
#define __NR_clone3 435
#endif
#ifndef __NR_faccessat2
#define __NR_faccessat2 439
#endif
#ifndef __NR_pidfd_send_signal
#define __NR_pidfd_send_signal 424
#endif

/*
 * Fatal-error marker written to the result pipe when the child cannot complete
 * a credential swap (setresuid/setresgid/setgroups failure).  Distinguished
 * from real per-component failures by cred_idx == UINT32_MAX; comp_idx then
 * encodes the failing stage (0..3) and err is the captured errno.
 */
#define FATAL_CRED_IDX UINT32_MAX
#define FATAL_STAGE_SETRESUID_0 0
#define FATAL_STAGE_SETRESGID 1
#define FATAL_STAGE_SETGROUPS 2
#define FATAL_STAGE_SETRESUID_N 3

struct failure_rec {
	uint32_t cred_idx;
	uint32_t comp_idx;
	int err;
};

struct cred_arr {
	PyObject *id_name;
	uint32_t uid;
	uint32_t gid;
	gid_t *groups;
	size_t ngroups;
};


/* ── CredEntry / AccessFailure PyStructSequence types ───────────────────── */

static PyStructSequence_Field cred_entry_fields[] = {
	{"id_name", "Human-readable identifier (user or synthetic group name)"},
	{"uid", "Effective UID to assume for the check"},
	{"gid", "Primary GID to assume for the check"},
	{"groups", "Sequence of supplementary group IDs"},
	{NULL}
};

static PyStructSequence_Desc cred_entry_desc = {
	.name = "truenas_os.CredEntry",
	.doc = "A credential identity to test path access against. Construct via "
	       "truenas_os.create_cred_entry().",
	.fields = cred_entry_fields,
	.n_in_sequence = 4,
};

static PyStructSequence_Field access_failure_fields[] = {
	{"id_name", "id_name of the credential that was denied"},
	{"failing_component", "Path-component bytes at which the check failed"},
	{"errnum", "errno value from the failing faccessat2(2)"},
	{NULL}
};

static PyStructSequence_Desc access_failure_desc = {
	.name = "truenas_os.AccessFailure",
	.doc = "A single (credential, path-component) pair that failed an "
	       "execute-access probe.",
	.fields = access_failure_fields,
	.n_in_sequence = 3,
};

int init_acl_check_types(PyObject *module)
{
	truenas_os_state_t *state;

	state = get_truenas_os_state(module);
	if (state == NULL) {
		return -1;
	}

	state->CredEntryType =
	    (PyObject *)PyStructSequence_NewType(&cred_entry_desc);
	if (state->CredEntryType == NULL) {
		return -1;
	}
	if (PyModule_AddObjectRef(module, "CredEntry",
	                          state->CredEntryType) < 0) {
		return -1;
	}

	state->AccessFailureType =
	    (PyObject *)PyStructSequence_NewType(&access_failure_desc);
	if (state->AccessFailureType == NULL) {
		return -1;
	}
	if (PyModule_AddObjectRef(module, "AccessFailure",
	                          state->AccessFailureType) < 0) {
		return -1;
	}

	return 0;
}


/* ── do_create_cred_entry: validating constructor ──────────────────────── */

PyObject *do_create_cred_entry(PyObject *id_name, unsigned long uid,
                                 unsigned long gid, PyObject *groups)
{
	truenas_os_state_t *state;
	PyObject *fast = NULL;
	PyObject *groups_tuple = NULL;
	PyObject *entry = NULL;
	PyObject *uid_obj = NULL;
	PyObject *gid_obj = NULL;
	Py_ssize_t n;
	Py_ssize_t i;

	if (!PyUnicode_Check(id_name)) {
		PyErr_SetString(PyExc_TypeError, "id_name must be a str");
		return NULL;
	}
	if (uid > UINT32_MAX) {
		PyErr_SetString(PyExc_ValueError,
		                "uid must be in [0, UINT32_MAX]");
		return NULL;
	}
	if (gid > UINT32_MAX) {
		PyErr_SetString(PyExc_ValueError,
		                "gid must be in [0, UINT32_MAX]");
		return NULL;
	}

	fast = PySequence_Fast(groups, "groups must be a sequence of int");
	if (fast == NULL) {
		return NULL;
	}
	n = PySequence_Fast_GET_SIZE(fast);
	groups_tuple = PyTuple_New(n);
	if (groups_tuple == NULL) {
		Py_DECREF(fast);
		return NULL;
	}
	for (i = 0; i < n; i++) {
		PyObject *g = PySequence_Fast_GET_ITEM(fast, i);
		unsigned long gv;
		PyObject *gi;

		gv = PyLong_AsUnsignedLong(g);
		if (gv == (unsigned long)-1 && PyErr_Occurred()) {
			Py_DECREF(fast);
			Py_DECREF(groups_tuple);
			return NULL;
		}
		if (gv > UINT32_MAX) {
			PyErr_Format(PyExc_ValueError,
			    "groups[%zd] must be in [0, UINT32_MAX]", i);
			Py_DECREF(fast);
			Py_DECREF(groups_tuple);
			return NULL;
		}
		gi = PyLong_FromUnsignedLong(gv);
		if (gi == NULL) {
			Py_DECREF(fast);
			Py_DECREF(groups_tuple);
			return NULL;
		}
		PyTuple_SET_ITEM(groups_tuple, i, gi);
	}
	Py_DECREF(fast);

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->CredEntryType == NULL) {
		PyErr_SetString(PyExc_SystemError,
		                "CredEntry type not initialized");
		Py_DECREF(groups_tuple);
		return NULL;
	}

	uid_obj = PyLong_FromUnsignedLong(uid);
	gid_obj = PyLong_FromUnsignedLong(gid);
	if (uid_obj == NULL || gid_obj == NULL) {
		Py_XDECREF(uid_obj);
		Py_XDECREF(gid_obj);
		Py_DECREF(groups_tuple);
		return NULL;
	}

	entry = PyStructSequence_New((PyTypeObject *)state->CredEntryType);
	if (entry == NULL) {
		Py_DECREF(uid_obj);
		Py_DECREF(gid_obj);
		Py_DECREF(groups_tuple);
		return NULL;
	}

	Py_INCREF(id_name);
	PyStructSequence_SET_ITEM(entry, 0, id_name);
	PyStructSequence_SET_ITEM(entry, 1, uid_obj);
	PyStructSequence_SET_ITEM(entry, 2, gid_obj);
	PyStructSequence_SET_ITEM(entry, 3, groups_tuple);

	return entry;
}


/* ── Parse Python inputs into C arrays ───────────────────────────────────── */

/*
 * Caller must hold the GIL: this drops a Python reference per entry.
 */
static void free_creds(struct cred_arr *creds, size_t n)
{
	size_t i;

	if (creds == NULL) {
		return;
	}
	for (i = 0; i < n; i++) {
		Py_XDECREF(creds[i].id_name);
		PyMem_RawFree(creds[i].groups);
	}
	PyMem_RawFree(creds);
}

/*
 * Convert a CredEntry's `groups` field into a freshly allocated gid_t array.
 * On success returns 0 with *out_gs set to a PyMem_RawMalloc'd buffer of
 * *out_ng elements (caller PyMem_RawFrees), or *out_gs == NULL with *out_ng
 * == 0 when the source sequence is empty.  Returns -1 with a Python
 * exception set on error; `cred_idx` is used only for error-message
 * context.
 */
static int parse_groups(PyObject *p_groups, Py_ssize_t cred_idx,
                          gid_t **out_gs, size_t *out_ng)
{
	PyObject *groups_fast = NULL;
	gid_t *gs = NULL;
	Py_ssize_t ng;
	Py_ssize_t j;

	*out_gs = NULL;
	*out_ng = 0;

	groups_fast = PySequence_Fast(p_groups, "groups must be a sequence");
	if (groups_fast == NULL) {
		return -1;
	}
	ng = PySequence_Fast_GET_SIZE(groups_fast);
	if (ng == 0) {
		Py_DECREF(groups_fast);
		return 0;
	}

	gs = PyMem_RawCalloc((size_t)ng, sizeof(gid_t));
	if (gs == NULL) {
		Py_DECREF(groups_fast);
		PyErr_NoMemory();
		return -1;
	}

	for (j = 0; j < ng; j++) {
		unsigned long g;

		g = PyLong_AsUnsignedLong(
		    PySequence_Fast_GET_ITEM(groups_fast, j));
		if (g == (unsigned long)-1 && PyErr_Occurred()) {
			goto err;
		}
		if (g > UINT32_MAX) {
			PyErr_Format(PyExc_ValueError,
			    "creds[%zd].groups[%zd]: must fit in uint32",
			    cred_idx, j);
			goto err;
		}
		gs[j] = (gid_t)g;
	}

	Py_DECREF(groups_fast);
	*out_gs = gs;
	*out_ng = (size_t)ng;
	return 0;

err:
	PyMem_RawFree(gs);
	Py_DECREF(groups_fast);
	return -1;
}

/*
 * Parse `seq` as a sequence of CredEntry instances into a C array.  The
 * returned array (and its per-entry `groups` blocks) must be freed with
 * free_creds() by the caller.  Sets a Python exception and returns -1 on
 * error.
 */
static int parse_creds(PyObject *seq, struct cred_arr **out, size_t *out_n)
{
	PyObject *fast = NULL;
	truenas_os_state_t *state;
	PyTypeObject *entry_type;
	Py_ssize_t n;
	Py_ssize_t i;
	struct cred_arr *arr = NULL;

	*out = NULL;
	*out_n = 0;

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->CredEntryType == NULL) {
		PyErr_SetString(PyExc_SystemError,
		                "CredEntry type not initialized");
		return -1;
	}
	entry_type = (PyTypeObject *)state->CredEntryType;

	fast = PySequence_Fast(seq, "creds: expected a sequence");
	if (fast == NULL) {
		return -1;
	}

	n = PySequence_Fast_GET_SIZE(fast);
	if (n == 0) {
		PyErr_SetString(PyExc_ValueError,
		                "check_path_access: creds must be non-empty");
		Py_DECREF(fast);
		return -1;
	}

	arr = PyMem_RawCalloc((size_t)n, sizeof(*arr));
	if (arr == NULL) {
		Py_DECREF(fast);
		PyErr_NoMemory();
		return -1;
	}

	for (i = 0; i < n; i++) {
		PyObject *item;
		PyObject *p_uid;
		PyObject *p_gid;
		PyObject *p_groups;
		unsigned long uid;
		unsigned long gid;

		item = PySequence_Fast_GET_ITEM(fast, i);
		if (!PyObject_TypeCheck(item, entry_type)) {
			PyErr_Format(PyExc_TypeError,
			             "creds[%zd] must be a CredEntry "
			             "(use truenas_os.create_cred_entry)",
			             i);
			goto err;
		}

		/* Snapshot a strong reference to id_name so result construction
		 * never re-iterates the caller's `seq` (which may be a generator
		 * or otherwise unsafe to replay). */
		arr[i].id_name = PyStructSequence_GET_ITEM(item, 0);
		Py_INCREF(arr[i].id_name);

		p_uid = PyStructSequence_GET_ITEM(item, 1);
		p_gid = PyStructSequence_GET_ITEM(item, 2);
		p_groups = PyStructSequence_GET_ITEM(item, 3);

		uid = PyLong_AsUnsignedLong(p_uid);
		if (uid == (unsigned long)-1 && PyErr_Occurred()) {
			goto err;
		}
		gid = PyLong_AsUnsignedLong(p_gid);
		if (gid == (unsigned long)-1 && PyErr_Occurred()) {
			goto err;
		}
		if (uid > UINT32_MAX || gid > UINT32_MAX) {
			PyErr_Format(PyExc_ValueError,
			             "creds[%zd]: uid/gid must fit in uint32", i);
			goto err;
		}

		if (parse_groups(p_groups, i,
		                 &arr[i].groups, &arr[i].ngroups) < 0) {
			goto err;
		}

		arr[i].uid = (uint32_t)uid;
		arr[i].gid = (uint32_t)gid;
	}

	Py_DECREF(fast);
	*out = arr;
	*out_n = (size_t)n;
	return 0;

err:
	free_creds(arr, (size_t)i + 1);
	Py_DECREF(fast);
	return -1;
}

/*
 * Parse `seq` as a sequence of bytes (path components).  Returns an array of
 * NUL-terminated C strings; the caller PyMem_RawFrees both the array and each
 * element.  Holds Python references via `*holder` (a tuple) for the lifetime
 * of the array so the underlying bytes objects remain valid; caller must
 * Py_DECREF(*holder) when done.
 */
static int parse_components(PyObject *seq, char ***out, size_t *out_n,
                             PyObject **holder)
{
	PyObject *fast = NULL;
	Py_ssize_t n;
	Py_ssize_t i;
	char **arr = NULL;

	*out = NULL;
	*out_n = 0;
	*holder = NULL;

	fast = PySequence_Fast(seq, "components: expected a sequence");
	if (fast == NULL) {
		return -1;
	}
	n = PySequence_Fast_GET_SIZE(fast);
	if (n == 0) {
		*holder = fast;  /* empty tuple is fine */
		return 0;
	}

	arr = PyMem_RawCalloc((size_t)n, sizeof(char *));
	if (arr == NULL) {
		Py_DECREF(fast);
		PyErr_NoMemory();
		return -1;
	}

	for (i = 0; i < n; i++) {
		PyObject *item;
		char *buf;
		Py_ssize_t len;

		item = PySequence_Fast_GET_ITEM(fast, i);
		if (!PyBytes_Check(item)) {
			PyErr_Format(PyExc_TypeError,
			             "components[%zd] must be bytes", i);
			PyMem_RawFree(arr);
			Py_DECREF(fast);
			return -1;
		}
		if (PyBytes_AsStringAndSize(item, &buf, &len) < 0) {
			PyMem_RawFree(arr);
			Py_DECREF(fast);
			return -1;
		}
		arr[i] = buf;  /* borrows from item; lifetime tied to `fast` */
	}

	*out = arr;
	*out_n = (size_t)n;
	*holder = fast;  /* keep refs alive */
	return 0;
}


/* ── Child: cred-swap loop + faccessat2 probe ───────────────────────────── */

/*
 * write(2) the whole record to `wpipe`, retrying on EINTR.  Returns 0 on
 * success, -1 on any other write error (errno preserved).
 */
static int write_all(int wpipe, const void *buf, size_t len)
{
	const char *p = buf;

	while (len > 0) {
		ssize_t w = write(wpipe, p, len);
		if (w < 0) {
			if (errno == EINTR) {
				continue;
			}
			return -1;
		}
		p += w;
		len -= (size_t)w;
	}
	return 0;
}

static void emit_fatal(int wpipe, uint32_t stage, int err)
{
	struct failure_rec rec = {
		.cred_idx = FATAL_CRED_IDX,
		.comp_idx = stage,
		.err = err,
	};
	/* best-effort; if the pipe is broken we're about to _exit anyway */
	(void)write_all(wpipe, &rec, sizeof rec);
}

/*
 * Loop body of the cloned child.  Never returns; always _exit().
 *
 * Privilege model: the parent must be running with euid 0 (and the equivalent
 * capabilities) so that the saved-uid-0 idiom below lets the child re-elevate
 * back to root between credential swaps.  Effective capabilities are restored
 * on each setresuid(0,0,0) per security/commoncap.c:cap_emulate_setxuid().
 */
static void run_check_child(int wpipe,
                              const struct cred_arr *creds, size_t n_creds,
                              char *const *components, size_t n_components,
                              int path_must_exist)
{
	size_t ci;
	size_t pi;

	for (ci = 0; ci < n_creds; ci++) {
		/* Restore to full root before swapping; first iteration is a
		 * no-op, subsequent ones re-elevate. */
		if (setresuid(0, 0, 0) < 0) {
			emit_fatal(wpipe, FATAL_STAGE_SETRESUID_0, errno);
			_exit(1);
		}
		if (setresgid(creds[ci].gid, creds[ci].gid, 0) < 0) {
			emit_fatal(wpipe, FATAL_STAGE_SETRESGID, errno);
			_exit(1);
		}
		if (setgroups(creds[ci].ngroups, creds[ci].groups) < 0) {
			emit_fatal(wpipe, FATAL_STAGE_SETGROUPS, errno);
			_exit(1);
		}
		if (setresuid(creds[ci].uid, creds[ci].uid, 0) < 0) {
			emit_fatal(wpipe, FATAL_STAGE_SETRESUID_N, errno);
			_exit(1);
		}

		for (pi = 0; pi < n_components; pi++) {
			int r;
			int e;
			struct failure_rec rec;

			r = (int)syscall(__NR_faccessat2, AT_FDCWD,
			                 components[pi], X_OK, AT_EACCESS);
			if (r == 0) {
				continue;
			}
			e = errno;
			if (e == ENOENT && !path_must_exist) {
				continue;
			}

			rec.cred_idx = (uint32_t)ci;
			rec.comp_idx = (uint32_t)pi;
			rec.err = e;
			if (write_all(wpipe, &rec, sizeof rec) < 0) {
				_exit(1);
			}
		}
	}
	_exit(0);
}


/* ── Parent: clone3, drain pipe, reap, build result list ─────────────────── */

/*
 * Read failure records from the read end of the pipe until EOF.  On success
 * returns a freshly allocated array via *out_recs (caller PyMem_RawFrees) and
 * its length via *out_n.  Partial records at EOF are an error.
 */
static int drain_pipe(int rpipe, struct failure_rec **out_recs, size_t *out_n)
{
	size_t cap = 16;
	size_t n = 0;
	struct failure_rec *buf;

	*out_recs = NULL;
	*out_n = 0;

	buf = PyMem_RawCalloc(cap, sizeof(*buf));
	if (buf == NULL) {
		errno = ENOMEM;
		return -1;
	}

	for (;;) {
		ssize_t got;
		size_t need;

		if (n == cap) {
			size_t new_cap = cap * 2;
			struct failure_rec *nb;

			nb = PyMem_RawRealloc(buf, new_cap * sizeof(*buf));
			if (nb == NULL) {
				PyMem_RawFree(buf);
				errno = ENOMEM;
				return -1;
			}
			buf = nb;
			cap = new_cap;
		}

		need = sizeof(*buf);
		got = 0;
		while (need > 0) {
			ssize_t r = read(rpipe, (char *)&buf[n] + got, need);
			if (r < 0) {
				if (errno == EINTR) {
					continue;
				}
				PyMem_RawFree(buf);
				return -1;
			}
			if (r == 0) {
				if (got != 0) {
					/* short partial record at EOF */
					PyMem_RawFree(buf);
					errno = EPROTO;
					return -1;
				}
				*out_recs = buf;
				*out_n = n;
				return 0;
			}
			got += r;
			need -= (size_t)r;
		}
		n++;
	}
}

/*
 * clone3 the worker.  On success returns 0 with *pid_out and *pidfd_out set,
 * and the child has already entered run_check_child() (it never returns).
 * On failure returns -1 with errno set; pipefd is left untouched for the
 * caller to close.
 *
 * Safe to call from within Py_BEGIN_ALLOW_THREADS — no Python state touched.
 */
static int spawn_check_child(int pipefd[2],
                              const struct cred_arr *creds, size_t n_creds,
                              char *const *components, size_t n_components,
                              int path_must_exist,
                              pid_t *pid_out, int *pidfd_out)
{
	int pidfd = -1;
	pid_t pid;
	struct clone_args ca;

	ca = (struct clone_args){
		.flags = CLONE_PIDFD | CLONE_CLEAR_SIGHAND,
		.pidfd = (uintptr_t)&pidfd,
		.exit_signal = SIGCHLD,
	};
	pid = (pid_t)syscall(__NR_clone3, &ca, sizeof ca);
	if (pid < 0) {
		return -1;
	}

	if (pid == 0) {
		/* CHILD — own MM via CoW.  Close the read end; the write end
		 * stays open so EOF reaches the parent when we _exit().  No
		 * libc cleanup hazards: clone3 without CLONE_VM gives us a
		 * fresh address space. */
		close(pipefd[0]);
		run_check_child(pipefd[1], creds, n_creds,
		                components, n_components, path_must_exist);
		_exit(127); /* unreachable */
	}

	*pid_out = pid;
	*pidfd_out = pidfd;
	return 0;
}

/*
 * Best-effort SIGKILL via pidfd_send_signal (PID-reuse-safe vs raw kill(2)),
 * then waitpid with EINTR retry.  Used on error paths in run_check to dispose
 * of the worker before returning.
 */
static void kill_and_reap(int pidfd, pid_t pid)
{
	syscall(__NR_pidfd_send_signal, pidfd, SIGKILL, (void *)NULL, 0u);
	while (waitpid(pid, NULL, 0) < 0 && errno == EINTR) {
		/* retry */
	}
}

/*
 * waitpid(pid, status, 0) with EINTR retry.  Returns 0 on success, -1 with
 * errno set on any non-EINTR failure.
 */
static int reap_child(pid_t pid, int *status_out)
{
	while (waitpid(pid, status_out, 0) < 0) {
		if (errno != EINTR) {
			return -1;
		}
	}
	return 0;
}

/*
 * Set up the result pipe, spawn the worker, drain its output, reap it.
 * Returns 0 on success; on error returns -1 with errno set and *stage_out
 * pointing to a static description string.
 *
 * Safe to call from within Py_BEGIN_ALLOW_THREADS — no Python state touched.
 */
static int run_check(const struct cred_arr *creds, size_t n_creds,
                      char *const *components, size_t n_components,
                      int path_must_exist,
                      struct failure_rec **out_recs, size_t *out_n,
                      int *child_status_out,
                      const char **stage_out)
{
	int pipefd[2] = {-1, -1};
	int pidfd = -1;
	int saved_errno;
	pid_t pid;
	int wstatus = 0;

	*out_recs = NULL;
	*out_n = 0;
	*child_status_out = 0;
	*stage_out = NULL;

	if (pipe2(pipefd, O_CLOEXEC) < 0) {
		*stage_out = "pipe2";
		return -1;
	}

	if (spawn_check_child(pipefd, creds, n_creds, components, n_components,
	                      path_must_exist, &pid, &pidfd) < 0) {
		*stage_out = "clone3(CLONE_PIDFD|CLONE_CLEAR_SIGHAND)";
		saved_errno = errno;
		close(pipefd[0]);
		close(pipefd[1]);
		errno = saved_errno;
		return -1;
	}

	/* PARENT — leave only the child holding the write end so the read end
	 * gets EOF when the child exits. */
	close(pipefd[1]);

	if (drain_pipe(pipefd[0], out_recs, out_n) < 0) {
		*stage_out = "read result pipe";
		saved_errno = errno;
		kill_and_reap(pidfd, pid);
		close(pipefd[0]);
		close(pidfd);
		errno = saved_errno;
		return -1;
	}
	close(pipefd[0]);

	if (reap_child(pid, &wstatus) < 0) {
		*stage_out = "waitpid";
		saved_errno = errno;
		close(pidfd);
		PyMem_RawFree(*out_recs);
		*out_recs = NULL;
		*out_n = 0;
		errno = saved_errno;
		return -1;
	}
	close(pidfd);

	*child_status_out = wstatus;
	return 0;
}


/* ── Result-list construction & child-status decoding ──────────────────── */

/*
 * If the child exited 0, returns 0.  Otherwise scans `recs` for a fatal-
 * sentinel record (cred_idx == FATAL_CRED_IDX), sets an OSError describing
 * the failing setresuid/setresgid/setgroups stage, and returns -1.  When no
 * sentinel is present a generic OSError is raised carrying the wait status.
 */
static int raise_for_child_status(int child_status,
                                    const struct failure_rec *recs,
                                    size_t n_recs)
{
	const char *stage_name = "child";
	int err = 0;
	size_t i;

	if (WIFEXITED(child_status) && WEXITSTATUS(child_status) == 0) {
		return 0;
	}

	for (i = 0; i < n_recs; i++) {
		if (recs[i].cred_idx != FATAL_CRED_IDX) {
			continue;
		}
		err = recs[i].err;
		switch (recs[i].comp_idx) {
		case FATAL_STAGE_SETRESUID_0:
			stage_name = "setresuid(0,0,0)";
			break;
		case FATAL_STAGE_SETRESGID:
			stage_name = "setresgid";
			break;
		case FATAL_STAGE_SETGROUPS:
			stage_name = "setgroups";
			break;
		case FATAL_STAGE_SETRESUID_N:
			stage_name = "setresuid(uid,uid,0)";
			break;
		default:
			stage_name = "child";
			break;
		}
		break;
	}

	if (err != 0) {
		errno = err;
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, stage_name);
	} else {
		PyErr_Format(PyExc_OSError,
		             "check_path_access: child exited "
		             "abnormally (wait status %d)", child_status);
	}
	return -1;
}

/*
 * Convert `n_recs` failure records into a Python list[AccessFailure].
 * Returns a new reference on success, or NULL with a Python exception set on
 * error.  Fatal-sentinel records that slip through to a zero-exit child are
 * skipped defensively rather than mis-indexed.
 */
static PyObject *build_failure_list(const struct failure_rec *recs,
                                      size_t n_recs,
                                      const struct cred_arr *creds,
                                      size_t n_creds,
                                      PyObject *components_holder,
                                      size_t n_components,
                                      PyTypeObject *failure_type)
{
	PyObject *result;
	size_t i;

	result = PyList_New(0);
	if (result == NULL) {
		return NULL;
	}

	for (i = 0; i < n_recs; i++) {
		PyObject *entry;
		PyObject *id_name;
		PyObject *comp_bytes;
		PyObject *err_obj;

		if (recs[i].cred_idx == FATAL_CRED_IDX) {
			continue;
		}
		if (recs[i].cred_idx >= n_creds ||
		    recs[i].comp_idx >= n_components) {
			PyErr_SetString(PyExc_SystemError,
			    "check_path_access: out-of-range index from child");
			Py_DECREF(result);
			return NULL;
		}

		id_name = creds[recs[i].cred_idx].id_name;
		Py_INCREF(id_name);

		comp_bytes = PySequence_Fast_GET_ITEM(components_holder,
		                                      (Py_ssize_t)recs[i].comp_idx);
		Py_INCREF(comp_bytes);

		err_obj = PyLong_FromLong(recs[i].err);
		if (err_obj == NULL) {
			Py_DECREF(id_name);
			Py_DECREF(comp_bytes);
			Py_DECREF(result);
			return NULL;
		}

		entry = PyStructSequence_New(failure_type);
		if (entry == NULL) {
			Py_DECREF(id_name);
			Py_DECREF(comp_bytes);
			Py_DECREF(err_obj);
			Py_DECREF(result);
			return NULL;
		}
		PyStructSequence_SET_ITEM(entry, 0, id_name);
		PyStructSequence_SET_ITEM(entry, 1, comp_bytes);
		PyStructSequence_SET_ITEM(entry, 2, err_obj);

		if (PyList_Append(result, entry) < 0) {
			Py_DECREF(entry);
			Py_DECREF(result);
			return NULL;
		}
		Py_DECREF(entry);
	}

	return result;
}


/* ── Python entry point ──────────────────────────────────────────────────── */

PyObject *do_check_path_access(PyObject *creds_seq,
                                 PyObject *components_seq,
                                 int path_must_exist)
{
	struct cred_arr *creds = NULL;
	size_t n_creds = 0;
	char **components = NULL;
	size_t n_components = 0;
	PyObject *components_holder = NULL;
	struct failure_rec *recs = NULL;
	size_t n_recs = 0;
	int child_status = 0;
	int saved_errno = 0;
	const char *stage = NULL;
	int rc;
	truenas_os_state_t *state;
	PyTypeObject *failure_type;
	PyObject *result = NULL;

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->AccessFailureType == NULL) {
		PyErr_SetString(PyExc_SystemError,
		                "AccessFailure type not initialized");
		return NULL;
	}
	failure_type = (PyTypeObject *)state->AccessFailureType;

	if (parse_creds(creds_seq, &creds, &n_creds) < 0) {
		goto cleanup;
	}
	if (parse_components(components_seq, &components, &n_components,
	                     &components_holder) < 0) {
		goto cleanup;
	}

	if (n_components == 0) {
		/* Nothing to check — return empty list without forking. */
		result = PyList_New(0);
		goto cleanup;
	}

	Py_BEGIN_ALLOW_THREADS
	rc = run_check(creds, n_creds, components, n_components,
	               path_must_exist, &recs, &n_recs,
	               &child_status, &stage);
	saved_errno = errno;
	Py_END_ALLOW_THREADS

	if (rc < 0) {
		errno = saved_errno;
		PyErr_SetFromErrnoWithFilename(PyExc_OSError,
		    stage ? stage : "<unknown stage>");
		goto cleanup;
	}

	if (raise_for_child_status(child_status, recs, n_recs) < 0) {
		goto cleanup;
	}

	result = build_failure_list(recs, n_recs,
	                            creds, n_creds,
	                            components_holder, n_components,
	                            failure_type);

cleanup:
	free_creds(creds, n_creds);
	PyMem_RawFree(components);
	Py_XDECREF(components_holder);
	PyMem_RawFree(recs);
	return result;
}
