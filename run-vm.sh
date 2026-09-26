#!/bin/bash
# run-vm-affs.sh <bzImage> <logfile> [timeout-seconds]
# Boot <bzImage> with the writable AFFS image as a virtio disk (snapshot=on,
# writes are discarded) and run the affs extcache test from the initramfs.
set -u
cd "$(dirname "$0")"
BZIMAGE="${1:?bzImage}"
LOG="${2:?logfile}"
SECS="${3:-1500}"
IMG=affs-big.img
CPIO=affsrw-initramfs.cpio.gz

if [ ! -f "$CPIO" ]; then
	(cd rootfs && find . | cpio -o -H newc --quiet | gzip -9) > "$CPIO"
fi

timeout --signal=KILL "$SECS" qemu-system-x86_64 \
	-kernel "$BZIMAGE" \
	-initrd "$CPIO" \
	-append "console=ttyS0 panic=-1 oops=panic" \
	-drive file="$IMG",format=raw,if=virtio,snapshot=on \
	-m 1G -smp 1 -display none -serial stdio -no-reboot ${QEMU_MACHINE_ARGS:-} \
	-cpu "${QEMU_CPU:-max}" -net none > "$LOG" 2>&1
echo "run-vm-affs: exit=$? log=$LOG"
