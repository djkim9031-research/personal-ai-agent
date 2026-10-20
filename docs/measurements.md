# Measurements

Numbers from this machine, each dated. Every later change to the server is judged against them: a change is kept only if its answers are at least as good and it is not slower.

## How the numbers are taken

- `scripts/measure/run.sh` runs the whole protocol with the service stopped. It starts its own servers through `config/vllm/serve.sh`, so the measured server is the served one, and samples GPU power four times a second throughout.
- Inputs: 8 prompts each of about 1,000, 8,000 and 65,000 tokens, built by `scripts/measure/make-prompts.py` from vLLM's own source files (public, Apache-2.0), each asking for a five-point summary, 512 output tokens at most, effort `low`. The 65,000-token prompts always hit the 512-token cap; the speed numbers hold, the summaries were cut short.
- Quality: `scripts/measure/quality.jsonl`, 24 short tasks a script can grade: 14 with one exact answer (arithmetic, a word reversal, a sort, a chemical symbol) and 10 small Python functions run against hidden tests in a bubblewrap sandbox without network or home access. Each item carries a fixed random seed, so a repeated run on the same server configuration rolls the same dice; a different configuration (MTP off) is a different numeric path and can roll differently.
- Time to first token counts the first thinking token, not the first answer token. vLLM's own `vllm bench serve` was not used because its chat client ignores the thinking stream.
- Alternation: the MTP-on and MTP-off servers ran in turns, on, off, on, off, on, off, with the same prompts, so a slow drift of the GPU would land on both.
- Medians are reported; the raw records with every answer stay on this machine under `~/agent-data/measure/results/`.

## 2026-10-19: baseline

Qwen3.8-27B-FP8 revision 017b9c7a on vLLM 0.31.0, settings of `config/vllm/serve.env` (131,072-token context, 4 requests at most, 16-bit KV cache, MTP with 3 guesses), RTX PRO 5000 72GB, driver 580.126.09. The run took 49 minutes. `verify.py` passed 13 of 13 on the first server, with `FLASHINFER_NO_DOWNLOAD=1` now set in `serve.sh`; the six server logs contain no download line.

## Speed by input length and requests at once

| Input tokens | Requests at once | MTP | Requests | Time to first token (median) | Tokens/s per request (median) | Tokens/s total | MTP guesses accepted |
|---|---|---|---|---|---|---|---|
| 1,065 | 1 | on | 24 | 0.24 s | 79 | 76 | 63% |
| 1,065 | 1 | off | 24 | 0.26 s | 39 | 38 | n/a |
| 1,065 | 4 | on | 24 | 0.61 s | 75 | 239 | 62% |
| 1,065 | 4 | off | 24 | 0.58 s | 35 | 122 | n/a |
| 8,233 | 1 | on | 24 | 1.59 s | 70 | 57 | 62% |
| 8,233 | 1 | off | 24 | 1.54 s | 38 | 34 | n/a |
| 8,233 | 4 | on | 24 | 2.99 s | 48 | 141 | 61% |
| 8,233 | 4 | off | 24 | 4.15 s | 30 | 94 | n/a |
| 65,577 | 1 | on | 8 | 17.19 s | 42 | 17 | 73% |
| 65,577 | 4 | on | 8 | 36.43 s | 10 | 24 | 71% |

## MTP on and off, in turns

| Server | MTP | 1K input, 1 request: tokens/s | 8K input, 1 request: tokens/s | 1K input, 4 requests: total tokens/s | Quality set, low effort: correct of 24 | Seconds per item (median) |
|---|---|---|---|---|---|---|
| mtp1 | on | 79 | 70 | 227 | 23 | 1.1 |
| nomtp1 | off | 39 | 38 | 118 | 22 | 4.2 |
| mtp2 | on | 81 | 69 | 256 | 23 | 1.1 |
| nomtp2 | off | 39 | 38 | 118 | 22 | 3.3 |
| mtp3 | on | 77 | 71 | 235 | 23 | 1.0 |
| nomtp3 | off | 39 | 38 | 129 | 22 | 3.2 |

