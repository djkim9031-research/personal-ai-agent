#!/usr/bin/env bash
# Installs Hermes Agent for the hermes user at the pinned commit with the
# installer read for this repo (hash in config/hermes/hermes.env), without the
# browser and computer-use tools and without its setup wizard, then applies
# config/hermes/settings.txt. Run after install-rootless-docker.sh, while the
# egress allowlist is off:
#   sudo -u hermes -H scripts/shell/install-hermes.sh
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
[ "$(id -un)" = "$HERMES_USER" ] || { echo "run as $HERMES_USER: sudo -u $HERMES_USER -H $0" >&2; exit 1; }
cd "$HOME"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl -fsSL --retry 3 -o "$tmp/install.sh" \
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/$HERMES_COMMIT/scripts/install.sh"
echo "$HERMES_INSTALLER_SHA256  $tmp/install.sh" | sha256sum -c --quiet - || { echo "installer hash mismatch" >&2; exit 1; }
bash "$tmp/install.sh" --commit "$HERMES_COMMIT" --non-interactive --skip-browser --skip-computer-use

hermes=$HOME/.local/bin/hermes
[ -x "$hermes" ] || { echo "no hermes launcher at $hermes" >&2; exit 1; }
"$hermes" --version

while read -r key value; do
    case $key in ''|'#'*) continue ;; esac
    "$hermes" config set "$key" "$value" >/dev/null
    echo "set $key = $value"
done < "$repo/config/hermes/settings.txt"
"$hermes" config set terminal.docker_image "$SANDBOX_IMAGE" >/dev/null
echo "set terminal.docker_image = $SANDBOX_IMAGE"

# The installer's local receipt for the opt-in metrics; metrics stay off.
rm -rf "$HOME/.hermes/telemetry/shared_metrics"
chmod 0700 "$HOME/.hermes"
echo "next, from your account: sudo $repo/scripts/shell/set-mail-secret.sh"
