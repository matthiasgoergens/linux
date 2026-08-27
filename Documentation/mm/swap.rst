.. SPDX-License-Identifier: GPL-2.0

====
Swap
====

Offload-only swap areas
-----------------------

``SWAP_FLAG_OFFLOAD_ONLY`` marks a swap area as a destination for explicit
userspace proactive reclaim (``memory.reclaim`` and manual MGLRU eviction).
The swap allocator excludes such an area from kswapd, direct reclaim, and
other pressure-driven swap allocation.  Normal swap priority ordering still
applies among the areas eligible for the current reclaim context.

DAMON reclaim and ``MADV_PAGEOUT`` do not currently establish the proactive
reclaim context, so they cannot allocate slots from an offload-only area.

The flag controls allocation of new swap slots.  It does not prevent reads,
swapoff, or writes for slots which were allocated before the current reclaim
context began.  In particular, it does not by itself provide a forward
progress guarantee for an I/O path which allocates memory: queued and in-flight
writes must still be able to complete, and reads must remain reclaim-safe.

An offload-only area should therefore be configured with a reclaim-safe swap
area as fallback.  If no eligible swap space remains, swap allocation fails
and the existing reclaim and OOM policy applies.

The ``mm_vmscan_swap_device_skip`` tracepoint records an offload-only area
rejected by either allocator path.  ``mm_vmscan_swap_alloc`` records the
selected swap type, allocation order, and reclaim mode; a type of ``-1`` means
that no eligible slot was allocated.
