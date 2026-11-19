#!/usr/bin/env bash
# Fail if any ELF file has a LOAD segment aligned below the page size it has to run with.
# A kernel with 16 KiB pages (Asahi Linux on Apple Silicon, Raspberry Pi 5) cannot map a segment
# aligned to 4 KiB, so such a binary or library does not start (or fails to load) there.
#
# Run:      manager/packaging/check-elf-alignment.sh [--min 0x4000] PATH...
#           PATH: files or folders (searched recursively, symlinks not followed). An AppImage is an
#           ELF file too: its head is the runtime, which is what this checks of it.
# Needs:    readelf (binutils).
# Expected: one "ok" line with the number of ELF files checked, exit 0; otherwise each offending
#           file with its smallest LOAD alignment, exit 1. Exit 2 if no ELF file was found at all.
set -euo pipefail
min=0x4000
if [[ ${1:-} == --min ]]; then min=$2; shift 2; fi
(( $# )) || { echo "usage: $0 [--min ALIGN] PATH..." >&2; exit 2; }
command -v readelf >/dev/null || { echo "Missing tool: readelf" >&2; exit 2; }

checked=0 bad=0
while IFS= read -r -d '' f; do
  [[ $(head -c4 -- "$f" | od -An -tx1 | tr -d ' \n') == 7f454c46 ]] || continue
  aligns=$(readelf -lW -- "$f" 2>/dev/null | awk '$1 == "LOAD" {print $NF}') || true
  [[ -n $aligns ]] || continue   # relocatable objects and the like have no segments
  checked=$((checked + 1))
  smallest=$(for a in $aligns; do printf '%d\n' "$a"; done | sort -n | head -1)
  if (( smallest < min )); then
    printf 'LOAD alignment 0x%x < 0x%x: %s\n' "$smallest" "$((min))" "$f"
    bad=$((bad + 1))
  fi
done < <(find "$@" -type f -print0)

(( checked )) || { echo "No ELF file found in: $*" >&2; exit 2; }
if (( bad )); then
  echo "$bad of $checked ELF files would not load with $((min))-byte pages" >&2
  exit 1
fi
printf 'ok: %d ELF files, every LOAD segment aligned to at least 0x%x\n' "$checked" "$((min))"
