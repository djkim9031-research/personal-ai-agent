#!/usr/bin/env bash
# Starts the local model server with the pinned settings in serve.env.
# The same script serves a manual run and the system service; paths come
# from the environment:
#   AGENT_VENV   vLLM environment, default ~/agent-data/runtime/vllm-<version>
#   AGENT_MODEL  model folder, default ~/agent-data/hf/<model name>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/serve.env"
venv=${AGENT_VENV:-$HOME/agent-data/runtime/vllm-$VLLM_VERSION}
model=${AGENT_MODEL:-$HOME/agent-data/hf/${MODEL_REPO#*/}}
[ -x "$venv/bin/vllm" ] || { echo "vLLM not found in $venv" >&2; exit 1; }
[ -f "$model/config.json" ] || { echo "model not found in $model" >&2; exit 1; }

# No usage reports and no Hugging Face calls. The sleep and wake endpoints
# stay off until the GPU-sharing step needs them: the switch that adds them,
# VLLM_SERVER_DEV_MODE, also exposes an unauthenticated control endpoint.
# --enable-sleep-mode alone only selects the memory allocator sleep needs.
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1

# FlashInfer may fetch prebuilt kernels from NVIDIA when one is missing.
# Forbidden here, so a start never depends on the network; the kernels this
# model needs are built locally on first start.
export FLASHINFER_NO_DOWNLOAD=1

# Measurement runs (scripts/measure/run.sh) switch MTP off with
# MEASURE_SPECULATIVE_CONFIG=off. The service never sets it.
spec=${MEASURE_SPECULATIVE_CONFIG:-$SPECULATIVE_CONFIG}
spec_args=(--speculative-config "$spec")
[ "$spec" != off ] || spec_args=()

# GPU kernels are compiled on first use: ninja comes from the environment
# and nvcc from the CUDA toolkit, so neither depends on the caller's PATH.
export CUDA_HOME=${CUDA_HOME:-/usr/local/cuda}
export PATH="$venv/bin:$CUDA_HOME/bin:$PATH"

exec "$venv/bin/vllm" serve "$model" \
    --served-model-name "$SERVED_NAME" \
    --host "$HOST" --port "$PORT" \
    --max-model-len "$MAX_MODEL_LEN" --max-num-seqs "$MAX_NUM_SEQS" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" --kv-cache-dtype "$KV_CACHE_DTYPE" \
    --enable-prefix-caching \
    "${spec_args[@]}" \
    --reasoning-parser "$REASONING_PARSER" \
    --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER" \
    --limit-mm-per-prompt "$LIMIT_MM_PER_PROMPT" \
    --enable-sleep-mode --seed "$SEED"
