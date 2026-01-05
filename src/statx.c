// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "statx.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include <string.h>

// Check if newer fields are available
#ifdef STATX_DIO_READ_ALIGN
#define HAVE_STATX_DIO_READ_FIELDS 1
#else
#define HAVE_STATX_DIO_READ_FIELDS 0
#endif

// StatxResult structured sequence type
static PyStructSequence_Field statx_result_fields[] = {
	{"stx_mask", "Mask of bits indicating filled fields"},
	{"stx_blksize", "Block size for filesystem I/O"},
	{"stx_attributes", "Extra file attribute indicators"},
	{"stx_nlink", "Number of hard links"},
	{"stx_uid", "User ID of owner"},
	{"stx_gid", "Group ID of owner"},
	{"stx_mode", "File type and mode"},
	{"stx_ino", "Inode number"},
	{"stx_size", "Total size in bytes"},
	{"stx_blocks", "Number of 512B blocks allocated"},
	{"stx_attributes_mask", "Mask to show what's supported in stx_attributes"},
	{"stx_atime", "Time of last access"},
	{"stx_atime_ns", "Time of last access in nanoseconds"},
	{"stx_btime", "Time of creation"},
	{"stx_btime_ns", "Time of creation in nanoseconds"},
	{"stx_ctime", "Time of last status change"},
	{"stx_ctime_ns", "Time of last status change in nanoseconds"},
	{"stx_mtime", "Time of last modification"},
	{"stx_mtime_ns", "Time of last modification in nanoseconds"},
	{"stx_rdev_major", "Major ID of device if special file"},
	{"stx_rdev_minor", "Minor ID of device if special file"},
	{"stx_rdev", "Device type (if inode device)"},
	{"stx_dev_major", "Major ID of device containing file"},
	{"stx_dev_minor", "Minor ID of device containing file"},
	{"stx_dev", "Device"},
	{"stx_mnt_id", "Mount ID of the mount containing the file"},
	{"stx_dio_mem_align", "Memory alignment for direct I/O"},
	{"stx_dio_offset_align", "File offset alignment for direct I/O"},
	{"stx_subvol", "Subvolume identifier"},
	{"stx_atomic_write_unit_min", "Min atomic write unit in bytes"},
	{"stx_atomic_write_unit_max", "Max atomic write unit in bytes"},
	{"stx_atomic_write_segments_max", "Max atomic write segment count"},
#if HAVE_STATX_DIO_READ_FIELDS
	{"stx_dio_read_offset_align", "File offset alignment for direct I/O reads"},
	{"stx_atomic_write_unit_max_opt", "Optimised max atomic write unit in bytes"},
#endif
	{NULL}
};

static PyStructSequence_Desc statx_result_desc = {
	.name = "truenas_os.StatxResult",
	.doc = "Result from statx() system call\n\n"
	       "A named tuple containing extended file attributes. "
	       "Fields that were not requested or are unavailable will be None or 0 depending on the field.",
	.fields = statx_result_fields,
#if HAVE_STATX_DIO_READ_FIELDS
	.n_in_sequence = 34
#else
	.n_in_sequence = 32
#endif
};

static PyTypeObject StatxResultType;

