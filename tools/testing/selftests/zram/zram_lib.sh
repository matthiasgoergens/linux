#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2015 Oracle and/or its affiliates. All Rights Reserved.
#
# Author: Alexey Kodanev <alexey.kodanev@oracle.com>
# Modified: Naresh Kamboju <naresh.kamboju@linaro.org>

# IDs returned by hot_add, in allocation order; old kernels use 0..dev_num-1.
dev_ids=""
dev_swap_ids=""
dev_mount_ids=""
module_load=-1
sys_control=-1
# Kselftest framework requirement - SKIP code is 4.
ksft_skip=4
kernel_version=`uname -r | cut -d'.' -f1,2`
kernel_major=${kernel_version%.*}
kernel_minor=${kernel_version#*.}

# Whether this test enabled the memory controller on its cgroup parent.  Tests
# must leave a delegation which was already present alone.
cgroup_memory_controller_enabled=0

trap INT

check_prereqs()
{
	local msg="skip all tests:"
	local uid=$(id -u)

	if [ $uid -ne 0 ]; then
		echo $msg must be run as root >&2
		exit $ksft_skip
	fi
}

cgroup_enable_memory_controller()
{
	local cgroup_root=$1

	if grep -qw memory "$cgroup_root/cgroup.subtree_control"; then
		return 0
	fi

	if ! echo +memory > "$cgroup_root/cgroup.subtree_control"; then
		return 1
	fi

	cgroup_memory_controller_enabled=1
}

cgroup_disable_memory_controller()
{
	local cgroup_root=$1

	[ "$cgroup_memory_controller_enabled" -eq 1 ] || return 0
	echo -memory > "$cgroup_root/cgroup.subtree_control" || return 1
	cgroup_memory_controller_enabled=0
}

kernel_gte()
{
	major=${1%.*}
	minor=${1#*.}

	if [ $kernel_major -gt $major ]; then
		return 0
	elif [ $kernel_major -eq $major ] && [ $kernel_minor -ge $minor ]; then
		return 0
	fi

	return 1
}

zram_wait_for_udev()
{
	# Probing triggered by device changes can still hold the device open.
	# The queue is global; only the subsequent teardown can establish failure.
	if command -v udevadm >/dev/null 2>&1; then
		udevadm settle --timeout=5 ||
			echo "udev queue did not settle; attempting cleanup" >&2
	fi
	return 0
}

zram_cleanup()
{
	echo "zram cleanup"
	local i=
	local ret=0
	local busy_ids=""
	for i in $dev_ids; do
		case " $dev_swap_ids " in
			*" $i "*) ;;
			*)
				# A signal can arrive after a helper activates swap but
				# before its caller records the ID.
				grep -q "^/dev/zram${i}[[:space:]]" /proc/swaps ||
					continue
				;;
		esac
		if ! swapoff /dev/zram$i; then
			ret=1
			busy_ids="$busy_ids $i"
		fi
	done

	for i in $dev_mount_ids; do
		if ! umount /dev/zram$i; then
			ret=1
			busy_ids="$busy_ids $i"
		fi
	done

	zram_wait_for_udev
	for i in $dev_ids; do
		case " $busy_ids " in
			*" $i "*) continue ;;
		esac
		echo 1 > /sys/block/zram${i}/reset || ret=1
		case " $dev_mount_ids " in
			*" $i "*) rmdir "zram$i" || ret=1 ;;
		esac
	done
	# Reset emits another device-change event before removal.
	zram_wait_for_udev

	if [ $sys_control -eq 1 ]; then
		for i in $dev_ids; do
			case " $busy_ids " in
				*" $i "*) continue ;;
			esac
			echo $i > /sys/class/zram-control/hot_remove || ret=1
		done
	fi

	if [ $module_load -eq 1 ]; then
		rmmod zram || ret=1
	fi
	return "$ret"
}

zram_load()
{
	echo "create '$dev_num' zram device(s)"

	# zram module loaded, new kernel
	if [ -d "/sys/class/zram-control" ]; then
		echo "zram modules already loaded, kernel supports" \
			"zram-control interface"
		sys_control=1

		for i in $(seq 1 $dev_num); do
			if ! id=$(cat /sys/class/zram-control/hot_add); then
				echo "FAIL zram hot_add failed" >&2
				return 1
			fi
			case "$id" in
				''|*[!0-9]*)
					echo "FAIL invalid zram hot_add ID: $id" >&2
					return 1
					;;
			esac
			dev_ids="$dev_ids $id"
		done

		echo "all zram devices ($dev_ids)" \
			"successfully created"
		return 0
	fi

	# detect old kernel or built-in
	modprobe zram num_devices=$dev_num
	if [ ! -d "/sys/class/zram-control" ]; then
		if grep -q '^zram' /proc/modules; then
			rmmod zram > /dev/null 2>&1
			if [ $? -ne 0 ]; then
				echo "zram module is being used on old kernel" \
					"without zram-control interface"
				exit $ksft_skip
			fi
		else
			echo "test needs CONFIG_ZRAM=m on old kernel without" \
				"zram-control interface"
			exit $ksft_skip
		fi
		modprobe zram num_devices=$dev_num
	fi

	module_load=1
	local last=$(($dev_num - 1))
	for i in $(seq 0 $last); do
		dev_ids="$dev_ids $i"
	done
	echo "all zram devices (/dev/zram0~$last) successfully created"
}

