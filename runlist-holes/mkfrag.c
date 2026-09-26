/*
 * mkfrag.c - populate a freshly mkntfs'd image with a heavily fragmented
 * file, purely in userspace via libntfs-3g (no mount of any kind).
 *
 * Usage: mkfrag <image> <rounds>
 *
 * Creates /victim and /pad in the root directory and, for each round,
 * appends one 4 KiB block to victim and then one to pad.  The allocator
 * therefore interleaves them, and victim ends up with ~<rounds> runs, which
 * overflows its base MFT record and forces an $ATTRIBUTE_LIST with extent
 * records holding later parts of the $DATA runlist.
 *
 * Content is self-describing: every 8-byte little-endian word at byte
 * offset `off` of file `id` is (id << 56) | off.  A reader can therefore
 * tell correct data, zeros, and wrong data apart without a reference copy.
 * id 0x56 ('V') = victim, 0x50 ('P') = pad.
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

#define BLK 4096

static ntfs_attr *make_file(ntfs_volume *vol, ntfs_inode *root,
			    const char *name, ntfs_inode **nip)
{
	ntfschar *uname = NULL;
	int len = ntfs_mbstoucs(name, &uname);
	ntfs_inode *ni;
	ntfs_attr *na;

	if (len < 0) {
		perror("ntfs_mbstoucs");
		exit(1);
	}
	ni = ntfs_create(root, const_cpu_to_le32(0), uname, len, S_IFREG);
	if (!ni) {
		perror("ntfs_create");
		exit(1);
	}
	na = ntfs_attr_open(ni, AT_DATA, AT_UNNAMED, 0);
	if (!na) {
		perror("ntfs_attr_open");
		exit(1);
	}
	free(uname);
	*nip = ni;
	return na;
}

static void fill(uint8_t *buf, uint64_t id, uint64_t off)
{
	for (int i = 0; i < BLK; i += 8) {
		uint64_t w = (id << 56) | (off + i);
		memcpy(buf + i, &w, 8);
	}
}

int main(int argc, char **argv)
{
	ntfs_volume *vol;
	ntfs_inode *root, *vi, *pi;
	ntfs_attr *va, *pa;
	uint8_t buf[BLK];
	long rounds;

	if (argc != 3) {
		fprintf(stderr, "usage: %s <image> <rounds>\n", argv[0]);
		return 2;
	}
	rounds = strtol(argv[2], NULL, 0);
	vol = ntfs_mount(argv[1], 0);
	if (!vol) {
		perror("ntfs_mount");
		return 1;
	}
	root = ntfs_inode_open(vol, FILE_root);
	if (!root) {
		perror("open root");
		return 1;
	}
	va = make_file(vol, root, "victim", &vi);
	pa = make_file(vol, root, "pad", &pi);
	for (long r = 0; r < rounds; r++) {
		uint64_t off = (uint64_t)r * BLK;

		fill(buf, 0x56, off);
		if (ntfs_attr_pwrite(va, off, BLK, buf) != BLK) {
			fprintf(stderr, "victim write r=%ld: %s\n", r, strerror(errno));
			return 1;
		}
		fill(buf, 0x50, off);
		if (ntfs_attr_pwrite(pa, off, BLK, buf) != BLK) {
			fprintf(stderr, "pad write r=%ld: %s\n", r, strerror(errno));
			return 1;
		}
	}
	ntfs_attr_close(va);
	ntfs_attr_close(pa);
	if (ntfs_inode_close(vi) || ntfs_inode_close(pi) ||
	    ntfs_inode_close(root)) {
		perror("inode close");
		return 1;
	}
	if (ntfs_umount(vol, FALSE)) {
		perror("umount");
		return 1;
	}
	printf("wrote %ld rounds (%ld bytes per file)\n", rounds, rounds * BLK);
	return 0;
}
