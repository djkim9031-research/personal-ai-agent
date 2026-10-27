#!/usr/bin/python3 -I
"""Round-trip times of the agent's email answers, from Hermes' session store.

For every message of the owner in an email session, the time until the last
answer of the agent before the owner's next message. That is the time from the
gateway reading the mail to the answer being ready; the phone-to-phone time
adds Gmail's delivery both ways and up to one 15-second mailbox poll.
Reads the store read-only. Run with sudo (the store belongs to hermes).

  round-trips.py [--days N] [--db PATH]
"""
import argparse
import sqlite3
import statistics
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--db", default="/home/hermes/.hermes/state.db")
    args = ap.parse_args()
    since = time.time() - args.days * 86400
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT m.session_id, m.role, m.timestamp FROM messages m JOIN sessions s ON s.id = m.session_id "
        "WHERE s.source = 'email' AND m.timestamp >= ? AND m.role IN ('user', 'assistant') "
        "AND COALESCE(m.active, 1) = 1 ORDER BY m.session_id, m.timestamp, m.id", (since,)).fetchall()
    times, asked, last_answer, session = [], None, None, None
    for sid, role, ts in rows + [(None, "user", 0)]:
        if sid != session or role == "user":
            if asked is not None and last_answer is not None:
                times.append(last_answer - asked)
            asked, last_answer, session = (ts if role == "user" else None), None, sid
        elif role == "assistant" and asked is not None:
            last_answer = ts
    if not times:
        print(f"no answered email in the last {args.days:g} days")
        return
    times.sort()
    p90 = times[min(len(times) - 1, int(0.9 * len(times)))]
    print(f"email answers in the last {args.days:g} days: {len(times)}; median {statistics.median(times):.0f} s, "
          f"90th percentile {p90:.0f} s, longest {times[-1]:.0f} s (gateway time; add delivery and up to 15 s of polling)")


if __name__ == "__main__":
    main()
