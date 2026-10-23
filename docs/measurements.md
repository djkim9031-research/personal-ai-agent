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

The error rate of factual answers is measured from here on with `scripts/harness/repo-facts.py`, with and without the working rules.

### 2026-10-20: repo-facts test in the monorepo worktree, plan agent, with and without the working rules

33 private single-fact questions (counts, names, acronyms, mechanisms, each verified by command beforehand), every question in a fresh headless session, two rounds, the two settings in turns.

| Measure | Without the rules | With the rules |
|---|---|---|
| Runs | 66 | 66 |
| Correct as scored | 60 | 58 |
| Correct after the review of every answer marked wrong, 64 valid runs | 63 | 63 |
| Median per question | 23.4 s, 4.5 tool calls | 25.4 s, 4.5 tool calls |
| Total time | 54 min | 73 min |
| Requests and prompt tokens | 374; 5,142,000 | 405; 7,293,000 |
| Generated tokens, of which thinking | 141,600; 96,400 | 175,300; 122,000 |
| Runs over 120 s | 7 | 11 |
| Runs over 200,000 prompt tokens | 5, holding 1,716,000 tokens | 6, holding 3,581,000 tokens |
| Tool errors | 4, all the grep tool's output limit | 8: 4 of those, 4 denied writes to a scratch directory |
| Runs without an answer | 0 | 1: a read outside the worktree, auto-rejected headless |
| Verify subagent called | not available | 4 of 66 runs |

A first pass over the first 10 questions, one round, scored 8 and 8 of 10; after the same review 9 and 10.

The review of the 14 runs marked wrong: 12 marks were defects of the question set, not of the model. Two questions were ambiguous between a direct and a recursive directory count; the model gave both numbers in its reply and put the recursive one on the answer line (4 runs). One expected answer was narrower than the code, and the model's answer was right with evidence (4 runs). One question assumed a single table row where the table has nine; "unknown" twice was the right answer under the rules (3 runs). One answer key was too strict for a correct answer (1 run). One count in the answer key was wrong by two, and all four runs had the right number; it was inside the tolerance and so was never flagged. The 2 remaining failures: an answer line carrying a count where the list was asked for, with the list in the reply above it; and the headless run stopped by the permission prompt. The question set and the answer key were corrected.

Two defects of the test itself: both settings listed the private fact sheet as a skill, and the model loaded it in 16 of 132 runs, mostly on the product-description questions, so those answers were guided by the sheet; the runner now strips the skills in the baseline with the other pieces. And an answer key is a set of claims like any other: it was checked by the same method as the answers and lost on three questions.

What it says:

- **On single-fact questions the rules change nothing.** Both settings answer 63 of 64 once the key is right. The set does not reach the failure the rules were written for, which was a broad question answered from four merged subagent reports; that check is the window test, claim by claim.
- **The rules cost 42 percent more prompt tokens and 35 percent more time**, almost all of it in three questions (1,865,000 of the 2,151,000-token difference): with the rules the model kept corroborating a documented statement against the code after it had the answer, once by trying to write a scratch Bazel module to test a semantic, which the plan agent's write rule denied. The 2,400 characters the rules add to every request account for about 250,000 tokens of the difference.
- **"Unknown" is the rules working.** Where the question had no single answer the model said so twice instead of picking a row; the baseline picked a row once.

### 2026-10-21: the broad question through ask.py, narrow and broad modes, plan agent

The question of 2026-10-20, "analyze this codebase and understand the logic", in the same worktree, with the working rules and with the private fact sheet listed as a skill, as in the window. Narrow mode: the model writes the narrow questions, each runs in a fresh session, the checker verifies the pairs, the answer is composed from them. Broad mode: one session answers, lists its claims, the checker verifies them, the session rewrites the answer.

