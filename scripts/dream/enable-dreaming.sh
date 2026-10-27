#!/usr/bin/env bash
# Increment 9, the nightly review: makes the owner's address the email home
# channel (where the review's report is delivered), installs the
# nightly-review skill, limits cron sessions to memory, session search, skills
# and a to-do list, and creates the 03:00 job. The job runs inside the gateway,
# so only while the model server runs; a night missed while it is off runs
# once at the next start. Run with sudo from your own account; safe to run
# again. Restarts a running gateway, which sends the offline and online notices.
# Usage: sudo scripts/dream/enable-dreaming.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
hh=$HERMES_HOME_DIR/.hermes
mailcfg=$owner_home/agent-data/secrets/hermes-mail.env
cd /
hermes_cli() {
    runuser -u "$HERMES_USER" -- env HOME="$HERMES_HOME_DIR" HERMES_HOME="$hh" \
        "$HERMES_HOME_DIR/.local/bin/hermes" "$@"
}

# The report goes to the email home channel: the first allowed sender.
home=$(sed -n 's/^EMAIL_ALLOWED_USERS=//p' "$mailcfg" | cut -d, -f1 | tr -d " \"'")
[ -n "$home" ] || { echo "no EMAIL_ALLOWED_USERS in $mailcfg" >&2; exit 1; }
new=$(mktemp "$hh/.env.XXXXXX")
{ grep -v '^EMAIL_HOME_ADDRESS=' "$hh/.env" || true; echo "EMAIL_HOME_ADDRESS=$home"; } > "$new"
chown "$HERMES_USER:$HERMES_USER" "$new"
chmod 0600 "$new"
mv "$new" "$hh/.env"
echo "email home channel: the allowed sender"

install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0700 "$hh/skills/nightly-review"
install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 \
    "$repo/config/hermes/skills/nightly-review/SKILL.md" "$hh/skills/nightly-review/SKILL.md"
echo "skill installed: nightly-review"

while read -r key value; do
    hermes_cli config set "$key" "$value" > /dev/null
    echo "set $key = $value"
done < "$repo/config/hermes/settings-dream.txt"

prompt="Run the nightly-review skill for the last 24 hours."
if hermes_cli cron edit nightly-review --schedule "0 3 * * *" --prompt "$prompt" \
        --deliver email --skill nightly-review > /dev/null 2>&1; then
    echo "job nightly-review updated"
else
    hermes_cli cron create "0 3 * * *" "$prompt" --name nightly-review --deliver email --skill nightly-review
fi
hermes_cli cron list
if systemctl is-active --quiet agent-hermes.service; then
    systemctl restart agent-hermes.service
    echo "gateway restarted to read the home channel"
fi
