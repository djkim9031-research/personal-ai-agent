# Devlog

Each increment follows the same loop: a brief, a decision, the smallest build, a check, and a close.

## Increment 1: model selection

Dates: 2026-10-09 to 2026-10-15.

Decision: Qwen3.8-27B in FP8 is the local model. Qwen3.6-35B-A3B in NVFP4 is the faster helper for batch jobs. Muse Glimmer-30B is an A/B candidate for tool-heavy research. Larger open models run only on zero-retention hosts, and only for hard steps. The full record is `docs/decisions/001-model-selection.md`.

Why: it is the highest-ranked open model that fits a 72 GB GPU in FP8 with room for long context. Every stronger open model has 320 billion to 2.8 trillion parameters.

Check: the decision record lists every number it relies on, with sources.

## Increment 2: repository and checks

Dates: 2026-10-15.

Brief: the repository holds code, settings templates and docs only. Five layers keep everything else out:

1. The ignore list.
2. Three hooks: `pre-commit`, `commit-msg` and `pre-push`. The pre-push hook re-checks every commit, because the other two can be skipped with `--no-verify`.
3. A private deny list outside the repository. The hook code is public; the words it looks for are not.
4. GitHub secret scanning with push protection, for known token formats.
5. An audit of the remote after each push.

The workstation's global git settings carry another identity, commit template and hooks folder, so this repository sets its own in `.git/config`.

Decisions: commits are authored as `djkim9031-research`, and this repository uses only that account's credentials. The ignore list is as proposed, and the license is MIT.

Rejected: blocking force pushes on GitHub. Removing a leaked secret needs a history rewrite, and that needs a force push.

Check, 2026-10-15: `scripts/hygiene/selftest.sh` passed 16 of 16 attempts in a throwaway clone pushing to a local bare repository:

| Attempt | Result |
|---|---|
| Push of the existing history | accepted |
| Three deny-list samples, each in a commit message and in a file | refused by `commit-msg` and `pre-commit` |
| A random fake GitHub token in a file | refused by gitleaks, rule `github-pat` |
| A 6 MB file | refused by `pre-commit` |
| A file under `data/`, forced past the ignore list | refused by `pre-commit` |
| Another email, committed with `--no-verify` | refused by `pre-push` |
| A deny-list sample and a fake token, committed with `--no-verify` | refused by `pre-push` |
| A push of a notes ref | refused by `pre-push` |
| A clean commit and push | accepted; the remote holds only `HEAD` and `main` |

A missing deny list and a commit under another email are both refused as well.

Published, 2026-10-15: GitHub had secret scanning and push protection on by default. The audit passed with two commits and only `main` on GitHub, and both commits are linked to `djkim9031-research`.

Close, 2026-10-15: the checks on this machine can be skipped with `--no-verify`. GitHub's push protection still refuses known key formats, but a work name would only be caught by the audit after it is already public. So `--no-verify` is never used in this repository.

## Increment 3: serving the local model

Dates: 2026-10-15.

Brief: vLLM serves Qwen3.8-27B in FP8 on 127.0.0.1:8000 under the name `local-primary`, with a 131,072-token context, up to 4 requests at a time, MTP guessing 3 tokens ahead, and the qwen3 thinking and qwen3_coder tool parsers. It runs as a systemd service under its own unprivileged user, `agent-serve`. To that service, `/home` is empty except its own home, which it can write, and the runtime, the model and `config/vllm`, which it can only read. Every version is pinned: vLLM 0.31.0 from PyPI, built for CUDA 13.0, plus a 199-package lock, Python 3.12.13, and model revision `017b9c7a`.

Decisions:

- Thinking effort stays at the model's default, `xhigh`. Requests can ask for `medium` or `low`.
- The KV cache is 16-bit, not FP8 as first planned. The checkpoint ships no calibrated FP8 cache scales, so FP8 would use a default scale of 1.0. FP8 is an option to test later, not the default.
- The sleep and wake endpoints stay off until increment 5. The switch that adds them also exposes an unauthenticated control endpoint.
- The service restarts on any exit, because vLLM exits with status 0 when its engine dies. It gives up after 3 failed starts in 30 minutes, and it is not enabled at boot.

Found while building: the GPU kernels compiled on first start need `ninja` and `nvcc`. The start script now takes both from the environment and the CUDA toolkit instead of the caller's PATH. The publisher's CRC32 list predates three re-uploaded text files, so `download-model.sh` checks only the 66 weight files.

Check, before the service: `scripts/serve/verify.py` passed 13 of 13 in four setups:

- a plain run;
- a clean environment with an empty home;
- a sandbox where `/home` held only the three bound folders;
- the same sandbox with no network.

| Measure | Result |
|---|---|
| Weights in GPU memory | 29.0 GiB |
| KV cache pool | 357,335 tokens, 2.7 full-length requests at once |
| Cold start | 190 s; 139 s with warm caches |
| Speed, 3,520 tokens at `xhigh` | 95 tokens/s, of which 3,211 tokens were thinking |
| MTP acceptance | 82% |

Network calls made by the server: none.
