// SPDX-License-Identifier: GPL-2.0-only OR BSD-2-Clause

#ifndef THREAD_REC_H
#define THREAD_REC_H

/* Shared by thread_iter.bpf.c and truenas_threadstat.c. Times in ns. */
struct thread_rec {
	__u64 runtime;
	__u64 start_time;
	__u64 run_delay;
	__u64 exited_runtime;
	__u32 tid;
	__u32 tgid;
	__u32 state;
	__u32 exit_state;
	__u32 cpu;
	char comm[16];
	char process_comm[16];
};

#endif /* THREAD_REC_H */
