// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Common includes for the truenas_os.Uring io_uring binding.
 *
 * Copyright (C) iXsystems, 2026
 */

#ifndef _PYURING_COMMON_H
#define _PYURING_COMMON_H

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
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/stat.h>

/*
 * liburing ships its own copy of the io_uring UAPI header, which is why this
 * module is built against upstream rather than Debian's liburing: the 2.9 in
 * trixie predates the 6.18 opcodes and register opcodes we rely on.
 */
#include <liburing.h>

#endif /* _PYURING_COMMON_H */
