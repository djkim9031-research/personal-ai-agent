# Phase 1 validation: increments 8 to 12

Every command runs from the repository root. The scripts print what they did; the lines under "Expect" are what a pass looks like. The email steps need the model server running (`sudo systemctl start agent-vllm`); the gateway, the search engine and the fetch service start with it.

## Install, in this order

| Step | Command | What it does |
|---|---|---|
| 1 | `sudo scripts/agent-memory/seed-memory.sh` | Increment 8: writes `USER.md` and `MEMORY.md` from `~/agent-data/hermes/memory-seed/`, starts their git history. Expect `USER.md: 6 entries, 448 of 1375 characters`, `MEMORY.md: 5 entries, 601 of 2200 characters`, `memory repository: <id> seed`. |
| 2 | `sudo scripts/agent-memory/enable-memory.sh` | Increment 8: the nightly jobs (02:30 as `hermes`, 02:45 as you), run once now. Expect `nightly <date>: N sessions exported`, `filed: hermes sessions {...}, opencode sessions {...}`, two timers. |
| 3 | `sudo scripts/dream/enable-dreaming.sh` | Increment 9: email home channel, the `nightly-review` skill and job at 03:00. Expect `job nightly-review` in the job list; a running gateway restarts (offline and online notices). |
| 4 | `sudo scripts/web/setup-web.sh` | Increment 10: the `agentweb` user, SearXNG, the fetch service, the plugin and the firewall rules; downloads Python 3.12 and 32 packages, a few minutes. With the gateway running expect `test search: N results` and `test read: <url> -> N characters`. |
| 5 | `sudo scripts/backup/setup-backup.sh` | Increment 11: restic, the encrypted repository on `/var`, timers, the first backup. Expect `backup: <id> ..., N files left out for naming work identifiers` and one snapshot. Then keep a copy of the password outside this machine: `sudo cat /root/.config/agent-backup/restic-password`. |

## Increment 8: memory tiers

Day 1, by email:

1. Reply to any agent email with `/new` as the first line.
2. New email, subject `[personal] memory test`, text `What do you know about me, and about your own sandbox?` Expect an answer drawn from the seed: literal explanations, commands inline, no network in the sandbox, Python only.
3. Reply: `Please save three facts to memory: my favourite editor is Vim; I drink my coffee black; my home GPU board is a Jetson AGX Thor. Unrelated: the plant on my desk is a cactus, no need to save that.` Expect the answer to say the saves wait for approval.
4. Reply `/memory pending`, then `/memory approve all`.
5. `sudo scripts/agent-memory/check-memory.sh`. Expect the three facts as entries in the notes, not only in the reply.

Day 2:

6. Reply `/new`; new email, subject `[personal] recall test`, text `What are my favourite editor, my coffee, my home GPU board, and the plant on my desk?` Expect the first three from memory and the cactus found in a past session.
7. `sudo scripts/agent-memory/check-memory.sh`. Expect the day-1 session under `personal hermes` (its tag) and the earlier untagged one under `work hermes`; `~/agent-data/memory/hermes/diffs/<date>.diff` holds the three facts.
8. Undo, then redo, one night of memory:
   `(cd / && sudo -u hermes git -C /home/hermes/.hermes/memories log --oneline -3)`
   `(cd / && sudo -u hermes git -C /home/hermes/.hermes/memories revert --no-edit HEAD)` then `sudo scripts/agent-memory/check-memory.sh`: the facts are gone;
   the same `revert` command again: they are back.
9. `(cd / && sudo -u hermes test -x "$HOME/agent-data" && echo open || echo closed)`. Expect `closed`.
10. `systemctl show agent-memory-nightly.service agent-trajectories.service -p PrivateNetwork`. Expect `PrivateNetwork=yes` twice.

## Increment 8b: notes for the coding agent

1. `sudo scripts/opencode-memory/enable-notes.sh`, then `sudo scripts/backup/setup-backup.sh` again (it installs the backup job that now includes the personal notes).
2. In a project, start `opencode` and type `/remember <a fact about the project>`. Expect a request to run `agent-notes add "…"`; answer yes. `agent-notes show` in that folder lists it.
3. Start a new session and ask about that fact without naming the notes. Expect the answer to use it.
4. The first review starts by itself (at step 1 if the model server is up, otherwise at its next start). `journalctl -u agent-opencode-dream.service -n 5 --no-pager -o cat` shows it. Expect `nightly review: N sessions, M proposals waiting`. Then `agent-notes review --all-projects`: every proposal quotes your words or a command result from your sessions.
5. Ask `What did we decide about <a topic of an earlier session>?` Expect the agent to run `agent-notes recall` and name the session it found.

