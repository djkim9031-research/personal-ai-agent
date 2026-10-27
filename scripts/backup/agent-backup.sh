#!/usr/bin/env bash
# Nightly backup of the agent's personal data (increment 11), installed as
# /usr/local/libexec/agent-backup/agent-backup.sh and run by
# agent-backup.service as root, with no network. Backs up the agent's memory
# and configuration and the owner's personal agent data into the encrypted
# restic repository on /var; leaves out work-scope data and secrets, and any
# text file that names a work identifier (the owner's private list). With
# "check" it also thins old snapshots and checks part of the stored data.
# Usage: agent-backup.sh [check]   (environment from /etc/agent-backup.env)
set -euo pipefail
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD_FILE:?}" "${OWNER_HOME:?}" "${HERMES_HOME_DIR:?}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
restic=${RESTIC_BIN:-/usr/local/bin/restic}
hh=$HERMES_HOME_DIR/.hermes
ad=$OWNER_HOME/agent-data
list=$ad/hermes/work-patterns.txt
[ -s "$list" ] || { echo "no work-name list at $list: refusing to back up" >&2; exit 1; }

include=()
for p in "$hh/memories" "$hh/config.yaml" "$hh/SOUL.md" "$hh/skills" "$hh/plugins" "$hh/cron/jobs.json" \
         "$ad/memory" "$ad/trajectories/personal" "$ad/hermes" "$ad/docs" "$ad/opencode/notes/personal"; do
    [ -e "$p" ] && include+=("$p")
done
work=$(mktemp)
patterns=$(mktemp)
trap 'rm -f "$work" "$patterns"' EXIT
# Comments and blank lines out: an empty pattern would match every file.
grep -vE '^[[:space:]]*(#|$)' "$list" > "$patterns"
# Text files that name a work identifier stay out (compressed git objects are
# not read); the list itself never goes in and is not counted.
{ grep -rIlE -f "$patterns" "${include[@]}" || true; } | { grep -vxF -- "$list" || true; } > "$work"
"$restic" backup --quiet --tag nightly --exclude "$list" --exclude-file "$work" "${include[@]}"
echo "backup: $("$restic" snapshots --latest 1 --compact | grep -E '^[0-9a-f]{8} ' | tail -1 | cut -c1-40),"\
     "$(wc -l < "$work") files left out for naming work identifiers"
if [ "${1:-}" = check ]; then
    "$restic" forget --quiet --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
    "$restic" check --quiet --read-data-subset=10%
    echo "check: snapshots thinned, 10% of the data read back without errors"
fi
