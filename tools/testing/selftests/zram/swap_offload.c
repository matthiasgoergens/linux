// SPDX-License-Identifier: GPL-2.0
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#define SWAP_FLAG_PREFER	0x8000
#define SWAP_FLAG_OFFLOAD_ONLY	0x80000

static int activate(const char *path, int priority)
{
	int flags = SWAP_FLAG_PREFER | SWAP_FLAG_OFFLOAD_ONLY | priority;

	if (syscall(SYS_swapon, path, flags)) {
		perror("swapon");
		return 1;
	}

	return 0;
}

static int join_cgroup(const char *procs)
{
	char pid[32];
	int fd, len;

	fd = open(procs, O_WRONLY);
	if (fd < 0) {
		perror("open cgroup.procs");
		return 1;
	}

	len = snprintf(pid, sizeof(pid), "%d\n", getpid());
	if (write(fd, pid, len) != len) {
		perror("write cgroup.procs");
		close(fd);
		return 1;
	}
	close(fd);
	return 0;
}

static int allocate(const char *size_arg, const char *procs,
		    const char *ready)
{
	volatile char *memory;
	unsigned long size;
	char *end;
	int fd;

	errno = 0;
	size = strtoul(size_arg, &end, 0);
	if (errno || *end || !size) {
		fprintf(stderr, "invalid allocation size: %s\n", size_arg);
		return 1;
	}
	if (join_cgroup(procs))
		return 1;

	memory = mmap(NULL, size, PROT_READ | PROT_WRITE,
		      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (memory == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	for (unsigned long i = 0; i < size; i += getpagesize())
		memory[i] = 1;

	fd = open(ready, O_WRONLY | O_CREAT | O_EXCL, 0600);
	if (fd < 0) {
		perror("create ready file");
		return 1;
	}
	close(fd);

	for (;;)
		pause();
}

int main(int argc, char **argv)
{
	if (argc == 4 && !strcmp(argv[1], "activate"))
		return activate(argv[2], atoi(argv[3]));
	if (argc == 5 && !strcmp(argv[1], "allocate"))
		return allocate(argv[2], argv[3], argv[4]);

	fprintf(stderr,
		"usage: %s activate DEVICE PRIORITY | "
		"allocate BYTES CGROUP.PROCS READY\n", argv[0]);
	return 1;
}
