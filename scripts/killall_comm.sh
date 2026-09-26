#!/bin/sh
# busybox here has no pkill: STOP every process named $1, then KILL them
for sig in STOP KILL; do
	for p in /proc/[0-9]*; do
		[ "$(cat $p/comm 2>/dev/null)" = "$1" ] && kill -$sig ${p#/proc/} 2>/dev/null
	done
done
