// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <linux/types.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include "thread_rec.h"
#include "thread_iter.skel.h"

#define MODULE_DOC "Per-thread CPU usage from a BPF task iterator."

#define READ_SPACE (64 * 1024)

/* include/linux/sched.h */
#define TASK_UNINTERRUPTIBLE 0x0002
#define TASK_REPORT 0x007f
#define TASK_NOLOAD 0x0400
#define TASK_RTLOCK_WAIT 0x1000
#define TASK_FROZEN 0x8000
#define TASK_IDLE (TASK_UNINTERRUPTIBLE | TASK_NOLOAD)
#define TASK_REPORT_IDLE (TASK_REPORT + 1)

typedef struct {
	PyObject *ThreadStatType;
	PyObject *ThreadSampleType;
} threadstat_state_t;

/*
 * carry is indexed like the records. On a main thread record it holds a zero or
 * negative balance, left when a thread vanished before the process exited
 * total included it. The next sample adds it back so totals stay exact.
 */
typedef struct {
	char *buf;
	size_t len;
	size_t cap;
	long long *carry;
	size_t carry_cap;
	unsigned long long time_ns;
} snapshot_t;

typedef struct {
	const struct thread_rec *rec;
	unsigned long long cpu_ns;
	unsigned long long exited_ns;
	unsigned long long wait_ns;
} selected_t;

typedef struct {
	const struct thread_rec *prev;
	unsigned long long gone_ns;
} match_t;

typedef struct {
	unsigned int tgid;
	long long rss_pages;
} proc_rss_t;

typedef struct {
	PyObject_HEAD
	struct thread_iter *skel;
	struct bpf_link *link;
	snapshot_t cur;
	snapshot_t prev;
	unsigned long long page_size;
	int busy;
	PyObject *stat_type;
	PyObject *sample_type;
} ThreadSamplerObject;

static PyStructSequence_Field thread_stat_fields[] = {
	{"tid", "Thread ID"},
	{"tgid", "Process ID"},
	{"name", "Thread name"},
	{"process_name", "Name of the process main thread"},
	{"state", "State letter as shown by top"},
	{"cpu", "CPU the thread last ran on"},
	{"cpu_ns", "CPU time used during the interval"},
	{"exited_cpu_ns", "CPU time used during the interval by threads of this process that exited, set only on the main thread row"},
	{"wait_ns", "Time spent waiting for a CPU during the interval"},
	{"rss", "Process resident memory in bytes, or None if the process exited"},
	{NULL}
};

static PyStructSequence_Desc thread_stat_desc = {
	.name = "truenas_threadstat.ThreadStat",
	.doc = "Statistics for one thread.",
	.fields = thread_stat_fields,
	.n_in_sequence = 10,
};

static PyStructSequence_Field thread_sample_fields[] = {
	{"elapsed_ns", "Nanoseconds since the previous sample"},
	{"threads", "List of ThreadStat sorted by tid"},
	{NULL}
};

static PyStructSequence_Desc thread_sample_desc = {
	.name = "truenas_threadstat.ThreadSample",
	.doc = "Result of ThreadSampler.sample().",
	.fields = thread_sample_fields,
	.n_in_sequence = 2,
};

static struct PyModuleDef moduledef;

/* Mirrors __task_state_index() in include/linux/sched.h. */
static char
state_letter(unsigned int state, unsigned int exit_state)
{
	static const char letters[] = "RSDTtXZPI";
	unsigned int report = (state | exit_state) & TASK_REPORT;

	if ((state & TASK_IDLE) == TASK_IDLE)
		report = TASK_REPORT_IDLE;
	if (state & (TASK_RTLOCK_WAIT | TASK_FROZEN))
		report = TASK_UNINTERRUPTIBLE;
	if (report == 0)
		return letters[0];
	return letters[32 - __builtin_clz(report)];
}

static unsigned long long
counter_delta(unsigned long long now, unsigned long long then)
{
	return now > then ? now - then : 0;
}

