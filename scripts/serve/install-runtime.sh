#!/usr/bin/env bash
# Builds the pinned vLLM environment under ~/agent-data/runtime: a
# uv-managed Python in runtime/python and a venv named after the vLLM
# version, so an upgrade is built beside the current one. Every package
# version comes from config/vllm/requirements-<version>.txt.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/vllm/serve.env"
runtime=$HOME/agent-data/runtime
venv=$runtime/vllm-$VLLM_VERSION
lock=$repo/config/vllm/requirements-$VLLM_VERSION.txt
[ -f "$lock" ] || { echo "no lock file: $lock" >&2; exit 1; }
[ ! -e "$venv" ] || { echo "already exists: $venv" >&2; exit 1; }

export UV_PYTHON_INSTALL_DIR=$runtime/python
uv python install "$PYTHON_VERSION"
uv venv "$venv" --python "$PYTHON_VERSION" --python-preference only-managed
VIRTUAL_ENV=$venv uv pip install -r "$lock"
"$venv/bin/vllm" --version
