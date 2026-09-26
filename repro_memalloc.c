// Reproducer v14 (with instrumented test kernel): lazytime dirty-mark at
// final iput during a PF_MEMALLOC-forced slab shrink.
//
// The test kernel wraps drop_slab() in memalloc_noreclaim_save() (TEMP
// instrumentation), so "echo 2 > drop_caches" evicts in reclaim context.
// With lazytime, the final iput of a read file syncs I_DIRTY_TIME through
// ext4_dirty_inode() -> sb_getblk (NOFAIL).  Buffers are cold from a
// preceding drop_caches=1 and a hog keeps free memory near zero, so the
// allocation enters the allocator slowpath -> WARN on a stock kernel.
// With the fix, those iputs are deferred to a workqueue instead.
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/swap.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

/* jan-v2-testing: $CHURN_DIR overrides; /tmp is tmpfs on the syzbot image */
static const char *CHURN_DIR = "/tmp/churn";
#define NFILES 5000

static volatile int stop;
static int sync_before_read;	/* jan-v2-testing variant, argv[2] == "sync" */
static size_t hog_len;

static void write_file(const char *path, const char *val)
{
	int fd = open(path, O_WRONLY);
	if (fd >= 0) {
		write(fd, val, strlen(val));
		close(fd);
	}
}

static void setup_swap(void)
{
	int fd = open("/swapfile", O_CREAT | O_RDWR | O_TRUNC, 0600);
	if (fd < 0)
		return;
	for (int i = 0; i < 256; i++) {
		char buf[1 << 20] = {0};
		if (write(fd, buf, sizeof(buf)) != sizeof(buf))
			break;
	}
	close(fd);
	system("mkswap /swapfile >/dev/null 2>&1");
	if (swapon("/swapfile", 0))
		perror("swapon");
	else
		printf("swap on\n");
}

static void ballast_supervise(void)
{
	if (!fork()) {
		for (;;) {
			pid_t pid = fork();
			if (pid == 0) {
				write_file("/proc/self/oom_score_adj", "1000");
				size_t len = 64 << 20;
				char *p = mmap(NULL, len, PROT_READ | PROT_WRITE,
					       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
				if (p != MAP_FAILED)
					for (size_t off = 0; off < len; off += 4096)
						p[off] = 1;
				pause();
				_exit(0);
			}
			int st;
			while (waitpid(pid, &st, 0) != pid)
				;
		}
	}
}

static void *hog_thread(void *arg)
{
	(void)arg;
	char *p = mmap(NULL, hog_len, PROT_READ | PROT_WRITE,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (p == MAP_FAILED)
		return NULL;
	while (!stop) {
		for (size_t off = 0; off < hog_len; off += 4096)
			p[off]++;
		usleep(5000);
	}
	return NULL;
}

static void *churn_thread(void *arg)
{
	(void)arg;
	char path[160];
	char buf[64];
	int base = 0;
	memset(buf, 'x', sizeof(buf));
	while (!stop) {
		/* create + read a fresh batch, then leave it to age out;
		 * eviction must come from the shrinker, not from us */
		for (int i = 0; i < NFILES; i++) {
			snprintf(path, sizeof(path), "%s/f%d", CHURN_DIR, base + i);
			int fd = open(path, O_CREAT | O_RDWR | O_TRUNC, 0600);
			if (fd < 0)
				continue;
			write(fd, buf, sizeof(buf));
			close(fd);
		}
		/* variant: write the batch back first, so that the read below
		 * leaves the inodes dirty only in their timestamps
		 * (I_DIRTY_TIME), which is what sync_lazytime() acts on */
		if (sync_before_read) {
			sync();
			usleep(20 * 1000);
		}
		for (int i = 0; i < NFILES; i++) {
			snprintf(path, sizeof(path), "%s/f%d", CHURN_DIR, base + i);
			int fd = open(path, O_RDONLY);
			if (fd < 0)
				continue;
			read(fd, buf, sizeof(buf));
			close(fd);
		}
		base += NFILES;
		if (base >= 40000) {   /* bound disk usage; inline evict of old */
			char cmd[256];
			snprintf(cmd, sizeof(cmd), "rm -rf %s", CHURN_DIR);
			system(cmd);
			mkdir(CHURN_DIR, 0755);
			base = 0;
		}
	}
	return NULL;
}

int main(int argc, char **argv)
{
	pthread_t t1, t2;

	setvbuf(stdout, NULL, _IONBF, 0);
	hog_len = (size_t)(argc > 1 ? atoi(argv[1]) : 300) << 20;
	sync_before_read = argc > 2 && !strcmp(argv[2], "sync");
	if (getenv("CHURN_DIR"))
		CHURN_DIR = getenv("CHURN_DIR");
	printf("churn dir %s\n", CHURN_DIR);
	printf("sync_before_read=%d\n", sync_before_read);

	write_file("/proc/self/oom_score_adj", "-1000");
	write_file("/proc/sys/vm/vfs_cache_pressure", "10000");
	write_file("/proc/sys/vm/swappiness", "100");
	setup_swap();
	ballast_supervise();

	if (mount(NULL, "/", NULL, MS_REMOUNT, "lazytime"))
		perror("remount lazytime");
	else
		printf("lazytime on\n");

	mkdir(CHURN_DIR, 0755);
	if (pthread_create(&t1, NULL, hog_thread, NULL) ||
	    pthread_create(&t2, NULL, churn_thread, NULL)) {
		perror("pthread_create");
		return 1;
	}

	for (int i = 0; !stop; i++) {
		/* cold page+buffer cache, then a forced-PF_MEMALLOC slab shrink */
		write_file("/proc/sys/vm/drop_caches", "1\n");
		usleep(200 * 1000);
		write_file("/proc/sys/vm/drop_caches", "2\n");
		usleep(200 * 1000);
		if (i % 25 == 0)
			printf("drop round %d\n", i / 2 + 1);
	}
	return 0;
}
