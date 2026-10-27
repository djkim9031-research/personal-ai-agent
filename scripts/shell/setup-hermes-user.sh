#!/usr/bin/env bash
# One-time setup, run with sudo from your own account. Creates the hermes
# system user that runs the agent's shell, with what rootless Docker needs:
# subordinate IDs, uidmap and slirp4netns, cgroup delegation and linger (its
# user manager, which runs its Docker daemon, starts at boot). Closes your
# agent data folder to other local users. Installs nothing that runs yet.
# Usage: sudo scripts/shell/setup-hermes-user.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
user=$HERMES_USER
first=$HERMES_SUBID_START
last=$((HERMES_SUBID_START + HERMES_SUBID_COUNT - 1))

# A system user with no login shell, no password and no extra groups.
if ! id "$user" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$HERMES_HOME_DIR" --shell /usr/sbin/nologin --user-group "$user"
fi
chmod 0750 "$HERMES_HOME_DIR"
uid=$(id -u "$user")
extra=$(id -nG "$user" | tr ' ' '\n' | grep -vx "$user" || true)
[ -z "$extra" ] || { echo "$user is in other groups: $extra" >&2; exit 1; }

# Subordinate IDs for the container's users; refuse a range another account has.
for f in /etc/subuid /etc/subgid; do
    if ! grep -q "^$user:" "$f"; then
        while IFS=: read -r name start count; do
            [ "$name" = "$user" ] && continue
            if [ "$start" -le "$last" ] && [ $((start + count - 1)) -ge "$first" ]; then
                echo "$f: $first-$last overlaps $name ($start, $count)" >&2; exit 1
            fi
        done < "$f"
    fi
done
grep -q "^$user:" /etc/subuid || usermod --add-subuids "$first-$last" "$user"
grep -q "^$user:" /etc/subgid || usermod --add-subgids "$first-$last" "$user"

# newuidmap/newgidmap and the user-mode network for rootless Docker, from Ubuntu.
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends uidmap slirp4netns

# Let the containers' CPU and memory limits work under hermes' user manager only.
install -d -m 0755 "/etc/systemd/system/user@$uid.service.d"
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' > "/etc/systemd/system/user@$uid.service.d/delegate.conf"
systemctl daemon-reload

# hermes' user manager runs without a login, from boot.
loginctl enable-linger "$user"
for _ in $(seq 30); do [ -S "/run/user/$uid/bus" ] && break; sleep 1; done
[ -S "/run/user/$uid/bus" ] || { echo "user manager for $user did not start" >&2; exit 1; }

# Your agent data was readable by every local user. Named access stays
# (agent-serve reads the model through its own entries).
chmod o-rwx "$owner_home/agent-data"
echo "closed to other users: $owner_home/agent-data"

as_hermes() { runuser -u "$user" -- env HOME="$HERMES_HOME_DIR" "$@"; }
as_hermes test ! -x "$owner_home/agent-data" || { echo "$user can still enter $owner_home/agent-data" >&2; exit 1; }
as_hermes test -r "$repo/scripts/shell/install-hermes.sh" || { echo "$user cannot read $repo/scripts/shell" >&2; exit 1; }
echo "$user: $(id "$user")"
echo "subordinate IDs: $(grep "^$user:" /etc/subuid)"
echo "next, as $user: sudo -u $user -H $repo/scripts/shell/install-rootless-docker.sh"
