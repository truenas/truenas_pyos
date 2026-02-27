// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include <endian.h>
#include <stdint.h>
#include <sys/stat.h>
#include "acl.h"
#include "truenas_os_state.h"
#include "util_enum.h"

#define NFS4_HDR_SZ     8   /* bytes: acl_flags (u32 BE) + naces (u32 BE) */
#define NFS4_ACE_SZ    20   /* bytes: type + flags + iflag + access_mask + who (each u32 BE) */
#define NFS4_HDR_WORDS  2   /* uint32_t words in header */
#define NFS4_ACE_WORDS  5   /* uint32_t words per ACE */

/* NFS4Who special-principal values (XDR encoding, matches nfs4_acl_whotype) */
#define NFS4_ACL_WHO_NAMED    0
#define NFS4_ACL_WHO_OWNER    1
#define NFS4_ACL_WHO_GROUP    2
#define NFS4_ACL_WHO_EVERYONE 3

/* ACE type values */
#define NFS4_ACE_ACCESS_ALLOWED_ACE_TYPE  0
#define NFS4_ACE_ACCESS_DENIED_ACE_TYPE   1
#define NFS4_ACE_SYSTEM_AUDIT_ACE_TYPE    2
#define NFS4_ACE_SYSTEM_ALARM_ACE_TYPE    3

/* ACE permission bits */
#define NFS4_ACE_READ_DATA            0x00000001
#define NFS4_ACE_WRITE_DATA           0x00000002
#define NFS4_ACE_APPEND_DATA          0x00000004
#define NFS4_ACE_READ_NAMED_ATTRS     0x00000008
#define NFS4_ACE_WRITE_NAMED_ATTRS    0x00000010
#define NFS4_ACE_EXECUTE              0x00000020
#define NFS4_ACE_DELETE_CHILD         0x00000040
#define NFS4_ACE_READ_ATTRIBUTES      0x00000080
#define NFS4_ACE_WRITE_ATTRIBUTES     0x00000100
#define NFS4_ACE_DELETE               0x00010000
#define NFS4_ACE_READ_ACL             0x00020000
#define NFS4_ACE_WRITE_ACL            0x00040000
#define NFS4_ACE_WRITE_OWNER          0x00080000
#define NFS4_ACE_SYNCHRONIZE          0x00100000

/* ACE flag bits */
#define NFS4_ACE_FILE_INHERIT_ACE             0x00000001
#define NFS4_ACE_DIRECTORY_INHERIT_ACE        0x00000002
#define NFS4_ACE_NO_PROPAGATE_INHERIT_ACE     0x00000004
#define NFS4_ACE_INHERIT_ONLY_ACE             0x00000008
#define NFS4_ACE_SUCCESSFUL_ACCESS_ACE_FLAG   0x00000010
#define NFS4_ACE_FAILED_ACCESS_ACE_FLAG       0x00000020
#define NFS4_ACE_IDENTIFIER_GROUP             0x00000040
#define NFS4_ACE_INHERITED_ACE                0x00000080

/* Mask covering all ACE-level inheritance flag bits */
#define NFS4_ACE_INHERIT_MASK \
	(NFS4_ACE_FILE_INHERIT_ACE | NFS4_ACE_DIRECTORY_INHERIT_ACE | \
	 NFS4_ACE_NO_PROPAGATE_INHERIT_ACE | NFS4_ACE_INHERIT_ONLY_ACE)

/* ACL-level flag bits */
#define ACL_AUTO_INHERIT  0x0001
#define ACL_PROTECTED     0x0002
#define ACL_DEFAULTED     0x0004
/* ZFS extensions stored in the on-disk acl_flags field */
#define NFS4_ACL_IS_TRIVIAL  0x10000  /* ACL is equivalent to mode bits */
#define NFS4_ACL_IS_DIR      0x20000  /* ACL belongs to a directory      */

/* ── enum member tables ─────────────────────────────────────────────────── */

static const py_intenum_tbl_t nfs4_ace_type_table[] = {
	{ "ALLOW", NFS4_ACE_ACCESS_ALLOWED_ACE_TYPE },
	{ "DENY",  NFS4_ACE_ACCESS_DENIED_ACE_TYPE  },
	{ "AUDIT", NFS4_ACE_SYSTEM_AUDIT_ACE_TYPE   },
	{ "ALARM", NFS4_ACE_SYSTEM_ALARM_ACE_TYPE   },
};

static const py_intenum_tbl_t nfs4_who_table[] = {
	{ "NAMED",    NFS4_ACL_WHO_NAMED    },
	{ "OWNER",    NFS4_ACL_WHO_OWNER    },
	{ "GROUP",    NFS4_ACL_WHO_GROUP    },
	{ "EVERYONE", NFS4_ACL_WHO_EVERYONE },
};

static const py_intenum_tbl_t nfs4_perm_table[] = {
	{ "READ_DATA",         NFS4_ACE_READ_DATA         },
	{ "WRITE_DATA",        NFS4_ACE_WRITE_DATA        },
	{ "APPEND_DATA",       NFS4_ACE_APPEND_DATA       },
	{ "READ_NAMED_ATTRS",  NFS4_ACE_READ_NAMED_ATTRS  },
	{ "WRITE_NAMED_ATTRS", NFS4_ACE_WRITE_NAMED_ATTRS },
	{ "EXECUTE",           NFS4_ACE_EXECUTE           },
	{ "DELETE_CHILD",      NFS4_ACE_DELETE_CHILD      },
	{ "READ_ATTRIBUTES",   NFS4_ACE_READ_ATTRIBUTES   },
	{ "WRITE_ATTRIBUTES",  NFS4_ACE_WRITE_ATTRIBUTES  },
	{ "DELETE",            NFS4_ACE_DELETE            },
	{ "READ_ACL",          NFS4_ACE_READ_ACL          },
	{ "WRITE_ACL",         NFS4_ACE_WRITE_ACL         },
	{ "WRITE_OWNER",       NFS4_ACE_WRITE_OWNER       },
	{ "SYNCHRONIZE",       NFS4_ACE_SYNCHRONIZE       },
};

