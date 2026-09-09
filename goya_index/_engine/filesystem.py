#!/usr/bin/env python3
"""Phase 1 metadata-only filesystem index for HARNESS_INDEX_0.

The source tree is opened read-only.  The generated SQLite database contains
only mechanically derived filesystem metadata and bounded SHA-256 hashes; it
does not contain file contents and it grants no execution authority.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import statistics
import time
from typing import Callable, Iterable


SCHEMA_VERSION = "harness-index.phase1/1"
DEFAULT_HASH_MAX_BYTES = 16 * 1024 * 1024
EXCLUDED_DIRECTORIES = frozenset(
    {
        "__pycache__",
        "node_modules",
        "vendor",
        "site-packages",
        "dist",
        "build",
        "target",
        "venv",
        "env",
        "eggs",
        "htmlcov",
        "coverage",
        "logs",
        "tmp",
    }
)
SENSITIVE_PATH = re.compile(
    r"(?:^|[._/\-])(secrets?|credentials?|password|passwd|private_key|"
    r"api_key|access_token)(?:$|[._/\-])",
    re.IGNORECASE,
)
TOKEN = re.compile(r"[a-z0-9]+")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path, before: os.stat_result) -> tuple[str | None, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, "unreadable"
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        if identity != opened_identity:
            return None, "changed_before_hash"
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if opened_identity != after_identity:
            return None, "changed_during_hash"
    except OSError:
        return None, "unreadable"
    finally:
        os.close(descriptor)
    return digest.hexdigest(), "hashed"


def scan_tree(
    root: Path,
    *,
    hash_max_bytes: int,
    include_hashes: bool,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Return a deterministic, non-following snapshot of visible entries."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("root_not_directory")

    pending = [root]
    rows: list[dict[str, object]] = []
    counters = {
        "directories_visited": 0,
        "entries": 0,
        "files": 0,
        "directories": 0,
        "symlinks": 0,
        "other": 0,
        "hashed_files": 0,
        "hashed_bytes": 0,
        "skipped_large": 0,
        "skipped_sensitive": 0,
        "unreadable": 0,
        "source_file_bytes": 0,
    }

    while pending:
        directory = pending.pop(0)
        counters["directories_visited"] += 1
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            counters["unreadable"] += 1
            continue
        try:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    counters["unreadable"] += 1
                    continue

                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if entry.is_symlink():
                    entry_type = "symlink"
                elif entry.is_dir(follow_symlinks=False):
                    if entry.name in EXCLUDED_DIRECTORIES:
                        continue
                    entry_type = "directory"
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    entry_type = "file"
                else:
                    entry_type = "other"

                content_hash: str | None = None
                if entry_type != "file":
                    hash_status = "not_applicable"
                else:
                    counters["files"] += 1
                    counters["source_file_bytes"] += stat.st_size
                    if not include_hashes:
                        hash_status = "not_requested"
                    elif SENSITIVE_PATH.search(relative):
                        hash_status = "skipped_sensitive"
                        counters["skipped_sensitive"] += 1
                    elif stat.st_size > hash_max_bytes:
                        hash_status = "skipped_large"
                        counters["skipped_large"] += 1
                    else:
                        content_hash, hash_status = _sha256(path, stat)
                        if hash_status == "hashed":
                            counters["hashed_files"] += 1
                            counters["hashed_bytes"] += stat.st_size
                        else:
                            counters["unreadable"] += 1

                if entry_type == "directory":
                    counters["directories"] += 1
                elif entry_type == "symlink":
                    counters["symlinks"] += 1
                elif entry_type == "other":
                    counters["other"] += 1
                counters["entries"] += 1
                rows.append(
                    {
                        "path": relative,
                        "filename": entry.name,
                        "entry_type": entry_type,
                        "extension": path.suffix.lower(),
                        "size_bytes": stat.st_size,
                        "modified_ns": stat.st_mtime_ns,
                        "content_sha256": content_hash,
                        "hash_status": hash_status,
                    }
                )
        finally:
            for entry in entries:
                entry.close() if hasattr(entry, "close") else None

    rows.sort(key=lambda row: str(row["path"]))
    return rows, counters


def build_index(root: Path, database: Path, hash_max_bytes: int) -> dict[str, object]:
    started = time.perf_counter()
    rows, counters = scan_tree(
        root, hash_max_bytes=hash_max_bytes, include_hashes=True
    )
    scan_ms = (time.perf_counter() - started) * 1000
    root = root.resolve(strict=True)
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + ".building")
    if temporary.exists():
        temporary.unlink()

    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE entries (
                path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                extension TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                content_sha256 TEXT,
                hash_status TEXT NOT NULL
            );
            CREATE INDEX entries_filename ON entries(filename);
            CREATE INDEX entries_modified ON entries(modified_ns DESC);
            CREATE INDEX entries_hash ON entries(content_sha256, size_bytes);
            CREATE INDEX entries_type ON entries(entry_type);
            """
        )
        connection.executemany(
            """
            INSERT INTO entries (
                path, filename, entry_type, extension, size_bytes, modified_ns,
                content_sha256, hash_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["path"],
                    row["filename"],
                    row["entry_type"],
                    row["extension"],
                    row["size_bytes"],
                    row["modified_ns"],
                    row["content_sha256"],
                    row["hash_status"],
                )
                for row in rows
            ],
        )
        metadata = {
            "schema": SCHEMA_VERSION,
            "root": str(root),
            "created_unix_ns": str(time.time_ns()),
            "hash_max_bytes": str(hash_max_bytes),
            "excluded_directories": _json(sorted(EXCLUDED_DIRECTORIES)),
            "hidden_entries_excluded": "true",
            "file_contents_stored": "false",
            "authority_granted": "none",
            "scan_ms": f"{scan_ms:.6f}",
            "counts": _json(counters),
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
        )
        connection.commit()
        check = connection.execute("PRAGMA integrity_check").fetchone()
        if check != ("ok",):
            raise RuntimeError("sqlite_integrity_check_failed")
    finally:
        connection.close()
    os.replace(temporary, database)
    total_ms = (time.perf_counter() - started) * 1000
    return {
        "schema": SCHEMA_VERSION,
        "database": str(database),
        "root": str(root),
        "hash_max_bytes": hash_max_bytes,
        "scan_ms": round(scan_ms, 3),
        "total_ms": round(total_ms, 3),
        "database_bytes": database.stat().st_size,
        "counts": counters,
    }


@contextmanager
def _connect(database: Path):
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, object]]:
    return [dict(row) for row in cursor.fetchall()]


def find_metadata(database: Path, query: str, limit: int = 20) -> list[dict[str, object]]:
    terms = TOKEN.findall(query.lower())
    if not terms:
        raise ValueError("empty_query")
    predicates = " AND ".join("instr(lower(path), ?) > 0" for _ in terms)
    with _connect(database) as connection:
        return _rows(
            connection.execute(
                f"""
                SELECT * FROM entries
                WHERE {predicates}
                ORDER BY CASE WHEN entry_type = 'file' THEN 0 ELSE 1 END,
                         length(path), modified_ns DESC, path
                LIMIT ?
                """,
                (*terms, limit),
            )
        )


def newest_report(database: Path, project: str) -> list[dict[str, object]]:
    with _connect(database) as connection:
        return _rows(
            connection.execute(
                """
                SELECT * FROM entries
                WHERE entry_type = 'file'
                  AND instr(lower(path), lower(?)) > 0
                  AND lower(filename) IN ('result.json', 'verification.json',
                                           'report.json', 'report.md')
                ORDER BY modified_ns DESC, path
                LIMIT 10
                """,
                (project,),
            )
        )


def project_files(database: Path, project: str) -> list[dict[str, object]]:
    with _connect(database) as connection:
        return _rows(
            connection.execute(
                """
                SELECT * FROM entries
                WHERE entry_type = 'file' AND instr(lower(path), lower(?)) > 0
                ORDER BY path
                """,
                (project,),
            )
        )


def duplicate_groups(database: Path, limit: int = 1000) -> list[dict[str, object]]:
    with _connect(database) as connection:
        groups = connection.execute(
            """
            SELECT content_sha256, size_bytes, count(*) AS copies
            FROM entries
            WHERE entry_type = 'file' AND content_sha256 IS NOT NULL
                  AND size_bytes > 0
            GROUP BY content_sha256, size_bytes
            HAVING count(*) > 1
            ORDER BY copies DESC, size_bytes DESC, content_sha256
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for group in groups:
            paths = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT path FROM entries
                    WHERE content_sha256 = ? AND size_bytes = ?
                    ORDER BY path
                    """,
                    (group["content_sha256"], group["size_bytes"]),
                ).fetchall()
            ]
            result.append(
                {
                    "content_sha256": group["content_sha256"],
                    "size_bytes": group["size_bytes"],
                    "copies": group["copies"],
                    "paths": paths,
                }
            )
        return result


def superseded_files(database: Path) -> list[dict[str, object]]:
    with _connect(database) as connection:
        return _rows(
            connection.execute(
                """
                SELECT * FROM entries
                WHERE entry_type = 'file'
                  AND (instr(lower(path), '/superseded/') > 0
                       OR lower(filename) LIKE '%superseded%')
                ORDER BY path
                """
            )
        )
