// SPDX-License-Identifier: GPL-2.0-only OR BSD-2-Clause

#include <linux/types.h>
#include <bpf/bpf_helpers.h>
#include "thread_rec.h"

/*
 * Only the fields read below are declared. libbpf resolves their offsets
 * against the running kernel's BTF when the program is loaded.
 */
#define KERNEL_STRUCT __attribute__((preserve_access_index))

struct seq_file;

struct bpf_iter_meta {
	struct seq_file *seq;
	__u64 session_id;
	__u64 seq_num;
};

struct bpf_iter__task {
	struct bpf_iter_meta *meta;
	struct task_struct *task;
};

struct thread_info {
	__u32 cpu;
} KERNEL_STRUCT;

struct sched_entity {
	__u64 sum_exec_runtime;
} KERNEL_STRUCT;

struct sched_info {
	unsigned long long run_delay;
} KERNEL_STRUCT;

struct signal_struct {
	unsigned long long sum_sched_runtime;
} KERNEL_STRUCT;

struct task_struct {
	struct thread_info thread_info;
	unsigned int __state;
	struct sched_entity se;
	struct sched_info sched_info;
	int exit_state;
	int pid;
	int tgid;
	__u64 start_time;
	struct task_struct *group_leader;
	struct signal_struct *signal;
	char comm[16];
} KERNEL_STRUCT;

SEC("iter/task")
int dump_thread(struct bpf_iter__task *ctx)
{
	struct task_struct *task = ctx->task;
	struct thread_rec rec;

	if (task == NULL)
		return 0;

	__builtin_memset(&rec, 0, sizeof(rec));
	rec.runtime = task->se.sum_exec_runtime;
	rec.start_time = task->start_time;
	rec.run_delay = task->sched_info.run_delay;
	rec.exited_runtime = task->signal->sum_sched_runtime;
	rec.tid = task->pid;
	rec.tgid = task->tgid;
	rec.state = task->__state;
	rec.exit_state = task->exit_state;
	rec.cpu = task->thread_info.cpu;
	__builtin_memcpy(rec.comm, task->comm, sizeof(rec.comm));
	__builtin_memcpy(rec.process_comm, task->group_leader->comm, sizeof(rec.process_comm));

	bpf_seq_write(ctx->meta->seq, &rec, sizeof(rec));
	return 0;
}

char LICENSE[] SEC("license") = "Dual BSD/GPL";
