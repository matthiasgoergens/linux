#!/bin/bash
# run-vm.sh <bzImage> <image> <initramfs> <log> [secs] -- boot the kernel
# under qemu with the image as a virtio disk in snapshot mode (nothing written
# back, nothing mounted on the host) and save the serial console.
set -u
BZ="${1:?bzImage}" IMG="${2:?image}" INITRD="${3:?initramfs}" LOG="${4:?log}" SECS="${5:-300}"
{
	echo "# bzImage $BZ sha256 $(sha256sum "$BZ" | cut --delimiter=' ' --fields=1)"
	echo "# image $IMG sha256 $(sha256sum "$IMG" | cut --delimiter=' ' --fields=1)"
	echo "# initramfs $INITRD sha256 $(sha256sum "$INITRD" | cut --delimiter=' ' --fields=1)"
	echo "# date $(date --iso-8601=seconds)"
	timeout --signal=KILL "$SECS" qemu-system-x86_64 -kernel "$BZ" -initrd "$INITRD" \
		-append "console=ttyS0 panic=-1 oops=panic" \
		-drive file="$IMG",format=raw,if=virtio,snapshot=on \
		-m 1G -smp 2 -display none -serial stdio -no-reboot -cpu max -enable-kvm -net none
	echo "# qemu exit $?"
} > "$LOG" 2>&1