static const py_intenum_tbl_t nfs4_flag_table[] = {
	{ "FILE_INHERIT",         NFS4_ACE_FILE_INHERIT_ACE           },
	{ "DIRECTORY_INHERIT",    NFS4_ACE_DIRECTORY_INHERIT_ACE      },
	{ "NO_PROPAGATE_INHERIT", NFS4_ACE_NO_PROPAGATE_INHERIT_ACE   },
	{ "INHERIT_ONLY",         NFS4_ACE_INHERIT_ONLY_ACE           },
	{ "SUCCESSFUL_ACCESS",    NFS4_ACE_SUCCESSFUL_ACCESS_ACE_FLAG },
	{ "FAILED_ACCESS",        NFS4_ACE_FAILED_ACCESS_ACE_FLAG     },
	{ "IDENTIFIER_GROUP",     NFS4_ACE_IDENTIFIER_GROUP           },
	{ "INHERITED",            NFS4_ACE_INHERITED_ACE              },
};

static const py_intenum_tbl_t nfs4_acl_flag_table[] = {
	{ "AUTO_INHERIT", ACL_AUTO_INHERIT },
	{ "PROTECTED",    ACL_PROTECTED    },
	{ "DEFAULTED",    ACL_DEFAULTED    },
	/* ZFS extensions stored in the on-disk acl_flags field. */
	{ "ACL_IS_TRIVIAL", NFS4_ACL_IS_TRIVIAL }, /* ACL is equivalent to mode bits */
	{ "ACL_IS_DIR",     NFS4_ACL_IS_DIR     }, /* ACL belongs to a directory      */
};

/* ═══════════════════════════════════════════════════════════════════════════
 * NFS4Ace type
 * ═════════════════════════════════════════════════════════════════════════ */

typedef struct {
	PyObject_HEAD
	PyObject *ace_type;    /* NFS4AceType enum member */
	PyObject *ace_flags;   /* NFS4Flag enum member    */
	PyObject *access_mask; /* NFS4Perm enum member    */
	PyObject *who_type;    /* NFS4Who enum member     */
	PyObject *who_id;      /* int: uid/gid or -1      */
} NFS4Ace_t;

static void
NFS4Ace_dealloc(NFS4Ace_t *self)
{
	Py_CLEAR(self->ace_type);
	Py_CLEAR(self->ace_flags);
	Py_CLEAR(self->access_mask);
	Py_CLEAR(self->who_type);
	Py_CLEAR(self->who_id);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
NFS4Ace_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	NFS4Ace_t *self = (NFS4Ace_t *)type->tp_alloc(type, 0);
	if (self == NULL)
		return NULL;

	self->ace_type = Py_NewRef(Py_None);
	self->ace_flags = Py_NewRef(Py_None);
	self->access_mask = Py_NewRef(Py_None);
	self->who_type = Py_NewRef(Py_None);
	self->who_id = PyLong_FromLong(-1);
	if (self->who_id == NULL) {
		Py_DECREF(self);
		return NULL;
	}
	return (PyObject *)self;
}

static int
NFS4Ace_init(NFS4Ace_t *self, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = {
		"ace_type", "ace_flags", "access_mask",
		"who_type", "who_id", NULL
	};
	PyObject *ace_type = NULL;
	PyObject *ace_flags = NULL;
	PyObject *access_mask = NULL;
	PyObject *who_type = NULL;
	PyObject *who_id = NULL;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOOO|O", kwlist,
	                                 &ace_type, &ace_flags, &access_mask,
	                                 &who_type, &who_id))
		return -1;

	Py_INCREF(ace_type);
	Py_SETREF(self->ace_type, ace_type);
	Py_INCREF(ace_flags);
	Py_SETREF(self->ace_flags, ace_flags);
	Py_INCREF(access_mask);
	Py_SETREF(self->access_mask, access_mask);
	Py_INCREF(who_type);
	Py_SETREF(self->who_type, who_type);

	if (who_id != NULL) {
		Py_INCREF(who_id);
		Py_SETREF(self->who_id, who_id);
	}
	return 0;
}

static PyObject *
NFS4Ace_repr(NFS4Ace_t *self)
{
	return PyUnicode_FromFormat(
	    "NFS4Ace(ace_type=%R, ace_flags=%R, access_mask=%R, "
	    "who_type=%R, who_id=%R)",
	    self->ace_type, self->ace_flags, self->access_mask,
	    self->who_type, self->who_id);
}

/* Getters */
#define MAKE_GETTER(field)                                                     \
static PyObject *NFS4Ace_get_##field(NFS4Ace_t *self, void *c)               \
{ return Py_NewRef(self->field); }

MAKE_GETTER(ace_type)
MAKE_GETTER(ace_flags)
MAKE_GETTER(access_mask)
MAKE_GETTER(who_type)
MAKE_GETTER(who_id)

static PyGetSetDef NFS4Ace_getsets[] = {
	{ "ace_type",    (getter)NFS4Ace_get_ace_type,    NULL, NULL, NULL },
	{ "ace_flags",   (getter)NFS4Ace_get_ace_flags,   NULL, NULL, NULL },
	{ "access_mask", (getter)NFS4Ace_get_access_mask, NULL, NULL, NULL },
	{ "who_type",    (getter)NFS4Ace_get_who_type,    NULL, NULL, NULL },
	{ "who_id",      (getter)NFS4Ace_get_who_id,      NULL, NULL, NULL },
	{ NULL }
};

