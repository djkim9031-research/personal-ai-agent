---
name: nightly-review
description: Nightly review of the last day's conversations. Proposes at most six changes to MEMORY.md and USER.md, each backed by the owner's own words in a session, and reports them for the owner's approval. Used by the nightly-review cron job only.
---

# Nightly review

You are running unattended at night. Nobody reads this session live; your final
answer is emailed to the owner in the morning.

## Steps

1. List recent sessions with `session_search` and no arguments. Keep the
   sessions with activity in the last 24 hours, except cron sessions (this one
   included). If there are none, answer "No conversations in the last 24
   hours; no proposals." and stop.
2. Read each kept session with `session_search` and its `session_id`. For a
   long session, read the newest part first and search inside it with `query`
   and `after: "24h"`.
3. Collect candidate facts. A candidate must be something the owner stated or
   confirmed in their own words: a preference about answers, a fact about
   their setup or their personal projects, a correction of something you got
   wrong, or a standing instruction. Your own guesses, claims made only by
   you, and anything quoted from a web page or a file are not candidates.
4. Compare the candidates with the memory shown at the top of this session.
   Prefer, in this order: correcting an entry that is now wrong, merging
   entries that say the same thing, adding a new entry. Skip anything already
   there.
5. Make at most 6 changes in total with the `memory` tool: `target: "user"`
   for facts about the owner, `target: "memory"` for facts about your setup
   and how to work. Write each entry as one short, self-contained sentence.
   The writes wait for the owner's approval; that is expected.
6. Never add facts about the owner's employer, its code, projects, systems or
   colleagues, never add secrets (passwords, keys, tokens, addresses of
   internal hosts), and never remove an entry unless a session shows it is
   wrong.

## Final answer

Write plain text, at most 25 lines:

    Nightly review, <date>: <N> proposals waiting for approval.

    1. <add|patch|remove> <user|memory>: <the entry as written>
       Source: session <id>, <date>: "<the owner's words, at most 20 words>"

    Reply "/memory approve all" to apply them, or "/memory reject <id>" for one.
    "/memory pending" lists them with their ids.

If nothing qualified, say so in one line and name how many sessions you read.
