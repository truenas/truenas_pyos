// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include <endian.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include "acl.h"
#include "truenas_os_state.h"
#include "util_enum.h"

#define POSIX_HDR_SZ       4    /* version u32 LE */
#define POSIX_ACE_SZ       8    /* tag u16 LE + perm u16 LE + id u32 LE */
#define POSIX_ACL_VERSION  2
#define POSIX_SPECIAL_ID   0xFFFFFFFFU

/* Tags whose id field is always POSIX_SPECIAL_ID */
#define POSIX_TAG_USER_OBJ  0x0001
#define POSIX_TAG_GROUP_OBJ 0x0004
#define POSIX_TAG_MASK      0x0010
#define POSIX_TAG_OTHER     0x0020

/* Tags that carry a uid/gid in the id field */
#define POSIX_TAG_USER      0x0002
#define POSIX_TAG_GROUP     0x0008

/* ── little-endian I/O helpers ──────────────────────────────────────────── */

static inline uint32_t
read_le32(const uint8_t *p)
{
	uint32_t v;
	memcpy(&v, p, sizeof(v));
	return le32toh(v);
}

static inline void
write_le32(uint8_t *p, uint32_t v)
{
	v = htole32(v);
	memcpy(p, &v, sizeof(v));
}

static inline uint16_t
read_le16(const uint8_t *p)
{
	uint16_t v;
	memcpy(&v, p, sizeof(v));
	return le16toh(v);
}

static inline void
write_le16(uint8_t *p, uint16_t v)
{
	v = htole16(v);
	memcpy(p, &v, sizeof(v));
}

/* ── enum member tables ─────────────────────────────────────────────────── */

static const py_intenum_tbl_t posix_tag_table[] = {
	{ "USER_OBJ",  0x0001 },
	{ "USER",      0x0002 },
	{ "GROUP_OBJ", 0x0004 },
	{ "GROUP",     0x0008 },
	{ "MASK",      0x0010 },
	{ "OTHER",     0x0020 },
};

static const py_intenum_tbl_t posix_perm_table[] = {
	{ "EXECUTE", 0x01 },
	{ "WRITE",   0x02 },
	{ "READ",    0x04 },
};

/* ── helper: is this tag a "special" entry (id always POSIX_SPECIAL_ID)? ── */

static int
is_special_tag(long tag)
{
	return tag == POSIX_TAG_USER_OBJ
	    || tag == POSIX_TAG_GROUP_OBJ
	    || tag == POSIX_TAG_MASK
	    || tag == POSIX_TAG_OTHER;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * POSIXAce type
 * ═════════════════════════════════════════════════════════════════════════ */

typedef struct {
	PyObject_HEAD
	PyObject *tag;      /* POSIXTag enum member  */
	PyObject *perms;    /* POSIXPerm enum member  */
	PyObject *id;       /* int: uid/gid or -1     */
	PyObject *default_; /* bool: default ACL flag */
} POSIXAce_t;

static void
POSIXAce_dealloc(POSIXAce_t *self)
{
	Py_CLEAR(self->tag);
	Py_CLEAR(self->perms);
	Py_CLEAR(self->id);
	Py_CLEAR(self->default_);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
POSIXAce_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	POSIXAce_t *self = (POSIXAce_t *)type->tp_alloc(type, 0);
	if (self == NULL)
		return NULL;
	self->tag = Py_NewRef(Py_None);
	self->perms = Py_NewRef(Py_None);
	self->id = PyLong_FromLong(-1);
	self->default_ = Py_NewRef(Py_False);
	if (self->id == NULL) {
		Py_DECREF(self);
		return NULL;
	}
	return (PyObject *)self;
}

static int
POSIXAce_init(POSIXAce_t *self, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = { "tag", "perms", "id", "default", NULL };
	PyObject *tag, *perms;
	PyObject *id = NULL;
	PyObject *default_ = NULL;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|OO", kwlist,
	                                 &tag, &perms, &id, &default_))
		return -1;

	Py_INCREF(tag);
	Py_SETREF(self->tag, tag);
	Py_INCREF(perms);
	Py_SETREF(self->perms, perms);

	if (id != NULL) {
		Py_INCREF(id);
		Py_SETREF(self->id, id);
	}
	if (default_ != NULL) {
		Py_INCREF(default_);
		Py_SETREF(self->default_, default_);
	}
	return 0;
}