/*
 * NFS4Ace_richcompare — Windows-compatible canonical ACL ordering.
 *
 * Windows requires ACEs in a specific order for correct access-check
 * semantics and interoperability with SMB clients.  Per Microsoft:
 *
 *   1. All explicit ACEs before any inherited ACEs.
 *   2. Within explicit ACEs: deny before allow.
 *   3. Within inherited ACEs: deny before allow.
 *
 * "Inherited" means the INHERITED flag (0x80) is set on the ACE,
 * indicating it was propagated from a parent object.  This is distinct
 * from FILE_INHERIT/DIRECTORY_INHERIT, which control whether the ACE
 * is propagated to children.
 *
 * Sort key = is_inherited * 2 + is_allow, giving four buckets:
 *   0  explicit + deny
 *   1  explicit + allow
 *   2  inherited + deny
 *   3  inherited + allow
 *
 * See http://docs.microsoft.com/en-us/windows/desktop/secauthz/order-of-aces-in-a-dacl
 */
static PyObject *
NFS4Ace_richcompare(PyObject *self, PyObject *other, int op)
{
	NFS4Ace_t *a = (NFS4Ace_t *)self;
	NFS4Ace_t *b = (NFS4Ace_t *)other;
	long type_a;
	long flags_a;
	long type_b;
	long flags_b;
	int key_a;
	int key_b;
	int cmp;

	if (!PyObject_TypeCheck(other, &NFS4Ace_Type))
		Py_RETURN_NOTIMPLEMENTED;

	type_a = PyLong_AsLong(a->ace_type);
	flags_a = PyLong_AsLong(a->ace_flags);
	type_b = PyLong_AsLong(b->ace_type);
	flags_b = PyLong_AsLong(b->ace_flags);
	if (PyErr_Occurred())
		return NULL;

	key_a = ((flags_a & NFS4_ACE_INHERITED_ACE) ? 1 : 0) * 2
	      + ((type_a == NFS4_ACE_ACCESS_ALLOWED_ACE_TYPE) ? 1 : 0);
	key_b = ((flags_b & NFS4_ACE_INHERITED_ACE) ? 1 : 0) * 2
	      + ((type_b == NFS4_ACE_ACCESS_ALLOWED_ACE_TYPE) ? 1 : 0);
	cmp = (key_a < key_b) ? -1 : (key_a > key_b) ? 1 : 0;

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

PyDoc_STRVAR(NFS4Ace_doc,
"NFS4 Access Control Entry.\n"
"\n"
"Fields: ace_type (NFS4AceType), ace_flags (NFS4Flag),\n"
"access_mask (NFS4Perm), who_type (NFS4Who), who_id (int).\n"
"who_id is the uid/gid for NAMED entries; -1 for special.");

PyTypeObject NFS4Ace_Type = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name        = "truenas_os.NFS4Ace",
	.tp_basicsize   = sizeof(NFS4Ace_t),
	.tp_dealloc     = (destructor)NFS4Ace_dealloc,
	.tp_repr        = (reprfunc)NFS4Ace_repr,
	.tp_richcompare = NFS4Ace_richcompare,
	.tp_flags       = Py_TPFLAGS_DEFAULT,
	.tp_doc         = NFS4Ace_doc,
	.tp_getset      = NFS4Ace_getsets,
	.tp_new         = NFS4Ace_new,
	.tp_init        = (initproc)NFS4Ace_init,
};

/* ═══════════════════════════════════════════════════════════════════════════
 * NFS4ACL type
 * ═════════════════════════════════════════════════════════════════════════ */

typedef struct {
	PyObject_HEAD
	PyObject *data;  /* bytes: raw big-endian XDR blob */
} NFS4ACL_t;

static void
NFS4ACL_dealloc(NFS4ACL_t *self)
{
	Py_CLEAR(self->data);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
NFS4ACL_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	NFS4ACL_t *self = (NFS4ACL_t *)type->tp_alloc(type, 0);
	if (self == NULL)
		return NULL;
	self->data = PyBytes_FromStringAndSize("", 0);
	if (self->data == NULL) {
		Py_DECREF(self);
		return NULL;
	}
	return (PyObject *)self;
}

static int
NFS4ACL_init(NFS4ACL_t *self, PyObject *args, PyObject *kwargs)
{
	PyObject *data = NULL;
	if (!PyArg_ParseTuple(args, "O!:NFS4ACL", &PyBytes_Type, &data))
		return -1;
	Py_INCREF(data);
	Py_SETREF(self->data, data);
	return 0;
}

PyDoc_STRVAR(NFS4ACL_from_aces_doc,
"from_aces(aces, acl_flags=NFS4ACLFlag(0))\n"
"\n"
"Construct an NFS4ACL by packing a list of NFS4Ace objects into XDR bytes.\n"
"acl_flags is written into the 4-byte XDR header.");

/*
 * Sort aces_arg into MS canonical order, allocate the XDR ACE buffer, and
 * return the fast sequence, buffer pointer, end pointer, and count via
 * out-parameters.  On success returns 0; caller owns *aces_seq_out and
 * *buf_out.  On failure returns -1 with a Python exception set.
 */
