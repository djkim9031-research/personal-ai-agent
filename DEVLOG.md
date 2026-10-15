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
