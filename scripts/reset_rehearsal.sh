#!/bin/sh
# Put the rehearsal track back to its as-induced state: playbook v1 only, empty taught list, no re-runs.
# Safe to run any time; never touches the main track. Usage: scripts/reset_rehearsal.sh [track]   (default: rehearsal)
set -e
cd "$(dirname "$0")/.."
track="${1:-rehearsal}"
[ "$track" = "main" ] && { echo "refusing to reset the main track"; exit 1; }
for c in A B; do
  find "data/$c/playbook/$track" -name 'v*.json' ! -name 'v1.json' -delete 2>/dev/null || true
  rm -f "data/$c/corrections_$track.jsonl" "data/$c/reopened_$track.jsonl"
  rm -rf runs/${track}_${c}__after_* runs/blast_${c}_${track}_*
  echo "$c: $(ls data/$c/playbook/$track 2>/dev/null | tr '\n' ' ')"
done