## Effort levels, quality set, one request at a time

| Effort | Correct of 24 | Exact items | Code items | Thinking tokens per item (median) | Seconds per item (median) | Whole set | Hit the 8,192-token cap |
|---|---|---|---|---|---|---|---|
| low | 23 | 13/14 | 10/10 | 93 | 1.1 | 0.8 min | 0 |
| medium | 23 | 13/14 | 10/10 | 116 | 1.7 | 0.9 min | 0 |
| xhigh | 23 | 13/14 | 10/10 | 86 | 1.0 | 0.9 min | 0 |

## Cache reuse: the same 24 items behind a shared 6,030-token prefix

| Run | Prefix tokens reused | Time to first token (median) | Correct of 24 | Tokens/s per request (median) |
|---|---|---|---|---|
| cache-miss-1 | 0 of 146,466 | 1.17 s | 24 | 92 |
| cache-hit-1 | 110,400 of 146,466 | 0.30 s | 24 | 92 |
| cache-miss-2 | 0 of 146,466 | 1.16 s | 24 | 92 |
| cache-hit-2 | 110,400 of 146,466 | 0.30 s | 24 | 92 |

## GPU power by state

| State | Samples | Power, mean | Power, max | Clock, mean | Temperature, max | GPU memory used |
|---|---|---|---|---|---|---|
| idle-unloaded | 120 | 18 W | 18 W | 196 MHz | 35 C | 1.2 GiB |
| idle-loaded | 240 | 35 W | 273 W | 805 MHz | 49 C | 56.5 GiB |
| idle-unloaded-end | 120 | 34 W | 110 W | 260 MHz | 82 C | 1.1 GiB |
| in64k-c1-mtp1 | 938 | 284 W | 305 W | 2157 MHz | 89 C | 59.5 GiB |
| in1k-c1-mtp1 | 159 | 276 W | 295 W | 2528 MHz | 68 C | 56.7 GiB |
| in1k-c4-mtp1 | 53 | 283 W | 303 W | 2469 MHz | 74 C | 57.6 GiB |
| quality-xhigh-mtp1 | 205 | 299 W | 301 W | 2403 MHz | 88 C | 60.2 GiB |

## Server starts

| Server | Seconds to ready |
|---|---|
| mtp1 | 50 |
| nomtp1 | 90 |
| mtp2 | 50 |
| nomtp2 | 51 |
| mtp3 | 50 |
| nomtp3 | 50 |

verify.py on the first server: 13 passed, 0 failed

## What the baseline says

