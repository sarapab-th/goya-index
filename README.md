# GOYA Index

### Give your AI the relevant source—not the whole folder.

**Local retrieval for RAG · hash-verified excerpts · Python API + CLI · MIT**

GOYA Index is the retrieval step of RAG, run locally. It turns a folder of source
code and text into a searchable local catalog. Find the file or Python symbol you need, verify its bytes, and assemble
a compact context packet for your AI application. No model account, API key,
vector database, GPU, subscription or GOYA installation is required.

**Developer preview: `0.1.0a3`.** macOS tested; Linux/WSL qualification pending.
Native Windows is not supported in this preview.

[GitHub](https://github.com/sarapab-th/goya-index) ·
[Download preview](https://github.com/sarapab-th/goya-index/releases/tag/v0.1.0a3) ·
[CI template](.github/ci-template/tests.yml)

[Quick start](#quick-start) · [Installation](docs/INSTALLATION.md) ·
[Usage & API](docs/USAGE.md) · [Benchmarks](docs/BENCHMARKS.md) ·
[Relay & GOYA](docs/GOYA.md) · [Security](SECURITY.md)

## What you get

- **Find:** SQLite FTS5 lexical search, source-path ranking and Python symbol lookup.
- **Ask, don't guess:** when a vague question fits many look-alike files, `which_one` asks "which one?" and shows the closest.
- **Verify:** refuse stale or changed content when its bytes disagree with the catalog.
- **Prepare context:** ranked excerpts, source paths and SHA-256 digests within a character budget.
- **Stay current:** incremental synchronization after source changes.
- **Keep control:** local catalogs, no provider calls, uploads or telemetry from this library.
- **Build on it:** a Python API and JSON CLI, with no third-party Python runtime dependencies.

Useful for repository Q&A, developer assistants, internal-document lookup and
local context preparation. Distinctive, answerable queries work best; vague
questions still need clarification, and `which_one` asks for it in some of those
cases. Indexing is not a promise of correct answers.

```text
Your chosen folder → local index → ranked, verified excerpts → your AI application
                                                            └─ your chosen model
```

The model step belongs to your application. Index never sends the packet for you.

## Quick start

Requires Python 3.10+ with SQLite FTS5 on macOS or Linux. Start inside a cloned or
downloaded checkout; this example needs no installation or network access:

```sh
git clone https://github.com/sarapab-th/goya-index.git
cd goya-index
python3 -B -m goya_index build --source ./examples/source --data ../goya-index-demo-data
python3 -B -m goya_index search --data ../goya-index-demo-data --query calculate_stock_level
python3 -B -m goya_index read --data ../goya-index-demo-data --path inventory.py
python3 -B -m goya_index context --data ../goya-index-demo-data --query calculate_stock_level --max-chars 2000
```

The search should rank `inventory.py` first. The read should report `verified: true`;
the context response includes `packet`, `packet_sha256` and `candidates`.

Choose a **new data directory outside your source folder**. `build` refuses an
existing directory so it cannot silently replace an index. To update an existing
index after edits:

```sh
python3 -B -m goya_index sync --data ../goya-index-demo-data
```

For an installed command:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/goya-index --help
```

Pip may download the setuptools build tool. Index operations themselves are local.
See [installation, Windows/WSL and troubleshooting](docs/INSTALLATION.md).

## Add it to your application

```python
from goya_index import build, context

build("./examples/source", "../my-private-index")  # once, into a new directory
result = context("../my-private-index", "calculate_stock_level", max_chars=2000)

if not result["candidates"]:
    raise ValueError("No verified source: clarify the question or sync the index")

context_text = result["packet"]
# Your application can attach context_text to its model request.
# Treat retrieved text as untrusted data, not instructions or tool permission.
```

Use it with the provider you choose, or a local model. There is no bundled
provider SDK or automatic agent connector. See the [working example](examples/prepare_context.py)
and [complete API guide](docs/USAGE.md).

## Benchmarks we have measured

These are **historical engine experiments on private test corpora**, not a fresh
benchmark or 24-hour soak of this standalone package:

| Experiment | Measured result | Scope |
|---|---|---|
| Continuous endurance | **24 hours; 3,775 queries; 0 query failures** | Earlier engine; 2,905 maintenance cycles, 48 restart and 24 fault probes |
| Distinctive retrieval | **96/100 top-1; 99/100 top-5** | Model-authored, mechanically anchored blind-v3 questions |
| Paired natural-answer test | **90.5% fewer input tokens; 78.9% lower recorded cost** | 8 questions per arm, one Claude model; semantic rescore 8/8 vs 7/8 |
| Broad/ambiguous retrieval | **16/120 top-1; 32/120 top-5** | Earlier blind-v1 failure; a real limit, not omitted from the record |

The new package has separate synthetic tests. Historical typed-state, memory and
relationship experiments used components **not populated by this portable folder
profile**. Savings vary by workload; these results do not establish universal
accuracy, speed or cost reductions.

Read the [methods, failures, full comparisons and reproducible local benchmark](docs/BENCHMARKS.md).

**"Which one?" (29 October 2026):** one blind run on a private code base, with
model-written questions. On 120 vague questions, asking instead of guessing cut
wrong answers from 71 to 54; on 100 specific questions it never asked. The person's
reply was simulated. [Details and limits](docs/BENCHMARKS.md#6-which-one-on-vague-questions-29-october-2026).

**Fresh package check (0.1.0a1, 9 September):** 19 tests passed on macOS. The included 100-file synthetic
benchmark returned 30/30 expected first results and verified every read and packet;
median search was 2.84 ms on that Mac. This small exact-symbol check is reproducible,
not a blind accuracy score. [Raw result](benchmarks/macos-arm64-py313-2026-09-09.json).

## Part of GOYA. Useful on its own.

| Component | Job | Included here? |
|---|---|---|
| **GOYA Index** | Select relevant, verifiable context | Yes—MIT library and CLI |
| **GOYA Relay** | Model gateway: connect and route to providers you choose | No—separate product; Index integration is a separate step |
| **GOYA Guardian** | Govern actions, approval and execution within supported capabilities | No—retrieval does not confer action authority |
| **GOYA OS** | Broader modular product direction | No—this repo is not a desktop installer or operating system |

Our direction is to include basic Index functionality in Relay's Free plan,
alongside paid and enterprise options. That packaging is not an entitlement or
integration implemented by this repository. This MIT package remains usable alone.

**[Explore GOYA](https://goyaos.ai/)** · **[GOYA product information](https://goyaos.ai/product)**

Looking for a GOYA desktop download? Visit the official site for current access
information. As checked on 9 September 2026, a working public installer download
page was not available; we will add a direct link when it is published. No
installer or working Relay download is implied by this source release.

## Important boundaries

Index data can contain source text and retain deleted snippets: **keep it private**.
Secret-name/content filters are best effort, not a complete secret detector.
Use only folders you are authorized to index.

A hash checks consistency, not authorship, truth, permission or rollback freshness.
Matching source text can still contain prompt injection. This library is not an OS
sandbox, Guardian's action-enforcement harness, or a model. It cannot constrain the
other tools used by a calling agent. [Read the security boundary](SECURITY.md).

## Test and contribute

```sh
PYTHONPATH=. python3 -B -m unittest discover -s tests -v
PYTHONPATH=. python3 -B tools/benchmark.py --files 100 --queries 30
```

The benchmark uses generated synthetic files and does not call a provider.
See [Contributing](CONTRIBUTING.md), [release evidence](docs/RELEASE_NOTES.md)
and [benchmark interpretation](docs/BENCHMARKS.md).

The macOS/Linux CI matrix is supplied as an **inactive template**. The publishing
credential does not have GitHub's workflow permission; no hosted CI pass is claimed.

## License

[MIT](LICENSE). Free to use, modify and distribute, including commercial use under
the license terms. Other GOYA products and private project data are not included
or relicensed by this repository.
