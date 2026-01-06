// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Python language bindings for libgfapi
 *
 * Copyright (C) Andrew Walker, 2022
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, see <http://www.gnu.org/licenses/>.
 */

#include <Python.h>
#include "common/includes.h"
#include "open.h"
#include <linux/mount.h>
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_statmount 457

int open_by_mount_id(uint64_t mount_id, int flags)
{
	struct mnt_id_req req = {0};
	char buf[1024];
	struct statmount *sm = (struct statmount *)buf;
	ssize_t ret;
	int fd = -1;
	const char *mnt_point;

	req.size = MNT_ID_REQ_SIZE_VER1;
	req.mnt_id = mount_id;
	req.param = STATMOUNT_MNT_POINT;

	Py_BEGIN_ALLOW_THREADS
	ret = syscall(__NR_statmount, &req, sm, sizeof(buf), 0);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	// Get mount point string
	if (sm->mnt_point == 0) {
		PyErr_SetString(PyExc_ValueError, "Mount point not available");
		return -1;
	}

	mnt_point = sm->str + sm->mnt_point;

	Py_BEGIN_ALLOW_THREADS
	fd = open(mnt_point, flags);
	Py_END_ALLOW_THREADS

	if (fd == -1) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	return fd;
}

PyObject *py_open_mount_id(uint64_t mount_id, int flags)
{
	int fd;

	fd = open_by_mount_id(mount_id, flags);
	if (fd == -1) {
		return NULL;
	}

	return Py_BuildValue("i", fd);
}