- **MTP is kept.** With the 3-token guesser on, one request writes 79 tokens/s at a 1,000-token input and 70 at 8,000; off, 39 and 38. Four requests at once total 239 and 141 tokens/s with it, 122 and 94 without. That held in all three on/off pairs (1.8x to 2.2x). Quality: 23 of 24 in every MTP-on run, 22 of 24 in every MTP-off run; the extra miss (Fibonacci, answered 34 for 55) is one item on a different numeric path, not a loss from MTP. Time to first token was the same with and without.
- **Cache reuse is safe and worth 0.9 s per request at a 6,000-token prefix.** The same 24 items behind a shared 6,030-token prefix gave byte-identical answers in all four passes, reused or not, all 24 correct; the first token came after 0.30 s with the prefix reused instead of 1.17 s. 4,800 of the 6,030 prefix tokens were reused per request (whole 800-token blocks only).
- **Long inputs cost time to first token, and four long requests at once share the writing speed.** Reading 8,000 tokens takes 1.6 s, 65,000 takes 17 s (about 3,800 tokens/s). One request on a 65,000-token input then writes 42 tokens/s; four at once write 10 each, 24 in total, with the first token after 36 s.
- **The effort setting did not separate on this set.** All three levels scored 23 of 24 and thought about 90-120 tokens per item: these tasks are too short to make the model think longer. On an open question on 2026-10-15 (a 250-word explanation), `xhigh` thought 3,211 tokens before a 309-token answer. The `xhigh` default stays; effort per agent role is decided later on real tasks.
- **Power.** 18 W with the server stopped, 35 W with the model loaded and idle, 276-299 W while reading or writing (the card's 300 W limit), up to 89 C on the 65,000-token reads.
- **Starts.** 50 s to ready with warm caches, 90 s the first time a new configuration compiles, 251 s on the service's first ever start (2026-10-19 morning).

## 2026-10-19: coding harness

opencode 1.19.3 against the same server, through `scripts/harness/record-proxy.py`, which records every request and answer. `run-session.py` builds a small Python project with one failing test and sends twenty requests in one session: reading, docstrings, running tests, a bug fix, finding callers, a rename across three files, new functions with tests, a `--json` flag, an error exit, a subprocess test, a cleanup, a summary. `score.py` counts the model's tool requests, malformed ones (text that looks like a tool request but did not parse, or arguments that are not JSON), repeats of the same request within a step, and tool results that start with the word error. The runner checks the project's end state ten ways. With `--stream off` the proxy sends each request to the server without streaming and hands the complete answer back as a short stream, so the server's non-streaming tool parser is the one tested. `replay.py` sends a recording's requests again without streaming and compares the tool requests; it is the gate for later server changes.

| Measure | Result |
|---|---|
| Fixed part of every session | 10,002-character system prompt and 9 tool definitions; 7,256 tokens with the first question |
| First answer of a session, two file reads | first token after 1.3 s, done in 2.9 s |
| Twenty steps, streaming | 60 requests, 52 tool requests, 0 malformed, 0 repeated, 0 tool errors, 0 refused permissions, 10 of 10 end-state checks, 4.1 min |
| Twenty steps, non-streaming parser | 61 requests, 55 tool requests, 0 malformed, 0 repeated, 0 tool errors, 0 refused permissions, 10 of 10 end-state checks, 4.1 min |
| Replay of the streamed recording, 60 requests | 60 of 60 answered and parsed; 45 asked for the same tools as recorded, 59 the same finish reason; 3.6 min |
| Tokens over the streamed session | 997,622 prompt tokens in 60 requests, largest request 25,850; 13,371 generated, of which 6,806 thinking |
| First token, streamed session | median 0.36 s, at most 1.33 s |
| One task in a network namespace with no route out | done in 4 s, 1 tool call, no connection failure in the debug log |

What it says:

- **Tool requests parse.** 107 tool requests over the two runs and 60 replayed answers, none malformed, with both of the server's parsers, and the twenty steps left the project in the asked-for state both times. The 15 replayed answers that chose different tools (an edit split in three, a read before an edit) are sampling, not parsing: the server does not draw the same random numbers for the same request twice unless the request carries its own seed. A later server change is judged on the parsed count, which must stay at 60 of 60, and on the same-tools count staying near 45.
- **The fixed part is small.** 7,256 tokens of the 131,072-token window, read once per session start and then served from the cache: the median first token over the session was 0.36 s.
- **The session grows.** By step 20 one request carried 25,850 tokens; opencode starts a summarization turn when a request would reach the window minus the answer budget (99,072 tokens with these settings), which these twenty steps did not approach.

### 2026-10-20: a broad question in a 147,709-file monorepo, plan agent

| Measure | Result |
|---|---|
| Question | "analyze this codebase and understand the logic", in the window, plan agent |
| Work | 4 explore subagents in parallel, 158 tool calls, all read-only; 2 permission prompts for paths outside the worktree |
| Time and tokens | 14 min; 68 requests; 2,630,000 prompt tokens, largest request 79,755; 29,600 generated, of which 22,700 thinking |
| Answer checked claim by claim | 28 claims: 13 correct, 14 partly wrong, 1 wrong |
| Causes of the 15 flawed claims | 3 estimated counts, 6 paraphrased or inferred names and descriptions, 4 documented rules not checked against the code, 2 mechanisms described wrongly |

The error rate of factual answers is measured from here on with `scripts/harness/repo-facts.py`, with and without the working rules; the first baseline is still to be run.