| Measure | Narrow mode | Broad mode | 2026-10-20, one session in the window |
|---|---|---|---|
| Runs | 15: decompose, 12 questions, verify, compose | 4: answer, claims, verify, correct | 1, with 4 explore subagents |
| Time | 4.2 min, of which verify 70 s | 10.7 min, of which verify 536 s | 14 min |
| Requests; prompt tokens | 57; 580,000 | 16; 316,000 | 68; 2,630,000 |
| Generated tokens, of which thinking | 13,900; 7,700 | 30,000; 22,700 | 29,600; 22,700 |
| Tool calls; errors; rejected | 56; 0; 0 | 40; 0; 0 | 158; 2 permission prompts, answered in the window |
| Verdicts of the checker | 12 pairs: 11 correct, 1 partly | 22 claims: 21 correct, 1 partly | 28 claims: 13 correct, 14 partly, 1 wrong |
| Fact sheet loaded by | the decompose step | the answer step and the checker | not available then |
| Independent recheck of the verdicts, 13 read-only agents | 11 correct, 1 partly, as the checker; plus 1 partly in the composed answer | 19 correct, 3 partly: two more than the checker | 13, 14, 1, as above |

What the steps show:

- **Both answers restate the fact sheet.** The decompose step listed the root directory, loaded the fact sheet, and wrote 12 questions that are lines of the sheet: the domain count, the dependency rule, two directory listings, the control-loop period, a file count, the Bazel module and version, the hook, a flag default. The answer step of the broad mode loaded both local skills and ran two git commands, four tool calls in 27 s, and wrote one paragraph whose 22 claims come from the sheet (19), from git (commit, branch, recent commits) and from the search skill's description of one app family's layout (1). The checker of the broad mode loaded the sheet before checking, then checked every claim by command anyway; the narrow checker did not load it. So the verdicts measure the sheet plus verification, not the model's own reading of the repository. The estimated counts, guessed descriptions and wrong mechanisms of the first answer are absent because nothing was explored.
- **Five defects in 35 judged statements, after an independent pass that tried to refute every one.** The checker's two: the narrow pair 3 put the count on the answer line where the names were asked, with the three names in the reply above it; the broad claim 19 came from the claims step, which compressed the answer's "the apps directory + lib apps" into "apps and libs under the apps directory", and the libraries live in two other directories. The harness fixed both in the final answers. Three the checker missed: the composed narrow answer calls six directories matched by a word in their name "6 kernel entries", and the sixth is a comparison tool, not a kernel; the broad answer calls one domain "the base domain", which is the fact sheet's own inference where the repository says default and reference domain; and it says the product's runtime logic runs as generated state machines, where only the top-level supervisor is generated by a modelling tool and the dispatch layer, the control loops and some 200 other apps are hand-written. The independent pass also overruled itself twice on recheck, on the layer order and on a paraphrase of the messaging layer's name, in the checker's favour. None of the five is a guess where the repository is silent: one is a format slip, two are compressions by a harness step that writes without the files in front of it, one is the sheet's wording, one widens a documented scope.
- **Neither answer analyzes the logic.** The narrow answer is a list of verified counts, names and versions and says "not checked" for the runtime behaviour, because the decomposition chose the questions that are easiest to verify, not the ones that answer the question. The broad answer is a one-paragraph summary followed by the plan agent's question which slice to analyze, which no one answers in a headless run.
- **Cost.** Narrow mode is 12 fresh sessions of 17,000 to 74,000 prompt tokens each plus a 132,000-token verify step; broad mode spends 246,000 of its 316,000 tokens in the checker, which made 36 tool calls over 536 s for 22 claims, most of them corroborating two paraphrases of the messaging layer's role against several documents. Both are a fifth to a ninth of the 2.63 million tokens of the first answer.
- **Fixes.** The verifier agents now deny the skill tool, so a checker never sees a fact sheet; `ask.py --no-skills` leaves the skills out of the answering agent's settings, for the run that measures the model's own reading. The offline check covers both.

### 2026-10-23: the knowledge-boundary probe, no tools

The 34 private questions sent straight to the server with the answer-line suffix of the repo-facts test, no tools and no files, five seeded samples each, in two arms: with the working rules as the system message, and bare. 340 requests, four in flight, 42 minutes; the server's default sampling, effort `xhigh`, at most 8,192 generated tokens.

