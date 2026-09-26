#!/bin/bash
# Joliet-only image with placeholder names (103 units, xorriso's limit), then
# repack-joliet.py rewrites them to up to 111 units (255-byte records, no
# padding byte), plus a subdirectory bad/ with a record whose name_len claims
# more than the record holds.  Writes ./joliet-111.iso.
set -euo pipefail
S=$(dirname "$(readlink --canonicalize "$0")")
d=$S/j111; rm --recursive --force "$d"; mkdir --parents "$d/bad"
x=$(printf 'x%.0s' $(seq 1 100))
for t in J01 J02 J03 J04 J05 J06 J07; do printf 'joliet111 %s\n' $t > "$d/$t$x"; done
mkdir "$d/sub"; printf 'joliet111 sub\n' > "$d/sub/f.txt"
mkdir "$d/D01$x"; printf 'joliet111 inner\n' > "$d/D01$x/inner.txt"
for t in P01 P02 P03 P04 P05 P06; do printf 'pad\n' > "$d/$t$x"; done
printf 'joliet111 B01\n' > "$d/bad/B01$x"
printf 'joliet111 B02\n' > "$d/bad/B02$x"
printf 'joliet111 after\n' > "$d/bad/after-bad.txt"
for t in P01 P02; do printf 'pad\n' > "$d/bad/$t$x"; done
rm --force "$S/joliet-111-src.iso"
xorriso -no_rc -report_about SORRY -outdev "$S/joliet-111-src.iso" -rockridge off -joliet on \
	-compliance joliet_long_names -map "$d" / -commit
"$S/repack-joliet.py" "$S/joliet-111-src.iso" "$S/joliet-111.iso"
sha256sum "$S/joliet-111-src.iso" "$S/joliet-111.iso"
