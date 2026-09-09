#!/usr/bin/env python3
"""Phase 2 SQLite FTS5 content index for HARNESS_INDEX_0."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import time
from typing import Callable


from . import filesystem as phase1


SCHEMA_VERSION = "harness-index.phase2/1"
DEFAULT_CONTENT_MAX_BYTES = 2 * 1024 * 1024
TEXT_EXTENSIONS = frozenset(
    {
        ".cfg",
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsonl",
        ".lock",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
QUERY_TOKEN = re.compile(r"[A-Za-z0-9_]+")
SECRET_CONTENT_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|secret)\b"
        r"\s*[:=]\s*['\"][^'\"\r\n]{12,}['\"]"
    ),
)
SECRET_ASSIGNMENT_MARKER = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|password|secret)\b\s*[:=]"
)


@contextmanager
def _connect_readonly(database: Path):
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _phase1_metadata(database: Path) -> dict[str, str]:
    with _connect_readonly(database) as connection:
        return dict(connection.execute("SELECT key, value FROM metadata"))


def _read_stable_text(
    path: Path,
    *,
    expected_size: int,
    expected_mtime_ns: int,
) -> tuple[str | None, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, "unreadable"
    try:
        before = os.fstat(descriptor)
        if before.st_size != expected_size or before.st_mtime_ns != expected_mtime_ns:
            return None, "stale_phase1_metadata"
        chunks = []
        remaining = expected_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                return None, "changed_during_read"
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            return None, "changed_during_read"
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            return None, "changed_during_read"
    except OSError:
        return None, "unreadable"
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    if b"\x00" in payload:
        return None, "binary"
    try:
        return payload.decode("utf-8"), "indexed"
    except UnicodeDecodeError:
        return None, "non_utf8"


def _contains_secret_pattern(text: str) -> bool:
    if "PRIVATE KEY" in text and SECRET_CONTENT_PATTERNS[0].search(text):
        return True
    if "AKIA" in text and SECRET_CONTENT_PATTERNS[1].search(text):
        return True
    if "sk-" in text and SECRET_CONTENT_PATTERNS[2].search(text):
        return True
    if "gh" in text and SECRET_CONTENT_PATTERNS[3].search(text):
        return True
    return bool(
        SECRET_ASSIGNMENT_MARKER.search(text)
        and SECRET_CONTENT_PATTERNS[4].search(text)
    )


def build_lexical_index(
    phase1_database: Path,
    database: Path,
    *,
    content_max_bytes: int = DEFAULT_CONTENT_MAX_BYTES,
) -> dict[str, object]:
    started = time.perf_counter()
    phase1_meta = _phase1_metadata(phase1_database)
    root = Path(phase1_meta["root"]).resolve(strict=True)
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + ".building")
    if temporary.exists():
        temporary.unlink()
    shutil.copyfile(phase1_database, temporary)

    counters = {
        "candidate_files": 0,
        "indexed_documents": 0,
        "indexed_content_bytes": 0,
        "skipped_extension": 0,
        "skipped_large": 0,
        "skipped_sensitive": 0,
        "skipped_secret_pattern": 0,
        "stale_or_changed": 0,
        "binary_or_non_utf8": 0,
        "unreadable": 0,
    }
    connection = sqlite3.connect(temporary)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            DROP TABLE IF EXISTS documents_fts;
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS content_status;
            CREATE TABLE documents (
                path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE TABLE content_status (
                path TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                indexed_bytes INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                filename,
                content,
                content='documents',
                content_rowid='rowid',
                tokenize='unicode61'
            );
            """
        )
        source_rows = connection.execute(
            """
            SELECT path, filename, extension, size_bytes, modified_ns
            FROM entries WHERE entry_type = 'file' ORDER BY path
            """
        ).fetchall()
        for row in source_rows:
            relative = str(row["path"])
            extension = str(row["extension"])
            size = int(row["size_bytes"])
            counters["candidate_files"] += 1
            if extension not in TEXT_EXTENSIONS:
                status = "skipped_extension"
                counters["skipped_extension"] += 1
                text = None
            elif phase1.SENSITIVE_PATH.search(relative):
                status = "skipped_sensitive"
                counters["skipped_sensitive"] += 1
                text = None
            elif size > content_max_bytes:
                status = "skipped_large"
                counters["skipped_large"] += 1
                text = None
            else:
                text, status = _read_stable_text(
                    root / relative,
                    expected_size=size,
                    expected_mtime_ns=int(row["modified_ns"]),
                )
                if status in {"stale_phase1_metadata", "changed_during_read"}:
                    counters["stale_or_changed"] += 1
                elif status in {"binary", "non_utf8"}:
                    counters["binary_or_non_utf8"] += 1
                elif status == "unreadable":
                    counters["unreadable"] += 1
                elif text is not None and _contains_secret_pattern(text):
                    text = None
                    status = "skipped_secret_pattern"
                    counters["skipped_secret_pattern"] += 1
            indexed_bytes = 0
            if text is not None:
                indexed_bytes = len(text.encode("utf-8"))
                connection.execute(
                    "INSERT INTO documents(path, filename, content) VALUES (?, ?, ?)",
                    (relative, row["filename"], text),
                )
                counters["indexed_documents"] += 1
                counters["indexed_content_bytes"] += indexed_bytes
            connection.execute(
                "INSERT INTO content_status(path, status, indexed_bytes) VALUES (?, ?, ?)",
                (relative, status, indexed_bytes),
            )
        connection.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
        build_ms = (time.perf_counter() - started) * 1000
        updates = {
            "phase2_schema": SCHEMA_VERSION,
            "phase2_content_max_bytes": str(content_max_bytes),
            "phase2_text_extensions": json.dumps(sorted(TEXT_EXTENSIONS)),
            "phase2_build_ms": f"{build_ms:.6f}",
            "phase2_counts": json.dumps(counters, sort_keys=True, separators=(",", ":")),
            "phase2_embeddings": "false",
            "phase2_authority_granted": "none",
        }
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            updates.items(),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("sqlite_integrity_check_failed")
        connection.execute(
            "INSERT INTO documents_fts(documents_fts) VALUES('integrity-check')"
        )
    finally:
        connection.close()
    os.replace(temporary, database)
    total_ms = (time.perf_counter() - started) * 1000
    return {
        "schema": SCHEMA_VERSION,
        "source_phase1_database": str(phase1_database.resolve()),
        "database": str(database),
        "root": str(root),
        "content_max_bytes": content_max_bytes,
        "total_ms": round(total_ms, 3),
        "database_bytes": database.stat().st_size,
        "counts": counters,
        "embeddings": False,
        "authority_granted": "none",
        "sqlite_integrity_check": "ok",
        "fts5_integrity_check": "ok",
    }


def _fts_expression(query: str) -> str:
    tokens = QUERY_TOKEN.findall(query)
    if not tokens:
        raise ValueError("empty_query")
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def lexical_search(
    database: Path, query: str, *, limit: int = 20
) -> list[dict[str, object]]:
    expression = _fts_expression(query)
    with _connect_readonly(database) as connection:
        rows = connection.execute(
            """
            SELECT d.path,
                   d.filename,
                   bm25(documents_fts, 3.0, 1.0) AS score,
                   snippet(documents_fts, 1, '[', ']', '…', 24) AS snippet
            FROM documents_fts
            JOIN documents AS d ON d.rowid = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY score, length(d.path), d.path
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
        return [dict(row) for row in rows]
