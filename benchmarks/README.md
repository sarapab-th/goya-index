# Public reproducible runs

These JSON results come from `tools/benchmark.py` and generated fixtures only.
They are exact-symbol smoke checks, not the historical private-corpus benchmark.
No provider call, cost reduction or blind-query accuracy is measured here.

Run from the checkout:

```sh
PYTHONPATH=. python3 -B tools/benchmark.py --files 100 --queries 30 --output benchmark-local.json
```

Use a new output filename. Compare the same parameters and package version across
hosts; normal filesystem caches and host scheduling affect latency.
See [methods and historical evidence](../docs/BENCHMARKS.md).
