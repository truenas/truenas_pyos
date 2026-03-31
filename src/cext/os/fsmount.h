// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _FSMOUNT_H_
#define _FSMOUNT_H_

extern PyObject *do_fsopen(const char *fs_name, unsigned int flags);
extern PyObject *do_fsconfig(int fs_fd, unsigned int cmd, const char *key,
                              const void *value, int aux);
extern PyObject *do_fsmount(int fs_fd, unsigned int flags,
                             unsigned int attr_flags);
extern int init_fsmount_constants(PyObject *module);

#endif /* _FSMOUNT_H_ */