static PyObject *
POSIXAce_repr(POSIXAce_t *self)
{
	return PyUnicode_FromFormat(
	    "POSIXAce(tag=%R, perms=%R, id=%R, default=%R)",
	    self->tag, self->perms, self->id, self->default_);
}

#define MAKE_GETTER(field)                                                     \
static PyObject *POSIXAce_get_##field(POSIXAce_t *self, void *c)             \
{ return Py_NewRef(self->field); }

MAKE_GETTER(tag)
MAKE_GETTER(perms)
MAKE_GETTER(id)

static PyObject *
POSIXAce_get_default(POSIXAce_t *self, void *c)
{
	return Py_NewRef(self->default_);
}

static PyGetSetDef POSIXAce_getsets[] = {
	{ "tag",     (getter)POSIXAce_get_tag,     NULL, NULL, NULL },
	{ "perms",   (getter)POSIXAce_get_perms,   NULL, NULL, NULL },
	{ "id",      (getter)POSIXAce_get_id,      NULL, NULL, NULL },
	{ "default", (getter)POSIXAce_get_default, NULL, NULL, NULL },
	{ NULL }
};

/*
 * POSIXAce_richcompare — canonical POSIX ACL ordering.
 *
 * Primary key:   tag value (USER_OBJ=0x01 < USER=0x02 < GROUP_OBJ=0x04
 *                            < GROUP=0x08 < MASK=0x10 < OTHER=0x20).
 * Secondary key: id, so named USER/GROUP entries are sorted by uid/gid.
 *                Special entries all share id==-1, so they remain stable.
 *
 * This lets PyList_Sort() produce the canonical order the kernel requires
 * in posix_acl_valid() without any extra allocation.
 */
static PyObject *
POSIXAce_richcompare(PyObject *self, PyObject *other, int op)
{
	long tag_a, tag_b, id_a, id_b;
	int cmp;
	POSIXAce_t *a;
	POSIXAce_t *b;

	if (!PyObject_TypeCheck(other, &POSIXAce_Type))
		Py_RETURN_NOTIMPLEMENTED;

	a = (POSIXAce_t *)self;
	b = (POSIXAce_t *)other;

	tag_a = PyLong_AsLong(a->tag);
	tag_b = PyLong_AsLong(b->tag);
	id_a = PyLong_AsLong(a->id);
	id_b = PyLong_AsLong(b->id);
	if (PyErr_Occurred())
		return NULL;

	if (tag_a != tag_b)
		cmp = (tag_a < tag_b) ? -1 : 1;
	else if (id_a != id_b)
		cmp = (id_a < id_b) ? -1 : 1;
	else
		cmp = 0;

	switch (op) {
	case Py_LT: return PyBool_FromLong(cmp <  0);
	case Py_LE: return PyBool_FromLong(cmp <= 0);
	case Py_EQ: return PyBool_FromLong(cmp == 0);
	case Py_NE: return PyBool_FromLong(cmp != 0);
	case Py_GT: return PyBool_FromLong(cmp >  0);
	case Py_GE: return PyBool_FromLong(cmp >= 0);
	default:    Py_RETURN_NOTIMPLEMENTED;
	}
}

PyDoc_STRVAR(POSIXAce_doc,
"POSIX ACL entry.\n"
"\n"
"Fields: tag (POSIXTag), perms (POSIXPerm), id (int), default (bool).\n"
"id is the uid/gid for USER/GROUP; -1 for special entries.\n"
"default=True marks entries that belong to the default ACL.");

