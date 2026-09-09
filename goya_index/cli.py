#!/usr/bin/env python3
"""Portable local entrypoint for the retained Index Harness engines.

Uses the original metadata, lexical and catalog engines. The portable profile
starts with empty research-only projections instead of inventing experiments,
temporal history, capabilities or memory for an ordinary folder.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys


def engines():
    if sys.platform == "win32":
        raise RuntimeError("This preview requires macOS or Linux (POSIX file locking)")
    from ._engine import catalog, filesystem, lexical
    return catalog, filesystem, lexical



def empty_projections(path):
    """The catalog's optional research tables contain no fabricated records."""
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        CREATE TABLE objects(object_id TEXT, object_type TEXT, name TEXT, path TEXT,
          locator TEXT, status TEXT, project TEXT, source_schema TEXT, authority TEXT,
          modified_ns INTEGER, metadata_json TEXT);
        CREATE TABLE relationships(source_id TEXT, relation TEXT, target_id TEXT, evidence TEXT);
        CREATE TABLE capabilities(name TEXT, arguments_json TEXT, schema_name TEXT,
          description TEXT, permission TEXT);
        CREATE TABLE memories(memory_id TEXT, path TEXT, line_number INTEGER, kind TEXT,
          domain TEXT, task TEXT, round_name TEXT, strategy TEXT, outcome TEXT,
          strength TEXT, authority TEXT, record_sha256 TEXT);
        CREATE TABLE temporal_events(sequence INTEGER, target TEXT, value TEXT, status TEXT);
        CREATE TABLE continuity_state(phase INTEGER, status TEXT, finding TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata VALUES('authority_granted', 'none');
        """)
        db.commit()


def build(source, data):
    source = Path(source).expanduser().resolve(strict=True)
    data = Path(data).expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Source must be a directory")
    if data == source or source in data.parents:
        raise ValueError("Index data must be outside the source directory")
    if data.exists():
        raise ValueError("Choose a new, absent data directory; existing catalogs are preserved")
    harness, metadata, lexical = engines()
    data.mkdir(parents=True, mode=0o700)
    p1, p2, advanced = (data / name for name in ("metadata.sqlite3", "lexical.sqlite3", "projections.sqlite3"))
    metadata.build_index(source, p1, harness.HASH_MAX_BYTES)
    lexical.build_lexical_index(p1, p2, content_max_bytes=harness.CONTENT_MAX_BYTES)
    empty_projections(advanced)
    result = harness.build_catalog(data / "catalog.sqlite3", phase1=p1, phase2=p2, phase3=None, advanced=advanced)
    result["profile"] = "portable_folder_no_vectors"
    result["historical_soak_applies_to"] = "retained engine; this portable wrapper has separate fresh tests"
    (data / "build.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="GOYA Index — local source search, no model calls")
    parser.add_argument("command", choices=["build", "search", "read", "sync", "inventory", "context"])
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--path")
    parser.add_argument("--max-chars", type=int, default=4000)
    args = parser.parse_args(argv)
    if args.command == "build":
        if not args.source:
            parser.error("build requires --source")
        result = build(args.source, args.data)
    else:
        harness, _, _ = engines()
        catalog = args.data.resolve(strict=True) / "catalog.sqlite3"
        if not catalog.is_file():
            parser.error("No catalog exists at the supplied data directory")
        if args.command == "search":
            if not args.query:
                parser.error("search requires --query")
            result = harness.unified_search(args.query, catalog=catalog, limit=10, use_semantic=False)
        elif args.command == "context":
            if not args.query:
                parser.error("context requires --query")
            from . import context
            result = context(args.data, args.query, max_chars=args.max_chars)
        elif args.command == "read":
            if not args.path or Path(args.path).is_absolute() or ".." in Path(args.path).parts:
                parser.error("read requires a relative path within the indexed source")
            result = harness.read_verified_source(catalog, args.path)
        elif args.command == "sync":
            result = harness.incremental_sync(catalog)
        else:
            result = harness.inventory(catalog)
    print(json.dumps(result, indent=2, sort_keys=True))


def entrypoint():
    try:
        main()
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"GOYA Index: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    entrypoint()
