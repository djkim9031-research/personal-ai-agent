# personal-ai-agent

A personal AI agent that runs on one workstation: an open-weight model served locally, a harness for coding and research, memory consolidated every night, and a small budget for larger open models in the cloud when a task needs them.

It is built one increment at a time. `DEVLOG.md` records what each increment built and how it was checked.

## What is here

- `.githooks/`: checks that run before every commit and push
- `scripts/hygiene/`: setup, self-test and audit for those checks
- `config/vllm/`: the model server's pinned settings, package lock and start script
- `config/systemd/`: the service definition templates: the model server, the email gateway and its egress rules
- `scripts/serve/`: install, download, service setup, check and ask scripts for the model server
- `config/hermes/`, `config/nftables/`, `scripts/shell/`: the personal shell over email: pins, settings, the mail variables' names, the egress rules, and the install, start and check scripts
- `config/agent-memory/`, `scripts/agent-memory/`: the agent's memory: seed templates, the nightly memory commit and session export, the check
- `config/hermes/skills/nightly-review/`, `scripts/dream/`: the nightly review of the day's conversations
- `config/web/`, `scripts/web/`, `config/hermes/plugins/web/agentweb/`: web search and page reading through a local service
- `config/backup/`, `scripts/backup/`: encrypted backups and the restore test
- `scripts/review/`, `docs/phase1-validation.md`: the Phase 1 review: evidence script, round trips, every validation step
- `config/opencode/`: the coding harness's pinned release, its settings, the working rules every session reads and the subagent prompts
- `scripts/harness/`: install and start scripts for the harness, the recording proxy, the twenty-step test, its scorer, the replay, the repo-facts test, the step-by-step question runner and a stand-in model server for checks without the GPU
- `skills/`: templates for skill folders; filled-in copies live outside the repository
- `docs/decisions/`: decision records

## What is never here

Model weights, memory, conversation logs, documents, keys and tokens. They live in a private data folder outside the repository, and the checks refuse them.

## Setting up a clone

1. `scripts/hygiene/install-gitleaks.sh`
2. Create a deny-list file at the path in `.githooks/lib.sh`, in the format described there.
3. `scripts/hygiene/setup-repo.sh --name NAME --email EMAIL --gh-user LOGIN`
4. `scripts/hygiene/selftest.sh`

## Running the model server

One-time setup, after `scripts/serve/install-runtime.sh` and `scripts/serve/download-model.sh`:

    sudo scripts/serve/setup-service.sh

Then:

    sudo systemctl start agent-vllm      # returns once the model answers, about 3 minutes
    scripts/serve/verify.py              # 13 checks
    scripts/serve/ask.py "your question" # add --effort low or --image FILE
    sudo systemctl stop agent-vllm       # frees the GPU
    sudo journalctl -u agent-vllm -f     # the server's log

The service is not enabled at boot. It holds about 58 GB of GPU memory while it runs, so stop it before other GPU jobs.

## Measuring the server

`scripts/measure/run.sh` measures speed, answer quality, cache reuse and GPU power with the service stopped, MTP on and off in turns, and writes everything under `~/agent-data/measure/results/<date>`. `scripts/measure/report.py` turns a results folder into the tables in `docs/measurements.md`. The prompts come from public code and from `scripts/measure/quality.jsonl`, 24 short tasks a script can grade.


## Running the coding harness

opencode, pinned in `config/opencode/opencode.env`, works on files with the local model: it reads, searches, edits and runs commands in the folder it is started in. Once:

    scripts/harness/install-opencode.sh    # downloads the release, checks its hash, links ~/.local/bin/opencode

Then, with the model server running, inside a git worktree made for the task:

    git worktree add ../project-task -b task
    cd ../project-task
    opencode                               # the terminal window; `opencode run "..."` for one task without it

