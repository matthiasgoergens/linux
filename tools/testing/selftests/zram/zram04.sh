#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Reproduce offload-only swap leaking into the workingset refault heuristic.

set -eu

# shellcheck source=zram_lib.sh
. ./zram_lib.sh

TCID="zram04"
cg="/sys/fs/cgroup/zram-workingset-$$"
tmp="${TMPDIR:-/var/tmp}/zram-workingset-$$"
ready="$tmp/ready"
worker=""
mglru=""
zswap_enabled=""

skip()
{
	echo "$TCID: [SKIP] $*" >&2
	exit "$ksft_skip"
}

cleanup()
{
	set +e
	[ -n "$worker" ] && kill "$worker"
	[ -n "$worker" ] && wait "$worker"
	rm -rf "$tmp"
	rmdir "$cg"
	[ "$dev_end" -ge "$dev_start" ] && zram_cleanup
	[ -n "$mglru" ] && echo "$mglru" > /sys/kernel/mm/lru_gen/enabled
	[ -n "$zswap_enabled" ] &&
		echo "$zswap_enabled" > /sys/module/zswap/parameters/enabled
}

check_prereqs
[ -x ./swap_offload ] || skip "swap_offload helper is unavailable"
[ -x ./workingset_offload ] || skip "workingset helper is unavailable"
[ -e /sys/fs/cgroup/cgroup.controllers ] || skip "cgroup v2 is unavailable"
[ "$(awk 'END { print NR }' /proc/swaps)" -eq 1 ] ||
	skip "test requires no pre-existing swap"
[ "$(stat -f -c %T "${TMPDIR:-/var/tmp}")" != tmpfs ] ||
	skip "test files require a disk-backed filesystem"

[ -e /sys/kernel/mm/lru_gen/enabled ] &&
	mglru=$(cat /sys/kernel/mm/lru_gen/enabled)
[ -e /sys/module/zswap/parameters/enabled ] &&
	zswap_enabled=$(cat /sys/module/zswap/parameters/enabled)
trap cleanup EXIT
[ -n "$mglru" ] && echo 0 > /sys/kernel/mm/lru_gen/enabled
[ -n "$zswap_enabled" ] && echo N > /sys/module/zswap/parameters/enabled

dev_num=1
zram_sizes="134217728"
zram_load
zram_set_disksizes
offload="/dev/zram${dev_start}"
mkswap "$offload" >/dev/null
./swap_offload activate "$offload" 1
dev_makeswap=$dev_end

echo +memory > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
mkdir "$cg"
echo max > "$cg/memory.max"
echo max > "$cg/memory.swap.max"
mkdir "$tmp"

./workingset_offload "$cg/cgroup.procs" "$tmp/target" "$tmp/filler" \
	"$ready" unused &
worker=$!
for _ in $(seq 1 400); do
	[ -e "$ready" ] && break
	sleep 0.05
done
[ -e "$ready" ] || skip "workingset helper did not become ready"

echo "48M swappiness=0" > "$cg/memory.reclaim" ||
	skip "file-only proactive reclaim failed"
kill -USR1 "$worker"
if wait "$worker"; then
	worker=""
	echo "$TCID: [PASS]"
	exit 0
else
	status=$?
fi
worker=""
[ "$status" -eq 2 ] && skip "workingset calibration was insufficient"
echo "$TCID: [FAIL] refaulted file pages were not activated" >&2
exit 1
