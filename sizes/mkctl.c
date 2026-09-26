/*
 * mkctl.c - positive control for patch 6: write, with
 * libntfs-3g (userspace, no mount), two valid files whose allocated_size
 * semantics differ from a plain file:
 *   /cz  compressed (FILE_ATTR_COMPRESSED): 64 KiB compressible, 64 KiB
 *        incompressible, a 128 KiB hole, then 40000 compressible bytes, so
 *        data_size (302144) is not a multiple of the compression block
 *   /sp  written at 0 and at 8 MiB + 123, leaving a hole between
 * Usage: mkctl <image>
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

static ntfs_attr *make_file(ntfs_inode *root, const char *name, ntfs_inode **nip)
{
	ntfschar *uname = NULL;
	int len = ntfs_mbstoucs(name, &uname);
	ntfs_inode *ni = ntfs_create(root, const_cpu_to_le32(0), uname, len, S_IFREG);
	ntfs_attr *na;

	if (!ni) { perror("ntfs_create"); exit(1); }
	na = ntfs_attr_open(ni, AT_DATA, AT_UNNAMED, 0);
	if (!na) { perror("ntfs_attr_open"); exit(1); }
	free(uname);
	*nip = ni;
	return na;
}

static void pw(ntfs_attr *na, s64 off, s64 len, const uint8_t *buf, const char *what)
{
	if (ntfs_attr_pwrite(na, off, len, buf) != len) {
		fprintf(stderr, "%s write off=%lld: %s\n", what, (long long)off, strerror(errno));
		exit(1);
	}
}

int main(int argc, char **argv)
{
	static uint8_t comp[65536], rnd[65536];
	ntfs_volume *vol;
	ntfs_inode *root, *ci, *si;
	ntfs_attr *ca, *sa;
	uint64_t x = 0x9e3779b97f4a7c15ULL;

	if (argc != 2) { fprintf(stderr, "usage: %s <image>\n", argv[0]); return 2; }
	for (size_t i = 0; i < sizeof(comp); i++)
		comp[i] = "compressible "[i % 13];
	for (size_t i = 0; i < sizeof(rnd); i++) {
		x ^= x << 13; x ^= x >> 7; x ^= x << 17;
		rnd[i] = x >> 56;
	}
	vol = ntfs_mount(argv[1], 0);
	if (!vol) { perror("ntfs_mount"); return 1; }
	NVolSetCompression(vol);
	root = ntfs_inode_open(vol, FILE_root);
	if (!root) { perror("open root"); return 1; }
	root->flags |= FILE_ATTR_COMPRESSED;
	ca = make_file(root, "cz", &ci);
	root->flags &= ~FILE_ATTR_COMPRESSED;
	sa = make_file(root, "sp", &si);
	pw(ca, 0, 65536, comp, "cz");
	pw(ca, 65536, 65536, rnd, "cz");
	pw(ca, 262144, 40000, comp, "cz");
	pw(sa, 0, 4096, rnd, "sp");
	pw(sa, 8 * 1048576 + 123, 4096, comp, "sp");
	if (ntfs_attr_pclose(ca)) { perror("pclose"); return 1; }
	printf("cz: attr flags 0x%x data %lld alloc %lld init %lld compressed %lld\n",
	       le16_to_cpu(ca->data_flags), (long long)ca->data_size,
	       (long long)ca->allocated_size, (long long)ca->initialized_size,
	       (long long)ca->compressed_size);
	printf("sp: attr flags 0x%x data %lld alloc %lld init %lld compressed %lld\n",
	       le16_to_cpu(sa->data_flags), (long long)sa->data_size,
	       (long long)sa->allocated_size, (long long)sa->initialized_size,
	       (long long)sa->compressed_size);
	ntfs_attr_close(ca);
	ntfs_attr_close(sa);
	if (ntfs_inode_close(ci) || ntfs_inode_close(si) || ntfs_inode_close(root)) {
		perror("inode close"); return 1;
	}
	if (ntfs_umount(vol, FALSE)) { perror("umount"); return 1; }
	return 0;
}