PyTypeObject POSIXAce_Type = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name        = "truenas_os.POSIXAce",
	.tp_basicsize   = sizeof(POSIXAce_t),
	.tp_dealloc     = (destructor)POSIXAce_dealloc,
	.tp_repr        = (reprfunc)POSIXAce_repr,
	.tp_richcompare = POSIXAce_richcompare,
	.tp_flags       = Py_TPFLAGS_DEFAULT,
	.tp_doc         = POSIXAce_doc,
	.tp_getset      = POSIXAce_getsets,
	.tp_new         = POSIXAce_new,
	.tp_init        = (initproc)POSIXAce_init,
};

/* ═══════════════════════════════════════════════════════════════════════════
 * POSIXACL type
 * ═════════════════════════════════════════════════════════════════════════ */

typedef struct {
	PyObject_HEAD
	PyObject *access_data;  /* bytes: raw system.posix_acl_access  */
	PyObject *default_data; /* bytes or None: system.posix_acl_default */
} POSIXACL_t;

static void
POSIXACL_dealloc(POSIXACL_t *self)
{
	Py_CLEAR(self->access_data);
	Py_CLEAR(self->default_data);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
POSIXACL_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	POSIXACL_t *self = (POSIXACL_t *)type->tp_alloc(type, 0);
	if (self == NULL)
		return NULL;
	self->access_data = PyBytes_FromStringAndSize("", 0);
	self->default_data = Py_NewRef(Py_None);
	if (self->access_data == NULL) {
		Py_DECREF(self);
		return NULL;
	}
	return (PyObject *)self;
}

static int
POSIXACL_init(POSIXACL_t *self, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = { "access_data", "default_data", NULL };
	PyObject *access_data;
	PyObject *default_data = Py_None;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O!|O", kwlist,
	                                 &PyBytes_Type, &access_data,
	                                 &default_data))
		return -1;

	if (default_data != Py_None && !PyBytes_Check(default_data)) {
		PyErr_SetString(PyExc_TypeError,
		                "POSIXACL: default_data must be bytes or None");
		return -1;
	}

	Py_INCREF(access_data);
	Py_SETREF(self->access_data, access_data);
	Py_INCREF(default_data);
	Py_SETREF(self->default_data, default_data);
	return 0;
}

/* ── encode a list of POSIXAce into a raw POSIX ACL blob ─────────────────── */

static PyObject *
encode_posix_aces(PyObject *ace_seq)
{
	Py_ssize_t naces;
	size_t bufsz;
	uint8_t *buf;
	PyObject *result;
	Py_ssize_t i;
	PyObject *ace;
	POSIXAce_t *a;
	long tag_v;
	long perm_v;
	long id_v;
	uint32_t xid;
	uint8_t *p;

	naces = PySequence_Fast_GET_SIZE(ace_seq);
	bufsz = POSIX_HDR_SZ + (size_t)naces * POSIX_ACE_SZ;
	buf = (uint8_t *)PyMem_Malloc(bufsz);
	if (buf == NULL)
		return PyErr_NoMemory();

	write_le32(buf, POSIX_ACL_VERSION);

	for (i = 0; i < naces; i++) {
		ace = PySequence_Fast_GET_ITEM(ace_seq, i);

		if (!PyObject_TypeCheck(ace, &POSIXAce_Type)) {
			PyMem_Free(buf);
			PyErr_SetString(PyExc_TypeError,
			                "from_aces: aces must contain POSIXAce objects");
			return NULL;
		}
		a = (POSIXAce_t *)ace;

		tag_v = PyLong_AsLong(a->tag);
		perm_v = PyLong_AsLong(a->perms);
		id_v = PyLong_AsLong(a->id);
		if (PyErr_Occurred()) {
			PyMem_Free(buf);
			return NULL;
		}

		xid = is_special_tag(tag_v) ? POSIX_SPECIAL_ID : (uint32_t)id_v;

		p = buf + POSIX_HDR_SZ + (size_t)i * POSIX_ACE_SZ;
		write_le16(p + 0, (uint16_t)tag_v);
		write_le16(p + 2, (uint16_t)perm_v);
		write_le32(p + 4, xid);
	}

	result = PyBytes_FromStringAndSize((char *)buf, (Py_ssize_t)bufsz);
	PyMem_Free(buf);
	return result;
}

