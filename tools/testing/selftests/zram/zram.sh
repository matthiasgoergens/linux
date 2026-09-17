#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
TCID="zram.sh"

. ./zram_lib.sh

run_zram () {
local ret status

echo "--------------------"
echo "running zram tests"
echo "--------------------"
./zram01.sh
ret=$?
echo ""
./zram02.sh
status=$?
if [ "$status" -ne 0 ] &&
   { [ "$ret" -eq 0 ] || [ "$ret" -eq "$ksft_skip" ]; }; then
	ret=$status
fi
return "$ret"
}

check_prereqs

run_zram
