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

This permits a system to combine a small conventional swap area, which is
engineered for forward progress in emergency reclaim, with a larger or more
complex area used for ordinary cold-page offload.  For example, the latter may
be RAM-compressed or may use a filesystem with compression, checksums, or
redundancy.  Making every such write path safe in direct reclaim can require
backend-specific reserves, preallocation, non-blocking allocation, and
recursion rules.  Excluding new pressure-reclaim writes can reduce that
requirement and its complexity.

For a RAM-compressed area such as zram, unused logical slots also do not imply
that enough physical memory remains to store their future contents.  Static
swap priority cannot express that distinction or provide late fallback after a
selected area's write fails.

The policy is attached to one activated swap area, not to its underlying
physical storage.  A raw swap partition and a filesystem swapfile on the same
device are separate areas and may use different policies.  The kernel does not
infer this policy from the block driver, filesystem, or swap priority.

This is a reclaim-provenance policy, not a measurement of current memory
headroom.  Userspace should only request proactive offload while its own
watermark or PSI policy considers memory pressure low.

DAMON reclaim and ``MADV_PAGEOUT`` do not currently establish the proactive
reclaim context, so they cannot allocate slots from an offload-only area.
Offload-only areas are also ineligible for hibernation image allocation.

The flag controls allocation of new swap slots.  It does not prevent reads,
swapoff, or writes for slots which were allocated before the current reclaim
context began.  In particular, it does not by itself provide a forward
progress guarantee for an I/O path which allocates memory: queued and in-flight
writes must still be able to complete, and reads must remain reclaim-safe.

An offload-only area should therefore be configured with a reclaim-safe swap
area as fallback.  If no eligible swap space remains, swap allocation fails
and the existing reclaim and OOM policy applies.
