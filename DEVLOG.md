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
- The service restarts on any exit, because vLLM exits with status 0 when its engine dies. A 6th start within 30 minutes is refused, so a crash loop cannot hold the GPU for long, and the service is not enabled at boot.

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

Check as a service, 2026-10-19: `sudo scripts/serve/setup-service.sh`, then `sudo systemctl start agent-vllm`. The first start took 251 s: 26 s loading the weights, 42 s compiling, 66 s capturing CUDA graphs, 54 s building a FlashInfer kernel once, and the rest starting up. The server holds 56,644 MiB of GPU memory, and `verify.py` passed 13 of 13 in 2.3 s.

Fixed on the way: under the service, check 13 could not see a usage report, because files the service creates are private to it, so the check would have passed without looking. `setup-service.sh` now creates the folder vLLM writes that report to with pass-through permission, and `verify.py` fails when it cannot look.

Close, 2026-10-19: other machines cannot connect. Only 127.0.0.1 accepts connections, and requests to this machine's network addresses are refused. Web pages are the remaining path. The server answers every cross-origin request with `Access-Control-Allow-Origin: *` and accepts any `Host` name. Measured with Chrome 153, a page served from this machine, from another port or as a file, can ask the model questions and read the answers. The same page marked as coming from the internet is blocked by Chrome before anything is sent. Other browsers were not tested. Accepted as low risk. Before increment 5 turns on the sleep and wake routes, the server must stop sending that header and refuse other `Host` names.

## Increment 4: measurement

Dates: 2026-10-19.

Brief: from here on, every change to the server is judged by numbers taken the same way each time. The numbers are time to first token (how long the model reads before it starts writing), tokens per second while writing, the total across requests running at once, the share of MTP guesses accepted, answer correctness on a fixed set, and GPU power in each state. They are taken in turns, A, B, A, B, A, B, with the same prompts, so a drift of the GPU over the run lands on both sides. A change is kept only if its answers are at least as good and it is not slower.

Decisions:

- All numbers go into the public `docs/measurements.md`: public model, public software, prompts from public code, a GPU anyone can buy. The raw records, which carry answers and paths, stay on this machine.
- `FLASHINFER_NO_DOWNLOAD=1` is set in `serve.sh`, so FlashInfer can never fetch a kernel from NVIDIA. The kernels this model needs are built locally on first start.
- MTP stays on: 1.8x to 2.2x faster writing in all three on/off pairs, no loss on the quality set, same time to first token.
- The effort default stays `xhigh`; the quality set is too short to separate the levels, and effort per agent role is decided later on real tasks.

Rejected: vLLM's own `vllm bench serve`. Its chat client counts only answer text, so with a thinking model it reports the first answer token, after all the thinking, as the first token.

