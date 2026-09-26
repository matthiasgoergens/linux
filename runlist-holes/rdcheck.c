/*
 * rdcheck.c - read a file written by mkfrag and classify every 4 KiB block.
 *
 * Usage: rdcheck <path> <id-hex> <mode> [offset]
 *   mode "seq":   read() the whole file sequentially in 64 KiB chunks.  On a
 *                 failed read(), report it and lseek past that chunk.
 *   mode "pread": one 4 KiB pread() at <offset> only.
 *
 * Every read() result is printed as "READ off=<o> ret=<r> errno=<e>" when it
 * is not a full-length success, and the block classifications are printed
 * run-length encoded:  "RANGE [start,end) bytes vcn [a,b] STATUS" with
 * STATUS one of OK (matches pattern), ZERO (all zero bytes), BAD (other),
 * ERR (read failed).  A final SUMMARY line gives counts per status.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

#define BLK 4096
#define CHUNK (64 * 1024)

enum st { S_OK, S_ZERO, S_BAD, S_ERR, S_NONE };
static const char *names[] = { "OK", "ZERO", "BAD", "ERR" };
static long counts[4];
static enum st cur = S_NONE;
static uint64_t cur_start;

static void emit(uint64_t end)
{
	if (cur == S_NONE)
		return;
	printf("RANGE [%llu,%llu) vcn [%llu,%llu] %s\n",
	       (unsigned long long)cur_start, (unsigned long long)end,
	       (unsigned long long)cur_start / BLK,
	       (unsigned long long)(end - 1) / BLK, names[cur]);
}

static void note(uint64_t off, enum st s)
{
	counts[s]++;
	if (s != cur) {
		emit(off);
		cur = s;
		cur_start = off;
	}
}

static enum st classify(const uint8_t *b, uint64_t id, uint64_t off)
{
	int zero = 1, ok = 1;

	for (int i = 0; i < BLK; i += 8) {
		uint64_t w;

		memcpy(&w, b + i, 8);
		if (w)
			zero = 0;
		if (w != ((id << 56) | (off + i)))
			ok = 0;
	}
	return ok ? S_OK : zero ? S_ZERO : S_BAD;
}

int main(int argc, char **argv)
{
	static uint8_t buf[CHUNK];
	uint64_t id, off = 0, size;
	int fd;

	if (argc < 4)
		return 2;
	id = strtoull(argv[2], NULL, 16);
	fd = open(argv[1], O_RDONLY);
	if (fd < 0) {
		printf("OPEN %s failed errno=%d (%s)\n", argv[1], errno, strerror(errno));
		return 1;
	}
	size = lseek(fd, 0, SEEK_END);
	lseek(fd, 0, SEEK_SET);
	printf("FILE %s size=%llu\n", argv[1], (unsigned long long)size);

	if (!strcmp(argv[3], "pread")) {
		off = strtoull(argv[4], NULL, 0);
		ssize_t r = pread(fd, buf, BLK, off);

		printf("READ off=%llu ret=%zd errno=%d (%s)\n",
		       (unsigned long long)off, r, r < 0 ? errno : 0,
		       r < 0 ? strerror(errno) : "-");
		if (r == BLK)
			note(off, classify(buf, id, off));
		else
			note(off, S_ERR);
		emit(off + BLK);
	} else {
		while (off < size) {
			ssize_t r = read(fd, buf, CHUNK);

			if (r < 0) {
				printf("READ off=%llu ret=%zd errno=%d (%s)\n",
				       (unsigned long long)off, r, errno, strerror(errno));
				for (uint64_t o = off; o < off + CHUNK && o < size; o += BLK)
					note(o, S_ERR);
				off += CHUNK;
				lseek(fd, off, SEEK_SET);
				continue;
			}
			if (r == 0) {
				printf("READ off=%llu ret=0 (unexpected EOF)\n",
				       (unsigned long long)off);
				break;
			}
			if (r != CHUNK && off + r != size)
				printf("READ off=%llu ret=%zd (short)\n",
				       (unsigned long long)off, r);
			for (ssize_t i = 0; i + BLK <= r; i += BLK)
				note(off + i, classify(buf + i, id, off + i));
			off += r;
		}
		emit(off);
	}
	printf("SUMMARY OK=%ld ZERO=%ld BAD=%ld ERR=%ld\n",
	       counts[S_OK], counts[S_ZERO], counts[S_BAD], counts[S_ERR]);
	close(fd);
	return 0;
}