static int
cmp_proc(const void *a, const void *b)
{
	const proc_rss_t *pa = a;
	const proc_rss_t *pb = b;

	return (pa->tgid > pb->tgid) - (pa->tgid < pb->tgid);
}

static int
cmp_tid(const void *a, const void *b)
{
	const struct thread_rec *ra = a;
	const struct thread_rec *rb = b;

	return (ra->tid > rb->tid) - (ra->tid < rb->tid);
}

static void
sort_by_tid(snapshot_t *snap)
{
	struct thread_rec *recs = (struct thread_rec *)snap->buf;
	size_t n = snap->len / sizeof(*recs);
	size_t i;

	for (i = 1; i < n; i++) {
		if (recs[i].tid < recs[i - 1].tid) {
			qsort(recs, n, sizeof(*recs), cmp_tid);
			return;
		}
	}
}

/* Called without the GIL. Returns 0 or an errno value. */
static int
take_snapshot(int link_fd, snapshot_t *snap)
{
	struct timespec now;
	char *grown = NULL;
	size_t newcap;
	ssize_t nread;
	int fd;
	int err = 0;

	clock_gettime(CLOCK_MONOTONIC, &now);
	snap->time_ns = (unsigned long long)now.tv_sec * 1000000000ULL + now.tv_nsec;
	snap->len = 0;

	fd = bpf_iter_create(link_fd);
	if (fd < 0)
		return -fd;

	for (;;) {
		if (snap->cap - snap->len < READ_SPACE) {
			newcap = snap->cap ? snap->cap * 2 : READ_SPACE * 4;
			grown = PyMem_RawRealloc(snap->buf, newcap);
			if (grown == NULL) {
				err = ENOMEM;
				break;
			}
			snap->buf = grown;
			snap->cap = newcap;
		}
		nread = read(fd, snap->buf + snap->len, snap->cap - snap->len);
		if (nread < 0) {
			if (errno == EINTR)
				continue;
			err = errno;
			break;
		}
		if (nread == 0)
			break;
		snap->len += (size_t)nread;
	}
	close(fd);

	if (err == 0 && snap->len % sizeof(struct thread_rec) != 0)
		err = EIO;
	if (err == 0)
		sort_by_tid(snap);
	return err;
}

/* Called without the GIL. Returns 0 or an errno value. */
static int
sampler_open(ThreadSamplerObject *self)
{
	self->skel = thread_iter__open_and_load();
	if (self->skel == NULL)
		return errno ? errno : EINVAL;

	self->link = bpf_program__attach_iter(self->skel->progs.dump_thread, NULL);
	if (self->link == NULL)
		return errno ? errno : EINVAL;

	return take_snapshot(bpf_link__fd(self->link), &self->prev);
}

static void
sampler_release(ThreadSamplerObject *self)
{
	bpf_link__destroy(self->link);
	self->link = NULL;
	thread_iter__destroy(self->skel);
	self->skel = NULL;
	PyMem_RawFree(self->cur.buf);
	PyMem_RawFree(self->prev.buf);
	PyMem_RawFree(self->cur.carry);
	PyMem_RawFree(self->prev.carry);
	memset(&self->cur, 0, sizeof(self->cur));
	memset(&self->prev, 0, sizeof(self->prev));
}

static int
sampler_check_usable(ThreadSamplerObject *self)
{
	if (self->link == NULL) {
		PyErr_SetString(PyExc_ValueError, "ThreadSampler is closed");
		return -1;
	}
	if (self->busy) {
		PyErr_SetString(PyExc_RuntimeError,
				"ThreadSampler is in use by another thread");
		return -1;
	}
	return 0;
}

/* Called without the GIL. Returns resident pages, or -1 if statm cannot be read. */
static long long
read_resident_pages(unsigned int tgid)
{
	char path[32];
	char buf[128];
	unsigned long long size, resident;
	ssize_t nread;
	int fd;

	snprintf(path, sizeof(path), "/proc/%u/statm", tgid);
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -1;
	nread = read(fd, buf, sizeof(buf) - 1);
	close(fd);
	if (nread <= 0)
		return -1;
	buf[nread] = '\0';
	if (sscanf(buf, "%llu %llu", &size, &resident) != 2)
		return -1;
	return (long long)resident;
}