PyDoc_STRVAR(POSIXACL_from_aces_doc,
"from_aces(aces)\n"
"\n"
"Construct a POSIXACL from an iterable of POSIXAce objects.\n"
"Entries with default=True go into the default ACL xattr;\n"
"all others go into the access ACL xattr.");

/* POSIXACL.from_aces(aces) classmethod */
static PyObject *
POSIXACL_from_aces(PyObject *cls, PyObject *args)
{
	PyObject *aces_arg;
	PyObject *seq;
	Py_ssize_t total;
	PyObject *access_list;
	PyObject *default_list;
	Py_ssize_t i;
	PyObject *ace;
	POSIXAce_t *a;
	PyObject *target;
	PyObject *access_seq;
	PyObject *default_seq;
	PyObject *access_bytes;
	PyObject *default_bytes_or_none;
	PyObject *result;

	if (!PyArg_ParseTuple(args, "O:from_aces", &aces_arg))
		return NULL;

	seq = PySequence_Fast(aces_arg, "from_aces: aces must be iterable");
	if (seq == NULL)
		return NULL;

	total = PySequence_Fast_GET_SIZE(seq);

	/* Separate into access and default lists */
	access_list = PyList_New(0);
	default_list = PyList_New(0);
	if (!access_list || !default_list) {
		Py_XDECREF(access_list);
		Py_XDECREF(default_list);
		Py_DECREF(seq);
		return NULL;
	}

	for (i = 0; i < total; i++) {
		ace = PySequence_Fast_GET_ITEM(seq, i);
		if (!PyObject_TypeCheck(ace, &POSIXAce_Type)) {
			PyErr_SetString(PyExc_TypeError,
			                "from_aces: aces must contain POSIXAce objects");
			Py_DECREF(access_list);
			Py_DECREF(default_list);
			Py_DECREF(seq);
			return NULL;
		}
		a = (POSIXAce_t *)ace;
		target = PyObject_IsTrue(a->default_) ? default_list : access_list;
		if (PyList_Append(target, ace) < 0) {
			Py_DECREF(access_list);
			Py_DECREF(default_list);
			Py_DECREF(seq);
			return NULL;
		}
	}
	Py_DECREF(seq);

	if (PyList_Sort(access_list) < 0 || PyList_Sort(default_list) < 0) {
		Py_DECREF(access_list);
		Py_DECREF(default_list);
		return NULL;
	}

	access_seq = PySequence_Fast(access_list, "");
	default_seq = PySequence_Fast(default_list, "");
	Py_DECREF(access_list);
	Py_DECREF(default_list);

	access_bytes = NULL;
	default_bytes_or_none = NULL;
	result = NULL;

	if (!access_seq || !default_seq)
		goto done;

	access_bytes = encode_posix_aces(access_seq);
	if (access_bytes == NULL)
		goto done;

	if (PySequence_Fast_GET_SIZE(default_seq) > 0) {
		default_bytes_or_none = encode_posix_aces(default_seq);
		if (default_bytes_or_none == NULL)
			goto done;
	} else {
		default_bytes_or_none = Py_NewRef(Py_None);
	}

	result = PyObject_CallFunction(cls, "OO",
	                               access_bytes, default_bytes_or_none);
done:
	Py_XDECREF(access_seq);
	Py_XDECREF(default_seq);
	Py_XDECREF(access_bytes);
	Py_XDECREF(default_bytes_or_none);
	return result;
}

/* ── shared ACE parser ───────────────────────────────────────────────────── */