static int
nfs4acl_alloc_ace_buf(PyObject *aces_arg,
                      PyObject **aces_seq_out,
                      uint32_t **buf_out,
                      uint32_t **endp_out,
                      Py_ssize_t *naces_out)
{
	PyObject *aces_list = NULL;
	PyObject *aces_seq = NULL;
	Py_ssize_t naces;
	size_t nwords;
	uint32_t *buf = NULL;

	aces_list = PySequence_List(aces_arg);
	if (aces_list == NULL)
		return -1;

	if (PyList_Sort(aces_list) < 0) {
		Py_DECREF(aces_list);
		return -1;
	}

	aces_seq = PySequence_Fast(aces_list, "from_aces: aces must be iterable");
	Py_DECREF(aces_list);
	if (aces_seq == NULL)
		return -1;

	naces = PySequence_Fast_GET_SIZE(aces_seq);
	nwords = NFS4_HDR_WORDS + (size_t)naces * NFS4_ACE_WORDS;
	buf = (uint32_t *)PyMem_Malloc(nwords * sizeof(uint32_t));
	if (buf == NULL) {
		Py_DECREF(aces_seq);
		PyErr_NoMemory();
		return -1;
	}

	*aces_seq_out = aces_seq;
	*buf_out = buf;
	*endp_out = buf + nwords;
	*naces_out = naces;
	return 0;
}

/*
 * Write one 20-byte XDR ACE slot at p from raw uint32 field values.
 * Shared by nfs4ace_encode() and NFS4ACL_generate_inherited_acl().
 */
static void
nfs4ace_write_raw(uint32_t *p, uint32_t ace_type, uint32_t ace_flags,
                  uint32_t ace_iflag, uint32_t access_mask, uint32_t who)
{
	p[0] = htobe32(ace_type);
	p[1] = htobe32(ace_flags);
	p[2] = htobe32(ace_iflag);
	p[3] = htobe32(access_mask);
	p[4] = htobe32(who);
}

/*
 * Encode one NFS4Ace into the 20-byte XDR slot at p.
 * endp is one past the end of the allocated buffer; the check guards
 * against a buffer overrun if naces and the allocation ever diverge.
 * Returns 0 on success, -1 with a Python exception set on failure.
 */
static int
nfs4ace_encode(const NFS4Ace_t *a, uint32_t *p, const uint32_t *endp)
{
	long ace_type_v;
	long ace_flags_v;
	long access_mask_v;
	long who_type_v;
	long who_id_v;
	uint32_t iflag;
	uint32_t who;

	if ((size_t)(endp - p) < NFS4_ACE_WORDS) {
		PyErr_SetString(PyExc_RuntimeError,
		                "nfs4ace_encode: write would overrun ACE buffer");
		return -1;
	}

	ace_type_v = PyLong_AsLong(a->ace_type);
	ace_flags_v = PyLong_AsLong(a->ace_flags);
	access_mask_v = PyLong_AsLong(a->access_mask);
	who_type_v = PyLong_AsLong(a->who_type);
	who_id_v = PyLong_AsLong(a->who_id);

	if (PyErr_Occurred())
		return -1;

	if (who_type_v == NFS4_ACL_WHO_NAMED) {
		iflag = 0;
		who = (uint32_t)who_id_v;
	} else {
		iflag = 1;
		who = (uint32_t)who_type_v; /* 1=OWNER, 2=GROUP, 3=EVERYONE */
	}

	nfs4ace_write_raw(p, (uint32_t)ace_type_v, (uint32_t)ace_flags_v,
	                  iflag, (uint32_t)access_mask_v, who);
	return 0;
}

/* NFS4ACL.from_aces(aces, acl_flags=NFS4ACLFlag(0)) classmethod */
static PyObject *
NFS4ACL_from_aces(PyObject *cls, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = { "aces", "acl_flags", NULL };
	PyObject *aces_arg = NULL;
	PyObject *acl_flags_obj = NULL;
	PyObject *aces_seq = NULL;
	PyObject *ace = NULL;
	PyObject *bytes_obj = NULL;
	PyObject *result = NULL;
	Py_ssize_t naces;
	Py_ssize_t i;
	uint32_t *buf = NULL;
	uint32_t *p = NULL;
	uint32_t *endp = NULL;
	uint32_t acl_flags_val;
	long v;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|O", kwlist,
	                                 &aces_arg, &acl_flags_obj))
		return NULL;

	if (nfs4acl_alloc_ace_buf(aces_arg, &aces_seq, &buf, &endp, &naces) < 0)
		return NULL;

	acl_flags_val = 0;
	if (acl_flags_obj != NULL && acl_flags_obj != Py_None) {
		v = PyLong_AsLong(acl_flags_obj);
		if (v == -1 && PyErr_Occurred()) {
			PyMem_Free(buf);
			Py_DECREF(aces_seq);
			return NULL;
		}
		acl_flags_val = (uint32_t)v;
	}

	buf[0] = htobe32(acl_flags_val);
	buf[1] = htobe32((uint32_t)naces);

	for (i = 0; i < naces; i++) {
		ace = PySequence_Fast_GET_ITEM(aces_seq, i); /* borrowed */

		if (!PyObject_TypeCheck(ace, &NFS4Ace_Type)) {
			PyErr_SetString(PyExc_TypeError,
			                "from_aces: aces must contain NFS4Ace objects");
			PyMem_Free(buf);
			Py_DECREF(aces_seq);
			return NULL;
		}

		p = buf + NFS4_HDR_WORDS + (size_t)i * NFS4_ACE_WORDS;
		if (nfs4ace_encode((NFS4Ace_t *)ace, p, endp) < 0) {
			PyMem_Free(buf);
			Py_DECREF(aces_seq);
			return NULL;
		}
	}

	Py_DECREF(aces_seq);

	bytes_obj = PyBytes_FromStringAndSize((char *)buf,
	    (Py_ssize_t)((endp - buf) * sizeof(uint32_t)));
	PyMem_Free(buf);
	if (bytes_obj == NULL)
		return NULL;

	result = PyObject_CallOneArg(cls, bytes_obj);
	Py_DECREF(bytes_obj);
	return result;
}

PyDoc_STRVAR(NFS4ACL_acl_flags_doc,
"NFS4ACLFlag: ACL-level flags from the XDR header.");

