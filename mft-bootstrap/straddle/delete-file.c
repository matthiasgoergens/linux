/*
 * delete-file.c -- delete one file from the root directory of an NTFS image
 * file through libntfs-3g, entirely in userspace (no kernel mount, no loop
 * device).  Used to free the $MFT record that the straddle images then
 * reuse for an $MFT extent record.
 *
 *   delete-file <image> <name>
 */
#include "config.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <ntfs-3g/types.h>
#include <ntfs-3g/volume.h>
#include <ntfs-3g/inode.h>
#include <ntfs-3g/dir.h>
#include <ntfs-3g/unistr.h>
#include <ntfs-3g/logging.h>

int main(int argc, char **argv)
{
	ntfschar *uname = NULL;
	ntfs_volume *vol;
	ntfs_inode *dir, *ni;
	int ulen;

	if (argc != 3) {
		fprintf(stderr, "usage: %s <image> <name>\n", argv[0]);
		return 2;
	}
	ntfs_log_set_handler(ntfs_log_handler_stderr);
	vol = ntfs_mount(argv[1], NTFS_MNT_NONE);
	if (!vol) {
		perror("ntfs_mount (libntfs-3g, userspace)");
		return 1;
	}
	ulen = ntfs_mbstoucs(argv[2], &uname);
	dir = ntfs_pathname_to_inode(vol, NULL, "/");
	ni = ntfs_pathname_to_inode(vol, NULL, argv[2]);
	if (ulen < 0 || !dir || !ni) {
		perror("lookup");
		return 1;
	}
	printf("deleting %s = inode %llu\n", argv[2],
	       (unsigned long long)ni->mft_no);
	/* ntfs_delete() closes both ni and dir. */
	if (ntfs_delete(vol, argv[2], ni, dir, uname, ulen)) {
		perror("ntfs_delete");
		return 1;
	}
	free(uname);
	if (ntfs_umount(vol, FALSE)) {
		perror("ntfs_umount");
		return 1;
	}
	return 0;
}
