// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _RENAMEAT2_H_
#define _RENAMEAT2_H_

PyObject *do_renameat2(int olddirfd, const char *oldpath,
                       int newdirfd, const char *newpath,
                       unsigned int flags);
int init_renameat2_constants(PyObject *module);

#endif /* _RENAMEAT2_H_ */
