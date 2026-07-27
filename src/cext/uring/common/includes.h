// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Common includes for the truenas_uring extension.
 *
 * Copyright (C) iXsystems, 2026
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

#ifndef _URING_INCLUDES_H_
#define _URING_INCLUDES_H_

/*
 * setup.py passes -D_GNU_SOURCE for the whole extension because liburing's
 * own sources require it; guard so the two definitions do not collide.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/types.h>
#include <linux/stat.h>

/*
 * liburing ships its own copy of the io_uring UAPI header, which is why this
 * module is built against upstream rather than Debian's liburing: the 2.9 in
 * trixie predates the 6.18 opcodes and register opcodes we rely on.
 */
#include <liburing.h>

#define discard_const(ptr) ((void *)((uintptr_t)(ptr)))
#define discard_const_p(type, ptr) ((type *)discard_const(ptr))

#define ARRAY_SIZE(a) (sizeof(a)/sizeof(a[0]))
#define __STRING(x) #x
#define __STRINGSTRING(x) __STRING(x)
#define __LINESTR__ __STRINGSTRING(__LINE__)
#define __location__ __FILE__ ":" __LINESTR__

#endif /* _URING_INCLUDES_H_ */