/* NFS4ACL.acl_flags property */
static PyObject *
NFS4ACL_get_acl_flags(NFS4ACL_t *self, void *closure)
{
	const uint32_t *p = NULL;
	uint32_t flags;
	truenas_os_state_t *state = NULL;
	PyObject *tmp = NULL;
	PyObject *result = NULL;

	if (PyBytes_GET_SIZE(self->data) < NFS4_HDR_SZ) {
		PyErr_SetString(PyExc_ValueError, "NFS4ACL data too short");
		return NULL;
	}
	p = (const uint32_t *)PyBytes_AS_STRING(self->data);
	flags = be32toh(p[0]);

	state = get_truenas_os_state(NULL);
	if (state == NULL)
		return NULL;

	tmp = PyLong_FromUnsignedLong(flags);
	if (tmp == NULL)
		return NULL;
	result = PyObject_CallOneArg(state->NFS4ACLFlag_enum, tmp);
	Py_DECREF(tmp);
	return result;
}

PyDoc_STRVAR(NFS4ACL_aces_doc,
"list[NFS4Ace]: parsed list of access control entries.");

/*
 * Decode one 20-byte XDR ACE slot at p into a new NFS4Ace object.
 * Returns a new reference, or NULL with a Python exception set on failure.
 */
static PyObject *
nfs4ace_decode(const uint32_t *p, truenas_os_state_t *state)
{
	PyObject *tmp = NULL;
	PyObject *ace_type_o = NULL;
	PyObject *ace_flags_o = NULL;
	PyObject *access_mask_o = NULL;
	PyObject *who_type_o = NULL;
	PyObject *who_id_o = NULL;
	PyObject *ace = NULL;
	uint32_t ace_type_v;
	uint32_t ace_flags_v;
	uint32_t iflag;
	uint32_t access_mask_v;
	uint32_t who_raw;
	uint32_t who_type_v;
	long who_id_v;

	ace_type_v = be32toh(p[0]);
	ace_flags_v = be32toh(p[1]);
	iflag = be32toh(p[2]);
	access_mask_v = be32toh(p[3]);
	who_raw = be32toh(p[4]);

	/* who_type: NFS4_ACL_WHO_NAMED if iflag==0, else 1/2/3 special */
	who_type_v = iflag ? who_raw : NFS4_ACL_WHO_NAMED;
	who_id_v = iflag ? -1L : (long)who_raw;

#define CALL_ENUM(e, v) PyObject_CallOneArg((e), tmp = PyLong_FromUnsignedLong(v))

	ace_type_o = CALL_ENUM(state->NFS4AceType_enum, ace_type_v);
	Py_XDECREF(tmp);
	ace_flags_o = CALL_ENUM(state->NFS4Flag_enum, ace_flags_v);
	Py_XDECREF(tmp);
	access_mask_o = CALL_ENUM(state->NFS4Perm_enum, access_mask_v);
	Py_XDECREF(tmp);
	who_type_o = CALL_ENUM(state->NFS4Who_enum, who_type_v);
	Py_XDECREF(tmp);
	who_id_o = PyLong_FromLong(who_id_v);

#undef CALL_ENUM

	if (!ace_type_o || !ace_flags_o || !access_mask_o ||
	    !who_type_o || !who_id_o) {
		Py_XDECREF(ace_type_o);
		Py_XDECREF(ace_flags_o);
		Py_XDECREF(access_mask_o);
		Py_XDECREF(who_type_o);
		Py_XDECREF(who_id_o);
		return NULL;
	}

	ace = PyObject_CallFunction(
	    (PyObject *)&NFS4Ace_Type, "OOOOO",
	    ace_type_o, ace_flags_o, access_mask_o, who_type_o, who_id_o);
	Py_DECREF(ace_type_o);
	Py_DECREF(ace_flags_o);
	Py_DECREF(access_mask_o);
	Py_DECREF(who_type_o);
	Py_DECREF(who_id_o);
	return ace;
}

/* NFS4ACL.aces property */
static PyObject *
NFS4ACL_get_aces(NFS4ACL_t *self, void *closure)
{
	const uint32_t *buf = NULL;
	truenas_os_state_t *state = NULL;
	PyObject *result = NULL;
	PyObject *ace = NULL;
	Py_ssize_t datasz;
	uint32_t naces;
	uint32_t i;

	datasz = PyBytes_GET_SIZE(self->data);
	if (datasz < NFS4_HDR_SZ) {
		PyErr_SetString(PyExc_ValueError, "NFS4ACL data too short");
		return NULL;
	}

	buf = (const uint32_t *)PyBytes_AS_STRING(self->data);
	naces = be32toh(buf[1]);

	if ((Py_ssize_t)(NFS4_HDR_SZ + (size_t)naces * NFS4_ACE_SZ) > datasz) {
		PyErr_SetString(PyExc_ValueError, "NFS4ACL data truncated");
		return NULL;
	}

	state = get_truenas_os_state(NULL);
	if (state == NULL)
		return NULL;

	result = PyList_New((Py_ssize_t)naces);
	if (result == NULL)
		return NULL;

	for (i = 0; i < naces; i++) {
		ace = nfs4ace_decode(buf + NFS4_HDR_WORDS + (size_t)i * NFS4_ACE_WORDS,
		                     state);
		if (ace == NULL) {
			Py_DECREF(result);
			return NULL;
		}
		PyList_SET_ITEM(result, (Py_ssize_t)i, ace); /* steals ref */
	}
	return result;
}

PyDoc_STRVAR(NFS4ACL_bytes_doc,
"Return the raw XDR bytes.");

/* NFS4ACL.__bytes__ */
static PyObject *
NFS4ACL_bytes(NFS4ACL_t *self, PyObject *Py_UNUSED(args))
{
	return Py_NewRef(self->data);
}