`~/.local/bin/opencode` is `scripts/harness/opencode.sh`, which starts the pinned release with `config/opencode/opencode.json` and the switches that keep it on this machine: the model is the local server, sharing and the update check are off, the model catalogue and language servers are not downloaded, web fetch and web search are denied, and the skills of other tools in the home folder are ignored. The agent never commits; review the diff and commit yourself.

Every session also reads `config/opencode/AGENTS.md`, the working rules: numbers only from commands, names only with their source, guesses marked as inferred, one question per subagent, a verify pass before a factual answer. The built-in explore subagent runs on `config/opencode/prompts/explore.md` with a cap of 30 steps; the `verify` subagent (`prompts/verify.md`) tries to refute a list of claims against the repository, and `/verify` in the window hands it the claims of the last answer. `skills/repo-facts/` is a template for a fact sheet the model reads instead of guessing; fill it in outside the repository.

`scripts/harness/ask.py --project DIR "question"` runs a question as a graph of opencode runs: the flows are data in `scripts/harness/ask_flows.py`, executed by `scripts/harness/graph.py`, and every node names its agent, prompt, thinking effort, session, skills, tool cap and timeout; the text of one node fills the prompt of the next, so no step depends on the model remembering a rule. Narrow mode, the default: the model splits the question into fact questions (one command or one file each) and mechanism questions (a chain of `path:line` hops); each is answered in a fresh session, four at a time in private opencode homes; the hidden `check` agent (the verifier as a primary agent, `prompts/verify.md`) refutes or confirms them in batches of about six units, a fact being one unit and a hop chain as many as its hops; the answer is composed from the verified pairs only, cited by number, with "not checked" where they do not reach. `--mode broad` answers in one session, has the model list its claims, checks them in batches and rewrites the answer only when a claim was partly or wrong. `--mode verifyset --claims FILE` checks claims with known truth, to measure the checker itself. `--mode self` is the model-driven graph: the window's `/ask` steps followed by the model in one session, then checked by the same claims and checker nodes, so the coded graph and the model-driven one are compared under one checker. `--no-skills` leaves the skills out, so a fact sheet cannot stand in for the repository when the model's own reading is what is measured; the verifier agents never see the skills. The headless settings copies deny what opencode would otherwise ask about, because a headless run that asks ends without an answer. `--effort node=variant,...` sets the thinking effort per node (`low`, `medium`, `xhigh`, `off`), `--resume DIR` reruns only the nodes whose spec or inputs changed (a raised tool cap or timeout reruns only the nodes it stopped), `--dry-run` prints the graph. The report, the raw events, `graph.json` and one trajectory line per run go under `~/agent-data/ask/`; `--scope` says whether the run is copied to the trajectory store. In the window, `/ask question` asks the model to follow the same steps itself and `/verify` checks the last answer; keep one task per session and start a new session for the next.

`scripts/harness/probe.py --questions FILE --out DIR` measures the model's knowledge boundary on the private questions: no tools, no files, several seeded samples each, with the working rules as the system message and without. Every sample is classed as correct, declined, wrong-confident or no answer line; the wrong-confident rate is what the verifier has to catch. An empty reply, the generation complete with nothing after the thinking, is counted apart: under the working rules the model writes a tool call, which the server drops when no tool was offered. `scripts/harness/make-verify-set.py` turns the answer key and the probe's wrong answers into a claims file for the verifyset mode.

Checks:

    scripts/harness/egress-check.sh DIR     # one task inside a network namespace that can only reach the model server
    scripts/harness/run-session.py --out OUT [--stream off]   # the twenty-step test on a scratch project, recorded
    scripts/harness/replay.py OUT/recording # sends the recorded requests again and compares the tool calls
    scripts/harness/repo-facts.py --questions scripts/harness/repo-facts.example.json --project DIR --out OUT --ab
                                            # factual questions, each in a fresh session, scored; --ab also runs the settings without the rules, agents, commands and skills; --no-skills leaves the skills out of every run; --variant medium also runs them at that thinking effort
    scripts/harness/ask-check.sh            # ask.py in every mode, resumed, with four lanes and one, plus the probe and the effort variant, against the stand-in model server; no GPU needed

