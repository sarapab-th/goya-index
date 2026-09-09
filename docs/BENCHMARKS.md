# Benchmarks and evidence

[Home](../README.md) · [Run the public benchmark](#reproduce-the-public-package-smoke-benchmark)

**Three different kinds of evidence are kept separate:** historical engine
retrieval/model experiments, the completed historical endurance soak, and fresh
tests of this standalone package. A good result in one does not prove the others.

Historical results below are maintainer-reported summaries of retained internal
records. Private corpora, source paths and model answers are not published here.
That limits independent reproduction. The public synthetic benchmark is separate
and reproducible; it does not reproduce the private accuracy or savings scores.

## 1. Retrieval: where the earlier engine worked—and failed

Top-1 means the expected source was ranked first. Top-5 means it occurred within
the first five results. Neither is a measurement of a model's general intelligence.

| Dataset / run | Top-1 | Top-5 | Interpretation |
|---|---:|---:|---|
| Broad/ambiguous blind v1 | 16/120 | 32/120 | Failed: underspecified language often did not identify the exact intended source |
| Answerable blind v2 | 107/120 | 116/120 | Narrowly missed that experiment's gate |
| v2 after inspection and tuning | 114/120 | 120/120 | Regression, not a new blind score |
| Distinctive blind v3 | 96/100 | 99/100 | Fresh model-authored set; required mechanical source-text anchors where omitted |
| v3 after later repairs | 98/100 | 100/100 | Regression, not independent holdout evidence |
| 16-case extension, first run | 14/16 | Not stated here | Preserved pre-fix result |
| 16-case extension after repair | 16/16 | 16/16 | Known-case regression confirmation |

The v3 query author did not receive file paths or names. However, mechanical
anchoring makes these questions more distinctive than typical vague user requests.
The result is not a human-independent benchmark or “96% correct on any question.”

In the frozen 16-case extension rerun, lexical-only retrieval took a median
107.798 ms and the indexed harness took 244.156 ms, while top-1 improved from
10/16 to 16/16. Additional structure can improve selection while costing time.

## 2. Paired natural-answer experiment

Eight questions were sent through each arm of a paired API experiment using the
recorded model identifier `claude-sonnet-5`: broad raw context versus selected
harness context. This used the earlier full research profile, including state and
memory projections that this portable folder package **does not populate**.

| Measure | Raw context | Harness context | Difference |
|---|---:|---:|---:|
| Final semantic score | 7/8 | 8/8 | One additional question passed |
| Input tokens | — | — | 90.5% fewer, as recorded in the experiment |
| Output tokens | 1,291 | 1,040 | 19.4% fewer |
| Recorded experiment cost | $0.079140 | $0.016692 | 78.9% lower |

Costs are those of the historical experiment, not current provider pricing or a
subscription promise. The combined recorded cost was $0.095832. No new paid model
calls were made to prepare this public repository.

The initial exact-string evaluator scored 5/8 raw and 4/8 harness, undercounting
punctuation and ordinary synonyms. A symmetric offline semantic rescore of the
same responses produced 7/8 and 8/8 without new provider calls. The remaining raw
failure hit its output cap before supplying two required facts. This was a tiny,
bounded experiment—not a statistical proof of general answer-quality improvement.

## 3. Context-size and model comparisons

Earlier source-selection tests asked a model to identify the exact file defining
a unique symbol. Four disjoint confirmation symbols were used after a packet
boundary defect was found and repaired.

| Recorded model / confirmation | Raw correct | Harness correct | Input-token reduction | Median latency raw → harness |
|---|---:|---:|---:|---|
| Local `gemma3:4b` | 3/4 | 4/4 | 68.4% | 10.9 s → 5.0 s |
| Local `gemma4:12b-it-q4_K_M` | 2/4 | 4/4 | 68.3% | 36.5 s → 17.0 s |
| Claude confirmation | 4/4 | 4/4 | 69.1% (7,344 → 2,269 tokens) | 2.40 s → 2.68 s |

The Claude arm was **slower**, despite using fewer input tokens. Its output
difference was only 102 → 101 tokens. Do not infer lower latency or substantially
shorter answers just from context compression.

On the earlier long-context cases (64,000 raw characters, 120-second timeout),
both local model arms timed out on all four raw cases, while the harness answered
4/4 for each with 93.6% fewer context characters. That is a constrained
source-selection result, not proof of general reasoning across long documents.

### Prompt caching is complementary

A separate eight-call Anthropic confirmation used two longer-answer tasks per
arm and a two-question cold/warm cache sequence per arm:

| Measurement | Raw | Harness | Difference |
|---|---:|---:|---:|
| Output-arm input tokens | 13,523 | 6,382 | 52.81% fewer |
| Output-arm output tokens | 425 | 381 | 10.35% fewer |
| Required facts | 6/6 | 6/6 | Equal |
| Cache-creation tokens | 7,132 | 3,063 | 57.05% fewer |
| Warm cache-read tokens | 7,132 | 3,063 | 57.05% fewer |

Both arms had observed cache writes and reads. Retrieval reduced the prefix;
caching reused it. The harness warm response was slower in this tiny sample.
Four fenced-JSON responses were initially rejected by a strict parser and later
normalized offline; the original failures and responses were retained. Cache
fields were **not measured** in the earlier source-selection confirmation and must
not be reported as zero there. No OpenAI comparison was completed in these runs.

## 4. Completed historical 24-hour soak

The repaired engine completed a fresh continuous run after earlier failed and
interrupted attempts. The final report records:

| Measurement | Result |
|---|---:|
| Continuous duration | 86,400.316 seconds |
| Maintenance cycles | 2,905 |
| Query attempts / failures | 3,775 / 0 |
| Concurrent lock cycles / failures | 290 / 0 |
| Restart probes / failures | 48 / 0 |
| Transactional fault probes / failures | 24 / 0 |
| Integrity and runner failures | 0 |
| Stable-stale maximum | 0 |
| Declared volatile-stale maximum | 7 |
| RSS growth | 68.33 MiB; inside the experiment's 256 MiB gate |
| Open file descriptors, initial → final | 6 → 6 |
| Harness source writes / provider calls / spend | 0 / 0 / $0 |

The runner deliberately changed synthetic/source-test inputs to test refresh;
“zero source writes” refers to the harness, not an absence of test mutations.
Full SQLite integrity validation passed. Earlier stale-file incidents and a
16-hour-39-minute interrupted run remain part of the record; the passing final
run does not erase them. The public profile does not import private volatile-path
exceptions from that deployment.

**This is not a fresh 24-hour soak of version 0.1.0a1.** The portable wrapper,
packaging and excluded projections have their own tests and still need endurance
qualification as a distributed product.

## 5. Provenance and reproducibility limits

The summaries were checked against retained artifacts on 9 September 2026.
Private source/answer data is withheld. These digests identify the internal
records used; without the records they are not independent proof of the claims.

| Internal record category | SHA-256 |
|---|---|
| Retrieval results report | `d571e52f2dd03568e72f6ed07d8a3e5a90749fc204e9ed71dff82633f6b58deb` |
| Context-spectrum report | `920ff539a25627fca0d2136947dcd304a1244cefc35d65dac4cc39c46b1c8021` |
| Natural-answer semantic-rescore report | `81ae8d6c7f00420fb0660f365a4be5f45ff055fb2f2041ba53c3b45a86cccd27` |
| Final soak summary | `37a84b5d651392b8e1eb8ce24e5a9eba6c048019eeddb06300e1feacb5bc7a49` |
| Final soak validation | `e56e3b19d6f6b8e2267b9c79f0dc8a7450dee2b7c1fcf1e5ec7b9a581fba942e` |

No private project is being marketed or released as part of Index. Historical
typed-state results do not imply that the portable folder API includes populated
memory/authority/relationship features. The private experiments are not a fair
head-to-head product comparison with commercial retrieval services.

## Reproduce the public-package smoke benchmark

From this checkout, with macOS/Linux Python:

```sh
PYTHONPATH=. python3 -B tools/benchmark.py --files 100 --queries 30
```

The script creates deterministic synthetic Python files in a disposable directory,
builds one catalog, performs a fixed query sequence, verifies reads and packets,
checks source bytes and SQLite integrity, and removes its temporary fixtures.
It prints machine-readable JSON with Python/platform information, build time,
search median/p95, top-1 count and verification results. Optionally retain it:

```sh
PYTHONPATH=. python3 -B tools/benchmark.py --files 100 --queries 30 --output benchmark-local.json
```

The output path must not already exist. Review results before sharing them.
The script does not include private source paths, provider calls or secrets.

This is an **author-designed exact-symbol smoke benchmark**, not a blind semantic
retrieval benchmark. Timings include normal host scheduling and filesystem cache
effects; there is no cold-cache claim, no competitive baseline and no token/cost
measurement. Run the same parameters and package version on another machine for
a useful portability comparison. A new externally authored broad-query benchmark
and a new package soak remain separate work.

### Recorded public-package run

| Environment | Fixture | Build | Search median / p95 | Result |
|---|---|---|---|---|
| macOS ARM64, Python 3.13.15, SQLite 3.53.1 | 100 files / 30 distinct queries | 36.434 ms | 2.836 / 3.304 ms | 30/30 top-1, verified reads and verified packets |

[Raw synthetic result](../benchmarks/macos-arm64-py313-2026-09-09.json).
Source-byte preservation and SQLite integrity passed. Linux/WSL and native Windows
are not represented by this measurement. The focused package suite separately
passed 19/19 tests on this Mac.
