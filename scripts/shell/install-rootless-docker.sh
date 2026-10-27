#!/usr/bin/env bash
# Installs rootless Docker for the hermes user from Docker's static release
# (version and hashes in config/hermes/hermes.env) into ~/bin, starts it as
# hermes' user service and pulls the sandbox image. The system Docker is not
# touched. Run after setup-hermes-user.sh, while the egress allowlist is off:
#   sudo -u hermes -H scripts/shell/install-rootless-docker.sh
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
[ "$(id -un)" = "$HERMES_USER" ] || { echo "run as $HERMES_USER: sudo -u $HERMES_USER -H $0" >&2; exit 1; }
cd "$HOME"
XDG_RUNTIME_DIR=/run/user/$(id -u)
export XDG_RUNTIME_DIR
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
[ -S "$XDG_RUNTIME_DIR/bus" ] || { echo "no user manager for $(id -un): run setup-hermes-user.sh first" >&2; exit 1; }

bin=$HOME/bin
mkdir -p "$bin"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
base=https://download.docker.com/linux/static/stable/x86_64
fetch() {
    curl -fsSL --retry 3 -o "$tmp/$1" "$base/$1"
    echo "$2  $tmp/$1" | sha256sum -c --quiet - || { echo "hash mismatch: $1" >&2; exit 1; }
}
fetch "docker-$DOCKER_VERSION.tgz" "$DOCKER_STATIC_SHA256"
fetch "docker-rootless-extras-$DOCKER_VERSION.tgz" "$DOCKER_ROOTLESS_SHA256"
tar -xzf "$tmp/docker-$DOCKER_VERSION.tgz" -C "$bin" --strip-components=1
tar -xzf "$tmp/docker-rootless-extras-$DOCKER_VERSION.tgz" -C "$bin" --strip-components=1
export PATH=$bin:$PATH

# The containers get no network, so dockerd needs no iptables rules.
dockerd-rootless-setuptool.sh install --skip-iptables
systemctl --user enable docker.service
systemctl --user restart docker.service
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock
for _ in $(seq 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker info --format 'rootless Docker {{.ServerVersion}}, security: {{.SecurityOptions}}'

docker pull -q "$SANDBOX_IMAGE"
docker run --rm --network none "$SANDBOX_IMAGE" python3 -c 'import os; print("sandbox runs as uid", os.getuid(), "with", sorted(os.listdir("/sys/class/net")))'
echo "next, as $HERMES_USER: sudo -u $HERMES_USER -H $repo/scripts/shell/install-hermes.sh"
