#!/bin/sh
# Test 1: lazytime + memory pressure (repro_memalloc) for DUR seconds.
# usage: run-memalloc.sh <seconds> <hog-MiB> [sync]
DUR=$1; HOG=$2; VAR=$3
echo 4 > /proc/sys/vm/drop_caches   # silence drop_caches msgs, print totals
CHURN_DIR=${CHURN_DIR:-/tmp/churn} /root/repro_memalloc "$HOG" $VAR &
PID=$!
(sleep 5; echo "MOUNTS:"; cat /proc/mounts) &
sleep "$DUR"
sh /root/killall_comm.sh repro_memalloc
wait $PID 2>/dev/null
sleep 2
echo 4 > /proc/sys/vm/drop_caches
echo MEMALLOC_DONE
