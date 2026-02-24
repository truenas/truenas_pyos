// SPDX-License-Identifier: LGPL-3.0-or-later
#pragma once
#include <Python.h>
#include <stddef.h>

/* ── ACL type discriminator ──────────────────────────────────────────────── */

typedef enum {
	ACLTYPE_NFS4  = 0,
	ACLTYPE_POSIX = 1,
} acltype_t;

/*
 * acl_xattr_t -- type-tagged union of raw xattr buffers.
 *
 * Filled by do_fgetacl().  Must be released with acl_xattr_free() on
 * success; on failure the struct is undefined and must not be freed.
 * All data pointers are malloc(3)-allocated; NULL means absent/empty.
 */
typedef struct {
	acltype_t type;
	union {
		struct {
			char   *data;
			size_t  len;
		} nfs4;
		struct {
			char   *access_data;
			size_t  access_len;
			char   *default_data;  /* NULL = no default ACL */
			size_t  default_len;
		} posix;
	} data;
} acl_xattr_t;

/* Release all buffers inside acl (does not free acl itself). */
void acl_xattr_free(acl_xattr_t *acl);

/* ── xattr operations (acl.c) ─────────────────────────────────────────────── */

/*
 * do_fgetacl: fill *out with the ACL xattr(s) from fd.
 * Returns 0 on success, -1 on failure (Python exception set).
 */
int do_fgetacl(int fd, acl_xattr_t *out);

/*
 * do_fsetacl_nfs4: write system.nfs4_acl_xdr on fd.
 * Returns 0 on success, -1 on failure (Python exception set).
 */
int do_fsetacl_nfs4(int fd, const char *data, size_t len);

/*
 * do_fsetacl_posix: write POSIX ACL xattrs on fd.
 * default_data == NULL removes the default ACL xattr (ENODATA silently ignored).
 * Returns 0 on success, -1 on failure (Python exception set).
 */
int do_fsetacl_posix(int fd,
                     const char *access_data, size_t access_len,
                     const char *default_data, size_t default_len);

/*
 * do_fremoveacl: remove all ACL xattr(s) from fd.
 * Probes for NFS4 vs POSIX the same way do_fgetacl() does.
 * ENODATA on any individual xattr is silently ignored.
 * Returns 0 on success, -1 on failure (Python exception set).
 */
int do_fremoveacl(int fd);

/* ── NFS4 types (nfs4acl.c) ──────────────────────────────────────────────── */

extern PyTypeObject NFS4Ace_Type;
extern PyTypeObject NFS4ACL_Type;

PyObject *NFS4ACL_from_xattr_bytes(PyObject *data);
PyObject *NFS4ACL_get_xattr_bytes(PyObject *acl);

int init_nfs4acl(PyObject *module);

/*
 * nfs4acl_valid: reject ACLs containing FILE_INHERIT / DIRECTORY_INHERIT /
 * NO_PROPAGATE_INHERIT / INHERIT_ONLY flags when fd is not a directory.
 * Returns 0 on success, -1 on failure (Python ValueError set).
 */
int nfs4acl_valid(int fd, const char *data, size_t len);

/* ── POSIX types (posixacl.c) ────────────────────────────────────────────── */

extern PyTypeObject POSIXAce_Type;
extern PyTypeObject POSIXACL_Type;

PyObject *POSIXACL_from_xattr_bytes(PyObject *access_data, PyObject *default_data);

void POSIXACL_get_xattr_bytes(PyObject *acl,
                               PyObject **access_out,
                               PyObject **default_out);

int init_posixacl(PyObject *module);

/*
 * posixacl_valid: reject a non-NULL default ACL when fd is not a directory.
 * Pass default_data=NULL when there is no default ACL (always valid).
 * Returns 0 on success, -1 on failure (Python ValueError set).
 */
int posixacl_valid(int fd,
                   const char *access_data, size_t access_len,
                   const char *default_data, size_t default_len);