/* NFS4ACL.__len__ */
static Py_ssize_t
NFS4ACL_len(NFS4ACL_t *self)
{
	const uint32_t *p = NULL;

	if (PyBytes_GET_SIZE(self->data) < NFS4_HDR_SZ)
		return 0;
	p = (const uint32_t *)PyBytes_AS_STRING(self->data);
	return (Py_ssize_t)be32toh(p[1]);
}

/* NFS4ACL.__repr__ */
static PyObject *
NFS4ACL_repr(NFS4ACL_t *self)
{
	PyObject *flags = NULL;
	PyObject *aces = NULL;
	PyObject *result = NULL;

	flags = NFS4ACL_get_acl_flags(self, NULL);
	aces = NFS4ACL_get_aces(self, NULL);
	if (!flags || !aces) {
		Py_XDECREF(flags);
		Py_XDECREF(aces);
		return NULL;
	}
	result = PyUnicode_FromFormat("NFS4ACL(flags=%R, aces=%R)", flags, aces);
	Py_DECREF(flags);
	Py_DECREF(aces);
	return result;
}

PyDoc_STRVAR(NFS4ACL_trivial_doc,
"bool: True if ACL_IS_TRIVIAL is set in acl_flags "
"(ACL is equivalent to mode bits).");

/* NFS4ACL.trivial property */
static PyObject *
NFS4ACL_get_trivial(NFS4ACL_t *self, void *closure)
{
	const uint32_t *p = NULL;
	uint32_t flags;

	if (PyBytes_GET_SIZE(self->data) < NFS4_HDR_SZ)
		Py_RETURN_TRUE;

	p = (const uint32_t *)PyBytes_AS_STRING(self->data);
	flags = be32toh(p[0]);
	return PyBool_FromLong((flags & NFS4_ACL_IS_TRIVIAL) != 0);
}

static PyGetSetDef NFS4ACL_getsets[] = {
	{ "acl_flags", (getter)NFS4ACL_get_acl_flags, NULL, NFS4ACL_acl_flags_doc, NULL },
	{ "aces",      (getter)NFS4ACL_get_aces,       NULL, NFS4ACL_aces_doc,      NULL },
	{ "trivial",   (getter)NFS4ACL_get_trivial,    NULL, NFS4ACL_trivial_doc,   NULL },
	{ NULL }
};

PyDoc_STRVAR(NFS4ACL_generate_inherited_acl_doc,
"generate_inherited_acl(is_dir=False)\n"
"\n"
"Apply NFS4 ACE inheritance rules to produce the ACL for a new child\n"
"object.  For a file child (is_dir=False) only ACEs with FILE_INHERIT\n"
"are included; for a directory child (is_dir=True) ACEs with\n"
"FILE_INHERIT or DIRECTORY_INHERIT are included.  In both cases all\n"
"inherit flags are cleared and INHERITED is set; for a directory child\n"
"without NO_PROPAGATE_INHERIT, FILE_INHERIT and DIRECTORY_INHERIT are\n"
"kept so the ACE propagates to grandchildren.\n"
"\n"
"Raises ValueError if no ACEs would be inherited.");

/*
 * Return non-zero if an ACE with ace_flags should be inherited by a child
 * of the given type: directories match FILE_INHERIT or DIRECTORY_INHERIT;
 * files match only FILE_INHERIT.
 */
static int
ace_is_inheritable(uint32_t ace_flags, int is_dir)
{
	if (is_dir)
		return (ace_flags &
		    (NFS4_ACE_FILE_INHERIT_ACE | NFS4_ACE_DIRECTORY_INHERIT_ACE)) != 0;
	return (ace_flags & NFS4_ACE_FILE_INHERIT_ACE) != 0;
}

/*
 * NFS4ACL.generate_inherited_acl(is_dir=False)
 *
 * Apply NFS4 ACE inheritance rules to produce the ACL for a new child object.
 *
 * For a file child (is_dir=False):
 *   Include each ACE whose FILE_INHERIT flag is set.
 *   New ace_flags: clear all inherit bits, set INHERITED.
 *
 * For a directory child (is_dir=True):
 *   Include each ACE with FILE_INHERIT or DIRECTORY_INHERIT set.
 *   If NO_PROPAGATE_INHERIT is set:
 *     clear all inherit bits, set INHERITED (no further propagation).
 *   Else:
 *     clear INHERIT_ONLY (ACE now applies to this directory), keep
 *     FILE_INHERIT / DIRECTORY_INHERIT for further propagation, set INHERITED.
 *
 * Raises ValueError if no ACEs would be inherited.
 */
