// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include <endian.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include "acl.h"

#define NFS4_ACL_XATTR      "system.nfs4_acl_xdr"
#define POSIX_ACCESS_XATTR  "system.posix_acl_access"
#define POSIX_DEFAULT_XATTR "system.posix_acl_default"


/*
 * Read an xattr into a malloc'd buffer.
 * sz must be the size returned by a prior fgetxattr probe (> 0).
 * Returns the buffer (caller frees), or NULL with exception set.
 */
static char *
read_xattr_raw(int fd, const char *name, ssize_t sz, size_t *out_len)
{
	char *buf = NULL;
	ssize_t ret;
	int async_err = 0;

	buf = PyMem_RawMalloc((size_t)sz);
	if (buf == NULL) {
		PyErr_NoMemory();
		return NULL;
	}

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = fgetxattr(fd, name, buf, (size_t)sz);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		PyMem_RawFree(buf);
		if (!async_err)
			PyErr_SetFromErrno(PyExc_OSError);
		return NULL;
	}

	*out_len = (size_t)ret;
	return buf;
}

void
acl_xattr_free(acl_xattr_t *acl)
{
	if (acl->type == ACLTYPE_NFS4) {
		PyMem_RawFree(acl->data.nfs4.data);
	} else {
		PyMem_RawFree(acl->data.posix.access_data);
		PyMem_RawFree(acl->data.posix.default_data);
	}
}

/*
 * synthesize_posix_access_from_mode -- build a minimal 3-entry POSIX ACL
 * blob from inode mode bits, matching getfacl(1) behaviour when the
 * system.posix_acl_access xattr is absent (ENODATA).
 *
 * Format: 4-byte LE version header (= 2) followed by three 8-byte ACEs:
 *   [tag u16 LE | perm u16 LE | id u32 LE]
 * Tags: USER_OBJ=0x0001, GROUP_OBJ=0x0004, OTHER=0x0020.
 * ID:   0xFFFFFFFF (POSIX_SPECIAL_ID) for all three.
 *
 * Returns a PyMem_RawMalloc'd buffer (caller frees), or NULL with OOM set.
 */
#define POSIX_TRIVIAL_ACL_SIZE (4 + 3 * 8)   /* header + 3 × 8-byte ACE */

static char *
synthesize_posix_access_from_mode(mode_t mode, size_t *out_len)
{
	uint8_t *buf;
	uint32_t hdr;
	size_t i;

	/* { tag, perm } for USER_OBJ / GROUP_OBJ / OTHER */
	static const uint16_t tags[3] = { 0x0001, 0x0004, 0x0020 };
	unsigned perms[3];
	uint16_t v16;
	uint32_t v32;

	buf = (uint8_t *)PyMem_RawMalloc(POSIX_TRIVIAL_ACL_SIZE);
	if (buf == NULL) {
		PyErr_NoMemory();
		return NULL;
	}

	/* Header: version = 2 (little-endian) */
	hdr = htole32(2);
	memcpy(buf, &hdr, 4);

	perms[0] = (mode >> 6) & 7;   /* user  bits */
	perms[1] = (mode >> 3) & 7;   /* group bits */
	perms[2] =  mode       & 7;   /* other bits */

	for (i = 0; i < 3; i++) {
		uint8_t *p = buf + 4 + i * 8;
		v16 = htole16(tags[i]);   memcpy(p + 0, &v16, 2);
		v16 = htole16(perms[i]);  memcpy(p + 2, &v16, 2);
		v32 = htole32(0xFFFFFFFFU); memcpy(p + 4, &v32, 4);
	}

	*out_len = POSIX_TRIVIAL_ACL_SIZE;
	return (char *)buf;
}

/*
 * do_fgetacl(fd, out) -- get the ACL xattr(s) on an open file descriptor.
 *
 * Fills *out with type-tagged raw xattr buffers.  Returns 0 on success,
 * -1 on failure (Python exception set).
 */
