// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _OPEN_TREE_H_
#define _OPEN_TREE_H_

#include <linux/mount.h>

extern PyObject *do_open_tree(int dirfd, const char *pathname,
                               unsigned int flags);
extern int init_open_tree_constants(PyObject *module);

#endif /* _OPEN_TREE_H_ */
