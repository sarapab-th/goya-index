# Security and privacy

This is a local retrieval library, not an authority or containment boundary.

Index only a folder you own or are authorized to read. Run with normal user
permissions, not root. Use a private data directory outside the source and keep
the catalog together with its component databases. The catalog contains source
paths and can retain text that has since been deleted from the source. Do not
share it as a diagnostic attachment or commit it to Git.

The public API/CLI performs no model calls and offers no shell, write-to-source,
publish, network or approval capability. Removing those capabilities from this
library does not restrict a calling application's own capabilities.

Secret detection is heuristic, not DLP. Hash verification detects disagreement
with an existing local catalog; it cannot prove origin, semantic truth or
freshness after a valid full rollback. Search metadata checks are weaker than
the content verification performed by `read` and `context`.

The host user, catalog files and source owner are trusted. A hostile same-user
process can alter source or database paths and is not contained by this library.
Do not expose its APIs as an unauthenticated remote service. Do not accept
untrusted catalog files, source roots or query sizes without your own boundary.

If you find a vulnerability, do not post secrets or exploit details in a public
issue. Use GitHub's private vulnerability reporting if enabled; otherwise open a
minimal issue asking the maintainer for a private reporting channel without
including the sensitive details. No private reporting service is claimed active
until configured on the published repository.
