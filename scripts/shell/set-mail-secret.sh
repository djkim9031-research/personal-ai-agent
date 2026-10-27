#!/usr/bin/env bash
# Writes the email lines of the hermes user's ~/.hermes/.env (mode 0600): the
# public settings from config/hermes/env.example, the two addresses from your
# private ~/agent-data/secrets/hermes-mail.env and the app password from
# ~/agent-data/secrets/agent-gmail-app-password. Prints no secret. Run with
# sudo from your own account after install-hermes.sh; safe to run again.
# Usage: sudo scripts/shell/set-mail-secret.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
secrets=$owner_home/agent-data/secrets
env_file=$HERMES_HOME_DIR/.hermes/.env
[ -f "$env_file" ] || { echo "missing $env_file: run install-hermes.sh first" >&2; exit 1; }

address=$(sed -n 's/^EMAIL_ADDRESS=//p' "$secrets/hermes-mail.env")
allowed=$(sed -n 's/^EMAIL_ALLOWED_USERS=//p' "$secrets/hermes-mail.env")
password=$(tr -d ' \n' < "$secrets/agent-gmail-app-password")
[ -n "$address" ] && [ -n "$allowed" ] || { echo "$secrets/hermes-mail.env needs EMAIL_ADDRESS and EMAIL_ALLOWED_USERS" >&2; exit 1; }
[ ${#password} -eq 16 ] || { echo "the app password file does not hold 16 characters" >&2; exit 1; }
[ "$address" != "$allowed" ] || { echo "the allowed sender must differ from the agent's address" >&2; exit 1; }

new=$(mktemp)
trap 'rm -f "$new"' EXIT
grep -v '^EMAIL_' "$env_file" > "$new" || true
grep -E '^EMAIL_' "$repo/config/hermes/env.example" | grep -vE '^EMAIL_(ADDRESS|PASSWORD|ALLOWED_USERS)=' >> "$new"
printf 'EMAIL_ADDRESS=%s\nEMAIL_ALLOWED_USERS=%s\nEMAIL_PASSWORD=%s\n' "$address" "$allowed" "$password" >> "$new"
install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$new" "$env_file"
echo "wrote $(grep -c '^EMAIL_' "$env_file") email settings to $env_file (0600, $HERMES_USER); password not shown"
echo "next, from your account: sudo $repo/scripts/shell/enable-shell.sh"
