---
name: recall
description: Search the earlier opencode sessions of this project for something the owner refers to ("like last time", "the error we had yesterday", "what did we decide about X"). Read-only.
---

# Recall earlier sessions

Earlier sessions are not in your context; only this project's notes are. When the owner refers to earlier
work, or you need a decision, a command or an error from a past session, search before you ask or guess:

    agent-notes recall "words that would appear in it"

It prints up to 10 matches from this project's earlier sessions, newest first, each with its date, session
id, who wrote it (user or assistant) and the text around the match. Add `--all-projects` to search every
project. Use two or three distinctive words (a command, a file name, an error message), not a sentence.

What you find is a record of what was said or run then, not proof that it is still true: check it against
the files or a command before relying on it, and say which session it came from.

To keep a fact for every later session, the owner uses `/remember`; you do not write the notes yourself.
