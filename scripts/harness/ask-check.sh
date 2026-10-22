#!/bin/sh
# Runs ask.py against the stand-in model server on a scratch project, so the steps, the parsing
# and the reports can be checked without the GPU: narrow mode, broad mode, and broad mode with
# --no-skills. Then checks the recorded requests: the checker never sees a skill, and the
# --no-skills run lists none of the local skills. Usage: ask-check.sh [OUT]
set -eu
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
out=${1:-$HOME/agent-data/ask/check}
rm -rf "$out"
mkdir -p "$out"
"$here/make-test-repo.sh" "$out/project" >/dev/null
python3 "$here/fake-model.py" --port 8099 --replies "$here/ask-fake-replies.json" --log "$out/requests.jsonl" &
fake=$!
trap 'kill $fake 2>/dev/null' EXIT
for i in 1 2 3 4 5 6 7 8 9 10; do
    python3 -c "import socket; socket.create_connection(('127.0.0.1', 8099), 1)" 2>/dev/null && break
    sleep 1
done
q="What does this project do and how is it tested?"
python3 "$here/ask.py" --project "$out/project" --server http://127.0.0.1:8099 --isolate --out "$out/narrow" "$q"
echo
python3 "$here/ask.py" --project "$out/project" --server http://127.0.0.1:8099 --isolate --mode broad --out "$out/broad" "$q"
echo
first_no_skills=$(wc -l < "$out/requests.jsonl")
python3 "$here/ask.py" --project "$out/project" --server http://127.0.0.1:8099 --isolate --no-skills --mode broad \
    --out "$out/broad-no-skills" "$q"
echo
echo "requests to the stand-in: $(wc -l < "$out/requests.jsonl"); reports under $out/{narrow,broad,broad-no-skills}/report.md"
python3 - "$out/requests.jsonl" "$first_no_skills" "${OPENCODE_CONFIG:-$here/../../config/opencode/opencode.json}" <<'PY'
import glob, json, os, sys
log, first_no_skills, config = sys.argv[1], int(sys.argv[2]), sys.argv[3]
local = set()  # the names of the skills the settings point at
for p in json.load(open(config)).get("skills", {}).get("paths", []):
    local.update(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(os.path.expanduser(p), "*", "SKILL.md")))
bad, checker, after = [], 0, 0
for i, line in enumerate(open(log)):
    body = json.loads(line)["body"]
    system = "\n".join(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
                       for m in body.get("messages", []) if m.get("role") == "system")
    tools = {t.get("function", {}).get("name") for t in body.get("tools", [])}
    listed = {n for n in local if f"<name>{n}</name>" in system}
    if "You are a verifier" in system:
        checker += 1
        if "skill" in tools or listed:
            bad.append(f"request {i}: the checker has the skill tool or sees {sorted(listed)}")
    if i >= first_no_skills:
        after += 1
        if listed:
            bad.append(f"request {i}: --no-skills still lists {sorted(listed)}")
print("\n".join(bad) if bad else
      f"skills: {len(local)} local, none visible to the checker in {checker} requests, none listed in the {after} requests of the --no-skills run")
sys.exit(1 if bad else 0)
PY