| Arm | Effort | Samples | Correct | Declined | Wrong, confident | No answer line | Of which empty replies; cut at the token limit | Questions with a confident-wrong majority | Thinking tokens per sample (median) | Seconds per sample (median) |
|---|---|---|---|---|---|---|---|---|---|---|
| rules | xhigh | 170 | 0 | 0 | 0 | 170 | 140; 1 | 0 of 34 | 29 | 1.4 |
| bare | xhigh | 170 | 0 | 71 | 4 (2.4%) | 95 | 41; 46 | 0 of 34 | 891 | 16.7 |

What the samples show:

- **Under the rules the model never answers from memory; it reaches for a tool.** 140 of its 170 replies are empty after a thought of about 30 tokens ("let me run commands to check"); 29 more say "I'll explore the repository" with no answer line; one hit the token limit. The empty replies are tool calls: the same request with one shell tool offered returns two shell calls (a listing, then a find of the directories asked for), and with no tool offered the server drops the call, leaving 50 generated tokens that appear in neither the content nor the thinking. So under the rules the probe does not measure what the model would guess; it measures that the rules stop it from answering from memory at all: 0 confident answers in 170, right or wrong.
- **Bare, the model guesses in 4 of 170 samples (2.4 percent), each on a different question.** An acronym expanded to a plausible phrase that is not the one in the code; a count of 0 where the key says 146; a control period 750 times too short; 1 where the key says 8. 71 samples declined on the answer line (five questions declined in all five samples), 41 were dropped tool calls like the rules arm's, 8 wrote a shell command as the reply, and 46 thought until the 8,192-token limit without concluding, circling general knowledge (the OSI layers, for a question about this product's layers) and the wording of the question. No question had a confident-wrong majority, and no question was answered correctly in any sample: nothing in the key is general knowledge, and the list of memory candidates is empty.
- **The Case reading.** Wrong-confident under the rules is 0 percent, with no confident-wrong majority: the boundary holds, and the errors the flows leave are Case 1 (a mistake any model makes with the files in front of it) or Case 3 (our instructions or the fact sheet), not Case 2 (guessing where it should stop). The probe makes no case for the cloud tier. Bare against rules on "not answered from memory" is 97.6 against 100 percent, a 2.4-point difference, under the 10-point threshold: the answer-line suffix alone keeps the model from guessing, and what the rules add here is to send it to the tools, which every harness node has anyway. Whether the rules can leave the question nodes, where they cost 42 percent more prompt tokens, is a cost question for the effort comparison, not an accuracy one.
- **Cost.** 30,000 generated tokens in the rules arm against 551,000 bare, nearly all of it thinking; 1.4 against 16.7 s per sample. The probe now counts empty replies apart, so the next run shows the tool attempts without reading the samples.

### 2026-10-23: the model's own reading, the model-driven graph, the window, and a frontier reference