## Running the personal shell

The agent is reached by email. Hermes Agent's gateway reads a mailbox that only the agent uses and answers mail from one allowed address with the local model. It runs as its own user, `hermes`, which may open connections only to the model server, the local resolver and the two mail ports; the commands the model runs go into a container with no network, under a rootless Docker daemon of that user.

One-time setup, in this order; each step prints the next:

    sudo scripts/shell/setup-hermes-user.sh                      # the hermes user, subordinate IDs, uidmap, linger
    sudo -u hermes -H scripts/shell/install-rootless-docker.sh   # Docker's static release for hermes, the sandbox image
    sudo -u hermes -H scripts/shell/install-hermes.sh            # the pinned Hermes release and its settings
    sudo scripts/shell/set-mail-secret.sh                        # the mailbox settings and app password, never printed
    sudo scripts/shell/enable-shell.sh                           # the egress rules, loaded at boot from now on, and the gateway unit

The pins are in `config/hermes/hermes.env`, the settings in `config/hermes/settings.txt` and the mail variables' names in `config/hermes/env.example`; the addresses themselves stay in a private file.

The gateway starts and stops with the model server. `sudo systemctl start agent-vllm` starts it once the model answers, and the allowed address gets an "Agent online" email; `sudo systemctl stop agent-vllm` stops it first and sends "Agent offline". While it is stopped nothing reads the mailbox, and the next start skips the mail already there, so send again what was written meanwhile.

    systemctl status agent-hermes          # the gateway
    sudo journalctl -u agent-hermes -f     # its log
    sudo scripts/shell/check-shell.sh      # what the gateway sees, what hermes can read, the sandbox from inside, the egress counters

## Memory, nightly review, web research and backups

Set up after the personal shell, in this order; each step prints what it did:

    sudo scripts/agent-memory/seed-memory.sh       # USER.md and MEMORY.md from the private seed folder, with git history
    sudo scripts/agent-memory/enable-memory.sh     # nightly memory commit and session export (02:30), filing into ~/agent-data (02:45)
    sudo scripts/dream/enable-dreaming.sh    # the nightly review at 03:00, its report by email
    sudo scripts/web/setup-web.sh            # SearXNG and the fetch service under the agentweb user
    sudo scripts/backup/setup-backup.sh      # encrypted backups on /var at 04:00, a weekly check

- **Memory.** `USER.md` (about the owner) and `MEMORY.md` (the agent's notes) are read when a session starts; a reply starting with `/new` starts one. Every save waits for `/memory approve all`; `/memory pending` lists them. Each night the notes are committed to git in hermes' memory folder, and the sessions are filed under `~/agent-data/trajectories/<work|personal>/`: an email session is personal only when its first message carries `[personal]`.
- **Nightly review.** A Hermes job reads the day's sessions and proposes at most six changes to the notes, each quoting the owner's own words, for approval; the report arrives by email. It runs while the model server runs, or once at its next start.
- **Web research.** The agent searches through a local SearXNG and reads only pages its searches returned in the last 24 hours, from public addresses only. The fetch service and SearXNG run as their own user; the agent's user reaches only the fetch service.
- **Backups.** restic snapshots of the memory, the agent's configuration and the personal agent data, encrypted, on `/var`. Work-scope data, secrets and any text file naming a work identifier stay out. `sudo scripts/backup/restore-test.sh` restores the newest snapshot and compares it.

    sudo scripts/agent-memory/check-memory.sh      # the notes as the next session sees them, their history, what was filed
    sudo scripts/review/phase1-check.sh 7    # evidence for the Phase 1 review, over the last 7 days

`docs/phase1-validation.md` lists every validation step with its expected result.

## License

MIT, see `LICENSE`.
