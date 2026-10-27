#!/bin/sh
# Prepares a project for opencode files that only that project uses: .opencode/ with skills/,
# commands/, tools/ and agents/; opencode's plugin package linked in from the config folder, because
# without it every start in the project, the graph's runs included, waits about 70 s for an install
# that cannot reach the network; and .opencode/ in the project's local ignore list (info/exclude,
# never committed, shared by the project's worktrees). Running it again changes nothing.
# Usage: scripts/harness/setup-project.sh DIR
set -eu
dir=$(cd "${1:?usage: setup-project.sh DIR}" && pwd)
seed="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
if [ ! -d "$seed/node_modules/@opencode-ai/plugin" ]; then
    echo "$seed has no plugin package; run scripts/harness/install-opencode.sh first" >&2
    exit 1
fi
oc="$dir/.opencode"
mkdir -p "$oc/skills" "$oc/commands" "$oc/tools" "$oc/agents"
[ -e "$oc/node_modules" ] || ln -s "$seed/node_modules" "$oc/node_modules"
for f in package.json package-lock.json; do
    [ -e "$oc/$f" ] || cp "$seed/$f" "$oc/$f"
done
ignored="not a git repository, nothing to ignore"
if git -C "$dir" rev-parse --git-dir > /dev/null 2>&1; then
    exclude=$(git -C "$dir" rev-parse --path-format=absolute --git-path info/exclude)
    mkdir -p "$(dirname "$exclude")"
    grep -qxF ".opencode/" "$exclude" 2>/dev/null || echo ".opencode/" >> "$exclude"
    ignored=".opencode/ listed in $exclude"
fi
echo "$oc: skills/, commands/, tools/, agents/ ready; plugin package linked; $ignored"