static const struct thread_rec *
find_tid(const struct thread_rec *recs, size_t n, unsigned int tid)
{
	struct thread_rec key = { .tid = tid };

	return bsearch(&key, recs, n, sizeof(*recs), cmp_tid);
}

/* Charge the last seen runtime of a vanished thread to its process, if the process is still there. */
static void
charge_gone(const struct thread_rec *cur, size_t ncur, const struct thread_rec *prev,
	    size_t nprev, const struct thread_rec *gone, match_t *match)
{
	const struct thread_rec *old_main = NULL;
	const struct thread_rec *new_main = NULL;

	if (gone->tid == gone->tgid)
		return;
	old_main = find_tid(prev, nprev, gone->tgid);
	new_main = find_tid(cur, ncur, gone->tgid);
	if (old_main == NULL || new_main == NULL ||
	    old_main->start_time != new_main->start_time)
		return;
	match[new_main - cur].gone_ns += gone->runtime;
}

/*
 * Called without the GIL. Diffs the current snapshot against the previous
 * one. Both are sorted by tid. Returns 0 or an errno value.
 */
static int
select_threads(snapshot_t *cur_snap, const snapshot_t *prev_snap,
	       int include_idle, selected_t **selp, size_t *nselp)
{
	const struct thread_rec *cur = (const struct thread_rec *)cur_snap->buf;
	const struct thread_rec *prev = (const struct thread_rec *)prev_snap->buf;
	const struct thread_rec *old = NULL;
	size_t ncur = cur_snap->len / sizeof(*cur);
	size_t nprev = prev_snap->len / sizeof(*prev);
	selected_t *sel = NULL;
	match_t *match = NULL;
	long long *carry = NULL;
	long long balance;
	unsigned long long cpu_ns, exited_ns;
	size_t nsel = 0;
	size_t i, j;

	if (cur_snap->carry_cap < ncur) {
		carry = PyMem_RawRealloc(cur_snap->carry, ncur * sizeof(*carry));
		if (carry == NULL)
			return ENOMEM;
		cur_snap->carry = carry;
		cur_snap->carry_cap = ncur;
	}
	sel = PyMem_RawMalloc((ncur ? ncur : 1) * sizeof(*sel));
	match = PyMem_RawCalloc(ncur ? ncur : 1, sizeof(*match));
	if (sel == NULL || match == NULL) {
		PyMem_RawFree(sel);
		PyMem_RawFree(match);
		return ENOMEM;
	}

	i = 0;
	j = 0;
	while (i < ncur || j < nprev) {
		if (i == ncur || (j < nprev && prev[j].tid < cur[i].tid)) {
			charge_gone(cur, ncur, prev, nprev, &prev[j], match);
			j++;
		} else if (j < nprev && prev[j].tid == cur[i].tid) {
			if (prev[j].start_time == cur[i].start_time)
				match[i].prev = &prev[j];
			else
				charge_gone(cur, ncur, prev, nprev, &prev[j], match);
			i++;
			j++;
		} else {
			i++;
		}
	}

	for (i = 0; i < ncur; i++) {
		old = match[i].prev;
		cpu_ns = counter_delta(cur[i].runtime, old ? old->runtime : 0);
		exited_ns = 0;
		cur_snap->carry[i] = 0;

		if (cur[i].tid == cur[i].tgid) {
			balance = (long long)counter_delta(cur[i].exited_runtime,
							   old ? old->exited_runtime : 0);
			balance -= (long long)match[i].gone_ns;
			if (old != NULL && prev_snap->carry != NULL)
				balance += prev_snap->carry[old - prev];
			if (balance > 0)
				exited_ns = (unsigned long long)balance;
			else
				cur_snap->carry[i] = balance;
		}

		if (cpu_ns == 0 && exited_ns == 0 && !include_idle)
			continue;

		sel[nsel].rec = &cur[i];
		sel[nsel].cpu_ns = cpu_ns;
		sel[nsel].exited_ns = exited_ns;
		sel[nsel].wait_ns = counter_delta(cur[i].run_delay,
						  old ? old->run_delay : 0);
		nsel++;
	}

	PyMem_RawFree(match);
	*selp = sel;
	*nselp = nsel;
	return 0;
}

