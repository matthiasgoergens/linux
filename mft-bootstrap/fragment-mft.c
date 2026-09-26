/*
 * fragment-mft.c -- grow a legitimately fragmented $MFT on an NTFS image
 * file, entirely in userspace through libntfs-3g (no kernel mount, no loop
 * device).
 *
 *   fragment-mft <image> <nfill> <ntiny>
 *
 * Phase 1: create up to <nfill> files f00000.. of 4 KiB (8 clusters at
 *          512-byte clusters), stopping while MARGIN clusters are still
 *          free.  Running into ENOSPC is avoided on purpose: a create that
 *          failed with ENOSPC left the root index's in-memory runlist
 *          inconsistent ("Run lists overlap") and every later create
 *          failed.  Consecutive files land in consecutive clusters.
 * Phase 2: delete every odd-numbered f file, leaving 4 KiB holes between
 *          kept files across the whole volume, MFT zone included.
 * Phase 3: create <ntiny> small resident files t00000...  Once the freed
 *          MFT records are reused, $MFT has to grow, and every growth
 *          (16 records = 32 clusters, mft.c ntfs_mft_data_extend_allocation)
 *          can only be satisfied from the 8-cluster holes, so each growth
 *          adds several runs to $MFT/$DATA.  Once the runlist no longer
 *          fits in record 0, libntfs-3g gives $MFT an attribute list and
 *          moves $DATA extents into extent records.
 *
 * File contents are a deterministic function of the name, so a checksum
 * taken inside the VM can be compared across kernels and against this
 * program's own manifest (printed on stdout as "KEEP <name> <size>").
 */
#include "config.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <ntfs-3g/types.h>
#include <ntfs-3g/volume.h>
#include <ntfs-3g/inode.h>
#include <ntfs-3g/dir.h>
#include <ntfs-3g/attrib.h>
#include <ntfs-3g/unistr.h>
#include <ntfs-3g/logging.h>

#define FILL_SIZE 4096
#define MARGIN 256	/* clusters left free after phase 1 */

static ntfs_volume *vol;

static void fill(char *buf, size_t len, const char *name)
{
	size_t i, n = strlen(name);

	for (i = 0; i < len; i++)
		buf[i] = name[i % n] ^ (char)(i * 131);
}

static int create_file(const char *name, const char *data, size_t len)
{
	ntfschar *uname = NULL;
	int ulen = ntfs_mbstoucs(name, &uname);
	ntfs_inode *dir, *ni;
	ntfs_attr *na;
	int ret = -1;

	if (ulen < 0)
		return -1;
	dir = ntfs_pathname_to_inode(vol, NULL, "/");
	if (!dir)
		goto out;
	ni = ntfs_create(dir, const_cpu_to_le32(0), uname, ulen, S_IFREG);
	/*
	 * Close the directory before touching the new file, as ntfscp does
	 * (ntfsprogs/ntfscp.c).  Closing the file syncs its FILE_NAME into
	 * the parent's index through a second instance of the parent; with
	 * our instance still open the two went out of step ("Index lookup
	 * failed, inode 5", later "Run lists overlap") after ~90 files.
	 */
	ntfs_inode_close(dir);
	if (!ni) {
		ret = -errno;
		goto out;
	}
	na = ntfs_attr_open(ni, AT_DATA, AT_UNNAMED, 0);
	if (na) {
		s64 w = ntfs_attr_pwrite(na, 0, len, data);

		ret = (w == (s64)len) ? 0 : -ENOSPC;
		ntfs_attr_close(na);
	}
	ntfs_inode_close(ni);
	if (ret) {
		/* Do not leave a short file behind: remove it again. */
		dir = ntfs_pathname_to_inode(vol, NULL, "/");
		ni = ntfs_pathname_to_inode(vol, NULL, name);
		if (dir && ni)
			ntfs_delete(vol, name, ni, dir, uname, ulen);
	}
out:
	free(uname);
	return ret;
}

static int delete_file(const char *name)
{
	ntfschar *uname = NULL;
	int ulen = ntfs_mbstoucs(name, &uname);
	ntfs_inode *dir, *ni;
	int ret;

	dir = ntfs_pathname_to_inode(vol, NULL, "/");
	ni = ntfs_pathname_to_inode(vol, NULL, name);
	if (ulen < 0 || !dir || !ni)
		return -1;
	/* ntfs_delete() closes both ni and dir. */
	ret = ntfs_delete(vol, name, ni, dir, uname, ulen);
	free(uname);
	return ret;
}

int main(int argc, char **argv)
{
	static char buf[FILL_SIZE];
	char name[32];
	int nfill, ntiny, i, made = 0, tiny = 0, err;

	if (argc != 4) {
		fprintf(stderr, "usage: %s <image> <nfill> <ntiny>\n", argv[0]);
		return 2;
	}
	nfill = atoi(argv[2]);
	ntiny = atoi(argv[3]);
	ntfs_log_set_handler(ntfs_log_handler_stderr);
	vol = ntfs_mount(argv[1], NTFS_MNT_NONE);
	if (!vol) {
		perror("ntfs_mount (libntfs-3g, userspace)");
		return 1;
	}
	/* free_clusters is computed lazily; it reads 0 until this runs. */
	if (ntfs_volume_get_free_space(vol)) {
		perror("ntfs_volume_get_free_space");
		return 1;
	}
	for (i = 0; i < nfill && vol->free_clusters > MARGIN; i++) {
		snprintf(name, sizeof(name), "f%05d", i);
		fill(buf, FILL_SIZE, name);
		if ((err = create_file(name, buf, FILL_SIZE))) {
			fprintf(stderr, "create %s failed: %d errno %d\n",
				name, err, errno);
			break;
		}
		made++;
	}
	printf("PHASE1 created %d fill files, %lld clusters free\n", made,
	       (long long)vol->free_clusters);
	for (i = 1; i < made; i += 2) {
		snprintf(name, sizeof(name), "f%05d", i);
		if (delete_file(name)) {
			fprintf(stderr, "delete %s failed\n", name);
			return 1;
		}
	}
	for (i = 0; i < made; i += 2)
		printf("KEEP f%05d %d\n", i, FILL_SIZE);
	for (i = 0; i < ntiny; i++) {
		snprintf(name, sizeof(name), "t%05d", i);
		fill(buf, 64, name);
		if ((err = create_file(name, buf, 64))) {
			fprintf(stderr, "create %s failed: %d errno %d\n",
				name, err, errno);
			break;
		}
		printf("KEEP %s 64\n", name);
		tiny++;
	}
	printf("PHASE3 created %d tiny files\n", tiny);
	if (ntfs_umount(vol, FALSE)) {
		perror("ntfs_umount");
		return 1;
	}
	return 0;
}
