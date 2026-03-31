// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _MOVE_MOUNT_H_
#define _MOVE_MOUNT_H_

#include <linux/mount.h>

extern PyObject *do_move_mount(int from_dirfd, const char *from_pathname,
                                int to_dirfd, const char *to_pathname,
                                unsigned int flags);
extern int init_move_mount_constants(PyObject *module);

#endif /* _MOVE_MOUNT_H_ */