PyObject *do_statx(int dirfd, const char *pathname, int flags, unsigned int mask)
{
	struct statx stx;
	int ret;
	PyObject *result = NULL;

	Py_BEGIN_ALLOW_THREADS
	ret = syscall(SYS_statx, dirfd, pathname, flags, mask, &stx);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		PyErr_SetFromErrno(PyExc_OSError);
		return NULL;
	}

	result = PyStructSequence_New(&StatxResultType);
	if (result == NULL) {
		return NULL;
	}

	// Set fields
	PyObject *tmp = NULL;

	#define SET_FIELD(idx, value) do { \
		tmp = (value); \
		if (tmp == NULL) { \
			Py_DECREF(result); \
			return NULL; \
		} \
		PyStructSequence_SET_ITEM(result, idx, tmp); \
	} while(0)

	SET_FIELD(0, PyLong_FromUnsignedLong(stx.stx_mask));
	SET_FIELD(1, PyLong_FromUnsignedLong(stx.stx_blksize));
	SET_FIELD(2, PyLong_FromUnsignedLongLong(stx.stx_attributes));
	SET_FIELD(3, PyLong_FromUnsignedLong(stx.stx_nlink));
	SET_FIELD(4, PyLong_FromUnsignedLong(stx.stx_uid));
	SET_FIELD(5, PyLong_FromUnsignedLong(stx.stx_gid));
	SET_FIELD(6, PyLong_FromUnsignedLong(stx.stx_mode));
	SET_FIELD(7, PyLong_FromUnsignedLongLong(stx.stx_ino));
	SET_FIELD(8, PyLong_FromLongLong(stx.stx_size));
	SET_FIELD(9, PyLong_FromUnsignedLongLong(stx.stx_blocks));
	SET_FIELD(10, PyLong_FromUnsignedLongLong(stx.stx_attributes_mask));

	// stx_atime as float and stx_atime_ns as total nanoseconds
	SET_FIELD(11, PyFloat_FromDouble((double)stx.stx_atime.tv_sec + stx.stx_atime.tv_nsec * 1e-9));
	SET_FIELD(12, PyLong_FromLongLong((long long)stx.stx_atime.tv_sec * 1000000000LL + stx.stx_atime.tv_nsec));

	// stx_btime as float and stx_btime_ns as total nanoseconds
	SET_FIELD(13, PyFloat_FromDouble((double)stx.stx_btime.tv_sec + stx.stx_btime.tv_nsec * 1e-9));
	SET_FIELD(14, PyLong_FromLongLong((long long)stx.stx_btime.tv_sec * 1000000000LL + stx.stx_btime.tv_nsec));

	// stx_ctime as float and stx_ctime_ns as total nanoseconds
	SET_FIELD(15, PyFloat_FromDouble((double)stx.stx_ctime.tv_sec + stx.stx_ctime.tv_nsec * 1e-9));
	SET_FIELD(16, PyLong_FromLongLong((long long)stx.stx_ctime.tv_sec * 1000000000LL + stx.stx_ctime.tv_nsec));

	// stx_mtime as float and stx_mtime_ns as total nanoseconds
	SET_FIELD(17, PyFloat_FromDouble((double)stx.stx_mtime.tv_sec + stx.stx_mtime.tv_nsec * 1e-9));
	SET_FIELD(18, PyLong_FromLongLong((long long)stx.stx_mtime.tv_sec * 1000000000LL + stx.stx_mtime.tv_nsec));

	SET_FIELD(19, PyLong_FromUnsignedLong(stx.stx_rdev_major));
	SET_FIELD(20, PyLong_FromUnsignedLong(stx.stx_rdev_minor));
	SET_FIELD(21, PyLong_FromUnsignedLongLong(makedev(stx.stx_rdev_major, stx.stx_rdev_minor)));
	SET_FIELD(22, PyLong_FromUnsignedLong(stx.stx_dev_major));
	SET_FIELD(23, PyLong_FromUnsignedLong(stx.stx_dev_minor));
	SET_FIELD(24, PyLong_FromUnsignedLongLong(makedev(stx.stx_dev_major, stx.stx_dev_minor)));
	SET_FIELD(25, PyLong_FromUnsignedLongLong(stx.stx_mnt_id));
	SET_FIELD(26, PyLong_FromUnsignedLong(stx.stx_dio_mem_align));
	SET_FIELD(27, PyLong_FromUnsignedLong(stx.stx_dio_offset_align));
	SET_FIELD(28, PyLong_FromUnsignedLongLong(stx.stx_subvol));
	SET_FIELD(29, PyLong_FromUnsignedLong(stx.stx_atomic_write_unit_min));
	SET_FIELD(30, PyLong_FromUnsignedLong(stx.stx_atomic_write_unit_max));
	SET_FIELD(31, PyLong_FromUnsignedLong(stx.stx_atomic_write_segments_max));

#if HAVE_STATX_DIO_READ_FIELDS
	SET_FIELD(32, PyLong_FromUnsignedLong(stx.stx_dio_read_offset_align));
	SET_FIELD(33, PyLong_FromUnsignedLong(stx.stx_atomic_write_unit_max_opt));
#endif

	#undef SET_FIELD

	return result;
}

