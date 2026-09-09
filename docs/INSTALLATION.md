# Installation and first run

[Home](../README.md) · [Usage](USAGE.md) · [Benchmarks](BENCHMARKS.md)

## Requirements

- Python 3.10 or newer, with SQLite FTS5 and POSIX file locking (`fcntl`).
- macOS or Linux. No GPU, model, API key or vector server is needed.
- Local space for the source metadata and text catalogs.

| Environment | Status for this package |
|---|---|
| macOS ARM64, Python 3.13.15 | Source and isolated installed-wheel tests passed |
| Linux | Intended target; machine qualification pending |
| Windows with WSL2/Linux | Use Linux Python inside WSL; qualification pending |
| Native Windows Python | Unsupported: POSIX file locking is required |

Do not equate a WSL result with a native-Windows result. No desktop installer is
needed to use the library. See [release notes](RELEASE_NOTES.md) for recorded runs.

## 1. Get the source

Clone the public repository:

```sh
git clone https://github.com/sarapab-th/goya-index-harness.git
cd goya-index-harness
```

Or use **Code → Download ZIP**, extract it, and open a terminal in the extracted
directory containing `pyproject.toml` and `goya_index/`.
[Release archives and wheel](https://github.com/sarapab-th/goya-index-harness/releases/tag/v0.1.0a1)
are available separately. This package is not published on PyPI.

## 2. Check Python and SQLite

```sh
python3 --version
python3 -c 'import fcntl, sqlite3; db=sqlite3.connect(":memory:"); db.execute("CREATE VIRTUAL TABLE probe USING fts5(text)"); print("POSIX locking and FTS5 available")'
```

If you have multiple Python installations, use the same executable in each step.

## 3. Try without installing

From the checkout:

```sh
python3 -B -m goya_index build --source ./examples/source --data ../goya-index-demo-data
python3 -B -m goya_index search --data ../goya-index-demo-data --query calculate_stock_level
python3 -B -m goya_index read --data ../goya-index-demo-data --path inventory.py
python3 -B -m goya_index context --data ../goya-index-demo-data --query calculate_stock_level --max-chars 2000
```

`inventory.py` should rank first and the read should say `verified: true`. Every
command prints JSON. Source files are not intentionally written; index databases
are stored in the separate data directory. Do not commit that directory.

If you already ran `build`, use `sync` or choose another absent data directory.
Do not delete an existing directory just to force a tutorial command to work.

## 4. Install the CLI and Python library

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/goya-index --help
```

Pip may obtain setuptools from a package index during the build. Runtime indexing
has no third-party Python dependency and performs no provider or telemetry calls.
Do not use `sudo pip` or install into the system Python.

For an already downloaded wheel and a virtual environment with pip:

```sh
.venv/bin/python -m pip install --no-index --no-deps /path/to/goya_index_harness-0.1.0a1-py3-none-any.whl
```

This command needs an actual release wheel; it does not download one. A
`py3-none-any` wheel tag describes Python packaging, not native Windows support.

## Windows / WSL

Use an existing Linux distribution in WSL2 and clone into its Linux filesystem.
Run the commands above **inside that Linux terminal**. Avoid a shared
Windows-mounted source/data tree for initial qualification because filesystem
semantics differ. This guide does not install WSL or change Windows features,
firewall rules or startup services.

Native Windows should fail with an explicit POSIX-locking requirement. Do not
remove locking checks to make an import succeed.

## Troubleshooting

| Message or symptom | Action |
|---|---|
| `No module named goya_index` | Run from the checkout, or install into the same Python environment |
| `No module named fcntl` / POSIX required | Use macOS or Linux; on Windows use an existing WSL distribution |
| `no such module: fts5` | Use a Python build with SQLite FTS5; check before creating your index |
| Data directory already exists | Use `sync`, or choose a new directory for a separate index |
| Index data must be outside source | Put data next to—not inside—the selected source folder |
| `verified: false` | Inspect the reason; synchronize after legitimate edits; do not treat refused content as verified |
| No useful result | Ask for a distinctive name/path/symbol; the preview has no embedding service |
| A file is missing | Hidden, symlinked, sensitive-looking, non-UTF8, unsupported or oversized content may be excluded |

Text retrieval is capped at 2 MiB per source file; metadata hashing at 16 MiB.
Ordinary text/code extensions are recognized, but this is not a PDF/Office
extractor. No configurable `.gitignore`-style policy is exposed by the public API;
built-in exclusions are not a substitute for choosing a safe root.

## Update and remove

Update your checkout deliberately, then reinstall in its virtual environment if
you use the installed CLI. Back up important private catalogs first; this alpha
does not promise cross-version schema migration. A fresh index from the same
authorized source is the fallback if a future release changes the schema.

Uninstalling the Python package does not delete your source or private catalog.
Manage any catalog separately and verify its exact path before removing it.