/* Called without the GIL. Reads statm once per process. Returns 0 or an errno value. */
static int
read_process_rss(const selected_t *sel, size_t nsel, proc_rss_t **procsp,
		 size_t *nprocsp)
{
	proc_rss_t *procs = NULL;
	size_t n = 0;
	size_t i;

	procs = PyMem_RawMalloc((nsel ? nsel : 1) * sizeof(*procs));
	if (procs == NULL)
		return ENOMEM;

	for (i = 0; i < nsel; i++)
		procs[i].tgid = sel[i].rec->tgid;
	qsort(procs, nsel, sizeof(*procs), cmp_proc);

	for (i = 0; i < nsel; i++) {
		if (n > 0 && procs[n - 1].tgid == procs[i].tgid)
			continue;
		procs[n].tgid = procs[i].tgid;
		procs[n].rss_pages = read_resident_pages(procs[n].tgid);
		n++;
	}

	*procsp = procs;
	*nprocsp = n;
	return 0;
}

#define SET_FIELD(idx, expr) do {			\
	val = (expr);					\
	if (val == NULL)				\
		goto fail;				\
	PyStructSequence_SetItem(out, (idx), val);	\
} while (0)

static PyObject *
build_thread_stat(ThreadSamplerObject *self, const selected_t *sel,
		  long long rss_pages)
{
	const struct thread_rec *rec = sel->rec;
	PyObject *out = NULL;
	PyObject *val = NULL;

	out = PyStructSequence_New((PyTypeObject *)self->stat_type);
	if (out == NULL)
		return NULL;

	SET_FIELD(0, PyLong_FromUnsignedLong(rec->tid));
	SET_FIELD(1, PyLong_FromUnsignedLong(rec->tgid));
	SET_FIELD(2, PyUnicode_DecodeUTF8(rec->comm, strnlen(rec->comm, sizeof(rec->comm)), "replace"));
	SET_FIELD(3, PyUnicode_DecodeUTF8(rec->process_comm, strnlen(rec->process_comm, sizeof(rec->process_comm)), "replace"));
	SET_FIELD(4, PyUnicode_FromOrdinal(state_letter(rec->state, rec->exit_state)));
	SET_FIELD(5, PyLong_FromUnsignedLong(rec->cpu));
	SET_FIELD(6, PyLong_FromUnsignedLongLong(sel->cpu_ns));
	SET_FIELD(7, PyLong_FromUnsignedLongLong(sel->exited_ns));
	SET_FIELD(8, PyLong_FromUnsignedLongLong(sel->wait_ns));
	if (rss_pages < 0)
		SET_FIELD(9, Py_NewRef(Py_None));
	else
		SET_FIELD(9, PyLong_FromUnsignedLongLong((unsigned long long)rss_pages * self->page_size));
	return out;

fail:
	Py_DECREF(out);
	return NULL;
}

#undef SET_FIELD

