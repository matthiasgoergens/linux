// Overlayfs-over-ext4 reclaim-context deletion reproducer (test only).
//
// Path under test (syzbot extid 7f94fe3ce0f6613e12b8, reports 12764a15580000
// and 13530905580000): reclaim prunes overlayfs dentries; dropping the
// overlay inode puts the last reference to an unlinked ext4 inode (lower via
// ovl_stack_put(), upper via ovl_destroy_inode()'s dput of __upperdentry),
// so ext4_evict_inode() runs its deletion path in PF_MEMALLOC context.
//
// Setup, per round: N lower files and N upper files, each with data blocks,
// all on the root ext4.  Instantiate overlay dentries/inodes for all of them
// (stat via the merged dir for lower files, create via the merged dir for
// upper files), then unlink the files directly in lowerdir/upperdir.  The
// ext4 inodes now have i_nlink == 0 but stay alive through the overlay
// inodes' dentry references.  Then cold block/inode-table buffers
// (drop_caches=1) and trigger reclaim:
//   mode "hook":    echo 2 > drop_caches (the test kernel runs drop_slab()
//                   under memalloc_noreclaim_save(), approximating reclaim)
//   mode "reclaim": no drop_caches=2; a child dirties anonymous memory until
//                   direct reclaim / kswapd prune the dcache (real reclaim).
// Space held by the unlinked inodes is reported before/after via statfs.
//
// usage: ovl_evict <hook|reclaim> <rounds> <files-per-kind> [hog-MiB-max]
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define BASE "/root/ovl"
#define FILE_KB 64

static void wfile(const char *path, const char *val)
{
	int fd = open(path, O_WRONLY);

	if (fd < 0) {
		perror(path);
		return;
	}
	if (write(fd, val, strlen(val)) < 0)
		perror(path);
	close(fd);
}

static long long free_kb(void)
{
	struct statfs s;

	if (statfs(BASE, &s))
		return -1;
	return (long long)s.f_bfree * s.f_bsize / 1024;
}

static void mkfile(const char *path)
{
	static char buf[FILE_KB * 1024];
	int fd = open(path, O_CREAT | O_WRONLY | O_TRUNC, 0644);

	if (fd < 0) {
		perror(path);
		exit(1);
	}
	memset(buf, 'y', sizeof(buf));
	if (write(fd, buf, sizeof(buf)) != sizeof(buf)) {
		perror("write");
		exit(1);
	}
	close(fd);
}

/* Dirty anonymous memory in 16 MiB steps until the held space comes back
 * or max_mib is reached; runs as an OOM-preferred child. */
static void hog(long long target_kb, int max_mib)
{
	pid_t pid = fork();

	if (pid == 0) {
		wfile("/proc/self/oom_score_adj", "1000");
		for (int mib = 0; mib < max_mib; mib += 16) {
			char *p = mmap(NULL, 16 << 20, PROT_READ | PROT_WRITE,
				       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
			if (p == MAP_FAILED)
				break;
			for (size_t off = 0; off < (16 << 20); off += 4096)
				p[off] = 1;
			if (free_kb() >= target_kb) {
				printf("OVL_HOG reached target at %d MiB\n", mib + 16);
				_exit(0);
			}
		}
		printf("OVL_HOG gave up at %d MiB\n", max_mib);
		_exit(0);
	}
	int st;

	waitpid(pid, &st, 0);
	if (WIFSIGNALED(st))
		printf("OVL_HOG killed by signal %d\n", WTERMSIG(st));
}

int main(int argc, char **argv)
{
	char path[256], opts[512];
	int reclaim_mode, rounds, n, hog_max;

	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc < 4) {
		fprintf(stderr, "usage: %s <hook|reclaim> <rounds> <n> [hog-MiB]\n",
			argv[0]);
		return 2;
	}
	reclaim_mode = !strcmp(argv[1], "reclaim");
	rounds = atoi(argv[2]);
	n = atoi(argv[3]);
	hog_max = argc > 4 ? atoi(argv[4]) : 3000;

	wfile("/proc/self/oom_score_adj", "-1000");
	wfile("/proc/sys/vm/vfs_cache_pressure", "10000");
	mkdir(BASE, 0755);
	mkdir(BASE "/lower", 0755);
	mkdir(BASE "/upper", 0755);
	mkdir(BASE "/work", 0755);
	mkdir(BASE "/merged", 0755);
	snprintf(opts, sizeof(opts),
		 "lowerdir=%s/lower,upperdir=%s/upper,workdir=%s/work",
		 BASE, BASE, BASE);

	for (int r = 0; r < rounds; r++) {
		/* lower files must exist before the overlay mount */
		for (int i = 0; i < n; i++) {
			snprintf(path, sizeof(path), BASE "/lower/r%d_L%d", r, i);
			mkfile(path);
		}
		sync();
		if (mount("overlay", BASE "/merged", "overlay", 0, opts)) {
			perror("mount overlay");
			return 1;
		}
		for (int i = 0; i < n; i++) {
			struct stat st;

			snprintf(path, sizeof(path), BASE "/merged/r%d_L%d", r, i);
			if (stat(path, &st))
				perror(path);
			snprintf(path, sizeof(path), BASE "/merged/r%d_U%d", r, i);
			mkfile(path);
		}
		sync();
		long long before = free_kb();

		for (int i = 0; i < n; i++) {
			snprintf(path, sizeof(path), BASE "/lower/r%d_L%d", r, i);
			if (unlink(path))
				perror(path);
			snprintf(path, sizeof(path), BASE "/upper/r%d_U%d", r, i);
			if (unlink(path))
				perror(path);
		}
		sync();
		long long held = free_kb();

		printf("OVL_ROUND %d unlinked 2x%d; free before %lld KiB, after unlink %lld KiB (held %lld KiB)\n",
		       r, n, before, held, before + 2LL * n * FILE_KB - held);
		/* cold bitmaps and inode tables, keep dentries */
		wfile("/proc/sys/vm/drop_caches", "1");
		if (reclaim_mode)
			hog(held + (long long)n * FILE_KB, hog_max);
		else
			wfile("/proc/sys/vm/drop_caches", "2");
		sleep(1);
		sync();
		printf("OVL_ROUND %d after reclaim: free %lld KiB (released %lld KiB)\n",
		       r, free_kb(), free_kb() - held);
		/* the overlay may still hold some: unmounting releases the rest
		 * in process context */
		if (umount(BASE "/merged"))
			perror("umount");
		sync();
		printf("OVL_ROUND %d after umount: free %lld KiB\n", r, free_kb());
		wfile("/proc/sys/vm/drop_caches", "4");
	}
	printf("OVL_DONE\n");
	return 0;
}