static PyObject *
NFS4ACL_generate_inherited_acl(NFS4ACL_t *self, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = { "is_dir", NULL };
	const uint32_t *buf = NULL;
	const uint32_t *ace_p = NULL;
	uint32_t *outbuf = NULL;
	uint32_t *outp = NULL;
	Py_ssize_t datasz;
	uint32_t naces_in;
	uint32_t naces_out;
	uint32_t i;
	uint32_t ace_type;
	uint32_t ace_flags;
	uint32_t ace_iflag;
	uint32_t access_mask;
	uint32_t who;
	uint32_t new_flags;
	uint32_t out_acl_flags;
	size_t nwords;
	int is_dir;
	PyObject *bytes_obj = NULL;
	PyObject *result = NULL;

	is_dir = 0;
	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p", kwlist, &is_dir))
		return NULL;

	datasz = PyBytes_GET_SIZE(self->data);
	if (datasz < NFS4_HDR_SZ) {
		PyErr_SetString(PyExc_ValueError,
		    "cannot generate inherited ACL: source ACL is empty");
		return NULL;
	}

	buf = (const uint32_t *)PyBytes_AS_STRING(self->data);
	naces_in = be32toh(buf[1]);

	/* First pass: count output ACEs. */
	naces_out = 0;
	for (i = 0; i < naces_in; i++) {
		if (NFS4_HDR_SZ + (size_t)(i + 1) * NFS4_ACE_SZ > (size_t)datasz)
			break;
		ace_p = buf + NFS4_HDR_WORDS + (size_t)i * NFS4_ACE_WORDS;
		ace_flags = be32toh(ace_p[1]);
		if (ace_is_inheritable(ace_flags, is_dir))
			naces_out++;
	}

	if (naces_out == 0) {
		PyErr_SetString(PyExc_ValueError,
		    "parent ACL has no inheritable ACEs for this object type");
		return NULL;
	}

	nwords = NFS4_HDR_WORDS + (size_t)naces_out * NFS4_ACE_WORDS;
	outbuf = (uint32_t *)PyMem_Malloc(nwords * sizeof(uint32_t));
	if (outbuf == NULL)
		return PyErr_NoMemory();

	out_acl_flags = is_dir ? NFS4_ACL_IS_DIR : 0;
	outbuf[0] = htobe32(out_acl_flags);
	outbuf[1] = htobe32(naces_out);

	/* Second pass: write inherited ACEs. */
	outp = outbuf + NFS4_HDR_WORDS;
	for (i = 0; i < naces_in; i++) {
		if (NFS4_HDR_SZ + (size_t)(i + 1) * NFS4_ACE_SZ > (size_t)datasz)
			break;
		ace_p = buf + NFS4_HDR_WORDS + (size_t)i * NFS4_ACE_WORDS;
		ace_type = be32toh(ace_p[0]);
		ace_flags = be32toh(ace_p[1]);
		ace_iflag = be32toh(ace_p[2]);
		access_mask = be32toh(ace_p[3]);
		who = be32toh(ace_p[4]);

		if (!ace_is_inheritable(ace_flags, is_dir))
			continue;

		if (is_dir && !(ace_flags & NFS4_ACE_NO_PROPAGATE_INHERIT_ACE)) {
			/*
			 * Directory child, propagation not suppressed:
			 * keep FILE/DIR_INHERIT for further propagation,
			 * clear INHERIT_ONLY so the ACE applies to this dir.
			 */
			new_flags = (ace_flags & ~NFS4_ACE_INHERIT_ONLY_ACE)
			          | NFS4_ACE_INHERITED_ACE;
		} else {
			/*
			 * File child, or directory with NO_PROPAGATE:
			 * strip all inheritance flags.
			 */
			new_flags = (ace_flags & ~NFS4_ACE_INHERIT_MASK)
			          | NFS4_ACE_INHERITED_ACE;
		}

		nfs4ace_write_raw(outp, ace_type, new_flags,
		                  ace_iflag, access_mask, who);
		outp += NFS4_ACE_WORDS;
	}

	bytes_obj = PyBytes_FromStringAndSize((char *)outbuf,
	    (Py_ssize_t)(nwords * sizeof(uint32_t)));
	PyMem_Free(outbuf);
	if (bytes_obj == NULL)
		return NULL;

	result = PyObject_CallOneArg((PyObject *)&NFS4ACL_Type, bytes_obj);
	Py_DECREF(bytes_obj);
	return result;
}

static PyMethodDef NFS4ACL_methods[] = {
	{ "from_aces",
	  (PyCFunction)NFS4ACL_from_aces,
	  METH_CLASS | METH_VARARGS | METH_KEYWORDS,
	  NFS4ACL_from_aces_doc },
	{ "__bytes__",
	  (PyCFunction)NFS4ACL_bytes,
	  METH_NOARGS,
	  NFS4ACL_bytes_doc },
	{ "generate_inherited_acl",
	  (PyCFunction)NFS4ACL_generate_inherited_acl,
	  METH_VARARGS | METH_KEYWORDS,
	  NFS4ACL_generate_inherited_acl_doc },
	{ NULL }
};

static PySequenceMethods NFS4ACL_as_seq = {
	.sq_length = (lenfunc)NFS4ACL_len,
};

PyDoc_STRVAR(NFS4ACL_doc,
"NFS4 ACL wrapper (system.nfs4_acl_xdr).\n"
"\n"
"Constructed from raw big-endian XDR bytes or via from_aces().\n"
"Attributes: acl_flags, aces.\n"
"Supports bytes() and len().");

PyTypeObject NFS4ACL_Type = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name        = "truenas_os.NFS4ACL",
	.tp_basicsize   = sizeof(NFS4ACL_t),
	.tp_dealloc     = (destructor)NFS4ACL_dealloc,
	.tp_repr        = (reprfunc)NFS4ACL_repr,
	.tp_as_sequence = &NFS4ACL_as_seq,
	.tp_flags       = Py_TPFLAGS_DEFAULT,
	.tp_doc         = NFS4ACL_doc,
	.tp_methods     = NFS4ACL_methods,
	.tp_getset      = NFS4ACL_getsets,
	.tp_new         = NFS4ACL_new,
	.tp_init        = (initproc)NFS4ACL_init,
};

/* ── public helpers used by truenas_os.c ─────────────────────────────────── */

PyObject *
NFS4ACL_from_xattr_bytes(PyObject *data)
{
	return PyObject_CallOneArg((PyObject *)&NFS4ACL_Type, data);
}

PyObject *
NFS4ACL_get_xattr_bytes(PyObject *acl)
{
	NFS4ACL_t *self = (NFS4ACL_t *)acl;
	return Py_NewRef(self->data);
}

/* ── module init ─────────────────────────────────────────────────────────── */

/*
 * Inheritance-propagation flags — only valid on directory ACLs.
 * INHERIT_ONLY is additionally only meaningful when paired with
 * FILE_INHERIT or DIRECTORY_INHERIT.
 *
 * NB: FILE_INHERIT and DIR_INHERIT are bits inside NFS4_PROPAGATE_MASK,
 * so has_inheritable=1 always implies has_propagate=1.
 */
