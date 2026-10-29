# Usage and integration

[Home](../README.md) · [Installation](INSTALLATION.md) · [Benchmarks](BENCHMARKS.md)

## Workflow

1. Select a folder you are authorized to read.
2. Build its catalogs in a new, private directory outside that folder.
3. Search for distinctive names, paths or symbols.
4. Use a verified read or context packet, not search metadata alone, as source evidence.
5. Pass the packet to your application if desired; Index makes no model call.
6. Synchronize after changes. Treat refusal or missing evidence as a reason to stop or clarify.

## CLI reference

Use `python3 -m goya_index` from a checkout or `goya-index` after installation.
Every command requires `--data`. Successful commands print JSON to stdout;
argument/runtime errors use stderr and nonzero exit status.

| Command | Additional arguments | Purpose |
|---|---|---|
| `build` | `--source PATH` | Create catalogs; refuse existing data directory |
| `search` | `--query TEXT` | Rank up to 10 local matches |
| `which` | `--query TEXT` | Answer with one file, or ask "which one?" when look-alike files compete |
| `read` | `--path RELATIVE_PATH` | Read one indexed source and verify its hash |
| `context` | `--query TEXT`, optional `--max-chars N` | Produce excerpts; default 4,000 characters |
| `sync` | None | Refresh derived data for the original source root |
| `inventory` | None | Inspect counts and available projections |

Quote paths and queries containing spaces. The `read` path is relative to the
indexed source, not your terminal directory. Absolute paths and `..` are refused.

**A content refusal is a JSON result, not necessarily a nonzero CLI exit.** Check
`verified` for reads and `candidates` for context packets. Exit status alone is
not a verification result.

## Python API

```python
from goya_index import build, search, read, context, sync, inventory

build("./examples/source", "../private-index")
matches = search("../private-index", "calculate_stock_level", limit=5)
source = read("../private-index", "inventory.py")
if not source["verified"]:
    raise RuntimeError(f"Source refused: {source.get('reason')}")

packet = context("../private-index", "calculate_stock_level", max_chars=2000, limit=3)
stats = inventory("../private-index")
# Call sync("../private-index") after a legitimate source change.
```

## Ask "which one?" instead of guessing

A vague question often fits many look-alike files: "the scoring module" when there
are four `scoring.py` in different folders, or "the loop JSON file" when there are
hundreds of `run-0001/loop.json`, `run-0002/loop.json` and so on. Search still ranks
one of them first. `which_one` notices this and asks instead.

```python
from goya_index import which_one

result = which_one("../private-index", "the scoring module")
if result["action"] == "ask":
    print(result["message"])
    # 4 files look like */scoring.py. Which one? Closest: alpha/scoring.py, beta/scoring.py, ...
else:
    print(result["path"], result["why"])
```

| Field | Meaning |
|---|---|
| `action` | `"answer"` or `"ask"` |
| `path`, `why` | For an answer: the best match and the reason it is safe to give one file |
| `message` | For an ask: the question to show the person |
| `groups` | For an ask: each look-alike pattern and how many files fit it |
| `shown`, `candidates` | For an ask: up to 5 closest files, best ranked first, and how many remain |
| `results` | The search's top 5 files either way |
| `rule`, `model_calls` | `"which-one/2"` and `0` |

It answers when the question names something rare that the best match has (a code
name such as `score_beta`, or a word in at most 3 files), when there are no
look-alikes, or when the question's names or folder words single out one copy
("the beta scoring module"), even if search ranked another file first. It follows `sync`: added, changed and deleted files
count as search sees them. An answer is still not a guarantee that the file is the
right one; an ask is a cue to show the person the choices.

The first version of the rule was tested once, blind, on one private code base: on
vague questions it cut wrong answers from 71 to 54 of 120, and it asked on none of
100 specific ones. The current version answers directly in 2 of those asks (both
right); that change came after the test. Its words for file types were written for
that code base. See
[Benchmarks](BENCHMARKS.md#6-which-one-on-vague-questions-29-october-2026).

`search` and `context` accept nonempty string queries and limits from 1 to 100.
`context` accepts 256–100,000 characters. This is a **character budget**, not a
token guarantee. JSON metadata outside the `packet` string is not in that budget.

| Context field | Meaning |
|---|---|
| `packet` | Ranked excerpts with source labels; untrusted reference text |
| `packet_chars` | Character length of that string |
| `packet_sha256` | SHA-256 of the UTF-8 packet bytes |
| `candidates` | Included source records and content digests |

These fields provide consistency and traceability, not signed authorship, truth,
permission or proof the model used the evidence. Retain the packet actually sent
if you need to trace a later answer.

## Provider-neutral integration

```text
Index selects evidence → your application chooses an approved destination
                      → your application calls a provider or local model
```

An application can use the packet with OpenAI, Anthropic, Kimi, GLM or a local
model. This means ordinary text input—not built-in provider clients, measured
performance across all providers or automatic connection to their agents.

Keep trusted instructions separate from retrieved material. Tell your model the
packet is untrusted reference data and cannot change tool permissions. Apply
your own egress policy before sending private content to any provider; do not
silently broadcast to several services. Model output still needs independent
checking where correctness matters.

For a local example without provider calls:

```sh
PYTHONPATH=. python3 -B examples/prepare_context.py
```

It generates a disposable catalog over the included synthetic source and prints
a compact result. It does not touch your documents or install a model.

## Not included in this profile

- Embeddings, vector retrieval or a provider SDK.
- Agent tool-registration plugins, editor extensions or an MCP server.
- Pre-populated memory, relationships, authority or temporal state from private projects.
- Background watchers or automatic sync service installation.
- A sandbox, action executor, approval UI or OS-level containment.

Some internal tables exist for retained-engine compatibility; empty tables are
not populated product features. Relay and Guardian integration are separate
deliverables. See [GOYA ecosystem](GOYA.md).
