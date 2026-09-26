#!/bin/sh
# Test 2: N pm_test=devices hibernation cycles while repro_memalloc runs
# (ext4 churn, sync variant) and, before each cycle, 300 linked 200 KiB
# files are written (fsync before close) so ext4 gives them inode preallocations
# (Jan's 5/5 marks those I_DEFER_RECLAIM).
# usage: run-hib.sh <cycles> <hog-MiB> <freeze_filesystems 0|1> <files>
N=$1; HOG=$2; FF=$3; NF=$4
echo 4 > /proc/sys/vm/drop_caches
if [ -e /sys/power/freeze_filesystems ]; then
	echo "$FF" > /sys/power/freeze_filesystems
	echo "HIB freeze_filesystems=$(cat /sys/power/freeze_filesystems)"
else
	echo "HIB freeze_filesystems absent"
fi
CHURN_DIR=/root/churn /root/repro_memalloc "$HOG" sync > /root/memalloc.out 2>&1 &
PID=$!
mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null
echo 1 > /sys/kernel/tracing/events/writeback/inode_reclaim_update_stat/enable 2>/dev/null
sleep 45
i=1
while [ $i -le $N ]; do
	mkdir -p /root/pa/$i
	j=0
	while [ $j -lt 300 ]; do
		dd if=/dev/zero of=/root/pa/$i/f$j bs=200k count=1 conv=fsync 2>/dev/null
		j=$((j+1))
	done
	sync
	[ $i -gt 1 ] && rm -rf /root/pa/$((i-1))
	/root/hibernate_smoke "$NF" > /root/hib.out 2>&1
	echo "HIB_CYCLE $i rc=$? $(grep -E 'HB_HIBERNATE_TEST_OK|HB_DISK_FAILED|HB_DEFER_EXEC' /root/hib.out | tr '\n' ' ')"
	sleep 5
	i=$((i+1))
done
sh /root/killall_comm.sh repro_memalloc
wait $PID 2>/dev/null
sleep 2
echo 4 > /proc/sys/vm/drop_caches
echo HIB_DONE