#define NFS4_PROPAGATE_MASK \
	(NFS4_ACE_FILE_INHERIT_ACE | NFS4_ACE_DIRECTORY_INHERIT_ACE | \
	 NFS4_ACE_NO_PROPAGATE_INHERIT_ACE | NFS4_ACE_INHERIT_ONLY_ACE)

#define NFS4_IFLAG_SPECIAL   1U   /* iflag=1: special who (OWNER/GROUP/EVERYONE) */

int
nfs4acl_valid(int fd, const char *data, size_t len)
{
	struct stat st;
	const uint32_t *p = NULL;
	const uint32_t *ace_p = NULL;
	uint32_t naces;
	uint32_t i;
	uint32_t ace_type;
	uint32_t ace_flags;
	uint32_t ace_iflag;
	int has_propagate;
	int has_inheritable;
	int is_dir;

	if (len < NFS4_HDR_SZ)
		return 0;

	p = (const uint32_t *)data;
	naces = be32toh(p[1]);
	has_propagate = 0;
	has_inheritable = 0;

	for (i = 0; i < naces; i++) {
		if (NFS4_HDR_SZ + (size_t)(i + 1) * NFS4_ACE_SZ > len)
			break;

		ace_p = p + NFS4_HDR_WORDS + (size_t)i * NFS4_ACE_WORDS;
		ace_type = be32toh(ace_p[0]);
		ace_flags = be32toh(ace_p[1]);
		ace_iflag = be32toh(ace_p[2]);

		/* DENY is not permitted for special principals. */
		if (ace_type == NFS4_ACE_ACCESS_DENIED_ACE_TYPE && ace_iflag == NFS4_IFLAG_SPECIAL) {
			PyErr_SetString(PyExc_ValueError,
			    "DENY entries are not permitted for special "
			    "principals (OWNER@, GROUP@, EVERYONE@)");
			return -1;
		}

		/* INHERIT_ONLY requires FILE_INHERIT or DIRECTORY_INHERIT. */
		if ((ace_flags & NFS4_ACE_INHERIT_ONLY_ACE) &&
		    !(ace_flags & (NFS4_ACE_FILE_INHERIT_ACE | NFS4_ACE_DIRECTORY_INHERIT_ACE))) {
			PyErr_SetString(PyExc_ValueError,
			    "INHERIT_ONLY requires FILE_INHERIT or "
			    "DIRECTORY_INHERIT to also be set");
			return -1;
		}

		if (ace_flags & NFS4_PROPAGATE_MASK)
			has_propagate = 1;
		if (ace_flags & (NFS4_ACE_FILE_INHERIT_ACE | NFS4_ACE_DIRECTORY_INHERIT_ACE))
			has_inheritable = 1;
	}

	if (fd == -1) {
		is_dir = 1;
	} else {
		if (fstat(fd, &st) < 0) {
			PyErr_SetFromErrno(PyExc_OSError);
			return -1;
		}
		is_dir = S_ISDIR(st.st_mode);
	}

	/* Propagation flags are only valid on directories. */
	if (has_propagate && !is_dir) {
		PyErr_SetString(PyExc_ValueError,
		    "FILE_INHERIT/DIRECTORY_INHERIT/NO_PROPAGATE_INHERIT/"
		    "INHERIT_ONLY flags are only valid on directories");
		return -1;
	}
	/* A directory ACL must have at least one inheritable ACE. */
	if (is_dir && !has_inheritable) {
		PyErr_SetString(PyExc_ValueError,
		    "directory ACL must contain at least one ACE with "
		    "FILE_INHERIT or DIRECTORY_INHERIT");
		return -1;
	}
	return 0;
}

int
init_nfs4acl(PyObject *module)
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

	if (add_enum(module, int_enum, "NFS4AceType",
	             nfs4_ace_type_table, TABLE_SIZE(nfs4_ace_type_table),
	             kwargs, &state->NFS4AceType_enum) < 0)
		goto out;

	if (add_enum(module, int_enum, "NFS4Who",
	             nfs4_who_table, TABLE_SIZE(nfs4_who_table),
	             kwargs, &state->NFS4Who_enum) < 0)
		goto out;

	if (add_enum(module, intflag, "NFS4Perm",
	             nfs4_perm_table, TABLE_SIZE(nfs4_perm_table),
	             kwargs, &state->NFS4Perm_enum) < 0)
		goto out;

	if (add_enum(module, intflag, "NFS4Flag",
	             nfs4_flag_table, TABLE_SIZE(nfs4_flag_table),
	             kwargs, &state->NFS4Flag_enum) < 0)
		goto out;

	if (add_enum(module, intflag, "NFS4ACLFlag",
	             nfs4_acl_flag_table, TABLE_SIZE(nfs4_acl_flag_table),
	             kwargs, &state->NFS4ACLFlag_enum) < 0)
		goto out;

	/* Register NFS4Ace type */
	if (PyType_Ready(&NFS4Ace_Type) < 0)
		goto out;
	if (PyModule_AddObjectRef(module, "NFS4Ace",
	                          (PyObject *)&NFS4Ace_Type) < 0)
		goto out;

	/* Register NFS4ACL type */
	if (PyType_Ready(&NFS4ACL_Type) < 0)
		goto out;
	if (PyModule_AddObjectRef(module, "NFS4ACL",
	                          (PyObject *)&NFS4ACL_Type) < 0)
		goto out;

	err = 0;
out:
	Py_XDECREF(kwargs);
	Py_XDECREF(enum_mod);
	Py_XDECREF(int_enum);
	Py_XDECREF(intflag);
	return err;
}
