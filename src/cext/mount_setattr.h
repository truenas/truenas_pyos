// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _MOUNT_SETATTR_H_
#define _MOUNT_SETATTR_H_

#include <linux/mount.h>

extern PyObject *do_mount_setattr(int dirfd, const char *pathname,
                                   unsigned int flags,
                                   struct mount_attr *attr);
extern int init_mount_setattr_constants(PyObject *module);

#endif /* _MOUNT_SETATTR_H_ */