int
do_fgetacl(int fd, acl_xattr_t *out)
{
	ssize_t sz;
	ssize_t dsz;
	int async_err = 0;

	/* Probe for NFS4 ACL xattr. */
	do {
		Py_BEGIN_ALLOW_THREADS
		sz = fgetxattr(fd, NFS4_ACL_XATTR, NULL, 0);
		Py_END_ALLOW_THREADS
	} while (sz == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (async_err)
		return -1;

	if (sz >= 0 || errno == ENODATA) {
		/* NFS4 filesystem. */
		out->type = ACLTYPE_NFS4;
		if (sz > 0) {
			out->data.nfs4.data = read_xattr_raw(fd, NFS4_ACL_XATTR, sz,
			                                     &out->data.nfs4.len);
			if (out->data.nfs4.data == NULL)
				return -1;
		} else {
			/* ENODATA: ACL present but empty. */
			out->data.nfs4.data = NULL;
			out->data.nfs4.len = 0;
		}
		return 0;
	}

	if (errno != EOPNOTSUPP) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	/* EOPNOTSUPP: not NFS4, try POSIX. */
	do {
		Py_BEGIN_ALLOW_THREADS
		sz = fgetxattr(fd, POSIX_ACCESS_XATTR, NULL, 0);
		Py_END_ALLOW_THREADS
	} while (sz == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (async_err)
		return -1;

	if (sz == -1 && errno == EOPNOTSUPP) {
		/* ACLs disabled entirely. */
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	out->type = ACLTYPE_POSIX;

	if (sz > 0) {
		out->data.posix.access_data = read_xattr_raw(fd, POSIX_ACCESS_XATTR, sz,
		                                             &out->data.posix.access_len);
		if (out->data.posix.access_data == NULL)
			return -1;
		out->data.posix.access_synthesized = 0;
	} else {
		/*
		 * ENODATA: system.posix_acl_access is absent.  The kernel
		 * implicitly derives the access ACL from the inode mode bits;
		 * synthesise a matching trivial 3-entry blob so that callers
		 * always see a non-empty access ACL, matching getfacl(1).
		 */
		struct stat st;
		int fstat_err;

		do {
			Py_BEGIN_ALLOW_THREADS
			fstat_err = fstat(fd, &st);
			Py_END_ALLOW_THREADS
		} while (fstat_err == -1 && errno == EINTR);

		if (fstat_err == -1) {
			PyErr_SetFromErrno(PyExc_OSError);
			return -1;
		}

		out->data.posix.access_data =
		    synthesize_posix_access_from_mode(st.st_mode,
		                                      &out->data.posix.access_len);
		if (out->data.posix.access_data == NULL)
			return -1;
		out->data.posix.access_synthesized = 1;
	}

	/* Probe for default ACL. */
	do {
		Py_BEGIN_ALLOW_THREADS
		dsz = fgetxattr(fd, POSIX_DEFAULT_XATTR, NULL, 0);
		Py_END_ALLOW_THREADS
	} while (dsz == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (async_err) {
		free(out->data.posix.access_data);
		return -1;
	}

	if (dsz == -1 && errno == ENODATA) {
		/* No default ACL. */
		out->data.posix.default_data = NULL;
		out->data.posix.default_len = 0;
	} else if (dsz == -1) {
		free(out->data.posix.access_data);
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	} else if (dsz > 0) {
		out->data.posix.default_data = read_xattr_raw(fd, POSIX_DEFAULT_XATTR, dsz,
		                                              &out->data.posix.default_len);
		if (out->data.posix.default_data == NULL) {
			free(out->data.posix.access_data);
			return -1;
		}
	} else {
		out->data.posix.default_data = NULL;
		out->data.posix.default_len = 0;
	}

	return 0;
}

/*
 * do_fsetacl_nfs4(fd, data, len) -- set system.nfs4_acl_xdr on fd.
 * Returns 0 on success, -1 on failure (Python exception set).
 */
int
do_fsetacl_nfs4(int fd, const char *data, size_t len)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = fsetxattr(fd, NFS4_ACL_XATTR, data, len, 0);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		if (!async_err)
			PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	return 0;
}

/*
 * do_fremoveacl(fd) -- remove all ACL xattr(s) from fd.
 *
 * Probes for filesystem type using the same fgetxattr sentinel as do_fgetacl():
 *   NFS4:  fgetxattr returns >= 0 or ENODATA  → fremovexattr(nfs4_acl_xdr)
 *   POSIX: fgetxattr returns EOPNOTSUPP        → fremovexattr(posix_acl_access)
 *                                                + fremovexattr(posix_acl_default)
 * ENODATA on any individual remove is silently ignored.
 */
int
do_fremoveacl(int fd)
{
	ssize_t sz;
	int ret;
	int async_err;

	async_err = 0;

	/* Probe for NFS4 xattr to determine filesystem type. */
	do {
		Py_BEGIN_ALLOW_THREADS
		sz = fgetxattr(fd, NFS4_ACL_XATTR, NULL, 0);
		Py_END_ALLOW_THREADS
	} while (sz == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (async_err)
		return -1;

	if (sz >= 0) {
		/* NFS4 filesystem. */
		do {
			Py_BEGIN_ALLOW_THREADS
			ret = fremovexattr(fd, NFS4_ACL_XATTR);
			Py_END_ALLOW_THREADS
		} while (ret == -1 && errno == EINTR &&
		         !(async_err = PyErr_CheckSignals()));

		if (ret == -1 && errno != ENODATA) {
			if (!async_err)
				PyErr_SetFromErrno(PyExc_OSError);
			return -1;
		}
		return 0;
	}

	if (errno == ENODATA)
		return 0;  /* NFS4 filesystem, no ACL present; nothing to remove. */

	if (errno != EOPNOTSUPP) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	/* POSIX filesystem: remove access xattr (ENODATA silently ignored). */
	do {
		Py_BEGIN_ALLOW_THREADS
		ret = fremovexattr(fd, POSIX_ACCESS_XATTR);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (ret == -1 && errno != ENODATA) {
		if (errno == EOPNOTSUPP) {
			/* acltype == DISABLED */
			return 0;
		}
		if (!async_err)
			PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	/* Remove default xattr (ENODATA silently ignored). */
	async_err = 0;
	do {
		Py_BEGIN_ALLOW_THREADS
		ret = fremovexattr(fd, POSIX_DEFAULT_XATTR);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (ret == -1 && errno != ENODATA) {
		if (!async_err)
			PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	return 0;
}

/*
 * do_fsetacl_posix(fd, ...) -- set POSIX ACL xattrs on fd.
 *
 * If default_data is NULL, the default ACL xattr is removed
 * (ENODATA is silently ignored -- it was already absent).
 * Returns 0 on success, -1 on failure (Python exception set).
 */
int
do_fsetacl_posix(int fd,
                 const char *access_data, size_t access_len,
                 const char *default_data, size_t default_len)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = fsetxattr(fd, POSIX_ACCESS_XATTR, access_data, access_len, 0);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		if (!async_err)
			PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	if (default_data != NULL) {
		do {
			Py_BEGIN_ALLOW_THREADS
			ret = fsetxattr(fd, POSIX_DEFAULT_XATTR,
			                default_data, default_len, 0);
			Py_END_ALLOW_THREADS
		} while (ret == -1 && errno == EINTR &&
		         !(async_err = PyErr_CheckSignals()));

		if (ret == -1) {
			if (!async_err)
				PyErr_SetFromErrno(PyExc_OSError);
			return -1;
		}
	} else {
		do {
			Py_BEGIN_ALLOW_THREADS
			ret = fremovexattr(fd, POSIX_DEFAULT_XATTR);
			Py_END_ALLOW_THREADS
		} while (ret == -1 && errno == EINTR &&
		         !(async_err = PyErr_CheckSignals()));

		if (ret == -1 && errno != ENODATA) {
			if (!async_err)
				PyErr_SetFromErrno(PyExc_OSError);
			return -1;
		}
	}

	return 0;
}
