/*
 * mksparse.c - write /sp into a freshly mkntfs'd image through libntfs-3g
 * (userspace, no mount): <n> chunks of 512 bytes at a stride of 1024 bytes,
 * the gaps left as holes, then <rest> more bytes, so that the file has
 * about 2 * <n> runs (enough to need an $ATTRIBUTE_LIST and extent records)
 * and a size that is not a multiple of 4096.  Content is self-describing
 * like mkfrag's: each 8-byte word at offset off is (0x53 << 56) | off.
 *
 * Usage: mksparse <image> <n> <rest>
 */
#include "config.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <sys/stat.h>
#include "types.h"
#include "volume.h"
#include "inode.h"
#include "attrib.h"
#include "dir.h"
#include "unistr.h"
#include "layout.h"

static void fill(uint8_t *buf, size_t len, uint64_t off)
{
	for (size_t i = 0; i < len; i += 8) {
		uint64_t w = (0x53ULL << 56) | (off + i);

		memcpy(buf + i, &w, len - i < 8 ? len - i : 8);
	}
}

int main(int argc, char **argv)
{
	static uint8_t buf[4096];
	ntfs_volume *vol;
	ntfs_inode *root, *ni;
	ntfs_attr *na;
	ntfschar *uname = NULL;
	long n, rest;
	int len;

	if (argc != 4) {
		fprintf(stderr, "usage: %s <image> <n> <rest>\n", argv[0]);
		return 2;
	}
	n = strtol(argv[2], NULL, 0);
	rest = strtol(argv[3], NULL, 0);
	if (rest < 0 || rest > (long)sizeof(buf))
		return 2;
	vol = ntfs_mount(argv[1], 0);
	if (!vol) { perror("ntfs_mount"); return 1; }
	root = ntfs_inode_open(vol, FILE_root);
	if (!root) { perror("open root"); return 1; }
	len = ntfs_mbstoucs("sp", &uname);
	ni = ntfs_create(root, const_cpu_to_le32(0), uname, len, S_IFREG);
	if (!ni) { perror("ntfs_create"); return 1; }
	na = ntfs_attr_open(ni, AT_DATA, AT_UNNAMED, 0);
	if (!na) { perror("ntfs_attr_open"); return 1; }
	for (long i = 0; i < n; i++) {
		uint64_t off = (uint64_t)i * 1024;

		fill(buf, 512, off);
		if (ntfs_attr_pwrite(na, off, 512, buf) != 512) {
			fprintf(stderr, "write %ld: %s\n", i, strerror(errno));
			return 1;
		}
	}
	fill(buf, rest, (uint64_t)n * 1024);
	if (rest && ntfs_attr_pwrite(na, (uint64_t)n * 1024, rest, buf) != rest) {
		fprintf(stderr, "last write: %s\n", strerror(errno));
		return 1;
	}
	printf("sp: data %lld alloc %lld init %lld\n", (long long)na->data_size,
	       (long long)na->allocated_size, (long long)na->initialized_size);
	ntfs_attr_close(na);
	if (ntfs_inode_close(ni) || ntfs_inode_close(root)) { perror("close"); return 1; }
	if (ntfs_umount(vol, FALSE)) { perror("umount"); return 1; }
	return 0;
}
