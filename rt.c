/*
 * rt.c - deterministic I/O probe for the ntfs zero-read regression matrix.
 *
 * Data written by this tool is self-describing, like mkfrag's: every 8-byte
 * little-endian word at absolute file offset `off` is (tag << 56) | off.
 * Reads classify each 4 KiB block as Z (all zero), the tag letter (correct
 * pattern for that tag), or X (anything else), run-length encoded, plus an
 * FNV-1a 64 hash of every byte returned.  Output is deterministic, so logs
 * from two kernels can be diffed directly.
 *
 * Usage: rt <cmd> <file> [args]
 *   w  <file> <off> <len> <tag> [d]   pwrite (d = O_DIRECT)
 *   r  <file> <off> <len> [d]         pread  (d = O_DIRECT)
 *   mw <file> <off> <len> <tag>       write through a MAP_SHARED mapping, msync
 *   mr <file> <off> <len>             read through a mapping
 *   fa <file> <mode> <off> <len>      fallocate; mode alloc|keep|punch|collapse|insert
 *   tr <file> <size>                  ftruncate
 *   fs <file>                         open, fsync, close
 *   wf <file> <off> <len> <tag>       pwrite, fsync, close (each rc printed)
 *   sz <file>                         st_size, st_blocks
 *   cx <file> <hexattr>               setxattr system.ntfs_attrib (0x800 compressed)
 *   h  <file>                         read the whole file, classify + hash
 * Every command prints one line starting with "RT".  errno is printed by name.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>
#include <linux/falloc.h>

#define BLK 4096

static const char *en(int e)
{
	switch (e) {
	case 0: return "0";
	case EIO: return "EIO";
	case ENOENT: return "ENOENT";
	case EINVAL: return "EINVAL";
	case ENOSPC: return "ENOSPC";
	case EOPNOTSUPP: return "EOPNOTSUPP";
	case ENOMEM: return "ENOMEM";
	case EFBIG: return "EFBIG";
	case EPERM: return "EPERM";
	default: {
		static char b[32];
		snprintf(b, sizeof(b), "E%d", e);
		return b;
	}
	}
}

static void fill(uint8_t *buf, uint64_t off, size_t len, int tag)
{
	for (size_t i = 0; i < len; i += 8) {
		uint64_t w = ((uint64_t)tag << 56) | (off + i);
		memcpy(buf + i, &w, 8);
	}
}

static uint64_t fnv(const uint8_t *p, size_t n)
{
	uint64_t h = 0xcbf29ce484222325ULL;

	for (size_t i = 0; i < n; i++) {
		h ^= p[i];
		h *= 0x100000001b3ULL;
	}
	return h;
}

static char cls(const uint8_t *b, uint64_t off, size_t n)
{
	uint64_t w0;
	int tag, zero = 1;

	for (size_t i = 0; i < n; i++)
		if (b[i]) {
			zero = 0;
			break;
		}
	if (zero)
		return 'Z';
	memcpy(&w0, b, 8);
	tag = w0 >> 56;
	for (size_t i = 0; i + 8 <= n; i += 8) {
		uint64_t w;

		memcpy(&w, b + i, 8);
		if (w != (((uint64_t)tag << 56) | (off + i)))
			return 'X';
	}
	return (tag >= 'A' && tag <= 'z') ? tag : 'X';
}

/* Print "@vcn C*n C*n ..." for [off, off+n) of buf. */
static void rle(const uint8_t *buf, uint64_t off, size_t n)
{
	char cur = 0;
	long cnt = 0;

	printf(" @%llu", (unsigned long long)(off / BLK));
	for (size_t i = 0; i < n; i += BLK) {
		size_t l = n - i < BLK ? n - i : BLK;
		char c = cls(buf + i, off + i, l);

		if (c == cur) {
			cnt++;
			continue;
		}
		if (cnt)
			printf(" %c*%ld", cur, cnt);
		cur = c;
		cnt = 1;
	}
	if (cnt)
		printf(" %c*%ld", cur, cnt);
}

