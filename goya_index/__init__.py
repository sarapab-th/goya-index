"""GOYA Index Harness: local retrieval; no model calls or execution authority."""

from pathlib import Path

from .cli import build, engines

__version__ = "0.1.0a1"
__all__ = ["build", "search", "which_one", "read", "sync", "inventory", "context"]


def _catalog(data):
    path = Path(data).expanduser().resolve(strict=True) / "catalog.sqlite3"
    if not path.is_file():
        raise ValueError("No catalog exists at the supplied data directory")
    return path


def search(data, query, *, limit=10):
    """Rank local source matches. Search is not an assertion of answer correctness."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    catalog, _, _ = engines()
    return catalog.unified_search(query, catalog=_catalog(data), limit=limit, use_semantic=False)


def which_one(data, query):
    """Search, then answer with the best file or ask "which one?" when look-alike files compete.

    Returns {"action": "answer", "path": ..., "why": ...} or {"action": "ask", "message": ..., "groups": [...],
    "shown": [...]}, plus the top results. No model calls. An answer is not an assertion of correctness."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    from ._engine import which_one as rule
    catalog, _, _ = engines()
    path = _catalog(data)
    with catalog._catalog_lock(path, exclusive=False):  # one catalog generation for search and rule
        rows = catalog.unified_search(query, catalog=path, limit=rule.RANKED_LIMIT, use_semantic=False)
        lexical = catalog._component_path(path, "phase2", path.parent / "lexical.sqlite3")
        return rule.which_one(query, rows, lexical, catalog=path)


def read(data, path):
    """Read an indexed relative path and compare its bytes with the catalog hash."""
    path = Path(path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("read requires a relative path within the indexed source")
    catalog, _, _ = engines()
    return catalog.read_verified_source(_catalog(data), path.as_posix())


def sync(data):
    """Update derived index data without intentionally writing source files."""
    catalog, _, _ = engines()
    return catalog.incremental_sync(_catalog(data))


def inventory(data):
    catalog, _, _ = engines()
    return catalog.inventory(_catalog(data))


def context(data, query, *, max_chars=4000, limit=5):
    """Return bounded, ranked excerpts with hashes; never contact a model provider."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 256 <= max_chars <= 100000:
        raise ValueError("max_chars must be between 256 and 100000")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    catalog, _, _ = engines()
    return catalog.retrieval_evidence_packet(query, catalog=_catalog(data), max_chars=max_chars, limit=limit)