static PyObject *
parse_posix_aces(const uint8_t *buf, Py_ssize_t bufsz, int is_default)
{
	Py_ssize_t naces;
	truenas_os_state_t *state = NULL;
	PyObject *result = NULL;
	PyObject *default_flag = NULL;
	Py_ssize_t i;
	const uint8_t *p = NULL;
	uint16_t tag_raw;
	uint16_t perm_raw;
	uint32_t xid;
	long id_v;
	PyObject *tmp = NULL;
	PyObject *tag_o = NULL;
	PyObject *perm_o = NULL;
	PyObject *id_o = NULL;
	PyObject *ace = NULL;

	if (bufsz == 0)
		return PyList_New(0);

	if (bufsz < POSIX_HDR_SZ) {
		PyErr_SetString(PyExc_ValueError, "POSIXACL data too short");
		return NULL;
	}

	naces = (bufsz - POSIX_HDR_SZ) / POSIX_ACE_SZ;

	state = get_truenas_os_state(NULL);
	if (state == NULL)
		return NULL;

	result = PyList_New(naces);
	if (result == NULL)
		return NULL;

	default_flag = is_default ? Py_True : Py_False;

	for (i = 0; i < naces; i++) {
		p = buf + POSIX_HDR_SZ + (size_t)i * POSIX_ACE_SZ;
		tag_raw = read_le16(p + 0);
		perm_raw = read_le16(p + 2);
		xid = read_le32(p + 4);

		id_v = (xid == POSIX_SPECIAL_ID) ? -1L : (long)xid;

		tag_o = PyObject_CallOneArg(state->POSIXTag_enum,
		    tmp = PyLong_FromUnsignedLong(tag_raw));
		Py_XDECREF(tmp);
		perm_o = PyObject_CallOneArg(state->POSIXPerm_enum,
		    tmp = PyLong_FromUnsignedLong(perm_raw));
		Py_XDECREF(tmp);
		id_o = PyLong_FromLong(id_v);

		if (!tag_o || !perm_o || !id_o) {
			Py_XDECREF(tag_o);
			Py_XDECREF(perm_o);
			Py_XDECREF(id_o);
			Py_DECREF(result);
			return NULL;
		}

		ace = PyObject_CallFunction(
		    (PyObject *)&POSIXAce_Type, "OOOO",
		    tag_o, perm_o, id_o, default_flag);
		Py_DECREF(tag_o);
		Py_DECREF(perm_o);
		Py_DECREF(id_o);

		if (ace == NULL) {
			Py_DECREF(result);
			return NULL;
		}
		PyList_SET_ITEM(result, i, ace);
	}
	return result;
}

PyDoc_STRVAR(POSIXACL_aces_doc,
"list[POSIXAce]: entries from the access ACL.");

/* POSIXACL.aces property */
static PyObject *
POSIXACL_get_aces(POSIXACL_t *self, void *closure)
{
	return parse_posix_aces(
	    (const uint8_t *)PyBytes_AS_STRING(self->access_data),
	    PyBytes_GET_SIZE(self->access_data), 0);
}

PyDoc_STRVAR(POSIXACL_default_aces_doc,
"list[POSIXAce]: entries from the default ACL (empty if none).");

/* POSIXACL.default_aces property */
static PyObject *
POSIXACL_get_default_aces(POSIXACL_t *self, void *closure)
{
	if (self->default_data == Py_None)
		return PyList_New(0);
	return parse_posix_aces(
	    (const uint8_t *)PyBytes_AS_STRING(self->default_data),
	    PyBytes_GET_SIZE(self->default_data), 1);
}

PyDoc_STRVAR(POSIXACL_access_bytes_doc,
"Return the raw bytes for system.posix_acl_access.");

/* POSIXACL.access_bytes() method */
static PyObject *
POSIXACL_access_bytes(POSIXACL_t *self, PyObject *Py_UNUSED(args))
{
	return Py_NewRef(self->access_data);
}

PyDoc_STRVAR(POSIXACL_default_bytes_doc,
"Return the raw bytes for system.posix_acl_default, or None.");

/* POSIXACL.default_bytes() method */
static PyObject *
POSIXACL_default_bytes(POSIXACL_t *self, PyObject *Py_UNUSED(args))
{
	return Py_NewRef(self->default_data);
}

