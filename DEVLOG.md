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
- `scripts/harness/repo-facts.py`: the test. Each question runs in a fresh headless session through the recording proxy; the `ANSWER:` line is scored, as a number within a tolerance, as required strings, or as a pattern. `--ab` runs the settings with and without the new pieces in turns. `repo-facts.example.json` asks about the scratch project; the private question file asks 33 questions about the monorepo with the answers verified above.

Checked 2026-10-20, once the GPU was free: the 33 private questions, plan agent, two rounds each with and without the new pieces (numbers in docs/measurements.md). As scored, 60 and 58 of 66. After a review of every answer marked wrong, 63 of 64 for both: 12 of the 14 marks were defects of the question set (ambiguous counts, an expected answer narrower than the code, a question assuming one table row where there are nine, a key too strict), and one key number was wrong where the model was right. So on single-fact questions the rules change nothing, and they cost 42 percent more prompt tokens, concentrated in three questions where the model kept corroborating after it had the answer. Two lessons went into the test: the baseline also listed the private fact sheet as a skill and the model loaded it in 16 runs, so the runner now strips the skills with the other pieces; and an answer key is itself a set of claims, checked by the same method as the answers. The question the rules were written for, the broad one answered from merged subagent reports, is checked in the window, claim by claim. Still open: that check, and the edit, build and test cycle in the monorepo worktree.

### Free changes after the baseline (2026-10-21)

The baseline said where the errors come from: a claim written with its evidence in the context was right 63 times of 64; a claim assembled from unchecked subagent reports was right 13 times of 28. It also said that the model keeps a format rule in all but one of 152 runs and a process rule in 5 of 76. So the process moved into the harness:

- `scripts/harness/ask.py`: asks a question in steps the script runs itself. Narrow mode, the default: the model writes the narrow factual questions behind the question; each runs in a fresh session with the answer-line suffix of the repo-facts test; the hidden `check` agent, the verifier as a primary agent, refutes or confirms every pair; the answer is composed from the verified pairs only, cited by number, with "not checked" where they do not reach. Broad mode: one session answers as asked and lists its claims, the checker checks them, the session rewrites the answer with each correction marked. Report and raw events per step under `~/agent-data/ask/`.
- `config/opencode/opencode.json`: the `check` agent (hidden primary, verify prompt, no edits, no subagents) and `/ask` for the window, which asks the model to follow the same four steps itself; whether it does is to be measured.
- `config/opencode/prompts/explore.md`: the subagent pastes the command and its output into its table instead of a number worked out from a listing, and stops at one corroboration. `AGENTS.md`: the same stop rule, and no test projects or builds to check a semantic. This targets the 42 percent of extra tokens the rules cost in the baseline.
- `scripts/harness/fake-model.py` and `ask-check.sh`: a stand-in model server with canned replies, so the steps, the parsing and the reports can be checked without the GPU. The check passes: 10 requests, both modes, verdict tables parsed, the continued session carrying its earlier turns, the checker running with the verifier prompt and five tools.

Next, with the GPU: the broad question asked three ways, through `ask.py`, `ask.py --mode broad` and `/ask` in the window, each checked claim by claim against the repository and against the first answer of 2026-10-20. Still open: the edit, build and test cycle in the monorepo worktree.

### The broad question through the harness (2026-10-21)

The user ran `ask.py` in both modes on the question of 2026-10-20 (numbers in docs/measurements.md). The checker passed 11 of 12 pairs in narrow mode and 21 of 22 claims in broad mode, in 4 and 11 minutes, at 580,000 and 316,000 prompt tokens against 2.63 million for the first answer. An independent pass of 13 read-only agents that tried to refute every statement, with a second opinion on each disputed one, left 5 defects in 35: the checker's two, a count where names were asked and a compressed restatement by the claims step, both corrected by the harness in the final answers; and three the checker had let through, "6 kernel entries" for six directories matched by a word in their name, "the base domain" where the repository says default domain, and the product's "runtime logic" for what is its top-level supervisor alone. None is a guess where the repository is silent; "base domain" is the fact sheet's own wording.

The step records change what the numbers mean. The model loaded the private fact sheet in the decompose step and wrote its 12 questions from the sheet's lines; the broad answer loaded both local skills, ran two git commands and wrote one paragraph from them; and the broad checker loaded the sheet before checking, then checked by command anyway. So the runs measured the sheet plus verification, which is the production setting, and not the model's own reading, which is what the three-way comparison was for. Neither answer analyzed the logic: narrow mode chose the questions that are easiest to verify and said "not checked" for the rest; broad mode summarized and asked which slice to analyze.

