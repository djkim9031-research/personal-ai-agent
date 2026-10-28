# personal-ai-agent

A personal AI agent that runs on one Linux workstation with its own GPU. One open-weight model, Qwen3.8-27B, runs locally, and two programs use it:

- **opencode**, for coding in a terminal, in a project folder you start it in.
- **Hermes Agent**, an assistant you reach by email from your phone. It answers with the same local model, runs commands in a sandbox with no network, keeps notes about you that you approve, reviews each day's conversations at night, and can search the web.

No cloud model is used. Your files, notes and conversations stay on the machine.

The project is built one increment at a time; `DEVLOG.md` records what each increment built and how it was checked.

- [Requirements](#requirements)
- [Part 1: the model server](#part-1-the-model-server)
- [Part 2: coding with opencode](#part-2-coding-with-opencode)
- [Part 3: the email assistant (Hermes and Gmail)](#part-3-the-email-assistant-hermes-and-gmail)
- [Part 4: memory, nightly review, web search and backups](#part-4-memory-nightly-review-web-search-and-backups)
- [Day to day](#day-to-day)
- [What is in this repository](#what-is-in-this-repository)
- [Committing to this repository](#committing-to-this-repository)

## Requirements

- Linux with systemd and sudo. Tested on Ubuntu 22.04.
- An NVIDIA GPU. Tested on one 72 GB card with driver 580: the server takes 80% of it (about 58 GB) for the model and a 131,072-token context. On a smaller card, lower `GPU_MEMORY_UTILIZATION` and `MAX_MODEL_LEN` in `config/vllm/serve.env`.
- About 40 GB of disk for the model and the runtime (29 GB and 8 GB here).
- `uv` (installs Python and the packages), `git`, `curl` and `acl` (`setfacl`). Part 3 also needs `nftables` (the `nft` command; its service stays disabled, see Part 3).
- For Part 3: outgoing connections to `imap.gmail.com` port 993 and `smtp.gmail.com` port 465.

Run every command from the repository's folder, for example `~/personal-ai-agent`.

**The private data folder.** Everything that is not code stays in `~/agent-data`: the model, the runtime, your secrets, the agent's notes and conversations. Nothing in it is ever committed. Create the folder for secrets once:

    mkdir -p ~/agent-data/secrets && chmod 700 ~/agent-data/secrets

## Part 1: the model server

The model runs in vLLM as the service `agent-vllm`, under its own user `agent-serve`, listening only on `127.0.0.1:8000`. It is not started at boot; you start it when you want the agent and stop it to free the GPU.

**Install, once** (mostly downloads, 20 to 40 minutes):

1. `scripts/serve/install-runtime.sh`: builds Python 3.12 and vLLM 0.31.0 under `~/agent-data/runtime` from the pinned package list.
2. `scripts/serve/download-model.sh`: downloads `Qwen/Qwen3.8-27B-FP8` at the pinned revision into `~/agent-data/hf` and checks every weight file.
3. `sudo scripts/serve/setup-service.sh`: creates the `agent-serve` user, gives it read-only access to the runtime and the model, and installs the `agent-vllm` service.

**Use:**

| Command | What it does |
|---|---|
| `sudo systemctl start agent-vllm` | Starts the server; returns once the model answers, about 3 minutes. |
| `scripts/serve/verify.py` | Checks the running server against the pinned settings. Expect 13 checks passed. |
| `scripts/serve/ask.py "What is 17 times 23?"` | One question; prints the model's thinking dimmed, then the answer and its speed. Add `--effort low` for a quick answer or `--image FILE` to ask about a picture. |
| `sudo systemctl stop agent-vllm` | Stops the server and frees the GPU. Stop it before other GPU jobs. |
| `journalctl -u agent-vllm -f` | The server's log. |

## Part 2: coding with opencode

opencode 1.19.3 reads, searches and edits the files of the folder you start it in and runs commands there, with the local model. It never commits; you review the changes and commit yourself.

**Install, once:**

    scripts/harness/install-opencode.sh

It downloads the pinned release, checks its hash, links `~/.local/bin/opencode`, and links the tool of `/custom-graph` (below) into `~/.config/opencode/tools/`. If `opencode` is not found afterwards, add `~/.local/bin` to your `PATH`. Run it again after an update of this repository; it downloads nothing when the release is already there.

**Use** (the model server must be running):

    cd ~/code/my-project
    git worktree add ../my-project-task -b my-task   # optional: a separate copy of the project for one task
    cd ../my-project-task
    opencode

- Type a request in the window, for example `Find where the settings file is read and list the functions it passes through.`
- The **plan** agent only reads; the **build** agent may edit files and run commands. The Tab key switches between them. Reading or editing a file outside the project folder asks for your permission first.
- `/verify` checks the factual claims of the last answer against the files. `/ask your question` answers in steps: narrow questions, one helper session each, every answer checked.
- `/custom-graph your question` runs the question through the step-by-step runner below (`ask.py`, not the model following steps by itself as in `/ask`) and shows its checked answer, the claims table and the report path in the window. It takes 6 to 30 minutes; "what does this repo do?" on this repository took 20. While it runs, the right-hand sidebar shows each step with its state (✓ done, ● running, ○ waiting, ✗ failed). The sidebar opens by itself when the terminal is wider than 120 columns; otherwise press ctrl+x, then b. `tail -f ~/agent-data/ask/latest/progress.log` shows the same in a terminal, and Esc twice stops the run. The runner sees only the question and the files, not the conversation, so write the question so it stands on its own. Your next message goes to the plan or build agent as usual and can ask about the answer. Asked a question, `/custom-graph` only reads.
- After an answer that describes a change, `/custom-graph let's build` (or `let's edit`, `let's create`, `go ahead`, and the like) makes it. The runner turns the latest `/custom-graph` answer of the session into a list of files, one build command, one test command and claims the result must meet. One session writes exactly those files and runs no commands; any other file is refused. The runner then runs the build and test commands in the project, as you, and sends a failure back to that session with the command's output, at most twice. A separate checker confirms or refutes each claim against the finished files.

  The reply says what was done and lists the files written, the build and test results, the plan it carried out and the claims table. Nothing is committed: look at the change with `git status` and `git diff`. The project must be a git repository. You can add instructions after the words, for example `/custom-graph let's build it, but put the files in tools/`, or describe a change in full with no earlier answer: `/custom-graph let's build a script hello.sh that prints hello`. A question that asks for a change ends with the line to send. A CUDA and C++ 1000x1000 matrix product, timed and compared, took about 7 minutes to plan, write, build, test and check.
- For one task without the window: `opencode run "your request"`.

What the launcher sets for every session: the model is the local server only; sharing and the update check are off; web fetch and web search are denied; skills of other tools in your home folder are ignored; every session reads the working rules in `config/opencode/AGENTS.md` (numbers only from commands, sources named, guesses marked as guesses).

**Your own graphs, skills, tools and commands:** see [below](#your-own-graphs-skills-tools-and-commands), with where each one goes for all projects or for one.

**Checked answers to questions about a codebase**, without the window:

    scripts/harness/ask.py --project ~/code/my-project --scope personal "How does a request get from the HTTP handler to the database?"

It splits the question into small ones, answers each in a fresh session, has a separate checker confirm or refute every answer against the files, and writes the report under `~/agent-data/ask/`. Modes, options, the checks and the measurements are described in `docs/coding-harness.md`.

### Notes and nightly review for opencode

opencode keeps notes per project, like the email assistant keeps notes about you. Install once, after Part 1:

    sudo scripts/opencode-memory/enable-notes.sh

From then on every opencode session reads two short files:

- **The project's notes**, `~/agent-data/opencode/notes/<work|personal>/<project>.md`, at most 3,000 characters. Worktrees of one repository share one file. The scope comes from `~/agent-data/hermes/scope.json` (Part 4); a project in neither list is work.
- **Your profile** from the email assistant (`USER.md`), when Part 4's memory is set up. opencode only reads it.

| In the opencode window | What happens |
|---|---|
| `/remember the build only works with --config=noremote` | The agent writes it as one sentence and asks to run `agent-notes add "…"`. Nothing is saved until you answer yes. |
| `Run the test that failed last time` | The agent searches this project's earlier sessions with `agent-notes recall "…"`. |

| In a terminal | What it does |
|---|---|
| `agent-notes show` | This project's notes. |
| `agent-notes review` | The nightly review's proposals for this project, one at a time: `a` accept, `r` reject, `s` skip. Add `--all-projects` for every project. |
| `agent-notes replace "OLD" "NEW"`, `agent-notes remove "OLD"`, `agent-notes undo` | Correct the notes yourself. Every change is a git commit in the notes folder. |

**Nightly review.** At 03:30, and each time the model server starts, the local model reads each project's sessions since the last review. It proposes at most 6 note changes per project, each quoting your words or a command result from those sessions; a proposal whose quote is not found there is dropped. When opencode starts in a project with proposals waiting, it prints `agent-notes: N note proposals wait for this project`. The first review reads all your earlier sessions; `enable-notes.sh` starts it when the model server is already up, otherwise it runs at the next start. Then `agent-notes review --all-projects`.

Work notes stay in `notes/work/` and are never backed up; personal notes are backed up (Part 4). `ask.py` and the other measurement scripts run without notes, so their results stay comparable.

### Your own graphs, skills, tools and commands

The launcher gives every project you open the settings of this repository: the commands above, the agents, the working rules, the graph panel. You copy nothing into a project for those. Your own additions go in the places below. opencode reads them when it starts, so close and reopen the window after a change.

| What | For every project | For one project | Template |
|---|---|---|---|
| A graph: the steps of a checked answer, for `ask.py` and the window | `~/agent-data/flows/<name>.py` | none: a graph is Python code, so it runs only from your own folder | `config/flows/facts.py.example` |
| A command, `/name` in the window | `~/.config/opencode/commands/<name>.md` | `<project>/.opencode/commands/<name>.md` | the `command` entries of `config/opencode/opencode.json` |
| A skill: instructions the model reads when a task matches its description | `~/agent-data/work/skills/<name>/SKILL.md` | `<project>/.opencode/skills/<name>/SKILL.md` | `skills/repo-facts/`, a fact sheet about one codebase |
| A tool: a function the model can call | `~/.config/opencode/tools/<name>.ts` | `<project>/.opencode/tools/<name>.ts` | `config/opencode/tools/custom_graph.ts` |
| An agent | `~/.config/opencode/agents/<name>.md` | `<project>/.opencode/agents/<name>.md` | the `agent` entries of `config/opencode/opencode.json` |
| Other settings | `config/opencode/opencode.json` in this repository | `<project>/opencode.json` or `<project>/.opencode/opencode.json` | |

- **Before the first file for one project**, run `scripts/harness/setup-project.sh ~/code/my-project` once. It makes the four `.opencode/` folders. It links opencode's plugin package into `.opencode/`: without it, every start in that project, and every step of a graph run there, waits about 70 seconds for a package install that cannot reach the network. And it adds `.opencode/` to the project's local ignore list (`.git/info/exclude`, never committed, shared by the project's worktrees), so none of it lands in that project's git.
- **Who sees what.** Files for one project are used by the window in that project and by the graph's runs there. Files in `~/.config/opencode/` are used by the window in every project, not by the graph's runs, which use private opencode homes. Skills in `~/agent-data/work/skills/` are used by both, except by the checker.
- **Which wins.** A project's own settings are read after this repository's and win where they differ.
- **Private stays private.** This repository is public: names, prompts or paths of a private codebase go only into the home-folder places above.

**A graph of your own**, from the template:

    mkdir -p ~/agent-data/flows
    cp config/flows/facts.py.example ~/agent-data/flows/facts.py
    scripts/harness/ask.py --project ~/code/my-project --mode facts --dry-run "What does this project do?"
    scripts/harness/ask.py --project ~/code/my-project --scope personal --mode facts "What does this project do?"

The template asks only fact questions, so it is faster than the default graph. Its comments explain each field of a step, so you can change the prompts and add or remove steps. Each entry of `FLOWS` in the file is one graph; its name becomes a `--mode` and must not be a built-in one (narrow, broad, self, verifyset, edit). `--dry-run` prints the steps without running anything. To run it from the window, create `~/.config/opencode/commands/facts-graph.md` with these lines:

    ---
    description: answer with my facts graph
    agent: graph
    ---
    Answer the question between the markers with the custom_graph tool, then reply with its output exactly as returned.
    <mode>facts</mode>
    <question>
    $ARGUMENTS
    </question>

Reopen the window and type `/facts-graph What does this project do?`. The `<mode>` line names the graph; the rest of the file is the same for every graph; the sidebar shows your graph's steps.

**A skill** is a folder with a `SKILL.md`. Every session lists each skill's `name` and `description`, and the model reads the whole file when a task matches the description:

    ---
    name: build-commands
    description: How to build and test this project. Read it before running a build or a test.
    ---
    Run the tests with: python3 -m pytest tests

**A tool** is a TypeScript file whose default export has a `description`, its arguments (each one a JSON Schema) and an `execute` function; the file name is the tool's name. For example, `~/.config/opencode/tools/line_count.ts`:

    import { readFileSync } from "node:fs"
    import path from "node:path"

    export default {
      description: "Counts the lines of one file of the project, as wc -l does.",
      args: { file: { type: "string", description: "the file's path relative to the project folder" } },
      async execute(args: { file: string }, ctx: any) {
        const text = readFileSync(path.join(ctx.directory, args.file), "utf8")
        return String((text.match(/\n/g) ?? []).length)
      },
    }

Every agent is offered a new tool unless its settings deny it. To give a tool to one agent only, deny it under `permission` and allow it in that agent's own `permission`, as `config/opencode/opencode.json` does for `custom_graph`.

**A command** is a Markdown file: `description` and, if you want, `agent` at the top, and below them the text sent to the model, where `$ARGUMENTS` becomes what you type after the command. Anything written as `` !`command` `` in that text, including what you type after the command, runs in the shell before the model sees it, so never type one into a question.

## Part 3: the email assistant (Hermes and Gmail)

You email the agent's own Gmail address from your personal address. Hermes Agent reads that mailbox every 15 seconds while the model server runs, answers with the local model, and replies in the same thread. It runs as its own user, `hermes`, which cannot read your files. The commands the model runs go into a Docker container with no network. A firewall rule lets the `hermes` user connect only to the model server, the local name resolver, the two mail ports (993 and 465), the local fetch service once web search is installed, and programs that `hermes` itself runs on this machine (the nightly review connects back to the gateway this way). Mail from any other address, or mail that Gmail did not verify as coming from your address, is ignored.

### Step 1: a Gmail account for the agent

1. Create a new Google account used only by the agent, for example `yourname.agent@gmail.com`. Do not use your main account: the agent reads everything that arrives in this inbox.
2. Turn on 2-Step Verification for that account (Google Account, Security).
3. Create an app password at https://myaccount.google.com/apppasswords. Google shows 16 letters; keep the page open for step 2. App passwords need 2-Step Verification and are not offered when the account signs in with security keys only.
4. Choose the one address the agent will answer: your own address. It must differ from the agent's address, because the agent ignores mail from itself.

IMAP needs no switching on in Gmail. Port 587 is not used.

### Step 2: two private files

The addresses (replace both):

    cat > ~/agent-data/secrets/hermes-mail.env <<'EOF'
    EMAIL_ADDRESS=yourname.agent@gmail.com
    EMAIL_ALLOWED_USERS=you@example.com
    EOF
    chmod 600 ~/agent-data/secrets/hermes-mail.env

The app password, typed without showing it on screen (spaces are fine):

    (umask 077; read -rs -p 'app password: ' pw; echo; printf '%s' "$pw" > ~/agent-data/secrets/agent-gmail-app-password)

### Step 3: install

Five commands, 10 to 20 minutes; each one prints the next.

1. `sudo scripts/shell/setup-hermes-user.sh`
   Creates the `hermes` user (no login, no extra groups), installs `uidmap` and `slirp4netns`, lets its services start at boot, and closes `~/agent-data` to other users.
   If it stops with `hermes cannot read …/scripts/shell`, your home folder is closed to other users (Ubuntu's default is mode 750). Let `hermes` pass through it for the install, then run step 1 again:
   `sudo setfacl -m u:hermes:--x ~`
   This lets `hermes` reach the repository by its full path. It still cannot list your home folder or enter `~/agent-data`.
2. `sudo -u hermes -H scripts/shell/install-rootless-docker.sh`
   A Docker daemon for the `hermes` user only (the system Docker is not touched), and the sandbox image (Python 3.12, pinned by digest). Expect `sandbox runs as uid 0 with ['lo']`: the container's only network is loopback.
3. `sudo -u hermes -H scripts/shell/install-hermes.sh`
   Hermes Agent v0.22.5 at a pinned commit (its installer is hash-checked) and its settings: the local model, the Docker sandbox with no network, telemetry and update checks off, approval needed before the agent saves a note or a skill. 5 to 15 minutes.
4. `sudo scripts/shell/set-mail-secret.sh`
   Writes the mailbox settings into the `hermes` user's private settings file. It prints no password. Then delete your copy:
   `rm ~/agent-data/secrets/agent-gmail-app-password`
5. `sudo scripts/shell/enable-shell.sh`
   Loads the firewall rule for the `hermes` user (and again at every boot) and installs the gateway service `agent-hermes`, which starts and stops with the model server.

If you ran the `setfacl` line in step 1, remove it now: `sudo setfacl -x u:hermes ~`. The services do not need it. Running steps 2 or 3 again later does.

Do not enable `nftables.service`: it would flush the whole firewall, including ufw's rules. The agent's rules are loaded by their own small services.

### Step 4: start and test

1. `sudo systemctl start agent-vllm`. The gateway starts once the model answers. Within a minute your address receives **Agent online**.
2. From your address, email the agent: `What is 17 times 23? Check it by running python3 -c 'print(17*23)' in your sandbox and tell me the output.` Expect a reply with 391 in the same thread, within 1 to 3 minutes.
3. `sudo systemctl stop agent-vllm` stops the gateway first, then the model. Your address receives **Agent offline**.

**Good to know:**

- The agent works only while the model server runs. Mail sent while it is stopped is never answered: on the next start it skips the mail already in the inbox, and the Agent online email says how many it skipped. Send those again.
- A slash command works only as the first line of a **reply**; in a new email the subject is put in front of your text. `/new` starts a fresh conversation. The memory commands are in Part 4.
- `systemctl status agent-hermes` shows `failed` after every stop. Hermes exits with status 1 when stopped; nothing is wrong.

**Checks and logs:**

    journalctl -u agent-hermes -f          # the gateway's log
    sudo scripts/shell/check-shell.sh      # what the gateway can see, what hermes can read, the sandbox from inside, the firewall counters

## Part 4: memory, nightly review, web search and backups

Each piece is optional, but install them in this order. Each command prints what it did.

### Memory

The agent keeps two short files of notes: `USER.md` (about you, at most 1,375 characters) and `MEMORY.md` (about its own setup, at most 2,200 characters). It reads them when a conversation starts. Every save waits for your approval. All its conversations stay searchable.

1. Write the first notes from the templates. Entries are separated by a line holding only `§`.

        mkdir -p ~/agent-data/hermes/memory-seed
        cp config/agent-memory/USER.md.example   ~/agent-data/hermes/memory-seed/USER.md
        cp config/agent-memory/MEMORY.md.example ~/agent-data/hermes/memory-seed/MEMORY.md
        cp config/agent-memory/scope.example.json ~/agent-data/hermes/scope.json
        # edit the three files, then:
        sudo scripts/agent-memory/seed-memory.sh

   `scope.json` lists your project folders as `personal_paths` and `work_paths`. It decides how opencode conversations are filed; a folder in neither list counts as work.
2. `sudo scripts/agent-memory/enable-memory.sh` installs two nightly jobs, which run whether or not the model server is up:
   - 02:30, as `hermes`: commits the day's note changes to git and exports the day's conversations.
   - 02:45, as you: files them under `~/agent-data/trajectories/work/` or `personal/`, together with your opencode window sessions, and keeps a copy of the notes in `~/agent-data/memory/hermes/`.

**Using it by email** (each command as the first line of a reply):

| You send | What happens |
|---|---|
| `/new` | A fresh conversation, which reads the notes as they are now. |
| `Please remember that I prefer metric units.` | The agent prepares a note and says it is waiting for approval. |
| `/memory pending` | Lists the notes waiting, with their ids. Read them first: the agent may word a fact differently from you, or add one you did not ask for. |
| `/memory approve all` or `/memory reject <id>` | Applies them, or drops one. |
| `What did I say about the plant on my desk?` | Searches past conversations, including facts never saved as notes. |

To keep a conversation out of `work`, put `[personal]` in the subject of its first email after `/new`. Untagged conversations are filed as work.

**Correcting a note yourself**, without the model and without approval:

    sudo scripts/agent-memory/edit-memory.sh replace user "metric units" "I prefer metric units, and dates as 2026-10-27."
    sudo scripts/agent-memory/edit-memory.sh add user "I drink my coffee black."
    sudo scripts/agent-memory/edit-memory.sh remove memory "an old note"

Each change is committed to the notes' git history. `sudo scripts/agent-memory/check-memory.sh` shows the notes as the next conversation will see them, their history, and what was filed.

### Nightly review

    sudo scripts/dream/enable-dreaming.sh

At 03:00 the agent rereads the last day's conversations. It proposes at most six note changes, each quoting your own words, and emails you the list; approve with `/memory approve all`. The review needs the model. If the server is off at 03:00, the review runs once, a minute after the next start.

### Web search

    sudo scripts/web/setup-web.sh

This adds a new user, `agentweb`, which runs SearXNG (a search engine on this machine that asks Brave, Bing, Wikipedia, arXiv, GitHub and Stack Overflow) and a small fetch service. The agent may read only pages that its own searches returned in the last 24 hours, and only on public addresses. The `agentweb` user may connect only to web ports on public addresses; the agent's own user still has no internet access. Both services start and stop with the agent.

For example, ask `What is the latest release of restic? Give the page you read.` and the reply names the version and the release page. Ask `Read https://example.com/ directly, without searching` and the agent reports that the fetch service refused it. Engines are chosen in `config/web/searxng-settings.yml`.

### Backups

1. List what must never go into a backup, one extended regular expression per line, for example your employer's project names. A file with only a comment line works if there is nothing to exclude.

        printf '# names kept out of backups\n' > ~/agent-data/hermes/work-patterns.txt

2. `sudo scripts/backup/setup-backup.sh`

Every night at 04:00, restic stores an encrypted copy of the agent's notes and settings, of your personal conversations and of opencode's personal project notes in `/var/backups/agent-restic`. It leaves out work conversations, secrets, and any text file matching the list. Every Sunday old copies are thinned and part of the data is read back.

The copies are encrypted with a password that the script created in `/root/.config/agent-backup/restic-password`. Keep a copy of it somewhere else, because without it the backups cannot be read:

    sudo cat /root/.config/agent-backup/restic-password
    sudo scripts/backup/restore-test.sh    # restores the newest copy into a temporary folder and compares it with the live files

`/var` is on the same machine, so these copies protect against deleted or damaged files, not against losing the machine.

## Day to day

| When | Command or event |
|---|---|
| Use the agent | `sudo systemctl start agent-vllm`; wait for Agent online. |
| Free the GPU | `sudo systemctl stop agent-vllm`; Agent offline follows. |
| 02:30, 02:45 | Notes committed, conversations exported and filed. Runs with or without the model server. |
| 03:00 | Nightly review, only while the model server runs; otherwise once at the next start. |
| 03:30 | The coding agent's nightly review, same rule: only with the model server, otherwise at its next start. |
| 04:00, Sunday 05:00 | Backup; weekly backup check. Runs with or without the model server. |
| Any time | `sudo scripts/review/phase1-check.sh 7` prints the evidence for the last 7 days: services, firewall refusals, email round trips, backups, and the repository audit. |

`docs/phase1-validation.md` lists every check of Parts 3 and 4 with its expected result.

## What is in this repository

- `config/vllm/`, `scripts/serve/`: the model server's pinned settings, package list, start script, and the install, check and ask scripts.
- `config/opencode/`, `config/flows/`, `scripts/harness/`, `skills/`: the coding harness: pinned release, settings, working rules and prompts, the tool of `/custom-graph` and the window's graph panel; the template for a graph of your own; the install, start and project-setup scripts; the step-by-step runner for questions and changes, and its tests; skill templates.
- `scripts/opencode-memory/`, `skills/recall/`: the coding agent's notes, their search over past sessions, and its nightly review.
- `config/hermes/`, `config/nftables/`, `scripts/shell/`: the email assistant: pins, settings, the mail settings template, the firewall rules, and the install, start and check scripts.
- `config/agent-memory/`, `scripts/agent-memory/`: note templates, the nightly memory jobs, the note editor and the check.
- `config/hermes/skills/nightly-review/`, `scripts/dream/`: the nightly review.
- `config/web/`, `scripts/web/`, `config/hermes/plugins/web/agentweb/`: web search and page reading.
- `config/backup/`, `scripts/backup/`: backups and the restore test.
- `config/systemd/`: every service and timer, as templates the scripts fill in.
- `scripts/review/`, `scripts/measure/`: the review evidence, email round trips, and the server measurements.
- `docs/`: decision records, measurements, the coding harness in detail, the validation steps.
- `.githooks/`, `scripts/hygiene/`: checks that run before every commit and push.

**Never here:** model weights, notes, conversations, documents, keys and passwords. They live in `~/agent-data`, and the commit checks refuse them.

## Committing to this repository

Only needed if you commit to this repository:

1. `scripts/hygiene/install-gitleaks.sh`
2. Create a deny-list file at the path in `.githooks/lib.sh`, in the format described there.
3. `scripts/hygiene/setup-repo.sh --name NAME --email EMAIL --gh-user LOGIN`
4. `scripts/hygiene/selftest.sh`
5. Push with `scripts/hygiene/push.sh`. The GitHub token goes only to pushes started this way, so a program that pushes by itself with the hooks turned off (for example a tool that copies `refs/notes/*` to every remote) cannot log in. The script then deletes any notes ref it finds on the remote and checks the remote with `audit.sh`.

## License

MIT, see `LICENSE`.
