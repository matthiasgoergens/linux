/* affs_test.c - trigger and detect the affs_grow_extcache() linear-cache bug.
 *
 * The linear extension cache i_lc (AFFS_LC_SIZE = 512 entries on 4K pages)
 * maps ext >> i_lc_shift to extension-block keys.  When i_extcnt grows past
 * 512 << i_lc_shift, affs_grow_extcache() raises the shift and must compact
 * i_lc (entry i must hold what entry i*off held).  The buggy kernel compacts
 * i_ac instead, leaving i_lc stale: entry i still holds ext block i but is
 * read as ext block i << shift.
 *
 * Recipe (file must stay open the whole time):
 *  1. append until the file spans PRIME_EXTS (320) extension blocks
 *     (< 512, no shift change);
 *  2. drop page cache, pread at ext 200 and 319: fills i_lc[0..319] with
 *     correct keys, i_lc_size = 320;
 *  3. append until TOTAL_EXTS (700) extension blocks: sequential append uses
 *     the i_ext_last fast path and never touches i_lc;
 *  4. drop page cache again, pread at ext 698: lc_idx 698 >= i_lc_size
 *     triggers affs_grow_extcache, i_extcnt 700 > 512 forces shift 0 -> 1
 *     (the buggy compaction runs here);
 *  5. pread random aligned u32s and compare against the written pattern
 *     (u32 at offset o is o/4).  Any mismatch or I/O error = wrong block.
 *
 * Data pattern: the u32 at file offset o (o % 4 == 0) equals o/4, so a
 * misrouted read reveals which offset the data actually came from.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

#define BLK        512
#define KEYS_PER_EXT 72                      /* hashsize for 512-byte blocks */
#define EXT_BYTES  ((uint64_t)KEYS_PER_EXT * BLK)
#define PRIME_EXTS 320
#define TOTAL_EXTS 700
#define NREADS     4000

static void drop_caches(void)
{
	int s;
	sync();
	s = open("/proc/sys/vm/drop_caches", O_WRONLY);
	if (s >= 0) {
		write(s, "3\n", 2);
		close(s);
	} else
		perror("open drop_caches");
}

int main(void)
{
	const char *path = "/mnt/bigfile";
	uint64_t w1 = PRIME_EXTS * EXT_BYTES;
	uint64_t w2 = TOTAL_EXTS * EXT_BYTES;
	uint64_t off = 0;
	uint32_t *buf, v;
	int fd, primed = 0;
	const size_t CH = 256 * 1024;

	setvbuf(stdout, NULL, _IONBF, 0);
	fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
	if (fd < 0) {
		perror("open");
		return 2;
	}
	buf = malloc(CH);
	if (!buf) {
		perror("malloc");
		return 2;
	}

	while (off < w2) {
		size_t n = CH, i;
		ssize_t r;

		if (off + n > w2)
			n = w2 - off;
		for (i = 0; i < n / 4; i++)
			buf[i] = (uint32_t)(off / 4 + i);
		r = write(fd, buf, n);
		if (r != (ssize_t)n) {
			fprintf(stderr, "WRITE FAIL off=%llu r=%zd errno=%d\n",
				(unsigned long long)off, r, errno);
			return 3;
		}
		off += n;
		if (off % (EXT_BYTES * 50) < CH)
			printf("WROTE %llu bytes (~%llu ext blocks)\n",
			       (unsigned long long)off,
			       (unsigned long long)(off / EXT_BYTES));
		if (!primed && off >= w1) {
			/* fill i_lc[0..319] while i_extcnt (320) <= 512 */
			fsync(fd);
			drop_caches();
			if (pread(fd, &v, 4, 200 * EXT_BYTES) != 4)
				perror("prime pread 200");
			if (pread(fd, &v, 4, 319 * EXT_BYTES) != 4)
				perror("prime pread 319");
			printf("PHASE1 primed linear cache at %llu bytes (320 ext blocks)\n",
			       (unsigned long long)off);
			primed = 1;
		}
	}
	printf("PHASE2 grown to %llu bytes (%d ext blocks), fd still open\n",
	       (unsigned long long)off, TOTAL_EXTS);
	fsync(fd);
	drop_caches();

	/* trigger the shift change: ext 698, lc_idx >= i_lc_size=320 */
	if (pread(fd, &v, 4, 698 * EXT_BYTES) != 4)
		perror("trigger pread 698");
	printf("PHASE3 shift-change trigger read done\n");

	{
		int k, mism = 0, errs = 0, mism_low = 0, ok = 0;

		srand(12345);
		for (k = 0; k < NREADS; k++) {
			uint64_t o = (((uint64_t)rand() << 31) ^ rand())
				     % (w2 / 4) * 4;
			uint32_t got;
			ssize_t r = pread(fd, &got, 4, o);

			if (r != 4) {
				errs++;
				if (errs <= 10)
					printf("READERR off=%llu r=%zd errno=%d\n",
					       (unsigned long long)o, r, errno);
				continue;
			}
			if (got != (uint32_t)(o / 4)) {
				uint64_t src = (uint64_t)got * 4;
				mism++;
				if (o < w1)
					mism_low++;
				if (mism <= 20)
					printf("MISMATCH off=%llu (ext %llu) "
					       "got data of off=%llu (ext %llu)\n",
					       (unsigned long long)o,
					       (unsigned long long)(o / EXT_BYTES),
					       (unsigned long long)src,
					       (unsigned long long)(src / EXT_BYTES));
			} else
				ok++;
		}
		printf("RESULT reads=%d ok=%d mismatches=%d (in_primed_region=%d) read_errors=%d\n",
		       NREADS, ok, mism, mism_low, errs);
		if (mism || errs)
			printf("VERDICT BUG-REPRODUCED\n");
		else
			printf("VERDICT CLEAN\n");
	}
	close(fd);
	return 0;
}