Two fixes, checked offline with the stand-in server: the `verify` and `check` agents deny the skill tool, so a checker sees no skill at all (the recorded checker requests carry four tools and no skill listing), and `ask.py --no-skills` leaves the skills out of the answering agent's settings (its recorded requests list none of the local skills). Next, with the GPU, the same two runs with `--no-skills`, which is the model test, and `/ask` in the window, which is the production setting with the model driving the steps. Still open: the edit, build and test cycle.

### Increment 6b: the harness as a graph (2026-10-22)

Two sources were read against the open items. A review published in August 2026 (arXiv 2608.21156) names the way `ask.py` works "graph engineering": the structure of a task kept outside the model as explicit graphs of work, of agents and of state, with independent verifier nodes, above the prompt, context, harness and loop layers. The studies around it say where that pays and where it does not: fan-out helps tasks that split into independent, checkable parts and hurts sequential ones; a central verifier stops errors from propagating; free-form agent conversation is where the catalogued failures live; pruned communication keeps accuracy at a fraction of the tokens; code-graph retrieval gives the largest gains for small models on big repositories. Karpathy's "Deep Dive into LLMs" supplies the mechanisms behind the working rules: knowledge in the weights is a vague recollection and the context window is the working memory; every token gets the same small computation, so counting and spelling need tools; a model answers confidently where it knows nothing unless its boundary is probed and taught; thinking pays only on hard steps; the simple form of reinforcement learning is to sample, keep what a verifier passed, and train on that.

Our own numbers said the same: 13 of 28 claims correct when the plan agent decided for itself when to verify, 32 of 34 when the coded pipeline decided; and the one fix of 2026-10-21 that mattered, denying the verifier the skill tool, removed an edge rather than changing a prompt. So the pipeline became an explicit graph, and the open items were kept, changed or deferred on that evidence: the model test (`--no-skills`) and `/ask` in the window stay as the next measurements; the edit, build and test cycle becomes a flow with one editor that holds the whole context and command nodes for the build and the test, designed now and built next; the `.env` question is decided (secret files denied for every agent, and no headless run may wait for a permission); mechanism questions join the fact questions; question runs go four at a time; verification runs in batches; resume and per-node effort are added; the repository-graph node waits for increment 16 and its localization set.

Built, all standard library, checked offline against the stand-in server:

