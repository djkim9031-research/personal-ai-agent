# personal-ai-agent

A personal AI agent that runs on one workstation: an open-weight model served locally, a harness for coding and research, memory consolidated every night, and a small budget for larger open models in the cloud when a task needs them.

It is built one increment at a time. `DEVLOG.md` records what each increment built and how it was checked.

## What is here

- `.githooks/`: checks that run before every commit and push
- `scripts/hygiene/`: setup, self-test and audit for those checks
- `config/vllm/`: the model server's pinned settings, package lock and start script
- `config/systemd/`: the service definition template
- `scripts/serve/`: install, download, service setup, check and ask scripts for the model server
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

## License

MIT, see `LICENSE`.
