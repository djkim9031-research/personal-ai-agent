# The coding harness in detail

The README covers installing and starting opencode. This page describes what the settings do, the step-by-step question runner and the checks.

## What every session gets

`~/.local/bin/opencode` is `scripts/harness/opencode.sh`, which starts the release pinned in `config/opencode/opencode.env` with `config/opencode/opencode.json` and the switches that keep it on this machine: the model is the local server, sharing and the update check are off, the model catalogue and language servers are not downloaded, web fetch and web search are denied, and the skills of other tools in the home folder are ignored. The agent never commits; review the diff and commit yourself.

Every session also reads `config/opencode/AGENTS.md`, the working rules: numbers only from commands, names only with their source, guesses marked as inferred, one question per subagent, a verify pass before a factual answer. The built-in explore subagent runs on `config/opencode/prompts/explore.md` with a cap of 30 steps; the `verify` subagent (`prompts/verify.md`) tries to refute a list of claims against the repository, and `/verify` in the window hands it the claims of the last answer.

Skills are read from `~/agent-data/work/skills/`, outside the repository. `skills/repo-facts/` is a template for a fact sheet about one codebase that the model reads instead of guessing: copy the folder there and fill it in.

## Questions answered step by step: `ask.py`

`scripts/harness/ask.py --project DIR --scope personal "question"` runs a question as a graph of opencode runs: the flows are data in `scripts/harness/ask_flows.py`, executed by `scripts/harness/graph.py`, and every node names its agent, prompt, thinking effort, session, skills, tool cap and timeout; the text of one node fills the prompt of the next, so no step depends on the model remembering a rule.

- Narrow mode, the default: the model splits the question into fact questions (one command or one file each) and mechanism questions (a chain of `path:line` hops); each is answered in a fresh session, four at a time in private opencode homes; the hidden `check` agent (the verifier as a primary agent, `prompts/verify.md`) refutes or confirms them in batches of about six units, a fact being one unit and a hop chain as many as its hops; the answer is composed from the verified pairs only, cited by number, with "not checked" where they do not reach.
- `--mode broad` answers in one session, has the model list its claims, checks them in batches and rewrites the answer only when a claim was partly or wrong.
- `--mode verifyset --claims FILE` checks claims with known truth, to measure the checker itself.
- `--mode self` is the model-driven graph: the window's `/ask` steps followed by the model in one session, then checked by the same claims and checker nodes, so the coded graph and the model-driven one are compared under one checker.
- `--no-skills` leaves the skills out, so a fact sheet cannot stand in for the repository when the model's own reading is what is measured; the verifier agents never see the skills.
- `--effort node=variant,...` sets the thinking effort per node (`low`, `medium`, `xhigh`, `off`); `--resume DIR` reruns only the nodes whose spec or inputs changed (a raised tool cap or timeout reruns only the nodes it stopped); `--dry-run` prints the graph.
- Private flows: every `*.py` file in `~/agent-data/flows/` may define `FLOWS` of its own from the pieces of `ask_flows.py`, and each becomes a mode (template `config/flows/facts.py.example`); a built-in name is refused. They never enter the repository.

The checker sometimes checks a hop chain one hop per row, numbered 8.1, 8.2 or 8(1), 8(2). Those rows are folded into one verdict by the rule of `prompts/verify.md`: correct only if every hop is, wrong only if every hop is, partly otherwise, with the first hop that is not correct as the correction. A claim whose verdict still cannot be read reaches compose marked "not checked: do not use this answer". Before this rule a first real run lost the verdicts of three chains that the checker had found correct, and compose used them as given.

`/custom-graph question` runs the narrow mode from the window, on the window's project; a command that writes `<mode>NAME</mode>` before the question runs another flow (README, "Your own graphs, skills, tools and commands"). The command puts the text between `<question>` markers and hands it to the hidden `graph` agent (`prompts/graph.md`), whose only tool is `custom_graph` (`config/opencode/tools/custom_graph.ts`). `install-opencode.sh` links the tool into `~/.config/opencode/tools/`; the runner's own headless runs use private opencode homes without it, and the settings deny it to every other agent, so they are never offered it. The tool takes the question from the owner's message, not from the model's copy of it, picks work or personal with `agent-notes scope` (the notes' rule), runs `ask.py --project <session folder> --scope <scope> --out ~/agent-data/ask/<time>`, and returns what it prints: the answer, the claims table and the report path, which the agent repeats as its reply. opencode 1.19.3 drops a custom tool's progress updates, so the runner's progress goes to `progress.log` in the run folder, linked as `~/agent-data/ask/latest`; Esc sends the runner SIGTERM, on which it stops its runs. The tool also links the run folder as `~/agent-data/ask/sessions/<session id>`, and the window's graph panel (`config/opencode/tui/graph-view.tsx`, listed in `config/opencode/tui.json`, which the launcher passes to opencode as `OPENCODE_TUI_CONFIG`; headless runs never load it) draws the session's run in the sidebar from it every second. `graph.py` keeps `graph.json` current for it: the planned steps, every run as waiting, then running with its tool calls (written at most once a second), then its result, and the run as running, done, stopped or failed; the file is replaced whole, so a reader never sees half of it. The window shows a citation such as `[1]` without its brackets; `report.md` keeps them. opencode runs a `` !`command` `` written in any command's text, typed text included, in the shell before the model sees it, so never write one in a question.

The headless settings copies deny what opencode would otherwise ask about, because a headless run that asks ends without an answer. The report, the raw events, `graph.json` and one trajectory line per run go under `~/agent-data/ask/`; `--scope` says whether the run is copied to the trajectory store. In the window, `/ask question` asks the model to follow the same steps itself and `/verify` checks the last answer; keep one task per session and start a new session for the next.

## The knowledge-boundary probe

`scripts/harness/probe.py --questions FILE --out DIR` measures the model's knowledge boundary on a set of private questions: no tools, no files, several seeded samples each, with the working rules as the system message and without. Every sample is classed as correct, declined, wrong-confident or no answer line; the wrong-confident rate is what the verifier has to catch. An empty reply, the generation complete with nothing after the thinking, is counted apart: under the working rules the model writes a tool call, which the server drops when no tool was offered. `scripts/harness/make-verify-set.py` turns the answer key and the probe's wrong answers into a claims file for the verifyset mode.

## Checks

    scripts/harness/egress-check.sh DIR     # one task inside a network namespace that can only reach the model server
    scripts/harness/run-session.py --out OUT [--stream off]   # the twenty-step test on a scratch project, recorded
    scripts/harness/replay.py OUT/recording # sends the recorded requests again and compares the tool calls
    scripts/harness/repo-facts.py --questions scripts/harness/repo-facts.example.json --project DIR --out OUT --ab
                                            # factual questions, each in a fresh session, scored; --ab also runs the settings
                                            # without the rules, agents, commands and skills; --no-skills leaves the skills out
                                            # of every run; --variant medium also runs them at that thinking effort
    scripts/harness/ask-check.sh            # ask.py in every mode, resumed, with four lanes and one, /custom-graph through the
                                            # window's command and stopped through opencode's server, a private flow through a
                                            # private command, plus the probe and the effort variant, against the stand-in
                                            # model server; no GPU needed

## Measuring the model server

`scripts/measure/run.sh` measures speed, answer quality, cache reuse and GPU power with the service stopped, MTP on and off in turns, and writes everything under `~/agent-data/measure/results/<date>`. `scripts/measure/report.py` turns a results folder into the tables in `docs/measurements.md`. The prompts come from public code and from `scripts/measure/quality.jsonl`, 24 short tasks a script can grade.
