# personal-ai-agent

A personal AI agent that runs on one workstation: an open-weight model served locally, a harness for coding and research, memory consolidated every night, and a small budget for larger open models in the cloud when a task needs them.

It is built one increment at a time. `DEVLOG.md` records what each increment built and how it was checked.

## What is here

- `.githooks/`: checks that run before every commit and push
- `scripts/hygiene/`: setup, self-test and audit for those checks
- `config/vllm/`: the model server's pinned settings, package lock and start script
- `config/systemd/`: the service definition template
- `scripts/serve/`: install, download, service setup, check and ask scripts for the model server
- `config/opencode/`: the coding harness's pinned release and its settings
- `scripts/harness/`: install and start scripts for the harness, the recording proxy, the twenty-step test, its scorer and the replay
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

Checks:

    scripts/harness/egress-check.sh DIR     # one task inside a network namespace that can only reach the model server
    scripts/harness/run-session.py --out OUT [--stream off]   # the twenty-step test on a scratch project, recorded
    scripts/harness/replay.py OUT/recording # sends the recorded requests again and compares the tool calls

## License

MIT, see `LICENSE`.