- `scripts/harness/graph.py`: the runner. A flow is a list of nodes; a model node names its agent, prompt, session, thinking effort, skills, tool cap and timeout, and its prompt is filled from the outputs of earlier nodes. A fan-out node runs once per item of an earlier list, up to four at a time, each process in a private opencode home of its own, so no two processes share a database; a node that continues a session runs in the home that created it; the first run of a flow runs alone. Every run's events are read as they arrive: tokens from the `step_finish` events, tool calls counted and stopped at the cap, a timeout that ends the process group. `graph.json` keeps a hash of every node's spec and inputs, and `--resume` reuses what has not changed. `nodes.jsonl` holds one line per run (prompt, output, answer, tool calls, tokens, verdict, whether the answer occurs verbatim in a tool output), copied to the trajectory store for work and personal runs.
- `scripts/harness/ask_flows.py`: the flows as data. Narrow: decompose into a facts list and a mechanisms list; one fresh session per question, the mechanism ones answering with a chain of `path:line` hops; the checker over batches of about six pairs; compose. Broad: answer, claims in the same session, the checker over batches, and the rewrite only when a claim was partly or wrong. Verifyset: a file of claims with known truth through the same checker, scored on the true and the false halves separately. Every node runs at effort `xhigh` until the alternating runs settle a lower effort for it.
- `scripts/harness/ask.py`: the same command line as before, plus `--effort node=variant`, `--resume`, `--concurrency`, `--scope`, `--claims`, `--dry-run` printing the graph; the report keeps its sections and gains status, effort, lane and tokens per run.
- `config/opencode/opencode.json`: four variants on the model (`low`, `medium`, `xhigh`, `off`), each sent by `opencode run --variant` as the request's reasoning effort; reads of `.env` and key files denied for every agent. The runner's headless copies also deny a third identical tool call and reads outside the project (the skill folders and opencode's scratch files excepted) and set `continue_loop_on_deny`, because a headless run that is asked for a permission auto-rejects it and ends with exit 0 and no answer.
- `config/opencode/prompts/verify.md`: a hop chain is correct only if every hop exists and does what its sentence says; positions and line numbers come from commands.
- `scripts/harness/probe.py`: the knowledge-boundary probe. Each private question goes straight to the server with no tools, five seeded samples, with the working rules as the system message and without; every sample is scored with the key and classed as correct, declined, wrong-confident or no answer line. The wrong-confident rate is what the verifier has to catch.
- `scripts/harness/repo-facts.py --variant`, `scripts/harness/make-verify-set.py`: the two effort comparisons, question runs at `medium` against the default on the private set, and the checker at `medium` against `xhigh` on a claims set made from the key and the probe's wrong answers.
- `scripts/harness/fake-model.py --delay` and `scripts/harness/ask-check.sh`: the offline check. 35 requests in 2 minutes: the narrow flow on four lanes in 30.6 s of wall time against 44.9 s on one lane; the resumed run reran one node; every request body carried its node's effort and none the two values the template rejects; no permission of a headless agent resolves to ask; six checker requests without a skill; two verify batches merged by claim number; eleven trajectory lines; the probe's classes as designed.

Facts verified in the opencode 1.19.3 and vLLM 0.31.0 sources, which the design rests on: a variant's `reasoningEffort` reaches the request body as `reasoning_effort`, which vLLM passes to the chat template; this model's template accepts `xhigh`, `medium` and `low`, `none` turns thinking off, and `high` is a 400; a headless ask is auto-rejected and ends the run silently; an agent's `steps` only appends a "text only" message, so budgets are the runner's; the database is write-ahead logged with a busy timeout, but a new one is unguarded across processes; the server takes four sequences at once, at 48 tokens per second each against 70 alone.

Designed, not built: the edit flow. Preflight (server up, worktree clean, no build or gate running, memory free); a read-only plan listing the files, the target and the claims; a gate on the plan; one editor with edits allowed on the planned files only and no shell; a gate on the diff; build and test as command nodes in a systemd scope with a memory limit and a job cap, logs to files; at most two fix rounds in the editor's session with the log tail; the checker over the diff and the claims; a gate that the diff is unchanged; the build server shut down on every path. The first task is the by-hand close of increment 6: one missing test case in a small library of the monorepo, built and tested with the same command lines, which also give the cold and warm timings the flow's timeouts need.

Next, with the GPU: the probe (330 requests), the narrow and broad flows with `--no-skills` on four lanes, and `/ask` in the window; then the two effort comparisons. The probe's wrong-confident rate under the rules decides the Case reading: under 10 percent, the boundary holds and the remaining errors are Case 1 or 3; 30 percent or more, the case for the cloud tier is made if the flows leave a residual error whose answer never appeared in a tool output; between, the residuals decide.

### Results of increment 6b (2026-10-23)

The GPU sessions ran in one day: the knowledge-boundary probe in two arms; the narrow and the broad flow twice each without the fact sheet; the model-driven graph through a new `self` mode (the window's `/ask` steps followed by the model in one session, then the same claims and checker nodes), in place of `/ask` typed in the window; the window session of the day; the checker at `xhigh` against `medium` on a claims set with known truth; the question nodes at the default effort against `medium`; and a reference, Fable 5.1 answering the same broad question and the same private set under the same read-only rules. Every answer was checked by independent read-only agents, two per batch, the same way for every configuration. The tables are in `docs/measurements.md`; what they settle:

- **The boundary holds, so the cloud tier is not made necessary by guessing.** Under the working rules the model never answered a private question from memory: 170 of 170 probe samples reached for a tool it did not have. Bare, it guessed wrong in 4 of 170, on four different questions, and no question was answered correctly from memory in either arm. In the runs without the fact sheet, 2 false statements in 222 checked claims and hops, both misreadings with the files in hand, none invented where the repository is silent.
- **The coded graph reads deeper than the model-driven one, at four times the time.** The narrow flow's decompose prompt asks for mechanism questions and the checker verifies hop chains: 21 of 24 and 15 of 18 hops right, through the build tool, the commit hook, an application's build to its image and a control application's step loop, in 18 and 33 minutes. The model following the same steps on its own asked ten trivia questions and was right about 40 of its 46 claims in 4.6 minutes, with nothing about the runtime. The prompt of the decompose node decides what the graph asks. The decomposition's variance between runs is the open problem: run 2 asked harder questions, three nodes hit their caps, two pairs went unanswered.
- **Verification is the cost, and the budgets are where it fails.** The checker took half to three quarters of the run time; 123 claims from one broad answer needed 21 batches and 81 minutes of runs; nodes hit a cap or a timeout in three of the five runs. Where it parsed, the checker agreed with the independent agents on 72 to 100 percent of claims; where it did not, the unparsed claims were mostly correct ones. Batching by hops fixed one cause; a claims prompt that asks for the load-bearing claims only is the next.
- **Effort: `xhigh` stays on every node.** For the verify node, `medium` caught every false claim, as `xhigh` did, passed one true claim fewer in one of two passes, and was slower with more thinking tokens, not fewer. For the question nodes, `medium` was 31 percent faster with 42 percent fewer prompt tokens but scored 65 of 68 against 68 of 68: three times the names asked for were in the reply and their count on the answer line, and one question flipped in both rounds, which the adoption rule excludes. Thinking pays here at the last step, following the output format, not in the reading.
- **The reference gap is in composition, not in facts.** On the private set the local model at its default effort, without the fact sheet, scored 68 of 68 in two rounds, as did the reference; on the broad question the reference wrote 30 claims with one imprecision in 7 minutes, one runtime chain included, where the local model's best run needed the coded graph, 18 minutes and 4 million prompt tokens to verify three chains, and its single-session survey carried 25 imprecisions and 2 errors in 123 claims.
- **Six harness defects found by the measurements, all fixed the same day:** the checker's batches counted pairs, not hops; a pipe inside a claim split the claims table; an escaped pipe shifted the checker's columns; the parsers dropped the leading underscore of a path (nine hops had been marked partly for that alone); a stopped runner left its opencode runs alive; the probe could not tell a dropped tool call from a missing answer. One design change: the broad answer node has 30 minutes, because the plan agent's working rules fan out subagents inside it, which the runner does not count yet.
- **The trajectory store works.** The five runs of the day wrote 67 trajectory lines, 11 of them with a checker verdict (8 correct): the first verified traces for the Phase 3 data set, collected without extra work.

Next: a decompose prompt with a fixed shape (one chain per named entry point) or two samples with the checkable one kept; the claims node asked for load-bearing claims; the subagents' tokens counted from the lane database; the verify-set builder phrasing substring keys as "names at least"; then increment 6c, the edit flow, and the by-hand close of increment 6.

Closed 2026-10-26: increment 6 ends here. Increment 6c, the by-hand edit cycle and the follow-ups above are deferred.

## Increment 7: personal shell over email

Dates: 2026-10-26 (brief, build), 2026-10-27 (install, checks).

Brief: Hermes Agent (MIT, Nous Research) becomes the always-on program reached from the phone. Its gateway runs only while the model server runs: it starts with the server, waits until the model answers, and stops before it. While running it reads a mailbox that only the agent uses every 15 seconds, acts on mail from one allowed address whose signature the mail provider has verified, and answers in the same thread with the local model. Commands the model runs go into a container with no network, started by a rootless Docker daemon under the agent's own user, `hermes`, which belongs to no privileged group. That user may open connections only to the model server, the local resolver and the two mail ports.

Decisions (2026-10-26):

- The channel is email. Telegram and Signal were ruled out. WhatsApp was dropped: from this network the only route is an unofficial bridge on a linked device, which the service's terms forbid and can ban, and its official business API needs a public webhook and since 2026-01-15 excludes general-purpose assistants.
- One mailbox at Gmail for the agent; one allowed sender, the owner's personal address. The adapter drops mail sent from its own address, so the two must differ.
- Work tasks may be answered in full by email. The mailbox then holds work content; the rules for cloud models and training are unchanged.
- The agent works only while the model server runs. Hermes alone would keep reading mail with the server stopped and answer that the model is unreachable, using up each request; tied to the server's unit, it reads nothing while the server is off. On start it skips mail already in the mailbox, so a request sent while it was off must be sent again.
- The sandbox is rootless Docker. The system Docker, which other work uses, is not touched.

What could leave the machine, from the documentation and the source of v0.22.5, and the switch for each:

- Usage metrics, opt-in: `telemetry.shared_metrics` off; the installer's local receipt is deleted by the opt-out.
- A daily update check against the GitHub API: `updates.check: false`. One route ignores it (upstream issue 126888).
- A model registry fetch, with no switch (same issue).
- Web search and extraction through a vendor by default, with keyless free-tier fallbacks: the web toolset stays off until increment 10, the fallbacks off.
- The mail itself: the provider stores both sides of every thread.

Two of these have no switch, so an nftables allowlist for the `hermes` user enforces the rest: anything else is counted, logged and refused.

Measured before the build, from this workstation:

| Measure | Result |
|---|---|
| Mail ports | reading 993 open, sending 465 (implicit TLS) open, 587 closed |
| App-password login | reading and sending both work |
| A reply from the owner's address | `Authentication-Results` from `mx.google.com`: `dkim=pass, spf=pass, dmarc=pass`, which is what the adapter requires |

Check, after the build: the owner's mail gets a reply, with the round trip timed; mail from another address gets none; a message put in the mailbox without the provider's verdict gets none; inside the sandbox there is only loopback, no route to the model server, no Docker socket and no mail variables; the `hermes` user cannot read the agent's private data; during ten questions nothing leaves except mail, model and resolver traffic, and every refused attempt is listed; the request body carries `reasoning_effort: xhigh`; while the model server is stopped nothing reads the mailbox, and mail sent meanwhile is not answered later; a stop during a run lets that run finish within the drain limit; the gateway comes back with the model server after a crash restart.

### Results of increment 7 (2026-10-27)

Built on 2026-10-26 and installed by the owner in five steps, each run with sudo, the two that act as `hermes` as that user:

- `scripts/shell/setup-hermes-user.sh`: the `hermes` system user (no login shell, no extra groups), subordinate IDs, `uidmap` and `slirp4netns`, cgroup delegation and linger for its user manager; the private data folder closed to other local users.
- `scripts/shell/install-rootless-docker.sh`: Docker 29.8.1's static release for `hermes`, hash-checked, as its user service; the sandbox image pulled by digest.
- `scripts/shell/install-hermes.sh`: the reviewed installer at the v0.22.5 commit, hash-checked; the settings in `config/hermes/settings.txt`; the metrics receipt removed.
- `scripts/shell/set-mail-secret.sh`: the mailbox settings and the app password into `hermes`' private `.env`, never printed.
- `scripts/shell/enable-shell.sh`: the egress rules, loaded at boot from now on, and the gateway unit bound to the model server's.
- From the unit, `wait-ready.sh` holds the gateway until the model and Docker answer, and `mail-notice.py` sends the online and offline notices, at most three online notices an hour.
- `scripts/shell/check-shell.sh`: the read-only checks that need root.

| Check | Result |
|---|---|
| Lifecycle | start: the gateway active 9 s after the model server, online notice sent; stop: the gateway stopped first, after 26 s of draining, offline notice sent; start again: online again; the owner's mail answered by the local model |
| Another sender | a stranger's message with a provider-style pass verdict, put in the mailbox over IMAP: marked read after 19 s, no reply, no model request |
| No provider verdict | a message from the owner's address without `Authentication-Results`: dropped with the adapter's warning after 22 s, no reply, no model request |
| The gateway's own view | `/home` shows only `hermes`' home and the path to the two helper scripts, `/run/user` only its runtime folder; `hermes` cannot enter the private data folder |
| The sandbox | one container, image pinned by digest; network none, only `lo`; the model port and the internet unreachable from inside; no Docker socket; empty `/home`; no variable beyond the image's own; root inside is `hermes` outside, every other ID a subordinate one; all capabilities dropped but chown, dac_override, fowner, setgid and setuid; no-new-privileges; 2048 processes, 4 GiB, 2 CPUs; all 15 mounts from `hermes`' home, the sandbox home and workspace writable, Hermes' caches and skills read-only |
| Egress since the rules were corrected | model 5 connections, mail 21, resolver 0 (names go through the resolver's local socket, not port 53), refused 16: at start Hermes tried GitHub's raw-content servers and a web-search vendor (parallel.ai), with the web toolset off |

Two defects found by the checks, fixed and verified the same day:

- `ProtectHome=tmpfs` hides `/run/user` as well as `/home`, so `ReadWritePaths=/run/user/<uid>` had nothing to open and the gateway failed every start with status 226/NAMESPACE. The runtime folder is now in `BindPaths`; the failure was reproduced with a throwaway user unit first.
- The egress rules began with `meta skuid != <uid> accept`. Packets with no user socket behind them, the kernel's own (network-drive mounts, resets, ICMP errors, closing connections), match no skuid rule, so they fell through to the refusal: for 18 hours the workstation's network drives could not connect and other programs' closing packets were refused. Only packets with a positive skuid match now enter the agent's chain (`meta skuid <uid> jump hermes`). Old and new were compared in throwaway network namespaces, and afterwards a network drive mounted with nothing refused.

Open, postponed: the request body at `reasoning_effort: xhigh` (recording proxy), a stop during a run, a crash restart of the model server, restart and reboot behaviour, the round trip time.

Noted for later:

- The `hermes` account could read the owner's files that were open to every local user. The gateway cannot, since its unit hides the home folders, and the sandbox cannot, with no mounts from outside `hermes`' home; but the rootless Docker daemon runs as `hermes` outside the gateway's unit, so a flaw in Hermes could have reached them through it (the model has no tool that adds mounts). The owner closed the home folder to other users the same day (mode 750; the model server's user keeps its own entry, and the gateway's view does not depend on it). Re-running the `hermes` install steps now needs a copy of the scripts outside the home folder.
- Hermes exits with status 1 when stopped, so the unit reads "failed" after every stop and the offline notice says so; at start it warns that the unit's stop timeout is 90 s, reading another service manager (the unit has 180 s).
- The Docker storage driver ignores the 10 GB disk cap; Hermes warns that no host-visible output folder is mounted, which matters for files sent back by email; the sandbox image has Python only, no compiler.

Closed 2026-10-27. The source copy of the app password was deleted; the password now lives only in `hermes`' private `.env`. The five open checks above are postponed. Next: increment 8, memory tiers.


## Increment 8: memory tiers

Dates: 2026-10-27 (brief, build).

Brief: the agent's memory is files and word search, in four tiers. Tier 1, the core notes in every prompt: Hermes' `MEMORY.md` (the agent's notes, at most 2,200 characters) and `USER.md` (the owner's profile, at most 1,375), read when a session starts and frozen for that session, written only through the `memory` tool, which refuses a write that would pass the limit, so the agent has to merge notes rather than pile them up. Tier 2, the full history: every message, tool call and result in Hermes' SQLite store with full-text search, which the `session_search` tool queries without a model call. Tier 3, topic notes, playbooks and past cases under git, comes with the nightly consolidation (increment 9) and the second phase; tier 4, embeddings or a graph, only if a measured memory test shows word search losing.

Evidence, from the research of 2026-10-09: for a single-user agent the findings favour a layered memory built on files; an agent searching plain files scored 74.0% on LoCoMo, ahead of specialised memory libraries; EvoMemBench (May 2026, 15 systems) found no method that wins everywhere and long-context baselines competitive. The limits keep tier 1 at about 1,300 tokens per request, and the frozen copy keeps the start of every prompt identical within a session, so the server's prefix cache reuses it.

Verified in the v0.22.5 source and documentation:

- Over email, all mail from one address is one session. It survives restarts, has no automatic reset, and is compressed when it reaches half the context window. The memory files are read again only by a new session, which a reply starting with `/new` begins; a slash command works only as the first line of a reply, because a new message gets its subject put in front.
- With `memory.write_approval` on, writes from email wait as files under `pending/memory/` until `/memory approve`; `/memory pending` lists them. A review of the conversation runs every 10 of the owner's messages on the same model and stages its proposals without a notice.
- The documentation warns that models under about 30B parameters often confirm a save without calling the tool, so the checks read the files, not the replies.
- The memory files are written atomically with mode 0600. External memory providers, which are cloud services, stay off (`memory.provider` empty), as does the OpenTelemetry export.
- `hermes sessions export --session-id` writes a session as one JSON line with every message, tool call and result; opencode 1.19.3 has `opencode export <id>`, and its sessions are listed in its own SQLite database with their folder.

Decisions (2026-10-27):

- Seed: `USER.md` with the owner's preferences for answers (literal explanations with real examples, commands inline, brevity, no guessing, a plan before any change of state); `MEMORY.md` with facts about the agent's own setup (a local model, a sandbox with no network and Python only, how mail commands work, that saves wait for approval). No work facts. The seed files are private; the repository has templates.
- Scope of email conversations: the owner tags each session. The first message after `/new` carries `[work]` or `[personal]` in its subject or at the start of its text; an untagged session is work, and a session that compression continues keeps its tag. opencode window sessions are tagged by folder: folders listed as personal in a private file are personal, everything else is work.
- Approval stays on, and the post-turn review stays at every 10 messages.

Build: a seed script; a git repository in the agent's memory folder; a nightly job as `hermes` with no network (02:30) that commits the day's memory changes, exports every session with activity since the last run and puts both, with the list of writes waiting for approval, in an outbox that only the owner can read besides `hermes`; a nightly job as the owner with no network (02:45) that files the sessions under `~/agent-data/trajectories/<work|personal>/hermes/`, exports the opencode window sessions changed since its last run under `.../opencode/`, and keeps the memory files and the day's diff under `~/agent-data/memory/hermes/`; a check script.

Check: the seed appears in a new session; three facts taught by email reach the files after approval and are used the next day after `/new`; a fact never saved is found with `session_search`; the prefix-cache hit rate during one session is recorded; the next morning the export holds the day's sessions under the right tags, the opencode sessions under theirs, and the memory diff; a `git revert` in the memory repository restores the notes before it; `hermes` still cannot read the owner's files, and both nightly jobs run without a network.

## Increment 9: nightly review

Dates: 2026-10-27 (brief, build).

Brief: once a night the agent rereads the day's conversations and proposes changes to its two memory files: facts the owner stated, corrections, merges of entries that say the same thing. The proposals wait for the owner's approval like every other memory write, and the review's report arrives by email. Offline consolidation has measured support (research of 2026-10-09: Letta's sleep-time compute, about 5x less compute at answer time and 13-18% higher accuracy); the owner's approval is the gate against a wrong consolidation.

Verified in v0.22.5: scheduled jobs run inside the gateway, each in a fresh session with the skills attached to it; a job that came due while the gateway was stopped fires once at the next start (`cron.catch_up_missed`); `platform_toolsets.cron` limits the tools of every scheduled session; `cron.allow_agent_scheduling` is off, so the agent cannot create jobs; delivery `email` goes to `EMAIL_HOME_ADDRESS`; reports are kept under `~/.hermes/cron/output/`.

Decision (2026-10-27): Hermes' own scheduler and a skill written in this repository, not the plugin from Hermes' catalog.

Build: `config/hermes/skills/nightly-review/SKILL.md` (at most 6 changes a night, each with the session and the owner's own words, nothing about the employer, no secrets, a fixed report format); `scripts/dream/enable-dreaming.sh` (the allowed sender as the email home channel, the skill, scheduled sessions limited to memory, session search, skills and a to-do list, the job at 03:00 delivered by email); the nightly export carries the reports to `~/agent-data/memory/hermes/dreams/`.

Check: seven nights, one report for each night the model ran (or at its next start); every proposal sourced; nothing about the employer; approved proposals in the next night's memory commit; one `git revert` restores a night.

## Increment 10: web research

Dates: 2026-10-27 (brief, build).

Brief: the agent searches the web and reads pages, while its own user still has no internet access. A new system user, `agentweb`, runs SearXNG (a self-hosted metasearch engine) on 127.0.0.1:8888 and a small fetch service on 127.0.0.1:8889. Searches go through the fetch service, which records every result URL for 24 hours; it reads a page only if its URL was such a result, only from public addresses (every address the name resolves to, on every redirect), at most 3 MB, http and https only, and returns the page as plain text. The `hermes` user may reach only 127.0.0.1:8889 for this; `agentweb` may reach only the web ports 80 and 443 on public addresses, so a page cannot lead the agent into this machine or the office network. A Hermes plugin (`web/agentweb`) hands page reading to the service, and Hermes' SearXNG provider is pointed at the same service for searches. Both services start and stop with the gateway.

Verified: in Hermes, SearXNG only searches, and every bundled page reader is a cloud vendor (Firecrawl, Tavily, Exa, Parallel, Keenable, Perplexity); a user web plugin loads from `~/.hermes/plugins/web/<name>/` once enabled under the key `web/<name>`. SearXNG at the pinned commit imports `tomllib`, so it needs Python 3.11 or newer despite declaring 3.10. From this office's address DuckDuckGo answers with a CAPTCHA, and Bing is off in SearXNG's defaults; Brave and Bing returned 27 and 28 results for two test queries.

Decision (2026-10-27): search, plus reading only pages that searches returned; the agent's own user keeps no internet access.

Build: `config/web/` (pins, SearXNG settings, a hash-locked requirements file: 32 packages, 850 hashes), `scripts/web/agent-web.py` (standard library only), `scripts/web/setup-web.sh`, `config/hermes/plugins/web/agentweb/`, `config/hermes/settings-web.txt`, `config/nftables/agent-web-egress.nft.in`, units `agent-searxng`, `agent-web` and `agent-web-egress`; the `hermes` egress rules allow 127.0.0.1:8889.

Checked offline (a test instance run as the owner): results from Brave and Bing; the service refused a page never searched, a request with another Host header, and loopback, private, link-local and non-http addresses; a searched page came back as 13,125 characters of text, and the plugin passed on both the text and the refusals.

Check (owner): ten questions from the phone, the citations checked by hand; the engines over one week (CAPTCHAs and suspensions show in the service's log); `agentweb`'s refused list; the `hermes` counters show web traffic only to the local service.

## Increment 11: backups

Dates: 2026-10-27 (brief, build).

Brief: every night at 04:00 an encrypted restic snapshot of the agent's memory and configuration and of the owner's personal agent data (memory snapshots and diffs, review reports, personal sessions, the seed and scope files, research documents) goes into a repository on `/var`, a separate volume from `/home`. Work-scope sessions, secrets and `hermes`' `.env` never go in, and a text file that names a work identifier from the owner's private list is left out. Every week old snapshots are thinned (14 daily, 8 weekly, 12 monthly) and 10% of the stored data is read back. restic 0.19.1, hash-checked; the job runs as root from a root-owned copy, without network.

Finding: the planned target, Backblaze B2, is not usable from here: the workstation's security software killed a test connection to it (exit 137) while a request to GitHub went through.

Decision (2026-10-27): the `/var` volume of this machine for now. It protects against deletion and corruption, not against losing the machine; a USB disk can be added later.

Build: `config/backup/backup.env`, `scripts/backup/setup-backup.sh`, `scripts/backup/agent-backup.sh`, `scripts/backup/restore-test.sh`, units `agent-backup` and `agent-backup-check` with their timers.

Checked offline (a test repository as the owner): the snapshot held the memory file, the memory snapshot and the personal session; a planted file naming a work identifier, the work session, the secrets and the `.env` stayed out; forget, prune and check passed.

Check (owner): the first snapshot; `restore-test.sh` finds the restored files identical; a planted work name is left out; the weekly check passes; a copy of the repository password kept outside the machine.

## Increment 12: Phase 1 review

Dates: 2026-10-27 (kit).

Brief: a check, not a build. Five real tasks in the work codebase, done by hand in the opencode window because the automatic edit flow (6c) is deferred, at least 3 passing their tests; a real GPU campaign with the model server stopped by hand and started again afterwards, without an out-of-memory kill; the median email round trip under 60 s, or a recorded decision; no model call outside this machine; nothing from `~/agent-data` in the repository. Decision at the end: which slice to deepen.

Build: `scripts/review/round-trips.py`, `scripts/review/phase1-check.sh`, `docs/phase1-validation.md` (every check of increments 8 to 12 with its command and expected result), a private task sheet.

Checked offline: `round-trips.py` on a stand-in store (two answers of 60 and 10 s; an unanswered message and a scheduled session ignored); the private-file comparison as the owner: 64,341 private files against 70 tracked files, one identical, the public decision record of increment 1, which is also kept in the private documents folder.

## Validation of increments 8 to 11 (2026-10-27)

The owner ran the five install steps between 13:10 and 13:15 and started the model server; the checks below are from the services' logs and the filed data.

- Install: one failure. The filing job of step 2 could not write into its folders: `install -d -o <owner>` gives the owner only the last folder of a path and leaves the parents it creates to root. `enable-memory.sh` now creates the folders as the owner and gives back any that an earlier run left to root; the second run passed.
- Filing: 1 email session (41 messages, untagged, so work) and 29 opencode sessions (27 work, 2 personal). Seven of the 29 opencode exports were cut off at a multiple of 32 KiB: `opencode export` can lose the end of its output through a pipe when it exits (two of three pipe runs of one session stopped at 131,072 bytes; three of three runs into a file were complete, 139,528 bytes). The collector now has opencode write into a file, checks the JSON, retries once, and exports again any filed session that does not parse; the seven were repaired, and all 29 parse.
- Memory: the seed is in place (6 and 5 entries). The email test of the seed and of three facts ran from 13:20 to 13:25; the files are read after the next nightly copy. The prefix-cache hit rate during the session rose to 80.1%.
- Web: SearXNG and the fetch service ran with the gateway. Searches returned 22 and 24 results, and the agent read the restic releases page (51,062 characters) to answer a question. `agentweb` was refused 10 UDP packets to port 443 of a CDN (HTTP/3 attempts, after which the client used TCP); `hermes` was refused only Hermes' start-up calls to GitHub's raw-content servers and parallel.ai. SearXNG logged no CAPTCHA or suspension.
- Backup: the first snapshot at 13:15. Sixteen files were left out for naming a work identifier: 14 research documents and the 2 personal opencode sessions, which discuss the work harness. None of the memory files were left out.

Open: the nightly review over seven nights; the search engines over one week, and nine more web questions with their citations checked; the three facts in the files, the recall test after `/new`, and one memory revert; the restore test, the planted work name and the weekly check; a copy of the backup password outside the machine; increment 12's review (five coding tasks, a GPU campaign, `phase1-check.sh`).
