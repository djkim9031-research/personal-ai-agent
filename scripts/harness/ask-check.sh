#!/bin/sh
# Runs ask.py in both modes against the stand-in model server on a scratch project, so the
# steps, the parsing and the report can be checked without the GPU. Usage: ask-check.sh [OUT]
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
echo "requests to the stand-in: $(wc -l < "$out/requests.jsonl"); reports: $out/narrow/report.md $out/broad/report.md"
