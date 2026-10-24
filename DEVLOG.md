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
