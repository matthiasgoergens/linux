#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev 2>/dev/null
echo "=== AFFS EXTCACHE TEST START ==="
uname -a
mkdir -p /mnt
mount -t affs -o rw /dev/vda /mnt && echo "MOUNT RW OK" || echo "MOUNT FAILED"
df /mnt
/affs_test
echo "affs_test exit=$?"
dmesg | grep -iE 'kasan|ubsan|BUG|affs' | head -20
echo "=== AFFS EXTCACHE TEST END ==="
poweroff -f