/* POSIXACL.__repr__ */
static PyObject *
POSIXACL_repr(POSIXACL_t *self)
{
	PyObject *aces = NULL;
	PyObject *default_aces = NULL;
	PyObject *result = NULL;

	aces = POSIXACL_get_aces(self, NULL);
	default_aces = POSIXACL_get_default_aces(self, NULL);
	if (!aces || !default_aces) {
		Py_XDECREF(aces);
		Py_XDECREF(default_aces);
		return NULL;
	}
	result = PyUnicode_FromFormat(
	    "POSIXACL(aces=%R, default_aces=%R)", aces, default_aces);
	Py_DECREF(aces);
	Py_DECREF(default_aces);
	return result;
}

PyDoc_STRVAR(POSIXACL_trivial_doc,
"bool: True if no access ACL xattr was present (ENODATA) "
"and no default ACL.");

/* POSIXACL.trivial property */
static PyObject *
POSIXACL_get_trivial(POSIXACL_t *self, void *closure)
{
	return PyBool_FromLong(
	    PyBytes_GET_SIZE(self->access_data) == 0 &&
	    self->default_data == Py_None);
}

static PyGetSetDef POSIXACL_getsets[] = {
	{ "aces",         (getter)POSIXACL_get_aces,          NULL, POSIXACL_aces_doc,         NULL },
	{ "default_aces", (getter)POSIXACL_get_default_aces,  NULL, POSIXACL_default_aces_doc, NULL },
	{ "trivial",      (getter)POSIXACL_get_trivial,        NULL, POSIXACL_trivial_doc,      NULL },
	{ NULL }
};

PyDoc_STRVAR(POSIXACL_generate_inherited_acl_doc,
"generate_inherited_acl(is_dir=True)\n"
"\n"
"Produce the ACL for a new child object from this directory's default\n"
"ACL.  For a directory child (is_dir=True) the default ACL is used as\n"
"both the access and default ACL so it propagates further.  For a file\n"
"child (is_dir=False) only the access ACL is set.\n"
"\n"
"Raises ValueError if the ACL is trivial or has no default ACL.");

/*
 * POSIXACL.generate_inherited_acl(is_dir=True)
 *
 * Produce the ACL for a new child object from this directory's default ACL.
 *
 * For a directory child (is_dir=True, the default):
 *   access ACL = default ACL, default ACL = default ACL (propagates).
 * For a file child (is_dir=False):
 *   access ACL = default ACL, no default ACL.
 *
 * Raises ValueError if this ACL is trivial (no access xattr) or has no
 * default ACL.
 */
static PyObject *
POSIXACL_generate_inherited_acl(POSIXACL_t *self, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = { "is_dir", NULL };
	int is_dir;
	PyObject *new_default;

	is_dir = 1;
	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p", kwlist, &is_dir))
		return NULL;

	if (PyBytes_GET_SIZE(self->access_data) == 0 &&
	    self->default_data == Py_None) {
		PyErr_SetString(PyExc_ValueError,
		    "cannot generate inherited ACL from trivial ACL");
		return NULL;
	}

	if (self->default_data == Py_None) {
		PyErr_SetString(PyExc_ValueError,
		    "cannot generate inherited ACL: no default ACL");
		return NULL;
	}

	new_default = is_dir ? self->default_data : Py_None;
	return PyObject_CallFunction((PyObject *)&POSIXACL_Type, "OO",
	                             self->default_data, new_default);
}

static PyMethodDef POSIXACL_methods[] = {
	{ "from_aces",
	  (PyCFunction)POSIXACL_from_aces,
	  METH_CLASS | METH_VARARGS,
	  POSIXACL_from_aces_doc },
	{ "access_bytes",
	  (PyCFunction)POSIXACL_access_bytes,
	  METH_NOARGS,
	  POSIXACL_access_bytes_doc },
	{ "default_bytes",
	  (PyCFunction)POSIXACL_default_bytes,
	  METH_NOARGS,
	  POSIXACL_default_bytes_doc },
	{ "generate_inherited_acl",
	  (PyCFunction)POSIXACL_generate_inherited_acl,
	  METH_VARARGS | METH_KEYWORDS,
	  POSIXACL_generate_inherited_acl_doc },
	{ NULL }
};

