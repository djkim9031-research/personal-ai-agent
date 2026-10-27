#!/bin/sh
# Starts the pinned opencode with the repository's settings. Nothing leaves this machine:
# the model is the local server (config/opencode/opencode.json), and the model catalogue
# download, the update check, language-server downloads and the skills of other tools are
# off. The npm registry points at a closed local port, so the package install opencode
# attempts into its config folder on start cannot reach the network either;
# install-opencode.sh seeds that folder so the attempt is skipped anyway.
set -eu
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
repo=$(cd "$here/../.." && pwd)
. "$repo/config/opencode/opencode.env"
bin=${AGENT_RUNTIME:-$HOME/agent-data/runtime}/opencode-$OPENCODE_VERSION/opencode
if [ ! -x "$bin" ]; then
    echo "opencode $OPENCODE_VERSION is not installed; run scripts/harness/install-opencode.sh" >&2
    exit 1
fi
export OPENCODE_CONFIG="${OPENCODE_CONFIG:-$repo/config/opencode/opencode.json}"
export AGENT_REPO="$repo"   # the settings name config/opencode/AGENTS.md through it
# The project's notes and the owner's profile, loaded by the settings into every session;
# agent-notes is on PATH for /remember and recall. Measurement runs set AGENT_NO_NOTES.
notes_cli="$repo/scripts/opencode-memory/agent-notes.py"
if [ -z "${AGENT_NO_NOTES:-}" ] && project_notes=$(python3 -I "$notes_cli" path "$PWD" 2>/dev/null); then
    export AGENT_PROJECT_NOTES="$project_notes"
    AGENT_USER_PROFILE=$(python3 -I "$notes_cli" profile)
    export AGENT_USER_PROFILE
    export PATH="$repo/scripts/opencode-memory/bin:$PATH"
    waiting=$(python3 -I "$notes_cli" pending "$PWD")
    [ "$waiting" = 0 ] || echo "agent-notes: $waiting note proposals wait for this project; review them with: agent-notes review" >&2
fi
export OPENCODE_DISABLE_MODELS_FETCH=true
export OPENCODE_DISABLE_AUTOUPDATE=true
export OPENCODE_DISABLE_LSP_DOWNLOAD=true
export OPENCODE_DISABLE_EXTERNAL_SKILLS=true
export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-http://127.0.0.1:9/}"
exec "$bin" "$@"
