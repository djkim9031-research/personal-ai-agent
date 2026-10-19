#!/usr/bin/env bash
# One-time setup, run with sudo from your own account. Creates the
# agent-serve system user, gives it read-only access to the vLLM
# environment, the model and this repository's serve scripts, and installs
# the agent-vllm service. The service is not enabled: it runs only when
# started, so it never holds the GPU unless you ask it to.
# Usage: sudo scripts/serve/setup-service.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/vllm/serve.env"
runtime=$owner_home/agent-data/runtime
venv=$runtime/vllm-$VLLM_VERSION
model=$owner_home/agent-data/hf/${MODEL_REPO#*/}
serve_home=/home/agent-serve
[ -x "$venv/bin/vllm" ] || { echo "vLLM environment missing: $venv" >&2; exit 1; }
[ -f "$model/config.json" ] || { echo "model missing: $model" >&2; exit 1; }

# A system user with no login shell and no password; its home holds caches.
if ! id agent-serve >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$serve_home" --shell /usr/sbin/nologin agent-serve
fi
install -d -o agent-serve -g agent-serve -m 0755 "$serve_home"
install -d -o agent-serve -g agent-serve -m 0750 "$serve_home/.cache" "$serve_home/tmp"
# vLLM would write a usage report here; pass-through lets verify.py see one.
install -d -o agent-serve -g agent-serve -m 0711 "$serve_home/.config" "$serve_home/.config/vllm"

# Read-only access, granted by name: pass-through on the parent folders,
# read on the runtime, the model and the serve scripts. Default entries
# cover files added later, for example a new vLLM version.
for d in "$owner_home" "$owner_home/agent-data" "$owner_home/agent-data/hf" "$repo" "$repo/config"; do
    setfacl -m u:agent-serve:--x "$d"
done
setfacl -R -m u:agent-serve:rX "$runtime" "$model" "$repo/config/vllm"
setfacl -R -d -m u:agent-serve:rX "$runtime" "$model" "$repo/config/vllm"

sed -e "s|@SERVE_HOME@|$serve_home|g" -e "s|@AGENT_VENV@|$venv|g" -e "s|@RUNTIME@|$runtime|g" \
    -e "s|@AGENT_MODEL@|$model|g" -e "s|@REPO@|$repo|g" -e "s|@PORT@|$PORT|g" \
    "$repo/config/systemd/agent-vllm.service.in" > /etc/systemd/system/agent-vllm.service
systemctl daemon-reload

# Check the access as agent-serve before the first start.
as_serve() { runuser -u agent-serve -- env HOME="$serve_home" "$@"; }
as_serve test -r "$repo/config/vllm/serve.env"
as_serve test -r "$model/config.json"
as_serve "$venv/bin/python" -c "import torch, vllm; print('agent-serve loads vllm', vllm.__version__, 'and sees', torch.cuda.device_count(), 'GPU')"
as_serve test ! -r "$owner_home/.bashrc" || echo "note: agent-serve can read files in $owner_home that are open to all users; the service itself sees only the folders bound in its unit"
echo "agent-serve: $(id agent-serve)"
echo "installed /etc/systemd/system/agent-vllm.service, not enabled"
echo "start it with: sudo systemctl start agent-vllm"