Build: `scripts/measure/make-prompts.py` (prompts of 1K, 8K and 64K tokens from vLLM's Apache-2.0 source files, and the quality set with fixed seeds), `bench.py` (the client: streams, counts thinking and answer tokens, reads the MTP and cache counters before and after), `grade.py` (exact answers by text, code by hidden tests in a bubblewrap sandbox), `run.sh` (the protocol: six servers in turns, power sampling, resumable), `report.py` (the tables), `quality.jsonl` (24 items), `MEASURE_SPECULATIVE_CONFIG=off` in `serve.sh`.

Check, 2026-10-19, 49 minutes, tables in `docs/measurements.md`:

| Measure | Result |
|---|---|
| Writing speed, one request, 1K input | 79 tokens/s with MTP, 39 without |
| Writing speed, one request, 8K input | 70 with MTP, 38 without |
| Four requests at once, 1K input | 239 tokens/s total with MTP, 122 without |
| Time to first token | 0.24 s at 1K, 1.6 s at 8K, 17 s at 64K |
| 64K input, four at once | first token after 36 s, 10 tokens/s each |
| MTP guesses accepted | 61-73% |
| Quality set, MTP on / off | 23 / 22 of 24 in each of three pairs |
| Effort low / medium / xhigh | 23 / 23 / 23 of 24; 93 / 116 / 86 thinking tokens per item |
| Cache reuse, 6,030-token prefix | first token 0.30 s instead of 1.17 s; answers byte-identical, 24 of 24, in all four passes |
| GPU power | 18 W stopped, 35 W loaded and idle, 276-299 W working |
| Start to ready | 50 s warm, 90 s with a new configuration to compile |
| Network | no download line in six server logs; verify 13 of 13 |

The two items from the increment 3 fact-check are settled: downloads are forbidden and verified off, and MTP with cache reuse produced identical answers.

Close, 2026-10-19: closed with the decisions above standing. These tables are the baseline every later change to the server is compared against, starting with the browser protections and the sleep and wake routes of increment 5.

## Increment 5: GPU yield, deferred

Dates: 2026-10-19.

Brief: the plan had the model server give the GPU back to other jobs by itself. A wrapper around each GPU job would put the server to sleep, run the job, then wake the server, and a watcher would catch jobs started without the wrapper. In vLLM's level-1 sleep the weights are copied to host RAM and the cache of past conversations is discarded. Level 2 drops the weights too and is never used: wake does not load them again, and MTP breaks.

Measured with the sleep and wake routes switched on for the test (`VLLM_SERVER_DEV_MODE=1`, started from a user shell, not as the service):

| Measure | Result |
|---|---|
| GPU memory in use with the server loaded, desktop included | 57.8 GB |
| Freed by a level-1 sleep | 54.5 GiB: 29.2 GiB of weights copied to host RAM, 25.3 GiB of cache discarded; 2.1 GiB stay |
| First sleep, later sleeps | 19 s, then 0.6 s |
| Wake | 0.7 s; answers correct and at full speed afterwards |
| Host RAM kept after the first sleep | 29 GB for the life of the server process |
| Request sent while asleep | waits, answered after the wake |
| Request in flight when sleep is called | cut off, finish reason `abort` |
| Wake while another process holds 60 GB | HTTP 500, out of memory; the server stays asleep, and a retry after that process exits works |
| Stop and start instead | GPU free 10 s after the stop; answering about 60 s after the start |

The switch that adds the sleep and wake routes adds about twenty other control routes, one of which runs an engine function by name. A page open in a browser on this machine could call them, so they would need the origin, `Host` and token checks named at the close of increment 3.

Decision, 2026-10-19: deferred. One person uses this server. Before a GPU job that person stops the service, and afterwards starts it again:

```
sudo systemctl stop agent-vllm
<the job>
sudo systemctl start agent-vllm
```

Two rules go with that. Stop with `systemctl`, never with `kill`: the unit restarts a killed server after 30 s, in the middle of the job. The service is not enabled at boot, so after a restart of the machine the GPU is free until the service is started. A forgotten stop costs one failed job start; a forgotten start costs a refused connection until the next start. The control routes stay off, and `--enable-sleep-mode` stays in `serve.sh` because the increment 4 numbers were measured with it. Revisited when an unattended job needs the server back without a restart.

## Increment 6: coding harness

Dates: 2026-10-19.

Brief: opencode 1.19.3, a terminal coding agent under the MIT license, runs against the local model server. It reads, searches, edits and runs commands in the folder it is started in; the model asks for each of those with a tool request that the server's `qwen3_coder` parser turns into JSON. The release is pinned by version and hash in `config/opencode/opencode.env`. `scripts/harness/install-opencode.sh` downloads it, checks the hash, unpacks it under the private runtime folder and links `~/.local/bin/opencode` to `scripts/harness/opencode.sh`, which starts it with `config/opencode/opencode.json` and the switches below.

Decisions (2026-10-19): opencode first, Qwen Code only if the twenty-step test had failed; one git worktree per task, the agent never commits; the monorepo cycle is run by hand inside its dev shell.

What could leave the machine, read from the source at tag v1.19.3, and the switch for each:

- The model catalogue, fetched from the opencode site at start and every hour: `OPENCODE_DISABLE_MODELS_FETCH=true`. A provider defined in the settings does not need it.
- A version check against the npm registry, made by the terminal window: `OPENCODE_DISABLE_AUTOUPDATE=true` and `"autoupdate": false`.
- Language servers and formatters, downloaded on first use: none configured, and `OPENCODE_DISABLE_LSP_DOWNLOAD=true`.
- An npm install of `@opencode-ai/plugin` into the settings folder on every start when that folder lacks it, with no switch in the code: the install script seeds `~/.config/opencode` with the pinned package once, and the launcher points the npm registry at a closed local port.
- Sharing is discontinued in this release; `"share": "disabled"` stays as a guard. Web fetch and web search are denied. The code has no telemetry.

Skills: opencode reads the skill folders of other coding tools in the home folder and lists every skill it finds in the fixed part of every session. That is off (`OPENCODE_DISABLE_EXTERNAL_SKILLS`); own skills come from `skills.paths`. The skill with the monorepo's layout and build recipes lives outside the repository.

Other settings: `"snapshot": false`, since opencode would otherwise keep a second git index per project under its data folder; `small_model` is the same model, so the title it writes for each session is also local; `limit.context` 131,072 and `limit.output` 32,768, which opencode caps at 32,000 tokens per answer. Thinking effort stays at the server default.

Found while building:

- `opencode run` reads standard input to its end when it is not a terminal. Started from a script with an open pipe, it waited forever before its first request. The runner closes stdin.
- opencode writes a 40-byte project id file into the repository's `.git` folder; for a linked worktree, into the main repository's `.git`. Nothing else is written into the project.
- Tracing the binary's system calls stalls it, so the egress check runs one task inside a network namespace whose only way out is a local socket to the server.
- Tools sent under `opencode run`: bash, edit, glob, grep, read, skill, task, todowrite, write. The question tool is off without a terminal, and webfetch is denied.

Check: `scripts/harness/run-session.py` builds a small Python project with one failing test and runs twenty requests in one session: read, docstrings, run the tests, fix the bug, find the callers, rename a function across files, add functions and tests, a `--json` flag, an error exit, a subprocess test, a cleanup, a summary. Every request to the server and every answer are recorded through `scripts/harness/record-proxy.py`; `score.py` counts tool requests, malformed ones, repeats and error results; the runner checks the project's end state ten ways. The same recording is the input of `replay.py`, the gate for later server changes.

| Measure | Result |
|---|---|
| Fixed part of every session | 10,002-character system prompt and 9 tool definitions; 7,256 tokens with the first question |
| First answer of a session, two file reads | first token after 1.3 s, done in 2.9 s |
| Twenty steps, streaming | 60 requests, 52 tool requests, 0 malformed, 0 repeated, 0 tool errors, 0 refused permissions, 10 of 10 end-state checks, 4.1 min |
| Twenty steps, non-streaming parser | 61 requests, 55 tool requests, 0 malformed, 0 repeated, 0 tool errors, 0 refused permissions, 10 of 10 end-state checks, 4.1 min |
| Replay of the streamed recording, 60 requests | 60 of 60 answered and parsed; 45 asked for the same tools as recorded, 59 the same finish reason; 3.6 min |
| Tokens over the streamed session | 997,622 prompt tokens, largest request 25,850; 13,371 generated, of which 6,806 thinking; median first token 0.36 s |
| One task inside a network namespace with no route out | done in 4 s, 1 tool call, no connection failure in the debug log |

### Closing piece: grounding and verification (2026-10-20)

The reading half of the monorepo check ran first. In a worktree of a 147,709-file Bazel C++/Python monorepo, the plan agent was asked to analyze the codebase and explain its logic. It split the work into four parallel explore subagents on its own, made 158 tool calls, all read-only, asked twice before reading outside the worktree, and answered after 14 minutes and 2.6 million prompt tokens. Every factual claim in the answer was then checked against the repository: of 28 claims, 13 were correct, 14 partly wrong and 1 wrong. The flawed claims had four causes: counts estimated from partial listings instead of computed (3); acronym expansions and product descriptions paraphrased, or inferred from folder names where no document existed (6); rules repeated from documents without checking the code (4); and mechanisms described wrongly (2). The main agent had merged the subagent reports without checking them. The answer also described the structure of the repository, not its runtime logic, which was the question.

Built against those causes, all under `config/opencode/`:

- `AGENTS.md`: working rules for every session and every subagent: numbers only from commands, names and expansions only with their source, guesses marked as inferred, documented rules checked against the code, one question per subagent with a fixed evidence table, a verify pass before a factual answer, never commit. The settings file names it through `AGENT_REPO`, which the launcher exports, so no copy outside the repository is needed.
- `prompts/explore.md`: the built-in explore subagent answers one question with an evidence table and stops at 30 steps; the general subagent stops at 40.
- `prompts/verify.md` and the `verify` agent: a read-only subagent that tries to refute each claim and returns verdicts with evidence. `/verify` in the window hands it the claims of the last answer.
- `skills/repo-facts/`: a template for a fact sheet with evidence on every line, filled in outside the repository for a private codebase. The local copy for the monorepo holds the facts verified above.
- `scripts/harness/repo-facts.py`: the test. Each question runs in a fresh headless session through the recording proxy; the `ANSWER:` line is scored, as a number within a tolerance, as required strings, or as a pattern. `--ab` runs the settings with and without the new pieces in turns. `repo-facts.example.jsonl` asks about the scratch project; the private question file asks 33 questions about the monorepo with the answers verified above.

Check, once the GPU is free again (the service is stopped for other work): `repo-facts.py --ab --rounds 2` on the private questions gives the error rate with and without the rules, and the four-subagent question is asked again in the window and its answer checked the same way. Still open: those baseline numbers, and the edit, build and test cycle in the monorepo worktree.