The question of 2026-10-20, "analyze this codebase and understand the logic", in the same worktree, with the skills left out (`--no-skills`), so the model reads the repository itself; the coded graphs on four lanes; the same question through the model-driven graph (`--mode self`: the window's `/ask` steps followed by the model in one session, then the same claims and checker nodes); the window session of the day (the build agent with the fact sheet as a skill, two questions); and a reference: Fable 5.1, a frontier model, answering the same question in one read-only session under the same file rules. Every answer was then checked claim by claim, or hop by hop, by independent read-only agents, two per batch, disagreements settled by hand: the same method for every column, so the columns rank against each other. Two defects of the harness found on the way are taken out of the model's account: the parsers dropped the leading underscore of a path (two hops of narrow run 1 and seven of narrow run 2 were marked partly for that alone), and the checker's batches counted pairs instead of hops, which capped the batch holding three 8-hop chains in narrow run 1. Both are fixed; narrow run 2 and broad run 2 ran with the new batching.

| Measure | Narrow run 1 | Narrow run 2 | Broad run 1 | Broad run 2 | Self (model-driven) | Window, fact sheet | Reference, Fable 5.1 |
|---|---|---|---|---|---|---|---|
| Runs | 12: decompose, 5 facts, 3 chains, 2 verify, compose | 17: decompose, 6 facts, 4 chains, 5 verify, compose | 4; the answer step timed out at 15 min | 24: answer, claims, 21 verify, correct | 11: answer with 11 subagents inside, claims, 8 verify, correct | 1 session, 2 questions | 1 session |
| Time | 17.6 min wall; 31.5 min of runs | 32.5 min; 92.4 min of runs | 18.5 min, no answer | 56.1 min; 114.4 min of runs; the answer step 15.0 min | 18.3 min; 32.8 min of runs; the answer step 4.6 min | 2.3 min and 2.8 min | 7.3 min |
| Requests; prompt tokens | 118; 4,016,000 | 226; 8,090,000 | 19; 343,000, plus 2,367,000 in 5 subagents | 308; 7,240,000, plus 1,604,000 in 1 subagent | 74; 1,489,000, plus 254,000 in 11 subagents | 5; 81,000 | 125,000 tokens billed in all |
| Generated tokens, of which thinking | 71,000; 55,800 | 168,600; 128,700 | 13,900; 7,700, plus 60,900 in the subagents | 222,600; 157,400, plus 19,800 | 80,800; 58,800, plus 17,500 | 4,400; 3,000 | not measured |
| Tool calls; errors | 173; 3 | 349; 6 | 28; 2, the subagents' not counted | 537; 7 | 137; 1 | 5; 0 | 41; 0 |
| Runs stopped by a budget | 1 verify batch capped | 1 fact and 2 chain runs capped, 1 verify batch capped, 1 timed out | the answer step | 3 verify batches capped | none | none | none |
| Verdicts of the harness's checker | 4 correct, 4 unparsed (the capped batch) | 4 correct, 1 partly, 1 wrong, 1 unverifiable, 3 unparsed | none: the claims step listed 3 statements about the session itself | 123 claims: 85 correct, 14 partly, 2 wrong, 4 unverifiable, 18 unparsed | 46 claims: 37 correct, 6 partly, 3 unverifiable | not run | not run |
| Independent check | 8 pairs: 6 correct, 2 partly, 0 wrong; 24 hops: 21 correct, 3 partly | 10 pairs: 6 correct, 2 partly, 2 unanswered; 18 hops: 15 correct, 3 partly | no answer to check | 123 claims: 94 correct, 25 partly, 2 wrong, 2 unverifiable | 46 claims: 40 correct, 4 partly, 0 wrong, 2 unverifiable (its own "not checked" lines) | 30 claims: 22 correct, 8 partly, 0 wrong | 30 claims: 29 correct, 1 partly, 0 wrong |
| Strictly correct | 75% of pairs, 88% of hops | 60% of pairs, 83% of hops | none | 76% | 87% | 73% | 97% |
| False statements | 0 | 0 | none | 2 of 123 | 0 | 0 | 0 |
| Checker against the independent check | agrees on the 4 it parsed | agrees on 6 of 7 parsed | none | agrees on 88 of 123; the 18 unparsed hid 11 correct, 6 partly, 1 wrong | agrees on 39 of 46 | not run | not run |
| What the answer covers | 5 facts and 3 verified chains: build-domain selection, the commit hook, an app to its image | 6 facts and 4 chains, two of them deep into one control application | nothing | a survey of the whole tree in 123 claims, few of them mechanisms | 10 narrow questions, all trivia: a version pin, a 2-line file, a file size | the fact sheet restated | layers and their rule, the build path, one runtime chain from the entry point to the robot command, 517 words |

The ranking on the same question, by the independent check, strictly correct claims or pairs first and false statements second: the reference (97%, none false); the self run (87%, none false, on ten trivia questions); narrow run 1 (75% of pairs, 88% of hops, none false, three mechanism chains); broad run 2 (76%, 2 false in 123); the window with the fact sheet (73%, none false, but nothing read); narrow run 2 (60% of pairs with two unanswered, 83% of hops, none false); broad run 1 (no answer). Correctness alone misranks the self run: it is right about what it asked, and it asked nothing about the logic; the "what the answer covers" row is the other half of the ranking. On the private single-fact set the ranking is flat: the reference 68 of 68 in two rounds at 3 tool calls per question (8 minutes for all 68, ten at a time); the local model 63 of 64 on 2026-10-20 at 4.5 tool calls and 25 s per question; the effort comparison of today adds the local model's rounds without the fact sheet below.

What the runs show:

- **The boundary holds in the runs as in the probe.** Across 24 hops, 18 hops, 123 claims and 46 claims the local model wrote 2 false statements, both in the broad survey: a layer credited with code that lives in another layer, and a process manager inferred from the name of an application that is a state machine. Both are misreadings with the files in hand (Case 1), not inventions where the repository is silent (Case 2); nothing invented was found in any narrow chain. The partly verdicts are imprecision: a cited line range too short, an example line taken from a neighbouring target, an artifact named one stage early, an overstated "only", a dropped word of a name.
- **The coded graph reads deeper than the model-driven one, at four times the time.** Left to follow the `/ask` steps itself, the model wrote ten trivia questions (a version pin, the length of a two-line file, the size of a CI file), ran ten explore subagents of 8 to 65 s and one verify subagent, and had an answer in 4.6 min that says nothing about the runtime. The coded narrow flow, whose decompose prompt asks for mechanism questions, produced hop chains through the build tool, the commit hook, an application's build to its image, and a control application's step loop, 21 of 24 and 15 of 18 hops right, in 17.6 and 32.5 min. The same structure, two different outcomes: the prompt of the decompose node decides what the graph asks, and the model does not ask the hard questions on its own.
- **The decomposition is not stable.** Run 2 asked broader questions than run 1 (a camera frame's path through drivers and services; the whole build-to-deployable sequence), three of its runs hit their tool caps, two pairs ended without an answer, and the answer took twice as long. The questions the model writes are the largest source of variance in the narrow flow; the next step is a decompose prompt with a fixed shape (one chain per named entry point) or two samples with the checkable one kept.
- **The checker is the cost.** Verification took 458 s of runs in narrow run 1, 2,749 s in narrow run 2 and 4,867 s in broad run 2, where 123 claims took 21 batches and 81 min of runs against a 15-min answer; 3 of the 21 batches hit the tool cap. Where it parsed, the checker agreed with the independent agents on 72 to 100 percent of claims; where it did not, the unparsed claims were mostly correct ones. The claims step is the lever: 123 claims from a 6,000-character answer is one claim per sentence fragment, and a claims prompt that asks for the load-bearing claims only would cut the checker's work by two thirds.
- **The reference gap is in composition, not in facts.** On single facts the local model is at 98 percent and the reference at 100. On the broad question the reference wrote 30 citable claims with 1 imprecision in 7.3 min and 41 tool calls, one runtime chain included; the local model's best run needed the coded graph, 18 min and 4 million prompt tokens to verify 3 chains, and its single-session survey carried 25 imprecisions and 2 errors in 123 claims. The gap the cloud tier would close is the long, structured answer; the facts and the chains the local graph already gets.
- **Subagents inside a node are invisible to the runner.** Broad run 1's answer step fanned out four explore subagents and a verify subagent (2.4 million prompt tokens, 25 min of sessions) inside one node and timed out; the runner counted 11 tool calls and 343,000 tokens. The lane database holds the rest, and the table above reads it; the runner should count it.
- **Five harness defects found by the measurements, all fixed today.** The checker's batches counted pairs, not hops (a batch of three 8-hop chains hit the cap); a pipe inside a claim split the claims table and miscounted the verdicts; an escaped pipe in the checker's evidence shifted its columns; the parsers dropped a leading underscore from a path; a stopped runner left its opencode runs alive. One design change: the broad answer node gets 30 minutes, because the plan agent's working rules fan out subagents inside it.
