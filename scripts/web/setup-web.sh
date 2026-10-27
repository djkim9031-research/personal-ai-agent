#!/usr/bin/env bash
# Increment 10, web research. Creates the agentweb user (no login, no extra
# groups, home /var/lib/agentweb), which runs SearXNG and the agent's fetch
# service on loopback; installs SearXNG at the pinned commit on Python 3.12
# from a hash-locked requirements file; loads that user's egress rules (web
# ports on public addresses only, at every boot from now on); ties both
# services to the gateway; installs the Hermes plugin, points web search at the
# fetch service and adds the web tools to the email toolset; lets the hermes
# user reach the fetch service (re-runs enable-shell.sh). Run with sudo from
# your own account; safe to run again. Restarts a running gateway.
# Usage: sudo scripts/web/setup-web.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
: "${SUDO_USER:?run with sudo from your own account}"
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
. "$repo/config/web/web.env"
hh=$HERMES_HOME_DIR/.hermes
wh=$AGENTWEB_HOME
cd /
[ "$(uv --version | cut -d' ' -f2)" = "$UV_VERSION" ] || echo "note: uv is $(uv --version), the pin is $UV_VERSION"

if ! id "$AGENTWEB_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$wh" --shell /usr/sbin/nologin --user-group "$AGENTWEB_USER"
fi
chmod 0750 "$wh"
wuid=$(id -u "$AGENTWEB_USER")
as_web() {
    runuser -u "$AGENTWEB_USER" -- env HOME="$wh" UV_CACHE_DIR="$wh/.cache/uv" \
        UV_PYTHON_INSTALL_DIR="$wh/python" "$@"
}
hermes_cli() {
    runuser -u "$HERMES_USER" -- env HOME="$HERMES_HOME_DIR" HERMES_HOME="$hh" \
        "$HERMES_HOME_DIR/.local/bin/hermes" "$@"
}
set_env() {  # set_env KEY VALUE: one line in hermes' .env, replaced if present
    local new
    new=$(mktemp "$hh/.env.XXXXXX")
    { grep -v "^$1=" "$hh/.env" || true; echo "$1=$2"; } > "$new"
    chown "$HERMES_USER:$HERMES_USER" "$new"
    chmod 0600 "$new"
    mv "$new" "$hh/.env"
}

# SearXNG at the pinned commit; Python from uv (checksum-verified); every
# dependency checked against its hash.
[ -d "$wh/searxng/.git" ] || as_web git clone -q --filter=blob:none "$SEARXNG_REPO" "$wh/searxng"
as_web git -C "$wh/searxng" cat-file -e "$SEARXNG_COMMIT^{commit}" 2>/dev/null \
    || as_web git -C "$wh/searxng" fetch -q origin
as_web git -C "$wh/searxng" checkout -q --detach "$SEARXNG_COMMIT"
[ "$(as_web git -C "$wh/searxng" rev-parse HEAD)" = "$SEARXNG_COMMIT" ] \
    || { echo "SearXNG is not at $SEARXNG_COMMIT" >&2; exit 1; }
install -o "$AGENTWEB_USER" -g "$AGENTWEB_USER" -m 0644 "$repo/config/web/searxng-requirements.lock" "$wh/requirements.lock"
[ -x "$wh/venv/bin/python" ] || as_web uv venv -q --python "$PYTHON_VERSION" "$wh/venv"
as_web uv pip install -q --python "$wh/venv/bin/python" --require-hashes --no-deps -r "$wh/requirements.lock"
install -o "$AGENTWEB_USER" -g "$AGENTWEB_USER" -m 0644 "$repo/config/web/searxng-settings.yml" "$wh/settings.yml"
if [ ! -s "$wh/secret.env" ]; then
    (umask 077; echo "SEARXNG_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > "$wh/secret.env")
    chown "$AGENTWEB_USER:$AGENTWEB_USER" "$wh/secret.env"
fi
echo "SearXNG $(as_web git -C "$wh/searxng" rev-parse --short=12 HEAD) on $("$wh/venv/bin/python" -V)"

# agentweb's egress rules, then its two services, started and stopped with the gateway.
sed "s|@WEB_UID@|$wuid|g" "$repo/config/nftables/agent-web-egress.nft.in" > /etc/agent-web-egress.nft
chmod 0644 /etc/agent-web-egress.nft
nft -c -f /etc/agent-web-egress.nft
install -m 0644 "$repo/config/systemd/agent-web-egress.service" /etc/systemd/system/agent-web-egress.service
for unit in agent-searxng agent-web; do
    sed -e "s|@WEB_HOME@|$wh|g" -e "s|@REPO@|$repo|g" "$repo/config/systemd/$unit.service.in" \
        > "/etc/systemd/system/$unit.service"
    chmod 0644 "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
# Other units on this machine print warnings here too; only these lines matter.
{ systemd-analyze verify /etc/systemd/system/agent-searxng.service /etc/systemd/system/agent-web.service \
    /etc/systemd/system/agent-web-egress.service 2>&1 || true; } | grep -E 'agent-(searxng|web)' \
    || echo "web units: no errors"
systemctl enable --now agent-web-egress.service
systemctl restart agent-web-egress.service
systemctl enable agent-searxng.service agent-web.service

# Hermes: the plugin, the two backends, the web tools for email.
install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0700 "$hh/plugins" "$hh/plugins/web" "$hh/plugins/web/agentweb"
for f in plugin.yaml __init__.py; do
    install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$repo/config/hermes/plugins/web/agentweb/$f" \
        "$hh/plugins/web/agentweb/$f"
done
set_env SEARXNG_URL http://127.0.0.1:8889
set_env AGENTWEB_URL http://127.0.0.1:8889
hermes_cli plugins enable web/agentweb > /dev/null
echo "plugin enabled: web/agentweb"
while read -r key value; do
    hermes_cli config set "$key" "$value" > /dev/null
    echo "set $key = $value"
done < "$repo/config/hermes/settings-web.txt"

# The hermes user may now open the fetch service's port: re-render its rules.
"$repo/scripts/shell/enable-shell.sh" > /dev/null
echo "hermes egress: model, web service, resolver, mail"
nft list table inet agent_web_egress | grep -E 'counter (local|dns|web|refused)' -A1 | grep -E 'counter|packets'

if systemctl is-active --quiet agent-hermes.service; then
    systemctl start agent-searxng.service agent-web.service
    systemctl restart agent-hermes.service
    echo "gateway restarted with the web tools"
    for _ in $(seq 30); do curl -fsS -o /dev/null --max-time 2 http://127.0.0.1:8889/healthz && break; sleep 1; done
    as_web python3 -I - <<'EOF'
import json, urllib.error, urllib.parse, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8889/search?q=restic+backup+check&format=json", timeout=60) as r:
    results = json.load(r).get("results", [])
print(f"test search: {len(results)} results")
if results:
    url = results[0]["url"]
    q = urllib.parse.quote(url, safe="")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8889/fetch?url={q}", timeout=60) as r:
            page = json.load(r)
        print(f"test read: {url} -> {len(page.get('content', ''))} characters")
    except urllib.error.HTTPError as e:
        print(f"test read: {url} -> {json.load(e).get('error')}")
EOF
else
    echo "the web services start with the gateway: sudo systemctl start agent-vllm"
fi