int init_statx_types(PyObject *module)
{
	if (PyStructSequence_InitType2(&StatxResultType, &statx_result_desc) < 0) {
		return -1;
	}

	Py_INCREF(&StatxResultType);
	if (PyModule_AddObject(module, "StatxResult", (PyObject *)&StatxResultType) < 0) {
		Py_DECREF(&StatxResultType);
		return -1;
	}

	// Add STATX_* mask constants
	PyModule_AddIntConstant(module, "STATX_TYPE", STATX_TYPE);
	PyModule_AddIntConstant(module, "STATX_MODE", STATX_MODE);
	PyModule_AddIntConstant(module, "STATX_NLINK", STATX_NLINK);
	PyModule_AddIntConstant(module, "STATX_UID", STATX_UID);
	PyModule_AddIntConstant(module, "STATX_GID", STATX_GID);
	PyModule_AddIntConstant(module, "STATX_ATIME", STATX_ATIME);
	PyModule_AddIntConstant(module, "STATX_MTIME", STATX_MTIME);
	PyModule_AddIntConstant(module, "STATX_CTIME", STATX_CTIME);
	PyModule_AddIntConstant(module, "STATX_INO", STATX_INO);
	PyModule_AddIntConstant(module, "STATX_SIZE", STATX_SIZE);
	PyModule_AddIntConstant(module, "STATX_BLOCKS", STATX_BLOCKS);
	PyModule_AddIntConstant(module, "STATX_BASIC_STATS", STATX_BASIC_STATS);
	PyModule_AddIntConstant(module, "STATX_BTIME", STATX_BTIME);
	PyModule_AddIntConstant(module, "STATX_MNT_ID", STATX_MNT_ID);
	PyModule_AddIntConstant(module, "STATX_DIOALIGN", STATX_DIOALIGN);
	PyModule_AddIntConstant(module, "STATX_MNT_ID_UNIQUE", STATX_MNT_ID_UNIQUE);
	PyModule_AddIntConstant(module, "STATX_SUBVOL", STATX_SUBVOL);
	PyModule_AddIntConstant(module, "STATX_WRITE_ATOMIC", STATX_WRITE_ATOMIC);
#ifdef STATX_DIO_READ_ALIGN
	PyModule_AddIntConstant(module, "STATX_DIO_READ_ALIGN", STATX_DIO_READ_ALIGN);
#endif
	PyModule_AddIntConstant(module, "STATX__RESERVED", STATX__RESERVED);
	PyModule_AddIntConstant(module, "STATX_ALL", STATX_ALL);

	// Add AT_* flag constants
	PyModule_AddIntConstant(module, "AT_FDCWD", AT_FDCWD);
	PyModule_AddIntConstant(module, "AT_SYMLINK_NOFOLLOW", AT_SYMLINK_NOFOLLOW);
	PyModule_AddIntConstant(module, "AT_REMOVEDIR", AT_REMOVEDIR);
	PyModule_AddIntConstant(module, "AT_SYMLINK_FOLLOW", AT_SYMLINK_FOLLOW);
	PyModule_AddIntConstant(module, "AT_NO_AUTOMOUNT", AT_NO_AUTOMOUNT);
	PyModule_AddIntConstant(module, "AT_EMPTY_PATH", AT_EMPTY_PATH);
	PyModule_AddIntConstant(module, "AT_STATX_SYNC_AS_STAT", AT_STATX_SYNC_AS_STAT);
	PyModule_AddIntConstant(module, "AT_STATX_FORCE_SYNC", AT_STATX_FORCE_SYNC);
	PyModule_AddIntConstant(module, "AT_STATX_DONT_SYNC", AT_STATX_DONT_SYNC);

	// Add STATX_ATTR_* attribute constants
	PyModule_AddIntConstant(module, "STATX_ATTR_COMPRESSED", STATX_ATTR_COMPRESSED);
	PyModule_AddIntConstant(module, "STATX_ATTR_IMMUTABLE", STATX_ATTR_IMMUTABLE);
	PyModule_AddIntConstant(module, "STATX_ATTR_APPEND", STATX_ATTR_APPEND);
	PyModule_AddIntConstant(module, "STATX_ATTR_NODUMP", STATX_ATTR_NODUMP);
	PyModule_AddIntConstant(module, "STATX_ATTR_ENCRYPTED", STATX_ATTR_ENCRYPTED);
	PyModule_AddIntConstant(module, "STATX_ATTR_AUTOMOUNT", STATX_ATTR_AUTOMOUNT);
	PyModule_AddIntConstant(module, "STATX_ATTR_MOUNT_ROOT", STATX_ATTR_MOUNT_ROOT);
	PyModule_AddIntConstant(module, "STATX_ATTR_VERITY", STATX_ATTR_VERITY);
	PyModule_AddIntConstant(module, "STATX_ATTR_DAX", STATX_ATTR_DAX);
	PyModule_AddIntConstant(module, "STATX_ATTR_WRITE_ATOMIC", STATX_ATTR_WRITE_ATOMIC);

	return 0;
}
