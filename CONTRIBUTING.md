# Contributing

Keep changes focused and include a synthetic regression test. Run:

```sh
python3 -B -m unittest discover -s tests -v
```

Do not submit generated catalogs, real source datasets, personal paths, API keys,
model responses containing private information, or production traces. Use
invented fixtures instead. Preserve fail-closed verified-read behavior and
document failures and platform limitations instead of hiding them.

New provider calls, execution tools or automatic uploads are not part of this
library's local-only boundary. Discuss a proposed boundary change before coding.

By submitting code for inclusion, you confirm you have the right to contribute
it under this repository's MIT license. Keep notices on any third-party material.