PyDoc_STRVAR(POSIXACL_doc,
"POSIX1E ACL wrapper.\n"
"\n"
"Constructed from raw little-endian xattr bytes or via from_aces().\n"
"Attributes: aces, default_aces.\n"
"Methods: access_bytes(), default_bytes().");

PyTypeObject POSIXACL_Type = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name      = "truenas_os.POSIXACL",
	.tp_basicsize = sizeof(POSIXACL_t),
	.tp_dealloc   = (destructor)POSIXACL_dealloc,
	.tp_repr      = (reprfunc)POSIXACL_repr,
	.tp_flags     = Py_TPFLAGS_DEFAULT,
	.tp_doc       = POSIXACL_doc,
	.tp_methods   = POSIXACL_methods,
	.tp_getset    = POSIXACL_getsets,
	.tp_new       = POSIXACL_new,
	.tp_init      = (initproc)POSIXACL_init,
};

/* ── public helpers used by truenas_os.c ─────────────────────────────────── */

PyObject *
POSIXACL_from_xattr_bytes(PyObject *access_data, PyObject *default_data)
{
	return PyObject_CallFunction((PyObject *)&POSIXACL_Type, "OO",
	                             access_data, default_data);
}

void
POSIXACL_get_xattr_bytes(PyObject *acl,
                          PyObject **access_out,
                          PyObject **default_out)
{
	POSIXACL_t *self = (POSIXACL_t *)acl;
	*access_out = Py_NewRef(self->access_data);
	*default_out = Py_NewRef(self->default_data);
}

/* ── validation ──────────────────────────────────────────────────────────── */

/*
 * Validate a single POSIX ACL blob (access or default).
 * Checks version, required entry counts, and MASK presence rules.
 * label is "access" or "default" for error messages.
 * Returns 0 on success, -1 with Python exception set on failure.
 */
static int
validate_posix_blob(const char *data, size_t len, const char *label)
{
	const uint8_t *p = NULL;
	const uint8_t *ace_p = NULL;
	size_t naces;
	size_t i;
	uint32_t version;
	int n_user_obj;
	int n_group_obj;
	int n_other;
	int n_mask;
	int n_named;
	uint16_t tag;
	uint32_t xid;

	if (len < POSIX_HDR_SZ) {
		PyErr_Format(PyExc_ValueError, "%s ACL too short", label);
		return -1;
	}

	p = (const uint8_t *)data;
	version = read_le32(p);
	if (version != POSIX_ACL_VERSION) {
		PyErr_Format(PyExc_ValueError,
		    "%s ACL has unexpected version %u", label, (unsigned)version);
		return -1;
	}

	naces = (len - POSIX_HDR_SZ) / POSIX_ACE_SZ;
	n_user_obj = 0;
	n_group_obj = 0;
	n_other = 0;
	n_mask = 0;
	n_named = 0;

	for (i = 0; i < naces; i++) {
		ace_p = p + POSIX_HDR_SZ + i * POSIX_ACE_SZ;
		tag = read_le16(ace_p + 0);
		xid = read_le32(ace_p + 4);

		switch (tag) {
		case POSIX_TAG_USER_OBJ:
			n_user_obj++;
			break;
		case POSIX_TAG_USER:
			if (xid == POSIX_SPECIAL_ID) {
				PyErr_Format(PyExc_ValueError,
				    "%s ACL: named USER entry has no uid", label);
				return -1;
			}
			n_named++;
			break;
		case POSIX_TAG_GROUP_OBJ:
			n_group_obj++;
			break;
		case POSIX_TAG_GROUP:
			if (xid == POSIX_SPECIAL_ID) {
				PyErr_Format(PyExc_ValueError,
				    "%s ACL: named GROUP entry has no gid", label);
				return -1;
			}
			n_named++;
			break;
		case POSIX_TAG_MASK:
			n_mask++;
			break;
		case POSIX_TAG_OTHER:
			n_other++;
			break;
		default:
			PyErr_Format(PyExc_ValueError,
			    "%s ACL: unknown tag 0x%04x", label, (unsigned)tag);
			return -1;
		}
	}

	if (n_user_obj != 1) {
		PyErr_Format(PyExc_ValueError,
		    "%s ACL must have exactly one USER_OBJ entry", label);
		return -1;
	}
	if (n_group_obj != 1) {
		PyErr_Format(PyExc_ValueError,
		    "%s ACL must have exactly one GROUP_OBJ entry", label);
		return -1;
	}
	if (n_other != 1) {
		PyErr_Format(PyExc_ValueError,
		    "%s ACL must have exactly one OTHER entry", label);
		return -1;
	}
	if (n_named > 0 && n_mask != 1) {
		PyErr_Format(PyExc_ValueError,
		    "%s ACL must have exactly one MASK entry when named "
		    "USER or GROUP entries are present", label);
		return -1;
	}
	if (n_mask > 1) {
		PyErr_Format(PyExc_ValueError,
		    "%s ACL has more than one MASK entry", label);
		return -1;
	}

	return 0;
}

