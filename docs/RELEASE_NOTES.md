# 0.1.0a1 — standalone developer preview

## Included

- Existing filesystem, FTS5 and catalog engines, with package-local imports.
- Portable folder build/search/read/sync/inventory commands and Python API.
- Existing bounded context-packet algorithm exposed through API and CLI.
- Synthetic example, regression tests, security notes and MIT license.

## Excluded

- Private project trees, catalogs, memory/history data, production credentials,
  Guardian/Relay code, desktop installers and internal evidence dumps.
- Private research benchmarks, research catalog rebuilding and background-watch
  entrypoints. Deployment-specific volatile source paths are cleared.
- Embedding HTTP client/vector-search execution. Requesting semantic search in
  the internal catalog explicitly refuses rather than calling a local provider.

## Qualification — 9 September 2026

- macOS ARM64, Python 3.13.15: 16 unique synthetic tests passed from source.
- The same 16 tests passed after an offline wheel installation into a new
  temporary virtual environment, outside the checkout with PYTHONPATH removed.
- Installed command entrypoint and isolated module import passed.
- Wheel/source archive checks passed: intended Python source set, matching source
  bytes, MIT metadata, zero runtime dependencies, required docs/example, no
  generated databases/keys or detected personal absolute paths in the archives.
- Built wheel and source archive with the locally available setuptools 82.0.1.
- The macOS/Linux Python 3.10/3.13 CI matrix is prepared but has not run on GitHub.

Two packaging defects were found and corrected during preparation: removing an
embedding-only import also removed `math`, which lexical reranking still uses
(two initial test errors); the first archive audit matched its own private-key
header detector (one false-positive audit refusal). Neither failure is presented
as a passing run. No retrieval algorithm was altered to silence either failure.

At this preparation stage publication was pending. The public preview uses
`sarapab-th/goya-index-harness` and the included GOYA MIT copyright notice.
This is not a fresh 24-hour soak of the packaged derivative.

## Documentation and reproducible example follow-up — 9 September 2026

- Added installation, CLI/API, provider-neutral integration, Relay/GOYA product
  boundaries, troubleshooting and historical benchmark methods with limitations.
- Added a working context example and deterministic synthetic benchmark runner.
- The example and initial 100-file/30-query smoke benchmark passed locally.
- A source-checkout test invocation initially failed one fresh-process test:
  relative `PYTHONPATH=.` changed meaning when the child changed directory.
  The test now binds the already imported package under test explicitly. No
  runtime indexing or retrieval behavior was changed for this correction.
- Three regression tests cover the example, benchmark and create-only output.
- Updated source suite: 19/19 passed on macOS ARM64, Python 3.13.15.
- The rebuilt wheel passed the same 19 tests in a new offline-installed virtual
  environment, plus entrypoint, isolated import and archive checks. This follow-up
  build used locally available setuptools 84.0.0; no new build package was fetched.
- Retained synthetic benchmark: 100 files, 30/30 correct first results, 30/30
  verified reads and 30/30 verified packets; SQLite integrity and source-byte
  preservation passed. Build 36.434 ms; search median 2.836 ms, p95 3.304 ms.
  These are this host's small exact-symbol results, not general retrieval accuracy.
- Mini-PC qualification is pending: the saved pinned SSH endpoint timed out;
  no native-Windows or WSL execution result is claimed.
- GOYA homepage is live; checked public download and Relay paths returned 404.
  Documentation links to the working site without inventing a download URL.

## Public publication — 9 September 2026

- The owner authorized publication of the prepared MIT developer preview at
  `https://github.com/sarapab-th/goya-index-harness` before mini-PC qualification.
- Final source and isolated offline-wheel suites passed 19/19 tests each. Archive
  privacy/content checks, installed CLI and isolated import checks passed.
- GitHub refused the first push because its credential lacked `workflow` scope.
  No credential permissions were changed. The matrix is retained as an inactive
  template in `.github/ci-template/tests.yml`; no GitHub Actions run is claimed.
- The rejected first commit is retained on a local-only branch. The initial public
  commit contains the template instead of an active workflow.
- Native Windows remains unsupported; Linux/WSL and the mini-PC are not yet
  qualified. This preview is not a commercial desktop installer.

## Platform and security boundaries

- Python 3.10+ and SQLite FTS5; POSIX file locking required.
- Native Windows unsupported. No Linux execution is claimed without a recorded run.
- No model integration, hosted endpoint or automatic Relay integration.
- Native source paths may appear in local results and catalog data; keep them private.
- No protection against an attacker controlling the host user or the catalogs.
