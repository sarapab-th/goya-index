# Evidence scope

See [Benchmarks](BENCHMARKS.md) for the measured retrieval, model-comparison,
cost, caching and endurance results, including failed and corrected runs.

The earlier engine experiment (HARNESS_INDEX_0) recorded a final continuous soak of
86,400.316 seconds, 2,905 cycles and 3,775 queries, with 290 concurrency probes,
48 restart probes and 24 fault probes. Its retained report recorded zero failures
in that final run and zero source writes, provider calls or spend. Earlier
incidents and interrupted attempts remain part of the internal experiment record.

**Those measurements are not a completed soak of this public package.** This
release derives a portable, local-only profile from those engines, changes their
packaging and excludes private research projections and benchmark inputs. Fresh
package tests are reported in RELEASE_NOTES.md. Private datasets and internal
evidence are not uploaded, so the old soak is a maintainer-reported historical
result, not a publicly independently reproducible benchmark here.

No blanket 96% accuracy, 79% savings or similar marketing claim applies to this
release. Previous narrow-answerable and broad/ambiguous query evaluations had
different results; users should evaluate retrieval on their own authorized data.
