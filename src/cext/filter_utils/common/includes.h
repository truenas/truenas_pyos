// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _INCLUDES_H_
#define _INCLUDES_H_

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <errno.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

#define discard_const(ptr) ((void *)((uintptr_t)(ptr)))
#define discard_const_p(type, ptr) ((type *)discard_const(ptr))

#define ARRAY_SIZE(a) (sizeof(a) / sizeof(a[0]))

#endif /* _INCLUDES_H_ */