static void *abuf(size_t len)
{
	void *p;

	if (posix_memalign(&p, BLK, len ? len : BLK)) {
		perror("posix_memalign");
		exit(2);
	}
	memset(p, 0, len);
	return p;
}

static sigjmp_buf jb;
static void onbus(int s)
{
	(void)s;
	siglongjmp(jb, 1);
}

int main(int argc, char **argv)
{
	const char *cmd, *f;
	int fd, e;
	ssize_t rc;

	if (argc < 3) {
		fprintf(stderr, "usage: see source\n");
		return 2;
	}
	cmd = argv[1];
	f = argv[2];
	setvbuf(stdout, NULL, _IOLBF, 0);

	if (!strcmp(cmd, "w") || !strcmp(cmd, "wf")) {
		uint64_t off = strtoull(argv[3], NULL, 0);
		size_t len = strtoull(argv[4], NULL, 0);
		int tag = argv[5][0];
		int d = argc > 6 && argv[6][0] == 'd';
		uint8_t *b = abuf(len);

		fill(b, off, len, tag);
		fd = open(f, O_RDWR | O_CREAT | (d ? O_DIRECT : 0), 0644);
		if (fd < 0) {
			printf("RT %s %s open=-1 errno=%s\n", cmd, f, en(errno));
			return 1;
		}
		rc = pwrite(fd, b, len, off);
		e = rc < 0 ? errno : 0;
		printf("RT %s %s off=%llu len=%zu tag=%c%s ret=%zd errno=%s", cmd, f,
		       (unsigned long long)off, len, tag, d ? " direct" : "", rc, en(e));
		if (!strcmp(cmd, "wf")) {
			int r2 = fsync(fd);

			printf(" fsync=%d errno=%s", r2, en(r2 ? errno : 0));
			r2 = close(fd);
			printf(" close=%d errno=%s", r2, en(r2 ? errno : 0));
		} else {
			close(fd);
		}
		printf("\n");
		return 0;
	}
	if (!strcmp(cmd, "r") || !strcmp(cmd, "h")) {
		int h = !strcmp(cmd, "h");
		int d = !h && argc > 5 && argv[5][0] == 'd';
		uint64_t off = h ? 0 : strtoull(argv[3], NULL, 0);
		size_t len;
		uint8_t *b;
		struct stat st;

		fd = open(f, O_RDONLY | (d ? O_DIRECT : 0));
		if (fd < 0) {
			printf("RT %s %s open=-1 errno=%s\n", cmd, f, en(errno));
			return 1;
		}
		fstat(fd, &st);
		len = h ? (size_t)st.st_size : strtoull(argv[4], NULL, 0);
		b = abuf(len);
		if (h) {
			/* Chunked, so an error shows where it starts. */
			size_t done = 0;

			e = 0;
			while (done < len) {
				size_t c = len - done < 65536 ? len - done : 65536;

				rc = pread(fd, b + done, c, done);
				if (rc <= 0) {
					e = rc < 0 ? errno : 0;
					break;
				}
				done += rc;
			}
			printf("RT h %s size=%lld read=%zu errno=%s fnv=%016llx", f,
			       (long long)st.st_size, done, en(e),
			       (unsigned long long)fnv(b, done));
			rle(b, 0, done);
		} else {
			rc = pread(fd, b, len, off);
			e = rc < 0 ? errno : 0;
			printf("RT r %s off=%llu len=%zu%s ret=%zd errno=%s", f,
			       (unsigned long long)off, len, d ? " direct" : "", rc, en(e));
			if (rc > 0) {
				printf(" fnv=%016llx", (unsigned long long)fnv(b, rc));
				rle(b, off, rc);
			}
		}
		printf("\n");
		close(fd);
		return 0;
	}
	if (!strcmp(cmd, "mw") || !strcmp(cmd, "mr")) {
		int w = !strcmp(cmd, "mw");
		uint64_t off = strtoull(argv[3], NULL, 0);
		size_t len = strtoull(argv[4], NULL, 0);
		volatile uint8_t *m;
		uint8_t *b = abuf(len);
		struct sigaction sa = { .sa_handler = onbus };

		sigaction(SIGBUS, &sa, NULL);
		fd = open(f, w ? O_RDWR : O_RDONLY);
		if (fd < 0) {
			printf("RT %s %s open=-1 errno=%s\n", cmd, f, en(errno));
			return 1;
		}
		m = mmap(NULL, len, w ? PROT_READ | PROT_WRITE : PROT_READ,
			 MAP_SHARED, fd, off);
		if (m == MAP_FAILED) {
			printf("RT %s %s mmap=-1 errno=%s\n", cmd, f, en(errno));
			return 1;
		}
		printf("RT %s %s off=%llu len=%zu", cmd, f, (unsigned long long)off, len);
		if (sigsetjmp(jb, 1)) {
			printf(" SIGBUS\n");
			return 0;
		}
		if (w) {
			fill(b, off, len, argv[5][0]);
			for (size_t i = 0; i < len; i++)
				m[i] = b[i];
			rc = msync((void *)m, len, MS_SYNC);
			printf(" tag=%c msync=%zd errno=%s", argv[5][0], rc,
			       en(rc ? errno : 0));
		} else {
			for (size_t i = 0; i < len; i++)
				b[i] = m[i];
			printf(" fnv=%016llx", (unsigned long long)fnv(b, len));
			rle(b, off, len);
		}
		printf("\n");
		munmap((void *)m, len);
		close(fd);
		return 0;
	}
	if (!strcmp(cmd, "fa")) {
		const char *mo = argv[3];
		uint64_t off = strtoull(argv[4], NULL, 0);
		uint64_t len = strtoull(argv[5], NULL, 0);
		int mode = 0;

		if (!strcmp(mo, "keep"))
			mode = FALLOC_FL_KEEP_SIZE;
		else if (!strcmp(mo, "punch"))
			mode = FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE;
		else if (!strcmp(mo, "collapse"))
			mode = FALLOC_FL_COLLAPSE_RANGE;
		else if (!strcmp(mo, "insert"))
			mode = FALLOC_FL_INSERT_RANGE;
		fd = open(f, O_RDWR | O_CREAT, 0644);
		rc = fallocate(fd, mode, off, len);
		printf("RT fa %s %s off=%llu len=%llu ret=%zd errno=%s\n", f, mo,
		       (unsigned long long)off, (unsigned long long)len, rc,
		       en(rc ? errno : 0));
		close(fd);
		return 0;
	}
	if (!strcmp(cmd, "tr")) {
		uint64_t sz = strtoull(argv[3], NULL, 0);

		fd = open(f, O_RDWR | O_CREAT, 0644);
		rc = ftruncate(fd, sz);
		printf("RT tr %s size=%llu ret=%zd errno=%s\n", f,
		       (unsigned long long)sz, rc, en(rc ? errno : 0));
		close(fd);
		return 0;
	}
	if (!strcmp(cmd, "fs")) {
		fd = open(f, O_RDWR);
		rc = fsync(fd);
		printf("RT fs %s fsync=%zd errno=%s", f, rc, en(rc ? errno : 0));
		rc = close(fd);
		printf(" close=%zd errno=%s\n", rc, en(rc ? errno : 0));
		return 0;
	}
	if (!strcmp(cmd, "sz")) {
		struct stat st;

		rc = stat(f, &st);
		printf("RT sz %s ret=%zd size=%lld blocks=%lld\n", f, rc,
		       (long long)st.st_size, (long long)st.st_blocks);
		return 0;
	}
	if (!strcmp(cmd, "cx")) {
		uint32_t a = strtoul(argv[3], NULL, 0);

		fd = open(f, O_RDWR | O_CREAT, 0644);
		close(fd);
		rc = setxattr(f, "system.ntfs_attrib", &a, sizeof(a), 0);
		printf("RT cx %s attr=0x%x ret=%zd errno=%s\n", f, a, rc,
		       en(rc ? errno : 0));
		return 0;
	}
	fprintf(stderr, "unknown cmd %s\n", cmd);
	return 2;
}