## Increment 9: nightly review

1. `(cd / && sudo -u hermes env HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes /home/hermes/.local/bin/hermes cron list)`. Expect `nightly-review`, schedule `0 3 * * *`, delivery `email`.
2. Optional, now: the same command with `cron run nightly-review` instead of `cron list`. Expect an email `Nightly review, <date>: N proposals waiting for approval.` within minutes.
3. For seven mornings: one review email for every night the model server ran (or at its next start). Every proposal names a session and quotes your own words; none is about the employer or holds a secret. Approve with `/memory approve all` or reject with `/memory reject <id>`; the next night's commit shows the approved ones (`sudo scripts/agent-memory/check-memory.sh`, history).
4. One `revert` of a nightly commit, as in step 8 of increment 8.

## Increment 10: web research

1. `systemctl status agent-searxng agent-web --no-pager --lines=0`. Expect both `active (running)` while the gateway runs.
2. Ten questions from the phone that need the web, each asking for its sources, for example `What is the latest restic release and its date? Give the page you read.` Open every cited page and check that it says what the answer claims.
3. `journalctl -u agent-web --since today --no-pager | tail -20`. Expect `search q=...` and `fetch ok ...` lines; a `fetch refused` line names its reason.
4. Ask for a page that was never searched: `Read https://example.com/ directly, without searching.` Expect the answer to report the refusal (`not a search result of the last 24 hours`).
5. Over one week: `journalctl -u agent-searxng --since -7d --no-pager | grep -ciE 'captcha|suspend|too many requests'`. A rising count means an engine is blocking this office's address; drop it from `config/web/searxng-settings.yml` and run step 4 of the install again.
6. `sudo nft list table inet agent_web_egress | grep -A1 'counter refused'`. Expect few or no refused packets; `sudo scripts/review/phase1-check.sh` lists them by destination.

## Increment 11: backups

1. `sudo scripts/backup/restore-test.sh`. Expect `restore test: N files identical, M changed since the snapshot, 0 missing or different`.
2. A planted work name: `echo '<a name from your work codebase>' > ~/agent-data/memory/hermes/planted.txt`, then `sudo systemctl start agent-backup.service` and `journalctl -u agent-backup.service -n 2 --no-pager -o cat`. Expect `1 files left out for naming work identifiers` (one more than before). Then `rm ~/agent-data/memory/hermes/planted.txt`.
3. `sudo systemctl start agent-backup-check.service` and the same `journalctl` for `agent-backup-check.service`. Expect `check: snapshots thinned, 10% of the data read back without errors`.
4. The repository password is stored outside this machine.

## Increment 12: Phase 1 review

1. Five tasks in the work codebase, by hand: the task sheet, the procedure and the build and test commands are in the private file `~/agent-data/work/phase1-review/tasks.md`. The opencode agents plan and edit; builds and tests are run by you. Pass: at least 3 of 5 tasks pass their tests.
2. A real GPU campaign: `sudo systemctl stop agent-vllm`, the campaign, `sudo systemctl start agent-vllm`. Expect no out-of-memory kill (`journalctl -k --since '<campaign start>' --no-pager | grep -ciE 'out of memory|oom-kill'` prints 0), the gateway active again and an "Agent online" email.
3. `sudo scripts/review/phase1-check.sh 7`. Expect: every service active while the model server runs; no `*_API_KEY` or token variable in hermes' `.env`; the model address 127.0.0.1:8000 in `config.yaml`; refused connections only to update and vendor hosts Hermes tries at start; the email round-trip median under 60 s (or a decision recorded in the DEVLOG); a recent snapshot; `audit passed`; one identical file between the repository and `~/agent-data`, the public decision record `docs/decisions/001-model-selection.md`, which is also kept in the private documents folder.
4. Decide which slice to deepen first.

## Open from increment 7, not required for this review

`xhigh` in the request body; a stop while the agent is answering; a model-server crash restart; a reboot.
