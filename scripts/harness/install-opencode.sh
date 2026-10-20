#!/bin/sh
# Installs the pinned opencode release into the private runtime folder and links the
# launcher into ~/.local/bin. Running it again checks the hash and changes nothing.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
. "$repo/config/opencode/opencode.env"
runtime=${AGENT_RUNTIME:-$HOME/agent-data/runtime}
dir="$runtime/opencode-$OPENCODE_VERSION"
tar="$dir/$OPENCODE_ASSET"
mkdir -p "$dir" "$HOME/.local/bin"
if [ ! -f "$tar" ] || [ "$(sha256sum "$tar" | cut -d' ' -f1)" != "$OPENCODE_SHA256" ]; then
    echo "downloading $OPENCODE_URL"
    curl -fsSL --max-time 900 -o "$tar.part" "$OPENCODE_URL"
    mv "$tar.part" "$tar"
fi
sum=$(sha256sum "$tar" | cut -d' ' -f1)
if [ "$sum" != "$OPENCODE_SHA256" ]; then
    echo "hash mismatch for $tar: $sum" >&2
    rm -f "$tar"
    exit 1
fi
tar -xzf "$tar" -C "$dir"
got=$("$dir/opencode" --version)
if [ "$got" != "$OPENCODE_VERSION" ]; then
    echo "unexpected version: $got" >&2
    exit 1
fi
# On every start opencode installs its plugin package into its config folder from the npm
# registry unless the folder already holds it. Seed it once here, pinned, so that start-up
# never reaches the network.
cfg="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
mkdir -p "$cfg"
if [ ! -d "$cfg/node_modules/@opencode-ai/plugin" ]; then
    echo "seeding $cfg with @opencode-ai/plugin $OPENCODE_VERSION"
    [ -f "$cfg/package.json" ] || printf '{\n  "name": "opencode-config",\n  "private": true\n}\n' > "$cfg/package.json"
    (cd "$cfg" && npm install --ignore-scripts --no-audit --no-fund --save-exact "@opencode-ai/plugin@$OPENCODE_VERSION" > /dev/null)
fi
ln -sfn "$repo/scripts/harness/opencode.sh" "$HOME/.local/bin/opencode"
echo "opencode $OPENCODE_VERSION in $dir; ~/.local/bin/opencode -> scripts/harness/opencode.sh; config folder $cfg seeded"
