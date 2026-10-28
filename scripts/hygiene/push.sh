#!/usr/bin/env bash
# Pushes main with this clone's token, deletes any notes ref found on the
# remote, then reads the remote back with audit.sh. The credential helper from
# setup-repo.sh hands the token only to pushes started with AGENT_REPO_PUSH=1,
# so a program that pushes on its own with hooks turned off cannot log in.
# Usage: scripts/hygiene/push.sh [remote], default origin
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
remote=${1:-origin}
export AGENT_REPO_PUSH=1
git push -q "$remote" main
for ref in $(git ls-remote "$remote" 'refs/notes/*' | awk '{print $2}'); do
    git push -q "$remote" ":$ref"
    echo "deleted $ref from $remote"
done
exec scripts/hygiene/audit.sh "$remote"