int
posixacl_valid(int fd,
               const char *access_data, size_t access_len,
               const char *default_data, size_t default_len)
{
	struct stat st;

	if (validate_posix_blob(access_data, access_len, "access") < 0)
		return -1;

	if (default_data == NULL)
		return 0;

	if (fstat(fd, &st) < 0) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}
	if (!S_ISDIR(st.st_mode)) {
		PyErr_SetString(PyExc_ValueError,
		    "default ACL is only valid on directories");
		return -1;
	}

	return validate_posix_blob(default_data, default_len, "default");
}

/* ── module init ─────────────────────────────────────────────────────────── */

int
init_posixacl(PyObject *module)
{
	int err = -1;
	PyObject *enum_mod = NULL;
	PyObject *int_enum = NULL;
	PyObject *intflag = NULL;
	PyObject *kwargs = NULL;
	truenas_os_state_t *state = NULL;

	state = get_truenas_os_state(module);
	if (state == NULL)
		goto out;

	kwargs = Py_BuildValue("{s:s}", "module", "truenas_os");
	if (kwargs == NULL)
		goto out;

	enum_mod = PyImport_ImportModule("enum");
	if (enum_mod == NULL)
		goto out;

	int_enum = PyObject_GetAttrString(enum_mod, "IntEnum");
	if (int_enum == NULL)
		goto out;

	intflag = PyObject_GetAttrString(enum_mod, "IntFlag");
	if (intflag == NULL)
		goto out;

	if (add_enum(module, int_enum, "POSIXTag",
	             posix_tag_table, TABLE_SIZE(posix_tag_table),
	             kwargs, &state->POSIXTag_enum) < 0)
		goto out;

	if (add_enum(module, intflag, "POSIXPerm",
	             posix_perm_table, TABLE_SIZE(posix_perm_table),
	             kwargs, &state->POSIXPerm_enum) < 0)
		goto out;

	if (PyType_Ready(&POSIXAce_Type) < 0)
		goto out;
	if (PyModule_AddObjectRef(module, "POSIXAce",
	                          (PyObject *)&POSIXAce_Type) < 0)
		goto out;

	if (PyType_Ready(&POSIXACL_Type) < 0)
		goto out;
	if (PyModule_AddObjectRef(module, "POSIXACL",
	                          (PyObject *)&POSIXACL_Type) < 0)
		goto out;

	err = 0;
out:
	Py_XDECREF(kwargs);
	Py_XDECREF(enum_mod);
	Py_XDECREF(int_enum);
	Py_XDECREF(intflag);
	return err;
}
