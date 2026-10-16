#!/usr/bin/env bash
# Downloads the model revision pinned in config/vllm/serve.env into
# ~/agent-data/hf and checks every weight file against the publisher's
# CRC32 list. The publisher re-uploaded three small text files after
# writing that list, so only the weight files are compared.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/vllm/serve.env"
venv=${AGENT_VENV:-$HOME/agent-data/runtime/vllm-$VLLM_VERSION}
dest=$HOME/agent-data/hf/${MODEL_REPO#*/}

HF_HOME=$HOME/agent-data/hf/.hf-home HF_HUB_DISABLE_TELEMETRY=1 HF_XET_HIGH_PERFORMANCE=1 \
    "$venv/bin/hf" download "$MODEL_REPO" --revision "$MODEL_REVISION" --local-dir "$dest"

"$venv/bin/python" - "$dest" <<'PY'
import os, sys, zlib
folder, checked, bad = sys.argv[1], 0, []
for line in open(os.path.join(folder, "crc32.txt")):
    want, name = line.split(None, 1)
    name = name.strip()
    if not name.endswith(".safetensors"):
        continue
    crc = 0
    with open(os.path.join(folder, name), "rb") as f:
        while chunk := f.read(1 << 24):
            crc = zlib.crc32(chunk, crc)
    checked += 1
    if f"{crc:08x}" != want.lower():
        bad.append(name)
print(f"{checked} weight files checked, {len(bad)} mismatches {bad if bad else ''}")
sys.exit(1 if bad else 0)
PY