static PyObject *
sample_locked(ThreadSamplerObject *self, int include_idle)
{
	const proc_rss_t *proc = NULL;
	selected_t *sel = NULL;
	proc_rss_t *procs = NULL;
	PyObject *threads = NULL;
	PyObject *item = NULL;
	PyObject *elapsed = NULL;
	PyObject *result = NULL;
	proc_rss_t key;
	snapshot_t swap;
	size_t nsel = 0;
	size_t nprocs = 0;
	size_t i;
	int link_fd;
	int err;

	link_fd = bpf_link__fd(self->link);
	Py_BEGIN_ALLOW_THREADS
	err = take_snapshot(link_fd, &self->cur);
	if (err == 0)
		err = select_threads(&self->cur, &self->prev, include_idle, &sel, &nsel);
	if (err == 0)
		err = read_process_rss(sel, nsel, &procs, &nprocs);
	Py_END_ALLOW_THREADS
	if (err) {
		if (err == ENOMEM)
			PyErr_NoMemory();
		else {
			errno = err;
			PyErr_SetFromErrno(PyExc_OSError);
		}
		goto fail;
	}

	threads = PyList_New((Py_ssize_t)nsel);
	if (threads == NULL)
		goto fail;

	for (i = 0; i < nsel; i++) {
		key.tgid = sel[i].rec->tgid;
		proc = bsearch(&key, procs, nprocs, sizeof(*procs), cmp_proc);
		item = build_thread_stat(self, &sel[i], proc ? proc->rss_pages : -1);
		if (item == NULL)
			goto fail;
		PyList_SET_ITEM(threads, (Py_ssize_t)i, item);
	}

	elapsed = PyLong_FromUnsignedLongLong(counter_delta(self->cur.time_ns,
							    self->prev.time_ns));
	if (elapsed == NULL)
		goto fail;
	result = PyStructSequence_New((PyTypeObject *)self->sample_type);
	if (result == NULL)
		goto fail;
	PyStructSequence_SetItem(result, 0, elapsed);
	PyStructSequence_SetItem(result, 1, threads);

	swap = self->prev;
	self->prev = self->cur;
	self->cur = swap;
	PyMem_RawFree(sel);
	PyMem_RawFree(procs);
	return result;

fail:
	Py_XDECREF(elapsed);
	Py_XDECREF(threads);
	PyMem_RawFree(sel);
	PyMem_RawFree(procs);
	return NULL;
}

PyDoc_STRVAR(ThreadSampler_sample__doc__,
"sample(*, include_idle=False)\n"
"\n"
"Return a ThreadSample covering the time since the previous sample, or since\n"
"the sampler was created. Only threads that used CPU time are included unless\n"
"include_idle is True. Interval fields of a thread that started during the\n"
"interval cover its whole life.\n");

static PyObject *
ThreadSampler_sample(ThreadSamplerObject *self, PyObject *args, PyObject *kwargs)
{
	static char *kwnames[] = { "include_idle", NULL };
	PyObject *result = NULL;
	int include_idle = 0;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$p:sample", kwnames,
					 &include_idle))
		return NULL;
	if (sampler_check_usable(self) < 0)
		return NULL;

	self->busy = 1;
	result = sample_locked(self, include_idle);
	self->busy = 0;
	return result;
}

PyDoc_STRVAR(ThreadSampler_close__doc__,
"close()\n"
"\n"
"Unload the BPF program and free buffers. Safe to call more than once.\n");

static PyObject *
ThreadSampler_close(ThreadSamplerObject *self, PyObject *Py_UNUSED(ignored))
{
	if (self->busy) {
		PyErr_SetString(PyExc_RuntimeError,
				"ThreadSampler is in use by another thread");
		return NULL;
	}
	sampler_release(self);
	Py_RETURN_NONE;
}

static PyObject *
ThreadSampler_enter(ThreadSamplerObject *self, PyObject *Py_UNUSED(ignored))
{
	if (self->link == NULL) {
		PyErr_SetString(PyExc_ValueError, "ThreadSampler is closed");
		return NULL;
	}
	return Py_NewRef(self);
}

static PyObject *
ThreadSampler_exit(ThreadSamplerObject *self, PyObject *args)
{
	return ThreadSampler_close(self, NULL);
}

