/* Hibernation smoke test for the deferred-iput drain barrier.
 *
 * Creates many unlinked-but-cached inodes on the root fs (ext4), then drives
 * a pm_test=devices hibernation cycle, which runs
 * hibernate_preallocate_memory() (reclaim under PF_MEMALLOC -> deferred
 * iputs) and then the drain_deferred_iputs() barrier.  A hang in the
 * barrier shows up as a hung task / watchdog splat on the console; a splat
 * fails the expect wrapper.
 *
 * If the workqueue tracepoint is available, count deferred_iput_work
 * executions across the cycle and print the count as HB_DEFER_EXEC=<n>.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/mount.h>

static int wfile(const char *path, const char *val)
{
	int fd = open(path, O_WRONLY);
	ssize_t r;

	if (fd < 0) {
		perror(path);
		return -1;
	}
	r = write(fd, val, strlen(val));
	if (r < 0) {
		perror("write");
		close(fd);
		return -1;
	}
	close(fd);
	return 0;
}

static int count_deferred_in_trace(const char *fn)
{
	const char *tp = "/sys/kernel/tracing/trace";
	char *buf = malloc(16 << 20);
	int fd, n, count = 0;
	ssize_t got;
	size_t off = 0;

	if (!buf)
		return -1;
	fd = open(tp, O_RDONLY);
	if (fd < 0)
		fd = open("/sys/kernel/debug/tracing/trace", O_RDONLY);
	if (fd < 0) {
		free(buf);
		return -1;
	}
	while ((got = read(fd, buf + off, (16 << 20) - 1 - off)) > 0) {
		off += got;
		if (off >= (16 << 20) - 1)
			break;
	}
	close(fd);
	buf[off] = 0;
	for (char *p = buf; (p = strstr(p, fn)); p++)
		count++;
	free(buf);
	return count;
}

int main(int argc, char **argv)
{
	char dir[] = "/root/hibtestXXXXXX";
	char path[256];
	int nfiles = argc > 1 ? atoi(argv[1]) : 20000;
	int i, traced = 0;

	setbuf(stdout, NULL);

	/* best effort: enable workqueue execution tracing */
	if (!mount("tracefs", "/sys/kernel/tracing", "tracefs", 0, NULL) ||
	    !access("/sys/kernel/tracing/events/workqueue/workqueue_execute_start/enable", W_OK)) {
		if (!wfile("/sys/kernel/tracing/events/workqueue/workqueue_execute_start/enable", "1")) {
			wfile("/sys/kernel/tracing/trace", "");
			traced = 1;
		}
	}
	printf("HB_TRACE %s\n", traced ? "on" : "off");

	/* make preallocation reclaim meaningful but still succeed */
	if (wfile("/sys/power/image_size", "400000000"))
		printf("HB_IMAGE_SIZE unsettable\n");

	if (!mkdtemp(dir)) {
		perror("mkdtemp");
		return 1;
	}
	for (i = 0; i < nfiles; i++) {
		snprintf(path, sizeof(path), "%s/f%d", dir, i);
		int fd = open(path, O_CREAT | O_WRONLY, 0600);

		if (fd >= 0) {
			if (write(fd, "x", 1) != 1)
				perror("write file");
			close(fd);
			unlink(path);
		}
	}
	rmdir(dir);
	sync();
	printf("HB_SETUP_DONE %d files\n", nfiles);

	if (wfile("/sys/power/pm_test", "devices"))
		return 2;
	printf("HB_PMTEST_SET\n");
	if (wfile("/sys/power/state", "disk"))
		printf("HB_DISK_FAILED\n");
	else
		printf("HB_HIBERNATE_TEST_OK\n");
	wfile("/sys/power/pm_test", "none");

	if (traced)
		printf("HB_DEFER_EXEC=%d HB_JAN_DEFER_BATCHES=%d\n",
		       count_deferred_in_trace("deferred_iput_work"),
		       count_deferred_in_trace("inode_reclaim_update_stat:"));
	return 0;
}
