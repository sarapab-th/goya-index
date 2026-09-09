# Mini-PC qualification: Windows host, Linux/WSL execution

This is a test procedure, **not a completed result**. It does not qualify native
Windows Python. It uses only synthetic fixtures and a normal unprivileged account.
Do not change firewall, SSH, services, model installations or startup settings.

## Preflight on Windows

In normal PowerShell, check existing distributions:

```powershell
wsl --list --verbose
```

Use an already installed WSL2 Linux distribution. If none is available, stop and
report that requirement; do not install or upgrade Windows features as part of
this test. Obtain the public source checkout/archive once available and use its
Linux-filesystem copy, not real business files or an existing private index.

## Inside the Linux terminal

From the source checkout:

```sh
uname -srm
python3 --version
python3 -c 'import fcntl, sqlite3; db=sqlite3.connect(":memory:"); db.execute("CREATE VIRTUAL TABLE probe USING fts5(text)"); print(sqlite3.sqlite_version)'
python3 -B -m unittest discover -s tests -v
PYTHONPATH=. python3 -B examples/prepare_context.py
PYTHONPATH=. python3 -B tools/benchmark.py --files 100 --queries 30 --output benchmark-mini-pc.json
```

Use an absent output filename. The tests and example use disposable synthetic
folders. The benchmark retains only a JSON summary. It makes no model calls and
does not touch a live GOYA service.

Acceptance: 19 tests pass, the example selects `inventory.py`, all 30 benchmark
queries/reads/packets pass, source bytes are unchanged and SQLite reports `ok`.
Record any failure before diagnosing it. Do not patch the benchmark or disable
locking just to produce a passing result.

If a release wheel is available and the existing Python supports venv/pip, also
run `python3 -B tools/check_release.py` with the corresponding wheel and source
archive in `dist/`. It installs the wheel offline in a disposable environment.
No provider access or system-wide package installation is required. Missing
venv/pip is a separately reported environment prerequisite, not a passing test.

Label the final result **Linux under WSL2 on a Windows host**, with the observed
Python/platform information. Do not claim native Windows support or a Linux
desktop installer from this result. Keep machine hostnames, credentials and
personal paths out of any public evidence.
