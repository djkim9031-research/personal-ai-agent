# 001: Model selection

Date: 2026-10-09. Status: decided.

## Decision

- Local primary: Qwen3.8-27B, official FP8 checkpoint, served by vLLM 0.31 or later on the RTX PRO 5000 72GB.
- Local secondary: Qwen3.6-35B-A3B at NVFP4, for fast subagent, summary and classification work. It does not fit beside the primary at the planned memory split, and two vLLM servers on one GPU is unsupported, so it is swapped in for batch windows. Increment 17 measures whether that is worth it.
- A/B candidate: Muse Glimmer-30B, for tool-heavy research only, decided by the private research eval.
- Cloud tiers, open weights on zero-retention hosts: DeepSeek V4.1-Flash or GLM-5.3-Flash by default; GLM-5.3 or DeepSeek V4 Pro for hard steps; Qwen3.8 Max for large refactors; Kimi K3 for deep web research only.

## How candidates were ranked

1. SWE-bench Verified from one third-party harness: vals.ai, mini-SWE-agent, board archived 2026-09-01.
2. Then SWE-bench Pro, Terminal-Bench, HLE and tool-use boards. These are vendor-reported. Terminal-Bench 2.0, 2.1, 3.0 and 4.0 are different tests and are never compared across versions.

## Numbers relied on

| Model | Params, total / active | Fits 72 GB | SWE-bench Verified | SWE-bench Pro | Terminal-Bench | HLE, no tools |
|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | ~1.7T / 49B | no | 96.4 | 55.4 | 87.9 on 2.1 | 37.7 |
| GLM-5.3 | 753B / 40B | no | 95.4 | n/a | 88.2 on 2.1 | n/a |
| Kimi K3 | 2.8T / 104B | no | 93.4 | n/a | 88.3 on 2.1, vendor | 43.5 |
| GLM-5.3-Flash | 320B / 18B | no | 92.0 | n/a | n/a | n/a |
| Qwen3.8 Max | 2.45T / 95B | no | 85.6 | 67.7 | 86.6 on 2.1 | 43.6 |
| **Qwen3.8-27B** | 27B dense | yes, FP8 | **86.0** | **61.7** | **73.0 on 2.1** | **30.8** |
| Muse Glimmer-30B | 30B dense | yes | 76.0, vendor | 51.2 | 51.7 on 2.1 | 22.0 |
| Qwen3.6-35B-A3B | 35B / 3B | yes, NVFP4 | 73.4, vendor | 49.5 | 51.5 on 2.0 | 21.4 |
| Qwen3.6-27B | 27B dense | yes | 70.0 | 53.5 | 60.7-63.4 on 2.1 | 24.0 |
| Gemma 4 31B | 31B dense | yes | 66.6, reported by Meta | 36.9 | 43.4 on 2.1 | 19.5 |
| gpt-oss-120b | 117B / 5B | 4-bit only | 33.6 | n/a | n/a | ~19 |

Best closed models for reference: 96-97 on SWE-bench Verified, 81-90 on SWE-bench Pro, 85-88 on Terminal-Bench 2.1, 65-68 on HLE.

## Fit on a 72 GB card

- Weights: FP8 is 1 byte per parameter; NVFP4 is about 0.56 byte with its scales. The Qwen3.8-27B FP8 checkpoint is 31-38 GB on disk by the two sources found; measured at load in increment 3.
- KV cache: only 16 of the model's 64 layers use full attention, with 4 KV heads of dimension 256. The other 48 are Gated DeltaNet layers with a fixed-size state. KV per token at FP8 is 16 x 4 x 256 x 2 for K and V x 1 byte = 32 KB, so the full 262K context costs 8 GiB. With prefix caching and MTP, vLLM also stores DeltaNet state snapshots padded to attention blocks, so the real token pool is read from the startup log.
- Every model ranked above the 27B is 320B-2.8T parameters, at least 160 GB even at 4-bit. None fits.

## Why a dense 27B as primary and a 3B-active MoE as secondary

Decode is memory-bandwidth-bound: tokens/s is roughly bandwidth divided by bytes read per token. At 1,344 GB/s the FP8 27B reads about 27 GB per token, a ceiling near 50 tokens/s before speculative decoding. The 3B-active MoE reads 2-3 GB, a ceiling about ten times higher; published single-stream runs on a 96 GB card of the same generation show it about 3x faster in practice. The 27B is 13 points better on SWE-bench Verified and 12 on SWE-bench Pro, so it does the work that matters; the MoE takes fan-out and summarization where speed matters more. The 27B's built-in MTP head adds speculative decoding; its gain is measured in increment 4.

## Rejected

- Muse Glimmer-30B, Meta's only current open model: behind the 27B by 10 points on SWE-bench Pro, 21 on Terminal-Bench 2.1 and 9 on HLE. Its one documented edge is tool orchestration, MCP Atlas 75.5 against Qwen3.6-27B's 62.5; no Qwen3.8 figure exists. Kept as the A/B candidate.
- Kimi K3 and K2.x: no checkpoint fits 72 GB VRAM plus 124 GB RAM; the smallest K3 GGUF is 466 GB. API only.
- Gemma 4 31B, Qwen3-Coder-Next, Devstral Small 2, Nemotron 3.5 Lightning, Granite 4.2, gpt-oss-120b: 15-50 points behind on SWE-bench Pro or Terminal-Bench.
- MiMo-V2.6-Pro: no SWE-bench Verified row and no zero-retention host; excluded until one exists.
- Self-hosting anything above the 27B: GLM-5.3-Flash needs one B300 at $7-9 an hour and the rest need 2-8 GPU nodes at $12-76 an hour, against $0.02-0.76 per SWE-bench task through per-token APIs.

## Cloud tiers

Per million tokens, input / output, 2026-10-09. US-pinned endpoints cost 1.5x.

| Role | Model | Price | Host |
|---|---|---|---|
| default | DeepSeek V4.1-Flash | $0.30 / $1.20 | Fireworks |
| default | GLM-5.3-Flash | $0.15 / $0.50 | Fireworks, Together |
| hard steps | GLM-5.3 | $1.40 / $4.40 | Fireworks |
| hard steps | DeepSeek V4 Pro | $1.32 / $3.96 | Together, zero-retention toggle on |
| large refactors | Qwen3.8 Max | $2 / $6 | Fireworks, Together |
| deep web research | Kimi K3 | $3 / $15 | Fireworks |

Benchmark tasks are short. Real repository tasks run 5-20x longer, so tokens per task are metered from the first call.

## Open, to be measured

- Qwen3.8-27B tokens/s, TTFT and MTP acceptance at 8K and 64K context on this card: increment 4.
- Tool-call reliability with 20-30 tool schemas in the coding and shell harnesses: increments 6 and 7.
- Qwen3.8-27B has no published BrowseComp, tau2 or MCP Atlas score; the private research eval decides whether Glimmer earns a slot.
- vals.ai retired its SWE-bench Verified board on 2026-09-01, so newer models will lack a third-party row.

## Sources

- https://huggingface.co/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- https://huggingface.co/meta-models/Muse-Glimmer-30B
- https://huggingface.co/moonshotai/Kimi-K3
- https://www.vals.ai/benchmarks/swebench
- https://artificialanalysis.ai/models/open-source
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://docs.fireworks.ai/serverless/pricing