static PyMethodDef ThreadSampler_methods[] = {
	{
		.ml_name = "sample",
		.ml_meth = (PyCFunction)(void (*)(void))ThreadSampler_sample,
		.ml_flags = METH_VARARGS | METH_KEYWORDS,
		.ml_doc = ThreadSampler_sample__doc__
	},
	{
		.ml_name = "close",
		.ml_meth = (PyCFunction)ThreadSampler_close,
		.ml_flags = METH_NOARGS,
		.ml_doc = ThreadSampler_close__doc__
	},
	{
		.ml_name = "__enter__",
		.ml_meth = (PyCFunction)ThreadSampler_enter,
		.ml_flags = METH_NOARGS,
	},
	{
		.ml_name = "__exit__",
		.ml_meth = (PyCFunction)ThreadSampler_exit,
		.ml_flags = METH_VARARGS,
	},
	{ NULL, NULL, 0, NULL }
};

static PyObject *
ThreadSampler_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	static char *kwnames[] = { NULL };
	threadstat_state_t *state = NULL;
	ThreadSamplerObject *self = NULL;
	PyObject *module = NULL;
	int err;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, ":ThreadSampler", kwnames))
		return NULL;

	module = PyState_FindModule(&moduledef);
	if (module == NULL) {
		PyErr_SetString(PyExc_RuntimeError,
				"truenas_threadstat module state not found");
		return NULL;
	}
	state = PyModule_GetState(module);

	self = (ThreadSamplerObject *)type->tp_alloc(type, 0);
	if (self == NULL)
		return NULL;
	self->stat_type = Py_NewRef(state->ThreadStatType);
	self->sample_type = Py_NewRef(state->ThreadSampleType);
	self->page_size = (unsigned long long)sysconf(_SC_PAGESIZE);

	Py_BEGIN_ALLOW_THREADS
	err = sampler_open(self);
	Py_END_ALLOW_THREADS
	if (err) {
		Py_DECREF(self);
		errno = err;
		return PyErr_SetFromErrno(PyExc_OSError);
	}
	return (PyObject *)self;
}

static void
ThreadSampler_dealloc(ThreadSamplerObject *self)
{
	sampler_release(self);
	Py_CLEAR(self->stat_type);
	Py_CLEAR(self->sample_type);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(ThreadSampler__doc__,
"ThreadSampler()\n"
"\n"
"Load the BPF thread iterator and take a baseline snapshot. Requires\n"
"CAP_BPF and CAP_PERFMON. Use close() or a with block to unload it.\n");

static PyTypeObject ThreadSamplerType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_threadstat.ThreadSampler",
	.tp_doc = ThreadSampler__doc__,
	.tp_basicsize = sizeof(ThreadSamplerObject),
	.tp_flags = Py_TPFLAGS_DEFAULT,
	.tp_new = ThreadSampler_new,
	.tp_dealloc = (destructor)ThreadSampler_dealloc,
	.tp_methods = ThreadSampler_methods,
};

static struct PyModuleDef moduledef = {
	PyModuleDef_HEAD_INIT,
	.m_name = "truenas_threadstat",
	.m_doc = MODULE_DOC,
	.m_size = sizeof(threadstat_state_t),
};

PyMODINIT_FUNC PyInit_truenas_threadstat(void);

PyMODINIT_FUNC
PyInit_truenas_threadstat(void)
{
	threadstat_state_t *state = NULL;
	PyObject *m = NULL;

	if (PyType_Ready(&ThreadSamplerType) < 0)
		return NULL;

	m = PyModule_Create(&moduledef);
	if (m == NULL)
		return NULL;
	state = PyModule_GetState(m);

	state->ThreadStatType = (PyObject *)PyStructSequence_NewType(&thread_stat_desc);
	if (state->ThreadStatType == NULL)
		goto fail;
	if (PyModule_AddObjectRef(m, "ThreadStat", state->ThreadStatType) < 0)
		goto fail;

	state->ThreadSampleType = (PyObject *)PyStructSequence_NewType(&thread_sample_desc);
	if (state->ThreadSampleType == NULL)
		goto fail;
	if (PyModule_AddObjectRef(m, "ThreadSample", state->ThreadSampleType) < 0)
		goto fail;

	if (PyModule_AddObjectRef(m, "ThreadSampler", (PyObject *)&ThreadSamplerType) < 0)
		goto fail;

	return m;

fail:
	Py_DECREF(m);
	return NULL;
}