zram_max_streams()
{
	echo "set max_comp_streams to zram device(s)"

	kernel_gte 4.7
	if [ $? -eq 0 ]; then
		echo "The device attribute max_comp_streams was"\
		               "deprecated in 4.7"
		return 0
	fi

	set -- $dev_ids
	for max_s in $zram_max_streams; do
		local i=$1
		shift
		local sys_path="/sys/block/zram${i}/max_comp_streams"
		echo $max_s > $sys_path || \
			echo "FAIL failed to set '$max_s' to $sys_path"
		sleep 1
		local max_streams=$(cat $sys_path)

		[ "$max_s" -ne "$max_streams" ] && \
			echo "FAIL can't set max_streams '$max_s', get $max_stream"

		echo "$sys_path = '$max_streams'"
	done

	echo "zram max streams: OK"
}

zram_compress_alg()
{
	echo "test that we can set compression algorithm"

	set -- $dev_ids
	local i=$1
	local algs=$(cat /sys/block/zram${i}/comp_algorithm)
	echo "supported algs: $algs"

	for alg in $zram_algs; do
		local i=$1
		shift
		local sys_path="/sys/block/zram${i}/comp_algorithm"
		echo "$alg" >	$sys_path || \
			echo "FAIL can't set '$alg' to $sys_path"
		echo "$sys_path = '$alg'"
	done

	echo "zram set compression algorithm: OK"
}

zram_set_disksizes()
{
	echo "set disk size to zram device(s)"
	set -- $dev_ids
	for ds in $zram_sizes; do
		local i=$1
		shift
		local sys_path="/sys/block/zram${i}/disksize"
		echo "$ds" >	$sys_path || \
			echo "FAIL can't set '$ds' to $sys_path"

		echo "$sys_path = '$ds'"
	done

	echo "zram set disksizes: OK"
}

zram_set_memlimit()
{
	echo "set memory limit to zram device(s)"

	set -- $dev_ids
	for ds in $zram_mem_limits; do
		local i=$1
		shift
		local sys_path="/sys/block/zram${i}/mem_limit"
		echo "$ds" >	$sys_path || \
			echo "FAIL can't set '$ds' to $sys_path"

		echo "$sys_path = '$ds'"
	done

	echo "zram set memory limit: OK"
}

zram_makeswap()
{
	echo "make swap with zram device(s)"
	local i
	for i in $dev_ids; do
		mkswap /dev/zram$i > err.log 2>&1
		if [ $? -ne 0 ]; then
			cat err.log
			echo "FAIL mkswap /dev/zram$i failed"
			continue
		fi

		swapon /dev/zram$i > err.log 2>&1
		if [ $? -ne 0 ]; then
			cat err.log
			echo "FAIL swapon /dev/zram$i failed"
			continue
		fi

		echo "done with /dev/zram$i"
		dev_swap_ids="$dev_swap_ids $i"
	done

	echo "zram making zram mkswap and swapon: OK"
}

zram_swapoff()
{
	local i=
	local failed_ids=""
	for i in $dev_swap_ids; do
		swapoff /dev/zram$i > err.log 2>&1
		if [ $? -ne 0 ]; then
			cat err.log
			echo "FAIL swapoff /dev/zram$i failed"
			failed_ids="$failed_ids $i"
		fi
	done
	dev_swap_ids=$failed_ids

	echo "zram swapoff: OK"
}

zram_makefs()
{
	set -- $dev_ids
	for fs in $zram_filesystems; do
		local i=$1
		shift
		# if requested fs not supported default it to ext2
		which mkfs.$fs > /dev/null 2>&1 || fs=ext2

		echo "make $fs filesystem on /dev/zram$i"
		mkfs.$fs /dev/zram$i > err.log 2>&1
		if [ $? -ne 0 ]; then
			cat err.log
			echo "FAIL failed to make $fs on /dev/zram$i"
		fi
		echo "zram mkfs.$fs: OK"
	done
}

zram_mount()
{
	local i=0
	for i in $dev_ids; do
		echo "mount /dev/zram$i"
		mkdir "zram$i" || return 1
		if mount /dev/zram$i "zram$i" > /dev/null; then
			dev_mount_ids="$dev_mount_ids $i"
		else
			echo "FAIL mount /dev/zram$i failed"
			rmdir "zram$i" || return 1
			return 1
		fi
	done

	echo "zram mount of zram device(s): OK"
	return 0
}
