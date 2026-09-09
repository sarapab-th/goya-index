#!/usr/bin/env python3
"""Federated, read-only implementation of GOYA HARNESS_INDEX_0.

The catalog is a virtual namespace over separately inspectable metadata,
lexical, semantic, and typed-state indexes.  It locates authoritative source
objects but never replaces them and never grants execution authority.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time


SCHEMA = "harness-index.full/1"
HERE = Path(__file__).resolve().parent
PROXY = HERE.parent / "HARNESS_INDEX_0_PROXY"
DEFAULT_PHASE1 = PROXY / "phase1/index.sqlite3"
DEFAULT_PHASE2 = PROXY / "phase2/index.sqlite3"
DEFAULT_PHASE3 = PROXY / "phase3/index.sqlite3"
DEFAULT_ADVANCED = PROXY / "phases4_10/index.sqlite3"
DEFAULT_INDEX = HERE / "index.sqlite3"
LIVE_COMPONENTS = HERE / "live_components"
CATALOG_LOCK = HERE / ".catalog.lock"
TOKEN = re.compile(r"[A-Za-z0-9_]+")
QUERY_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "be", "by", "does", "for",
        "from", "how", "in", "is", "it", "its", "of", "on", "or",
        "that", "the", "their", "this", "to", "used", "what", "when", "where",
        "which", "who", "why", "with", "within",
    }
)
LEXICAL_CONCEPT_EXPANSIONS = {
    "grant": ("permission", "access"),
    "meaning": ("semantic",),
    "meanings": ("semantic",),
    "permission": ("authority", "access"),
    "vector": ("embedding", "semantic"),
    "vectors": ("embeddings", "semantic"),
    "workspace": ("access",),
}
ADVANCED_FAILURE_VALUES = frozenset(
    {
        "FAIL", "FAILED", "ERROR", "INTEGRITY_ERROR", "REJECTED",
        "ABSTAIN", "CORRUPT_EVIDENCE", "NO_MEASURED_GAIN", "FALSIFIED",
        "INDETERMINATE",
    }
)
ADVANCED_PROPOSAL = re.compile(r"(?:PROPOSAL|DESIGN).*\.md$", re.IGNORECASE)
ADVANCED_EVIDENCE_NAME = re.compile(
    r"(?:^|[_-])(evidence|verification|evaluation)(?:[_.-]|$)", re.IGNORECASE
)
ADVANCED_OUTPUT_NAME = re.compile(
    r"(?:^|[_-])(result|report|output)(?:[_.-]|$)", re.IGNORECASE
)
ADVANCED_EXPERIMENT_DIR = re.compile(r"^artifacts/(c\d[^/]+)(?:/|$)", re.IGNORECASE)
ADVANCED_AZURE_EXPERIMENT_DIR = re.compile(
    r"^artifacts/azure_lab_campaign/experiments/([^/]+)(?:/|$)",
    re.IGNORECASE,
)
ADVANCED_FILE_OBJECT_TYPES = (
    "action", "decision", "environment", "evidence", "experiment", "failure",
    "model_run", "output", "plan", "skill", "task",
)
HASH_MAX_BYTES = 16 * 1024 * 1024
CONTENT_MAX_BYTES = 2 * 1024 * 1024
LEXICAL_RERANK_CHARS = 16 * 1024
LEXICAL_RERANK_DOCUMENTS = 80
PROJECTION_READ_ATTEMPTS = 3
TEXT_EXTENSIONS = frozenset(
    {
        ".cfg", ".css", ".csv", ".html", ".ini", ".js", ".json",
        ".jsonl", ".lock", ".md", ".py", ".rst", ".sh", ".sql",
        ".toml", ".ts", ".txt", ".xml", ".yaml", ".yml",
    }
)
EXCLUDED_DIRECTORIES = frozenset(
    {
        "__pycache__", "node_modules", "vendor", "site-packages", "dist",
        "build", "target", "venv", "env", "eggs", "htmlcov", "coverage",
        "logs", "tmp",
    }
)
SENSITIVE_PATH = re.compile(
    r"(?:^|[._/\-])(secrets?|credentials?|password|passwd|private_key|"
    r"api_key|access_token)(?:$|[._/\-])",
    re.IGNORECASE,
)
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
# Deployment-specific volatile paths are not included in the public package.
VOLATILE_SOURCE_PREFIXES: tuple[str, ...] = ()
VOLATILE_SOURCE_PATHS: frozenset[str] = frozenset()
VOLATILE_SOURCE_NAME = re.compile(r"(?:^|/)experimental_[^/]*_checkpoint\.json$")

TYPE_NAMESPACE = {
    "file": "/reality/files",
    "folder": "/reality/folders",
    "code_symbol": "/reality/code",
    "environment": "/reality/environments",
    "action": "/history/actions",
    "model_run": "/history/model_runs",
    "tool_call": "/history/tool_calls",
    "failure": "/history/failures",
    "experiment": "/history/experiments",
    "output": "/history/outputs",
    "task": "/history/tasks",
    "decision": "/knowledge/decisions",
    "evidence": "/knowledge/evidence",
    "plan": "/knowledge/plans",
    "memory": "/knowledge/memories",
    "tool": "/capabilities/tools",
    "tool_schema": "/capabilities/schemas",
    "skill": "/capabilities/skills",
    "agent": "/capabilities/agents",
    "model": "/capabilities/models",
    "permission": "/authority/permissions",
    "approval": "/authority/approvals",
    "prohibition": "/authority/prohibitions",
    "budget": "/authority/budgets",
    "envelope": "/authority/envelopes",
}


@contextmanager
def _ro(path: Path):
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _is_volatile_source(source_path: str) -> bool:
    return (
        source_path in VOLATILE_SOURCE_PATHS
        or source_path.startswith(VOLATILE_SOURCE_PREFIXES)
        or VOLATILE_SOURCE_NAME.search(source_path) is not None
    )


def _volatile_unavailable(
    source_path: str,
    indexed_modified_ns: int | None,
    observed_modified_ns: int | None,
) -> dict[str, object]:
    indexed = int(indexed_modified_ns or 0)
    return {
        "volatile_policy": "explicit_unavailable_on_change",
        "last_verified_modified_ns": indexed,
        "observed_modified_ns": observed_modified_ns,
        "last_verified_age_ms": (
            round(max(0, time.time_ns() - indexed) / 1_000_000, 3)
            if indexed > 0
            else None
        ),
        "source_path": source_path,
    }


def _catalog_lock_path(catalog: Path) -> Path:
    """Return the lock identity for exactly one catalog generation."""
    resolved = catalog.resolve()
    if resolved == DEFAULT_INDEX.resolve():
        return CATALOG_LOCK
    return resolved.with_name(f".{resolved.name}.lock")


@contextmanager
def _catalog_lock(catalog: Path, *, exclusive: bool):
    """Coordinate catalog readers and writers across local processes."""
    lock_path = _catalog_lock_path(catalog)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _id(kind: str, path: str, locator: str = "") -> str:
    return hashlib.sha256(f"{kind}\0{path}\0{locator}".encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _live_files(root: Path) -> dict[str, os.stat_result]:
    """Return the visible regular-file snapshot used by the frozen index rules."""
    found: dict[str, os.stat_result] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in EXCLUDED_DIRECTORIES:
                        pending.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            relative = Path(entry.path).relative_to(root).as_posix()
            found[relative] = stat
    return found


def _read_stable_payload(
    path: Path,
    before: os.stat_result,
    *,
    max_bytes: int,
) -> tuple[bytes | None, str]:
    if before.st_size > max_bytes:
        return None, "skipped_large"
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, "unreadable"
    try:
        opened = os.fstat(descriptor)
        expected = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        observed = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if observed != expected:
            return None, "changed_before_read"
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                return None, "changed_during_read"
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            return None, "changed_during_read"
        after = os.fstat(descriptor)
        if observed != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None, "changed_during_read"
    except OSError:
        return None, "unreadable"
    finally:
        os.close(descriptor)
    return b"".join(chunks), "read"


def _safe_file_projection(
    root: Path,
    relative: str,
    stat: os.stat_result,
) -> dict[str, object]:
    extension = Path(relative).suffix.lower()
    sensitive = bool(SENSITIVE_PATH.search(relative))
    result: dict[str, object] = {
        "size_bytes": stat.st_size,
        "extension": extension,
        "modified_ns": stat.st_mtime_ns,
        "content_sha256": None,
        "hash_status": "skipped_sensitive" if sensitive else "skipped_large",
        "content": None,
        "content_status": "skipped_sensitive" if sensitive else "skipped_extension",
    }
    if sensitive:
        return result
    path = root / relative
    payload: bytes | None = None
    read_status = "unreadable"
    # A repository scan and the subsequent open are necessarily separate.
    # Retry only transient identity changes, always with a fresh stat. Each
    # individual read still has to remain stable from open through EOF.
    for attempt in range(PROJECTION_READ_ATTEMPTS):
        payload, read_status = _read_stable_payload(
            path, stat, max_bytes=HASH_MAX_BYTES
        )
        if payload is not None or read_status not in {
            "changed_before_read", "changed_during_read"
        }:
            break
        if attempt + 1 >= PROJECTION_READ_ATTEMPTS:
            break
        try:
            stat = path.stat()
        except OSError:
            read_status = "unreadable"
            break
        result["size_bytes"] = stat.st_size
        result["modified_ns"] = stat.st_mtime_ns
        time.sleep(0.01)
    if payload is None:
        result["hash_status"] = read_status
        result["content_status"] = read_status
        return result
    result["content_sha256"] = hashlib.sha256(payload).hexdigest()
    result["hash_status"] = "hashed"
    if extension not in TEXT_EXTENSIONS:
        return result
    if stat.st_size > CONTENT_MAX_BYTES:
        result["content_status"] = "skipped_large"
        return result
    if b"\x00" in payload:
        result["content_status"] = "binary"
        return result
    try:
        text_value = payload.decode("utf-8")
    except UnicodeDecodeError:
        result["content_status"] = "non_utf8"
        return result
    if _contains_secret_pattern(text_value):
        result["content_status"] = "skipped_secret_pattern"
        return result
    result["content"] = text_value
    result["content_status"] = "indexed"
    return result


def _namespace_path(kind: str, path: str, locator: str = "") -> str:
    base = TYPE_NAMESPACE.get(kind, "/knowledge/observations")
    suffix = path.strip("/") or _id(kind, path, locator)
    if locator:
        suffix += "#" + locator.strip("/")
    return f"{base}/{suffix}"


def _add_namespace(
    connection: sqlite3.Connection,
    *,
    object_id: str,
    object_type: str,
    name: str,
    source_path: str,
    locator: str = "",
    status: str | None = None,
    project: str | None = None,
    content_hash: str | None = None,
    modified_ns: int | None = None,
    authority: str | None = None,
    source_table: str,
    source_id: str,
    metadata: dict[str, object] | None = None,
) -> None:
    namespace_path = _namespace_path(object_type, source_path, locator)
    metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
    connection.execute(
        """
        INSERT OR IGNORE INTO namespace(
          object_id, namespace_path, object_type, name, source_path, locator,
          status, project, content_hash, modified_ns, authority, source_table,
          source_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            object_id, namespace_path, object_type, name, source_path, locator,
            status, project, content_hash, modified_ns, authority, source_table,
            source_id, metadata_json,
        ),
    )


def _refresh_namespace_fts(connection: sqlite3.Connection, object_id: str) -> None:
    row = connection.execute(
        "SELECT rowid, name, namespace_path, source_path, metadata_json FROM namespace WHERE object_id=?",
        (object_id,),
    ).fetchone()
    if row is None:
        return
    connection.execute("DELETE FROM namespace_fts WHERE rowid=?", (row["rowid"],))
    connection.execute(
        "INSERT INTO namespace_fts(rowid, name, namespace_path, source_path, metadata) VALUES (?, ?, ?, ?, ?)",
        (row["rowid"], row["name"], row["namespace_path"], row["source_path"], row["metadata_json"]),
    )


def _remove_symbols_for_path(connection: sqlite3.Connection, source_path: str) -> None:
    rows = connection.execute(
        "SELECT rowid, symbol_id FROM symbols WHERE source_path=?", (source_path,)
    ).fetchall()
    for row in rows:
        connection.execute("DELETE FROM symbols_fts WHERE rowid=?", (row["rowid"],))
        namespace_row = connection.execute(
            "SELECT rowid FROM namespace WHERE object_id=?", (row["symbol_id"],)
        ).fetchone()
        if namespace_row is not None:
            connection.execute("DELETE FROM namespace_fts WHERE rowid=?", (namespace_row["rowid"],))
        connection.execute("DELETE FROM namespace WHERE object_id=?", (row["symbol_id"],))
    connection.execute("DELETE FROM symbols WHERE source_path=?", (source_path,))


def _insert_symbols_for_content(
    connection: sqlite3.Connection,
    source_path: str,
    content: str,
) -> int:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return 0
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        locator = f"line:{node.lineno}"
        symbol_id = _id("code_symbol", source_path, locator)
        cursor = connection.execute(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?)",
            (symbol_id, node.name, kind, source_path, node.lineno),
        )
        connection.execute(
            "INSERT INTO symbols_fts(rowid, name, source_path) VALUES (?, ?, ?)",
            (cursor.lastrowid, node.name, source_path),
        )
        _add_namespace(
            connection,
            object_id=symbol_id,
            object_type="code_symbol",
            name=node.name,
            source_path=source_path,
            locator=locator,
            source_table="sync.ast",
            source_id=symbol_id,
            metadata={"symbol_kind": kind, "line_number": node.lineno},
        )
        _refresh_namespace_fts(connection, symbol_id)
        count += 1
    return count


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone() is not None


def _delete_namespace_types(
    connection: sqlite3.Connection,
    source_path: str,
    object_types: tuple[str, ...],
) -> None:
    placeholders = ",".join("?" for _ in object_types)
    rows = connection.execute(
        f"SELECT rowid, object_id FROM namespace WHERE source_path=? "
        f"AND object_type IN ({placeholders})",
        (source_path, *object_types),
    ).fetchall()
    for row in rows:
        connection.execute("DELETE FROM namespace_fts WHERE rowid=?", (row["rowid"],))
        connection.execute("DELETE FROM namespace WHERE object_id=?", (row["object_id"],))


def _parent_folder_paths(source_path: str) -> list[str]:
    return [
        parent.as_posix()
        for parent in reversed(Path(source_path).parents)
        if parent.as_posix() not in {"", "."}
    ]


def _refresh_parent_folder_projections(
    connection: sqlite3.Connection,
    root: Path,
    source_path: str,
) -> list[str]:
    """Refresh existing parents and remove parents that vanished from disk."""
    touched: list[str] = []
    for relative in _parent_folder_paths(source_path):
        folder = root / relative
        if not folder.is_dir():
            _delete_namespace_types(connection, relative, ("folder",))
            touched.append(relative)
            continue
        try:
            stat = folder.stat()
        except OSError:
            continue
        object_id = _id("folder", relative)
        metadata_json = json.dumps(
            {
                "extension": "",
                "hash_status": "not_applicable",
                "size_bytes": stat.st_size,
                "incremental_sync": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO namespace(
              object_id, namespace_path, object_type, name, source_path,
              locator, status, project, content_hash, modified_ns,
              authority, source_table, source_id, metadata_json
            ) VALUES (?, ?, 'folder', ?, ?, '', 'current', NULL, NULL, ?, NULL,
                      'sync.live', ?, ?)
            ON CONFLICT(object_id) DO UPDATE SET
              name=excluded.name, status='current', modified_ns=excluded.modified_ns,
              source_table='sync.live', source_id=excluded.source_id,
              metadata_json=excluded.metadata_json
            """,
            (
                object_id,
                _namespace_path("folder", relative),
                Path(relative).name,
                relative,
                stat.st_mtime_ns,
                relative,
                metadata_json,
            ),
        )
        _refresh_namespace_fts(connection, object_id)
        touched.append(relative)
    return touched


def _capability_projection(
    content: str,
) -> tuple[dict[str, tuple[str, ...]], dict[str, str], dict[str, str]]:
    tree = ast.parse(content)
    actions: dict[str, tuple[str, ...]] = {}
    schemas: dict[str, str] = {}
    docstrings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            docstrings[node.name] = ast.get_docstring(node) or ""
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "ACTION_KINDS" and isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        parsed = ast.literal_eval(value)
                        actions[key.value] = tuple(str(item) for item in parsed)
            elif (
                target.id.startswith("SCHEMA_")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                schemas[target.id] = node.value.value
    return actions, schemas, docstrings


def _refresh_capabilities_projection(
    connection: sqlite3.Connection,
    source_path: str,
    content: str,
) -> int:
    if not _table_exists(connection, "capabilities"):
        return 0
    actions, schemas, docstrings = _capability_projection(content)
    connection.execute("DELETE FROM capability_fts")
    connection.execute("DELETE FROM capabilities")
    _delete_namespace_types(connection, source_path, ("tool", "tool_schema"))
    for name, arguments in sorted(actions.items()):
        description = docstrings.get(name, "") or name.replace("_", " ")
        connection.execute(
            "INSERT INTO capabilities VALUES (?, ?, NULL, ?, NULL)",
            (name, json.dumps(arguments), description),
        )
        connection.execute(
            "INSERT INTO capability_fts(name, description) VALUES (?, ?)",
            (name, f"{description} {' '.join(arguments)}"),
        )
        object_id = _id("tool", source_path, f"ACTION_KINDS/{name}")
        _add_namespace(
            connection,
            object_id=object_id,
            object_type="tool",
            name=name,
            source_path=source_path,
            locator=f"ACTION_KINDS/{name}",
            source_table="sync.capabilities",
            source_id=object_id,
            metadata={"arguments": list(arguments)},
        )
        _refresh_namespace_fts(connection, object_id)
    for name, schema in sorted(schemas.items()):
        object_id = _id("tool_schema", source_path, name)
        _add_namespace(
            connection,
            object_id=object_id,
            object_type="tool_schema",
            name=name,
            source_path=source_path,
            locator=name,
            source_table="sync.capabilities",
            source_id=object_id,
            metadata={"source_schema": schema},
        )
        _refresh_namespace_fts(connection, object_id)
    return len(actions) + len(schemas)


def _refresh_memory_projection(
    connection: sqlite3.Connection,
    source_path: str,
    content: str,
) -> int:
    if not _table_exists(connection, "memories"):
        return 0
    connection.execute("DELETE FROM memories WHERE path=?", (source_path,))
    _delete_namespace_types(connection, source_path, ("memory",))
    count = 0
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("schema") != "goya.memory/1":
            continue
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
        memory_id = _id("memory", source_path, str(line_number))
        values = {
            key: str(record[key]) if record.get(key) is not None else None
            for key in (
                "kind", "domain", "task", "round_name", "strategy",
                "outcome", "strength", "authority",
            )
        }
        connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id, source_path, line_number, values["kind"],
                values["domain"], values["task"], values["round_name"],
                values["strategy"], values["outcome"], values["strength"],
                values["authority"], digest,
            ),
        )
        _add_namespace(
            connection,
            object_id=memory_id,
            object_type="memory",
            name=values["round_name"] or values["task"] or memory_id,
            source_path=source_path,
            locator=f"line:{line_number}",
            status=values["outcome"],
            authority=values["authority"],
            content_hash=digest,
            source_table="sync.memories",
            source_id=memory_id,
            metadata={
                "kind": values["kind"], "domain": values["domain"],
                "task": values["task"], "strength": values["strength"],
            },
        )
        _refresh_namespace_fts(connection, memory_id)
        count += 1
    return count


def _advanced_scalar(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    return str(value)


def _advanced_object_name(value: object, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:240]
    return fallback[:240]


def _experiment_ancestor(source_path: str) -> tuple[str, str] | None:
    match = ADVANCED_EXPERIMENT_DIR.match(source_path)
    if match:
        return f"artifacts/{match.group(1)}", match.group(1)
    match = ADVANCED_AZURE_EXPERIMENT_DIR.match(source_path)
    if match:
        return (
            f"artifacts/azure_lab_campaign/experiments/{match.group(1)}",
            match.group(1),
        )
    return None


def _advanced_projection_types(source_path: str) -> tuple[str, ...]:
    filename = Path(source_path).name
    types: set[str] = set()
    if ADVANCED_PROPOSAL.search(filename):
        types.update(("experiment", "plan"))
    if ADVANCED_EVIDENCE_NAME.search(filename):
        types.add("evidence")
    if ADVANCED_OUTPUT_NAME.search(filename):
        types.add("output")
    if source_path.endswith(".json"):
        types.update(("action", "decision", "failure", "model_run", "task"))
    if source_path == "artifacts/goya_skills/library.json":
        types.add("skill")
    if source_path == "core/environment.py":
        types.add("environment")
    return tuple(sorted(types))


def _ensure_experiment_projection(
    connection: sqlite3.Connection,
    source_path: str,
) -> str | None:
    ancestor = _experiment_ancestor(source_path)
    if ancestor is None:
        return None
    experiment_path, name = ancestor
    object_id = _id("experiment", experiment_path)
    _add_namespace(
        connection,
        object_id=object_id,
        object_type="experiment",
        name=name,
        source_path=experiment_path,
        source_table="sync.experiment_directory",
        source_id=object_id,
        metadata={"derivation": "experiment_directory_pattern"},
    )
    _refresh_namespace_fts(connection, object_id)
    return object_id


def _remove_relationships_for_ids(
    connection: sqlite3.Connection,
    object_ids: list[str],
) -> None:
    if not object_ids or not _table_exists(connection, "relationships"):
        return
    placeholders = ",".join("?" for _ in object_ids)
    connection.execute(
        f"DELETE FROM relationships WHERE source_id IN ({placeholders}) "
        f"OR target_id IN ({placeholders})",
        (*object_ids, *object_ids),
    )


def _refresh_file_relationship_projection(
    connection: sqlite3.Connection,
    source_path: str,
) -> int:
    if not _table_exists(connection, "relationships"):
        return 0
    file_id = _id("file", source_path)
    connection.execute(
        "DELETE FROM relationships WHERE source_id=? "
        "AND relation IN ('belongs_to','tested_by','superseded_by')",
        (file_id,),
    )
    connection.execute(
        "DELETE FROM relationships WHERE target_id=? AND relation='tested_by'",
        (file_id,),
    )
    count = 0
    experiment_id = _ensure_experiment_projection(connection, source_path)
    if experiment_id is not None:
        connection.execute(
            "INSERT OR IGNORE INTO relationships VALUES (?, 'belongs_to', ?, 'path_prefix')",
            (file_id, experiment_id),
        )
        count += 1

    name = Path(source_path).name
    if source_path.endswith(".py"):
        if name.startswith("test_"):
            implementation_name = name.removeprefix("test_")
            rows = connection.execute(
                "SELECT object_id FROM namespace WHERE object_type='file' AND name=? "
                "AND source_path NOT LIKE '%/test_%' ORDER BY source_path",
                (implementation_name,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "INSERT OR IGNORE INTO relationships VALUES (?, 'tested_by', ?, 'test_filename')",
                    (str(row[0]), file_id),
                )
                count += 1
        else:
            expected_test = f"test_{name}"
            rows = connection.execute(
                "SELECT object_id FROM namespace WHERE object_type='file' AND name=? "
                "ORDER BY source_path",
                (expected_test,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "INSERT OR IGNORE INTO relationships VALUES (?, 'tested_by', ?, 'test_filename')",
                    (file_id, str(row[0])),
                )
                count += 1

    if "/superseded/" in source_path:
        candidate = source_path.replace("/superseded/", "/")
        candidate = re.sub(
            r"\.(?:pre_[^.]+|contaminated[^.]*)\.(json|jsonl)$",
            r".\1",
            candidate,
        )
        target = connection.execute(
            "SELECT object_id FROM namespace WHERE object_type='file' AND source_path=?",
            (candidate,),
        ).fetchone()
        if target is not None:
            connection.execute(
                "INSERT OR IGNORE INTO relationships VALUES (?, 'superseded_by', ?, 'explicit_superseded_path')",
                (file_id, str(target[0])),
            )
            count += 1
    return count


def _refresh_advanced_file_projection(
    connection: sqlite3.Connection,
    source_path: str,
    content: str | None,
    modified_ns: int,
) -> dict[str, int]:
    """Rebuild every mechanically derived typed object for one source file."""
    if not _table_exists(connection, "relationships"):
        return {"objects": 0, "relationships": 0}
    projection_types = _advanced_projection_types(source_path)
    existing = connection.execute(
        "SELECT object_id FROM namespace WHERE source_path=? "
        + (
            "AND object_type IN (" + ",".join("?" for _ in projection_types) + ")"
            if projection_types
            else "AND 0"
        ),
        (source_path, *projection_types),
    ).fetchall()
    _remove_relationships_for_ids(connection, [str(row[0]) for row in existing])
    if projection_types:
        _delete_namespace_types(connection, source_path, projection_types)

    created: list[tuple[str, str]] = []

    def add(
        kind: str,
        name: str,
        *,
        locator: str = "",
        status: str | None = None,
        project: str | None = None,
        authority: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        object_id = _id(kind, source_path, locator)
        _add_namespace(
            connection,
            object_id=object_id,
            object_type=kind,
            name=name,
            source_path=source_path,
            locator=locator,
            status=status,
            project=project,
            modified_ns=modified_ns,
            authority=authority,
            source_table="sync.advanced_file",
            source_id=object_id,
            metadata=metadata,
        )
        _refresh_namespace_fts(connection, object_id)
        created.append((object_id, kind))

    filename = Path(source_path).name
    if ADVANCED_PROPOSAL.search(filename):
        stem = filename.rsplit(".", 1)[0]
        add("experiment", stem, status="PROPOSAL", metadata={"derivation": "proposal_filename"})
        add("plan", stem)
    if ADVANCED_EVIDENCE_NAME.search(filename):
        add("evidence", filename)
    if ADVANCED_OUTPUT_NAME.search(filename):
        add("output", filename)
    if source_path == "core/environment.py":
        add("environment", "seqtask-1", metadata={"derivation": "module_contract"})

    value: object = None
    if source_path.endswith(".json") and content is not None:
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            value = None
    if isinstance(value, dict):
        schema = _advanced_scalar(value.get("schema"))
        authority = _advanced_scalar(value.get("authority"))
        status = _advanced_scalar(
            value.get("status") or value.get("classification") or value.get("outcome")
        )
        if "decision" in value or "verdict" in value:
            decision = value.get("decision", value.get("verdict"))
            add(
                "decision",
                _advanced_object_name(decision, filename),
                status=_advanced_scalar(decision),
                authority=authority,
                metadata={"source_schema": schema},
            )
        if status and status.upper() in ADVANCED_FAILURE_VALUES:
            add(
                "failure", filename, status=status, authority=authority,
                metadata={"source_schema": schema},
            )
        actions = value.get("actions")
        if isinstance(actions, dict):
            for name, action in sorted(actions.items()):
                add(
                    "action",
                    str(name),
                    locator=f"/actions/{name}",
                    status=_advanced_scalar(action.get("classification"))
                    if isinstance(action, dict)
                    else None,
                    authority=authority,
                    metadata={"source_schema": schema},
                )
        if value.get("schema") == "goya.session/1" and isinstance(value.get("turns"), list):
            session_id = _advanced_scalar(value.get("id")) or Path(source_path).stem
            for index, turn in enumerate(value["turns"]):
                if not isinstance(turn, dict):
                    continue
                role = turn.get("role")
                if role == "user":
                    text = str(turn.get("content", ""))
                    add(
                        "task", text[:120] or f"turn {index}",
                        locator=f"/turns/{index}",
                        project=_advanced_scalar(value.get("lane")),
                        authority=authority,
                        metadata={"session_id": session_id, "source_schema": schema},
                    )
                elif role == "assistant":
                    add(
                        "model_run", f"{session_id}:{index}",
                        locator=f"/turns/{index}", authority=authority,
                        metadata={
                            "cost_usd_micros": turn.get("cost_usd_micros"),
                            "source_schema": schema,
                        },
                    )
        if source_path == "artifacts/goya_skills/library.json" and isinstance(value.get("skills"), list):
            for index, skill in enumerate(value["skills"]):
                if not isinstance(skill, dict) or not isinstance(skill.get("name"), str):
                    continue
                add(
                    "skill", skill["name"], locator=f"/skills/{index}",
                    metadata={
                        "kind": skill.get("kind"), "params": skill.get("params"),
                        "source_schema": schema,
                    },
                )

    experiment_id = _ensure_experiment_projection(connection, source_path)
    relationship_count = _refresh_file_relationship_projection(connection, source_path)
    if experiment_id is not None:
        for object_id, kind in created:
            relation = "produced_by" if kind in {"evidence", "output"} else "belongs_to"
            connection.execute(
                "INSERT OR IGNORE INTO relationships VALUES (?, ?, ?, 'path_prefix')",
                (object_id, relation, experiment_id),
            )
            relationship_count += 1
    return {"objects": len(created), "relationships": relationship_count}


def _delete_advanced_file_projection(
    connection: sqlite3.Connection,
    source_path: str,
) -> int:
    if not _table_exists(connection, "relationships"):
        return 0
    # Once a source is gone, every file-scoped projection it supported is gone
    # too, including an unknown projection that previously required fail-closed
    # invalidation while the source still existed.
    projection_types = ADVANCED_FILE_OBJECT_TYPES
    existing = connection.execute(
        "SELECT object_id FROM namespace WHERE source_path=? "
        + (
            "AND object_type IN (" + ",".join("?" for _ in projection_types) + ")"
            if projection_types
            else "AND 0"
        ),
        (source_path, *projection_types),
    ).fetchall()
    object_ids = [str(row[0]) for row in existing]
    _remove_relationships_for_ids(connection, object_ids)
    if projection_types:
        _delete_namespace_types(connection, source_path, projection_types)
    file_id = _id("file", source_path)
    connection.execute(
        "DELETE FROM relationships WHERE source_id=? OR "
        "(target_id=? AND relation='tested_by')",
        (file_id, file_id),
    )
    return len(object_ids)


def build_catalog(
    output: Path = DEFAULT_INDEX,
    *,
    phase1: Path = DEFAULT_PHASE1,
    phase2: Path = DEFAULT_PHASE2,
    phase3: Path | None = DEFAULT_PHASE3,
    advanced: Path = DEFAULT_ADVANCED,
) -> dict[str, object]:
    """Build the unified virtual namespace from immutable component indexes."""
    started = time.perf_counter()
    inputs = {"phase1": phase1, "phase2": phase2, "advanced": advanced}
    if phase3 is not None:
        inputs["phase3"] = phase3
    for path in inputs.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".building")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE namespace (
          object_id TEXT PRIMARY KEY,
          namespace_path TEXT UNIQUE NOT NULL,
          object_type TEXT NOT NULL,
          name TEXT NOT NULL,
          source_path TEXT NOT NULL,
          locator TEXT NOT NULL,
          status TEXT,
          project TEXT,
          content_hash TEXT,
          modified_ns INTEGER,
          authority TEXT,
          source_table TEXT NOT NULL,
          source_id TEXT NOT NULL,
          metadata_json TEXT NOT NULL
        );
        CREATE INDEX namespace_type ON namespace(object_type);
        CREATE INDEX namespace_source ON namespace(source_path);
        CREATE INDEX namespace_modified ON namespace(modified_ns DESC);
        CREATE INDEX namespace_hash ON namespace(content_hash);
        CREATE VIRTUAL TABLE namespace_fts USING fts5(
          name, namespace_path, source_path, metadata,
          tokenize='unicode61'
        );
        CREATE TABLE symbols (
          symbol_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          symbol_kind TEXT NOT NULL,
          source_path TEXT NOT NULL,
          line_number INTEGER NOT NULL
        );
        CREATE INDEX symbols_name ON symbols(name);
        CREATE VIRTUAL TABLE symbols_fts USING fts5(name, source_path);
        CREATE TABLE relationships (
          source_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          target_id TEXT NOT NULL,
          evidence TEXT NOT NULL,
          PRIMARY KEY(source_id, relation, target_id)
        );
        CREATE INDEX relationships_relation ON relationships(relation);
        CREATE TABLE capabilities (
          name TEXT PRIMARY KEY,
          arguments_json TEXT NOT NULL,
          schema_name TEXT,
          description TEXT NOT NULL,
          permission TEXT
        );
        CREATE VIRTUAL TABLE capability_fts USING fts5(name, description);
        CREATE TABLE memories (
          memory_id TEXT PRIMARY KEY,
          path TEXT NOT NULL,
          line_number INTEGER NOT NULL,
          kind TEXT,
          domain TEXT,
          task TEXT,
          round_name TEXT,
          strategy TEXT,
          outcome TEXT,
          strength TEXT,
          authority TEXT,
          record_sha256 TEXT NOT NULL
        );
        CREATE INDEX memories_subject ON memories(domain, task, line_number DESC);
        CREATE TABLE temporal_events (
          sequence INTEGER PRIMARY KEY,
          target TEXT NOT NULL,
          value TEXT NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE continuity_state (
          phase INTEGER PRIMARY KEY,
          status TEXT NOT NULL,
          finding TEXT NOT NULL
        );
        CREATE TABLE component_sources (
          name TEXT PRIMARY KEY,
          path TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          bytes INTEGER NOT NULL,
          integrity TEXT NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sync_overrides (
          source_path TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          size_bytes INTEGER,
          modified_ns INTEGER,
          content_sha256 TEXT,
          content TEXT,
          updated_unix_ns INTEGER NOT NULL
        );
        CREATE VIRTUAL TABLE sync_fts USING fts5(source_path, content);
        CREATE TABLE sync_tombstones (
          source_path TEXT PRIMARY KEY,
          deleted_unix_ns INTEGER NOT NULL
        );
        CREATE TABLE sync_invalidations (
          source_path TEXT PRIMARY KEY,
          reason TEXT NOT NULL,
          invalidated_unix_ns INTEGER NOT NULL
        );
        CREATE TABLE sync_runs (
          run_id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_unix_ns INTEGER NOT NULL,
          completed_unix_ns INTEGER NOT NULL,
          changed_files INTEGER NOT NULL,
          new_files INTEGER NOT NULL,
          deleted_files INTEGER NOT NULL,
          result_json TEXT NOT NULL
        );
        """
    )

    with _ro(phase1) as source:
        phase1_meta = dict(source.execute("SELECT key, value FROM metadata"))
        entry_rows = source.execute("SELECT * FROM entries ORDER BY path").fetchall()
    source_root = Path(phase1_meta["root"]).resolve(strict=True)
    for row in entry_rows:
        kind = "file" if row["entry_type"] == "file" else "folder" if row["entry_type"] == "directory" else str(row["entry_type"])
        _add_namespace(
            connection,
            object_id=_id(kind, str(row["path"])),
            object_type=kind,
            name=str(row["filename"]),
            source_path=str(row["path"]),
            content_hash=row["content_sha256"],
            modified_ns=int(row["modified_ns"]),
            source_table="phase1.entries",
            source_id=str(row["path"]),
            metadata={
                "size_bytes": int(row["size_bytes"]),
                "extension": str(row["extension"]),
                "hash_status": str(row["hash_status"]),
            },
        )

    with _ro(phase2) as source:
        python_documents = source.execute(
            "SELECT path, content FROM documents WHERE path LIKE '%.py' ORDER BY path"
        ).fetchall()
    symbol_count = 0
    for row in python_documents:
        try:
            tree = ast.parse(str(row["content"]))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            path = str(row["path"])
            locator = f"line:{node.lineno}"
            symbol_id = _id("code_symbol", path, locator)
            connection.execute(
                "INSERT INTO symbols VALUES (?, ?, ?, ?, ?)",
                (symbol_id, node.name, kind, path, node.lineno),
            )
            symbol_rowid = connection.execute(
                "SELECT rowid FROM symbols WHERE symbol_id=?", (symbol_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO symbols_fts(rowid, name, source_path) VALUES (?, ?, ?)",
                (symbol_rowid, node.name, path),
            )
            _add_namespace(
                connection,
                object_id=symbol_id,
                object_type="code_symbol",
                name=node.name,
                source_path=path,
                locator=locator,
                source_table="phase2.documents.ast",
                source_id=symbol_id,
                metadata={"symbol_kind": kind, "line_number": node.lineno},
            )
            symbol_count += 1

    with _ro(advanced) as source:
        object_rows = source.execute("SELECT * FROM objects ORDER BY object_id").fetchall()
        relation_rows = source.execute("SELECT * FROM relationships").fetchall()
        capability_rows = source.execute("SELECT * FROM capabilities ORDER BY name").fetchall()
        memory_rows = source.execute("SELECT * FROM memories ORDER BY memory_id").fetchall()
        temporal_rows = source.execute("SELECT * FROM temporal_events ORDER BY sequence").fetchall()
        continuity_rows = source.execute("SELECT * FROM continuity_state ORDER BY phase").fetchall()
    for row in object_rows:
        if row["object_type"] == "file":
            continue
        metadata = json.loads(str(row["metadata_json"]))
        _add_namespace(
            connection,
            object_id=str(row["object_id"]),
            object_type=str(row["object_type"]),
            name=str(row["name"]),
            source_path=str(row["path"]),
            locator=str(row["locator"]),
            status=row["status"],
            project=row["project"],
            modified_ns=row["modified_ns"],
            authority=row["authority"],
            source_table="advanced.objects",
            source_id=str(row["object_id"]),
            metadata=metadata,
        )
    connection.executemany(
        "INSERT INTO relationships VALUES (?, ?, ?, ?)",
        [(row["source_id"], row["relation"], row["target_id"], row["evidence"]) for row in relation_rows],
    )
    connection.executemany(
        "INSERT INTO capabilities VALUES (?, ?, ?, ?, ?)",
        [(row["name"], row["arguments_json"], row["schema_name"], row["description"], row["permission"]) for row in capability_rows],
    )
    connection.executemany(
        "INSERT INTO capability_fts VALUES (?, ?)",
        [
            (
                row["name"],
                f"{row['description']} {' '.join(json.loads(str(row['arguments_json'])))}",
            )
            for row in capability_rows
        ],
    )
    connection.executemany(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [tuple(row) for row in memory_rows],
    )
    connection.executemany(
        "INSERT INTO temporal_events VALUES (?, ?, ?, ?)",
        [tuple(row) for row in temporal_rows],
    )
    connection.executemany(
        "INSERT INTO continuity_state VALUES (?, ?, ?)",
        [tuple(row) for row in continuity_rows],
    )
    for row in memory_rows:
        _add_namespace(
            connection,
            object_id=str(row["memory_id"]),
            object_type="memory",
            name=str(row["round_name"] or row["task"] or row["memory_id"]),
            source_path=str(row["path"]),
            locator=f"line:{row['line_number']}",
            status=row["outcome"],
            authority=row["authority"],
            content_hash=str(row["record_sha256"]),
            source_table="advanced.memories",
            source_id=str(row["memory_id"]),
            metadata={"kind": row["kind"], "domain": row["domain"], "task": row["task"], "strength": row["strength"]},
        )

    authority_files = (
        ("approval", "core/approval.py", "approval mechanism"),
        ("envelope", "ui/local_adapter/goya_envelope.py", "operating envelope mechanism"),
        ("budget", "ui/local_adapter/provider_budget.py", "provider budget mechanism"),
    )
    for kind, path, name in authority_files:
        entry = next((row for row in entry_rows if row["path"] == path), None)
        if entry is None:
            continue
        _add_namespace(
            connection,
            object_id=_id(kind, path),
            object_type=kind,
            name=name,
            source_path=path,
            content_hash=entry["content_sha256"],
            modified_ns=int(entry["modified_ns"]),
            authority="mechanism_only_not_grant",
            source_table="phase1.entries",
            source_id=path,
            metadata={"derived_from": "exact_repository_path", "permission_granted": False},
        )

    rows = connection.execute("SELECT rowid, name, namespace_path, source_path, metadata_json FROM namespace ORDER BY rowid").fetchall()
    connection.executemany(
        "INSERT INTO namespace_fts(rowid, name, namespace_path, source_path, metadata) VALUES (?, ?, ?, ?, ?)",
        [(row["rowid"], row["name"], row["namespace_path"], row["source_path"], row["metadata_json"]) for row in rows],
    )
    for name, path in inputs.items():
        with _ro(path) as check:
            integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
        connection.execute(
            "INSERT INTO component_sources VALUES (?, ?, ?, ?, ?)",
            (name, str(path.resolve()), _sha256(path), path.stat().st_size, integrity),
        )
    spec = HERE / "benchmark_spec.json"
    created_ns = time.time_ns()
    metadata = {
        "schema": SCHEMA,
        "source_root": str(source_root),
        "created_unix_ns": str(created_ns),
        "generation_id": f"build:{created_ns}",
        "authority_granted": "none",
        "source_of_truth": "underlying GOYA objects",
        "benchmark_spec_sha256": _sha256(spec) if spec.is_file() else "UNAVAILABLE",
        "secret_contents_indexed_by_catalog": "false",
    }
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
    connection.commit()
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    counts = dict(connection.execute("SELECT object_type, count(*) FROM namespace GROUP BY object_type").fetchall())
    namespace_count = connection.execute("SELECT count(*) FROM namespace").fetchone()[0]
    relation_count = connection.execute("SELECT count(*) FROM relationships").fetchone()[0]
    connection.close()
    if integrity != "ok":
        raise RuntimeError("catalog_integrity_failed")
    with _catalog_lock(output, exclusive=True):
        temporary.replace(output)
    return {
        "schema": SCHEMA,
        "database": str(output.resolve()),
        "database_bytes": output.stat().st_size,
        "build_ms": round((time.perf_counter() - started) * 1000, 3),
        "namespace_objects": namespace_count,
        "object_type_counts": counts,
        "relationships": relation_count,
        "capabilities": len(capability_rows),
        "memories": len(memory_rows),
        "code_symbols": symbol_count,
        "source_root": str(source_root),
        "authority_granted": "none",
        "integrity": integrity,
    }


def _fts_expression(query: str) -> str:
    tokens = list(dict.fromkeys(_search_lexemes(query)))
    if not tokens:
        raise ValueError("query_has_no_tokens")
    return " OR ".join(f'"{token}"' for token in tokens)


def _literal_fts_expression(query: str) -> str:
    tokens = list(dict.fromkeys(token.lower() for token in TOKEN.findall(query)))
    if not tokens:
        raise ValueError("query_has_no_tokens")
    return " OR ".join(f'"{token}"' for token in tokens)


def _search_lexemes(value: str) -> list[str]:
    """Split prose, snake_case and CamelCase while dropping question noise."""
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    expanded = expanded.replace("_", " ").replace("-", " ").replace("/", " ")
    tokens = [token.lower() for token in re.findall(r"[A-Za-z]+|\d+", expanded)]
    meaningful = [
        token for token in tokens
        if token not in QUERY_STOPWORDS and (len(token) > 1 or token.isdigit())
    ]
    expanded_meaningful = list(meaningful)
    for token in meaningful:
        expanded_meaningful.extend(LEXICAL_CONCEPT_EXPANSIONS.get(token, ()))
    meaningful = list(dict.fromkeys(expanded_meaningful))
    return meaningful or [token.lower() for token in TOKEN.findall(value)]


def _bounded_rerank_contents(
    connection: sqlite3.Connection,
    *,
    table: str,
    path_column: str,
    paths: list[str],
    query: str,
) -> dict[str, str]:
    """Read bounded windows near a query match, never whole candidate files."""
    if (table, path_column) not in {
        ("documents", "path"),
        ("sync_overrides", "source_path"),
    }:
        raise ValueError("invalid_rerank_source")
    if not paths:
        return {}
    exact = query.strip().lower()
    tokens = [token.lower() for token in TOKEN.findall(query)]
    anchor = max(tokens, key=len) if tokens else exact
    half = LEXICAL_RERANK_CHARS // 2
    statement = (
        f"SELECT {path_column}, substr(content, "
        "CASE "
        "WHEN instr(lower(content), ?) > 0 "
        "THEN max(1, instr(lower(content), ?) - ?) "
        "WHEN instr(lower(content), ?) > 0 "
        "THEN max(1, instr(lower(content), ?) - ?) "
        "ELSE 1 END, ?) "
        f"FROM {table} WHERE {path_column} IN ("
        + ",".join("?" for _ in paths)
        + ")"
    )
    return {
        str(path): str(content or "")
        for path, content in connection.execute(
            statement,
            (exact, exact, half, anchor, anchor, half, LEXICAL_RERANK_CHARS, *paths),
        )
    }


def lexical_search(
    database: Path,
    query: str,
    limit: int = 20,
    *,
    catalog: Path | None = None,
) -> list[dict[str, object]]:
    with _ro(database) as connection:
        rows = connection.execute(
            """
            SELECT d.path, bm25(documents_fts, 5.0, 1.0) AS score
            FROM documents_fts JOIN documents d ON d.rowid=documents_fts.rowid
            WHERE documents_fts MATCH ? ORDER BY score, d.path LIMIT ?
            """,
            (_fts_expression(query), max(limit * 4, 200)),
        ).fetchall()
        rerank_paths = [
            str(row["path"])
            for row in rows[: max(limit, LEXICAL_RERANK_DOCUMENTS)]
        ]
        base_contents = _bounded_rerank_contents(
            connection,
            table="documents",
            path_column="path",
            paths=rerank_paths,
            query=query,
        )
    base = [
        {
            "path": str(row["path"]),
            "content": base_contents.get(str(row["path"]), ""),
            "score": float(row["score"]),
        }
        for row in rows
    ]
    if catalog is None:
        return [{"path": row["path"], "score": row["score"]} for row in base[:limit]]
    with _ro(catalog) as connection:
        replaced = {str(row[0]) for row in connection.execute("SELECT source_path FROM sync_overrides")}
        replaced.update(str(row[0]) for row in connection.execute("SELECT source_path FROM sync_tombstones"))
        overlay_rows = connection.execute(
            """
            SELECT source_path, bm25(sync_fts, 5.0, 1.0) AS score
            FROM sync_fts WHERE sync_fts MATCH ? ORDER BY score, source_path LIMIT ?
            """,
            (_fts_expression(query), max(limit * 4, 200)),
        ).fetchall()
        overlay_rerank_paths = [
            str(row["source_path"])
            for row in overlay_rows[: max(limit, LEXICAL_RERANK_DOCUMENTS)]
        ]
        overlay_contents = _bounded_rerank_contents(
            connection,
            table="sync_overrides",
            path_column="source_path",
            paths=overlay_rerank_paths,
            query=query,
        )
    base = [row for row in base if str(row["path"]) not in replaced]
    overlay = [
        {
            "path": str(row["source_path"]),
            "content": overlay_contents.get(str(row["source_path"]), ""),
            "score": float(row["score"]),
        }
        for row in overlay_rows
    ]
    query_tokens = list(dict.fromkeys(_search_lexemes(query)))
    query_token_set = set(query_tokens)
    phrase = query.lower()
    candidate_rows = overlay + base
    document_frequency = {token: 0 for token in query_tokens}
    for row in candidate_rows:
        searchable = f"{row['path']}\n{row['content']}".lower()
        for token in query_tokens:
            if token in searchable:
                document_frequency[token] += 1
    ranked: list[tuple[float, str]] = []
    for position, row in enumerate(candidate_rows, start=1):
        path = str(row["path"])
        content_lower = str(row["content"]).lower()
        path_lower = path.lower()
        rare_coverage = sum(
            math.log((len(candidate_rows) + 1) / (document_frequency[token] + 1)) + 1.0
            for token in query_tokens
            if token in content_lower or token in path_lower
        )
        path_terms = set(_search_lexemes(path))
        filename_terms = set(_search_lexemes(Path(path).stem))
        path_hits = len(query_token_set & path_terms)
        filename_hits = len(query_token_set & filename_terms)
        exact = int(phrase in content_lower or phrase in path_lower)
        score = (
            exact * 2.0
            + 2.0 / (10 + position)
            + 0.055 * path_hits
            + 0.035 * filename_hits
            + 0.005 * rare_coverage
        )
        asks_for_test = bool(query_token_set & {"test", "tests", "testing"})
        is_test = (
            path.startswith("tests/")
            or "/test_" in path
            or Path(path).name.startswith("test_")
        )
        if asks_for_test and is_test:
            score += 0.05
        asks_for_code = bool(
            query_token_set
            & {
                "class", "classes", "code", "defined", "function", "functions",
                "handles", "implementation", "implement", "method", "methods",
                "module", "turn",
            }
        )
        if asks_for_code and path_lower.endswith(".py"):
            score += 0.04
        if asks_for_code and is_test and not asks_for_test:
            score -= 0.06
        asks_for_proposal = bool(
            query_token_set & {"proposal", "authorize", "authorizes", "status"}
        )
        if asks_for_proposal and path_lower.endswith(".md"):
            score += 0.03
        noisy_history = path.startswith(
            (
                "artifacts/goya_sessions/",
                "artifacts/goya_corpus/",
                "artifacts/goya_lora/",
            )
        )
        asks_for_history = bool(
            query_token_set & {"corpus", "training", "lora", "transcript"}
            or (
                query_token_set & {"session", "sessions"}
                and query_token_set
                & {"chat", "conversation", "history", "record", "records"}
            )
        )
        if noisy_history and not asks_for_history:
            score -= 0.12
        ranked.append((score, path))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"path": path, "score": score}
        for score, path in ranked[:limit]
    ]


def symbol_search(catalog: Path, query: str, limit: int = 20) -> list[dict[str, object]]:
    raw_tokens = TOKEN.findall(query)
    quoted_identifiers = [
        token
        for block in re.findall(r"`([^`]+)`", query)
        for token in TOKEN.findall(block)
    ]
    identifier_tokens = []
    for token in raw_tokens + quoted_identifiers:
        if not (
            token in quoted_identifiers
            or "_" in token
            or re.search(r"[a-z][A-Z]", token)
            or (re.search(r"[A-Z]", token) and re.search(r"\d", token))
        ):
            continue
        lowered = token.lower()
        identifier_tokens.append(lowered)
    identifier_tokens = list(dict.fromkeys(identifier_tokens))
    if identifier_tokens:
        with _ro(catalog) as connection:
            identifier_rows = connection.execute(
                "SELECT * FROM symbols WHERE lower(name) IN (%s) "
                "ORDER BY source_path, name, line_number"
                % ",".join("?" for _ in identifier_tokens),
                identifier_tokens,
            ).fetchall()
            matched = {str(row["name"]).lower() for row in identifier_rows}
            fallback_tokens = [
                token.lstrip("_")
                for token in identifier_tokens
                if token.startswith("_")
                and token not in matched
                and len(token.lstrip("_")) >= 3
            ]
            if fallback_tokens:
                retry_tokens = list(dict.fromkeys(identifier_tokens + fallback_tokens))
                identifier_rows = connection.execute(
                    "SELECT * FROM symbols WHERE lower(name) IN (%s) "
                    "ORDER BY source_path, name, line_number"
                    % ",".join("?" for _ in retry_tokens),
                    retry_tokens,
                ).fetchall()
        if identifier_rows:
            matched_names: dict[str, set[str]] = {}
            first: dict[str, sqlite3.Row] = {}
            for row in identifier_rows:
                path = str(row["source_path"])
                matched_names.setdefault(path, set()).add(str(row["name"]).lower())
                first.setdefault(path, row)
            counts = {path: len(names) for path, names in matched_names.items()}
            ordered_paths = sorted(counts, key=lambda path: (-counts[path], path))
            result = []
            for path in ordered_paths[:limit]:
                value = dict(first[path])
                value["symbol_match_count"] = counts[path]
                value["symbol_match_kind"] = "implicit_identifier"
                result.append(value)
            return result
        # A marker may name a plain function while later query terms contain
        # snake-case constants ("def snapshot MONTHLY_CAP ..."). Preserve that
        # exact function lookup, but never use the word before the marker. The
        # old backward fallback turned "Find class MissingIdentifier" into a
        # fuzzy search for unrelated `_find*` functions.
        marker_tokens = list(
            dict.fromkeys(
                raw_tokens[index + 1]
                for index in range(len(raw_tokens) - 1)
                if raw_tokens[index].lower()
                in {"def", "class", "function", "method"}
            )
        )
        if marker_tokens:
            with _ro(catalog) as connection:
                exact_markers = connection.execute(
                    "SELECT * FROM symbols WHERE lower(name) IN (%s) "
                    "ORDER BY name, source_path, line_number LIMIT ?"
                    % ",".join("?" for _ in marker_tokens),
                    (*[token.lower() for token in marker_tokens], limit),
                ).fetchall()
            if exact_markers:
                result = []
                for row in exact_markers:
                    value = dict(row)
                    value["symbol_match_kind"] = "explicit_marker"
                    result.append(value)
                return result
        return []
    marker_positions = [
        index for index, token in enumerate(raw_tokens[:-1])
        if token.lower() in {"def", "class", "function", "method"}
    ]
    if not marker_positions:
        return []
    tokens = []
    for index in marker_positions:
        for candidate_index in (index + 1, index - 1):
            if not 0 <= candidate_index < len(raw_tokens):
                continue
            candidate = raw_tokens[candidate_index]
            if candidate.lower() in QUERY_STOPWORDS:
                continue
            if candidate_index < index and not (
                "_" in candidate
                or candidate[:1].isupper()
                or re.search(r"[a-z][A-Z]", candidate)
            ):
                continue
            tokens.append(candidate)
    tokens = list(dict.fromkeys(tokens))
    if not tokens:
        return []
    with _ro(catalog) as connection:
        exact = connection.execute(
            "SELECT * FROM symbols WHERE name IN (%s) ORDER BY name, source_path, line_number LIMIT ?"
            % ",".join("?" for _ in tokens),
            (*tokens, limit),
        ).fetchall()
        if exact:
            result = []
            for row in exact:
                value = dict(row)
                value["symbol_match_kind"] = "explicit_marker"
                result.append(value)
            return result
        rows = connection.execute(
            """
            SELECT s.* FROM symbols_fts f JOIN symbols s ON s.rowid=f.rowid
            WHERE symbols_fts MATCH ? ORDER BY bm25(symbols_fts, 5.0, 1.0), s.source_path LIMIT ?
            """,
            (_literal_fts_expression(" ".join(tokens)), limit),
        ).fetchall()
    return [dict(row) for row in rows]


def namespace_search(database: Path, query: str, limit: int = 20) -> list[dict[str, object]]:
    with _ro(database) as connection:
        rows = connection.execute(
            """
            SELECT n.*, bm25(namespace_fts, 4.0, 2.0, 2.0, 1.0) AS score
            FROM namespace_fts JOIN namespace n ON n.rowid=namespace_fts.rowid
            WHERE namespace_fts MATCH ? ORDER BY score, n.namespace_path LIMIT ?
            """,
            (_fts_expression(query), limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _component_path(catalog: Path, name: str, fallback: Path) -> Path:
    with _ro(catalog) as connection:
        row = connection.execute(
            "SELECT path FROM component_sources WHERE name=?", (name,)
        ).fetchone()
    if row is None:
        return fallback
    path = Path(str(row[0]))
    return path if path.is_file() else fallback


def _unified_search_unlocked(
    query: str,
    *,
    catalog: Path = DEFAULT_INDEX,
    lexical: Path | None = None,
    semantic: Path | None = None,
    limit: int = 20,
    use_lexical: bool = True,
    use_semantic: bool = False,
    use_namespace: bool = True,
    use_symbols: bool = True,
) -> list[dict[str, object]]:
    def unique_paths(paths: list[str]) -> list[str]:
        return list(dict.fromkeys(paths))

    rankings: list[tuple[float, list[str]]] = []
    symbol_counts_by_path: dict[str, int] = {}
    if use_symbols:
        symbol_rows = symbol_search(catalog, query, 50)
        if symbol_rows:
            for row in symbol_rows:
                path = str(row["source_path"])
                symbol_counts_by_path[path] = max(
                    symbol_counts_by_path.get(path, 0),
                    int(row.get("symbol_match_count", 1)),
                )
            symbol_weight = (
                5.0
                if any(
                    row.get("symbol_match_kind")
                    in {"implicit_identifier", "explicit_marker"}
                    for row in symbol_rows
                )
                else 1.9
            )
            rankings.append((symbol_weight, unique_paths([str(row["source_path"]) for row in symbol_rows])))
    if use_lexical:
        lexical_database = lexical or _component_path(catalog, "phase2", DEFAULT_PHASE2)
        rankings.append((2.0, unique_paths([str(row["path"]) for row in lexical_search(lexical_database, query, 50, catalog=catalog)])))
    if use_semantic:
        raise ValueError("semantic_search_not_in_portable_release")
    namespace_rows = namespace_search(catalog, query, 50) if use_namespace else []
    if namespace_rows:
        rankings.append((0.02, unique_paths([str(row["source_path"]) for row in namespace_rows])))
    scores: dict[str, float] = {}
    for weight, ranking in rankings:
        for position, path in enumerate(ranking, start=1):
            scores[path] = scores.get(path, 0.0) + weight / (60 + position)
    for path, count in symbol_counts_by_path.items():
        if path in scores and count > 1:
            scores[path] += 0.01 * (count - 1)
    query_path_tokens = {
        token.lower()
        for token in TOKEN.findall(query.replace("_", " "))
        if token.lower() not in {
            "a", "an", "and", "class", "def", "for", "from", "function",
            "in", "method", "of", "on", "only", "the", "to", "using", "with",
        }
    }
    for path in list(scores):
        path_tokens = {token.lower() for token in TOKEN.findall(path.replace("_", "/"))}
        scores[path] += 0.0004 * len(query_path_tokens & path_tokens)
    ordered = sorted(scores, key=lambda path: (-scores[path], path))
    with _ro(catalog) as connection:
        result = []
        for path in ordered:
            row = connection.execute(
                "SELECT * FROM namespace WHERE source_path=? ORDER BY CASE object_type WHEN 'file' THEN 0 ELSE 1 END LIMIT 1",
                (path,),
            ).fetchone()
            item = dict(row) if row else {"source_path": path, "object_type": "document", "namespace_path": None}
            item["score"] = scores[path]
            if item.get("status") == "deleted":
                continue
            if item.get("object_type") == "file":
                verification = verify_source(catalog, path)
                item["source_verification"] = verification
                if not verification["verified"]:
                    continue
            result.append(item)
            if len(result) >= limit:
                break
    return result


def unified_search(
    query: str,
    *,
    catalog: Path = DEFAULT_INDEX,
    lexical: Path | None = None,
    semantic: Path | None = None,
    limit: int = 20,
    use_lexical: bool = True,
    use_semantic: bool = False,
    use_namespace: bool = True,
    use_symbols: bool = True,
) -> list[dict[str, object]]:
    """Search one catalog generation while maintenance writers are excluded."""
    with _catalog_lock(catalog, exclusive=False):
        return _unified_search_unlocked(
            query,
            catalog=catalog,
            lexical=lexical,
            semantic=semantic,
            limit=limit,
            use_lexical=use_lexical,
            use_semantic=use_semantic,
            use_namespace=use_namespace,
            use_symbols=use_symbols,
        )


def _requested_code_identifiers(query: str) -> list[str]:
    """Extract identifiers the user explicitly presents as code symbols."""
    raw = TOKEN.findall(query)
    requested: list[str] = []
    for block in re.findall(r"`([^`]+)`", query):
        requested.extend(TOKEN.findall(block))
    for index, token in enumerate(raw[:-1]):
        if token.lower() in {"class", "def", "function", "method"}:
            requested.append(raw[index + 1])
    for token in raw:
        if (
            "_" in token
            or re.search(r"[a-z][A-Z]", token)
            or (re.search(r"[A-Z]", token) and re.search(r"\d", token))
        ):
            requested.append(token)
    return list(dict.fromkeys(requested))


def current_source_resolution(
    catalog: Path, source_path: str, *, maximum_hops: int = 32
) -> dict[str, object]:
    """Follow explicit supersession links and fail closed on forks or cycles."""
    current = source_path
    chain = [current]
    for _ in range(maximum_hops):
        targets = relationship_targets(catalog, current, "superseded_by")
        if not targets:
            verification = verify_source(catalog, current)
            return {
                "status": "current" if verification.get("verified") else "unavailable",
                "source_path": current,
                "chain": chain,
                "source_verification": verification,
            }
        if len(targets) != 1:
            return {
                "status": "clarify",
                "reason": "supersession_fork",
                "source_path": current,
                "candidates": targets,
                "chain": chain,
            }
        current = targets[0]
        if current in chain:
            return {
                "status": "unavailable",
                "reason": "supersession_cycle",
                "source_path": current,
                "chain": chain + [current],
            }
        chain.append(current)
    return {
        "status": "unavailable",
        "reason": "supersession_hop_limit",
        "source_path": current,
        "chain": chain,
    }


def retrieval_decision(
    query: str,
    *,
    catalog: Path = DEFAULT_INDEX,
    limit: int = 5,
) -> dict[str, object]:
    """Make uncertainty explicit before a chat model sees retrieval evidence.

    This layer does not grant authority and does not ask a model to invent a
    confidence score. Exact temporal state and code symbols are handled
    mechanically. Vague, duplicated, or absent identifiers fail closed into a
    clarification or not-found result.
    """
    lexemes = _search_lexemes(query)
    identifiers = _requested_code_identifiers(query)

    if "current" in query.lower():
        normalized = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
        with _ro(catalog) as connection:
            targets = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT target FROM temporal_events ORDER BY target"
                )
            ]
        matched_targets = [
            target
            for target in targets
            if target.lower() in normalized
            or target.lower().replace("_", " ") in query.lower()
        ]
        if len(matched_targets) == 1:
            target = matched_targets[0]
            return {
                "status": "answer",
                "kind": "current_temporal_value",
                "target": target,
                "value": current_temporal_value(catalog, target),
                "authority_granted": "none",
            }
        if len(matched_targets) > 1:
            return {
                "status": "clarify",
                "reason": "multiple_temporal_targets",
                "candidates": matched_targets,
                "authority_granted": "none",
            }

    symbol_rows = symbol_search(catalog, query, max(50, limit))
    if identifiers:
        if not symbol_rows:
            return {
                "status": "not_found",
                "reason": "explicit_identifier_absent",
                "identifiers": identifiers,
                "authority_granted": "none",
            }
        paths = list(dict.fromkeys(str(row["source_path"]) for row in symbol_rows))
        counts: dict[str, int] = {}
        for row in symbol_rows:
            path = str(row["source_path"])
            counts[path] = max(counts.get(path, 0), int(row.get("symbol_match_count", 1)))
        best_count = max(counts.values())
        best = sorted(path for path, count in counts.items() if count == best_count)
        if len(best) != 1:
            return {
                "status": "clarify",
                "reason": "identifier_has_multiple_definitions",
                "identifiers": identifiers,
                "candidates": best[:limit],
                "authority_granted": "none",
            }
        return {
            "status": "answer",
            "kind": "code_symbol",
            "source_path": best[0],
            "identifiers": identifiers,
            "authority_granted": "none",
        }

    if len(lexemes) <= 1 or set(lexemes).issubset(
        {"find", "it", "status", "thing", "where"}
    ):
        return {
            "status": "clarify",
            "reason": "query_too_ambiguous",
            "authority_granted": "none",
        }

    rows = unified_search(query, catalog=catalog, limit=limit)
    if not rows:
        return {
            "status": "not_found",
            "reason": "no_verified_candidates",
            "authority_granted": "none",
        }
    top = float(rows[0].get("score", 0.0))
    runner_up = float(rows[1].get("score", 0.0)) if len(rows) > 1 else 0.0
    if top < 0.04 or top - runner_up < 0.002:
        return {
            "status": "clarify",
            "reason": "retrieval_confidence_insufficient",
            "candidates": [str(row["source_path"]) for row in rows],
            "authority_granted": "none",
        }
    return {
        "status": "answer",
        "kind": "retrieval",
        "source_path": str(rows[0]["source_path"]),
        "candidates": [str(row["source_path"]) for row in rows],
        "authority_granted": "none",
    }


def _evidence_excerpt(content: str, query: str, budget: int) -> str:
    """Return a bounded window around the strongest literal query term."""
    if budget <= 0:
        return ""
    terms = sorted(
        {
            token
            for token in TOKEN.findall(query)
            if len(token) >= 3
            and token.lower()
            not in {
                "and", "class", "defines", "find", "for", "from", "function",
                "method", "source", "the", "this", "using", "where", "which",
                "with",
            }
        },
        key=lambda value: (-len(value), value.lower()),
    )
    lowered = content.lower()
    positions = [lowered.find(term.lower()) for term in terms]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - budget // 3)
    end = min(len(content), start + budget)
    start = max(0, end - budget)
    if start:
        newline = content.find("\n", start, min(end, start + 160))
        if newline >= 0:
            start = newline + 1
    if end < len(content):
        newline = content.rfind("\n", max(start, end - 160), end)
        if newline > start:
            end = newline
    return content[start:end]


def retrieval_evidence_packet(
    query: str,
    *,
    catalog: Path = DEFAULT_INDEX,
    max_chars: int = 4_000,
    limit: int = 5,
) -> dict[str, object]:
    """Build a verified, rank-explicit context packet for a model.

    Rank 1 receives most of the budget. Lower-ranked candidates are included
    only behind explicit separators so clipping cannot blend two sources.
    """
    if max_chars < 256:
        raise ValueError("max_chars_below_minimum")
    if limit < 1:
        raise ValueError("limit_below_minimum")
    rows = unified_search(query, catalog=catalog, limit=limit)
    preamble = (
        "VERIFIED RETRIEVAL EVIDENCE — candidates are ranked. "
        "Prefer RANK 1 unless its excerpt directly contradicts the query.\n"
    )
    parts = [preamble]
    included: list[dict[str, object]] = []
    remaining = max_chars - len(preamble)
    for position, row in enumerate(rows, start=1):
        source_path = str(row["source_path"])
        read = read_verified_source(catalog, source_path)
        if not read.get("verified"):
            continue
        separator = "\n---\n" if included else ""
        header = (
            f"{separator}RANK {position} | SOURCE {source_path} | "
            f"SHA256 {read['content_sha256']}\n"
        )
        reserve = len(header)
        if remaining - reserve < 192:
            break
        if not included:
            excerpt_budget = min(remaining - reserve, max(900, int(max_chars * 0.70)))
        else:
            slots = max(1, limit - len(included))
            excerpt_budget = min(900, (remaining - reserve) // slots)
        if excerpt_budget < 192:
            break
        excerpt = _evidence_excerpt(str(read["content"]), query, excerpt_budget)
        block = header + excerpt.rstrip() + "\n"
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        included.append(
            {
                "rank": position,
                "source_path": source_path,
                "score": row.get("score"),
                "content_sha256": read["content_sha256"],
            }
        )
        remaining -= len(block)
        if remaining < 256:
            break
    packet = "".join(parts)
    return {
        "query": query,
        "packet": packet,
        "packet_chars": len(packet),
        "packet_sha256": hashlib.sha256(packet.encode("utf-8")).hexdigest(),
        "candidates": included,
    }


def verify_source(catalog: Path, source_path: str) -> dict[str, object]:
    with _ro(catalog) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        row = connection.execute(
            "SELECT * FROM namespace WHERE source_path=? AND object_type='file' LIMIT 1",
            (source_path,),
        ).fetchone()
    if row is None:
        return {"verified": False, "reason": "not_a_file_object"}
    if row["status"] == "deleted":
        return {"verified": False, "reason": "source_deleted"}
    path = Path(metadata["source_root"]) / source_path
    try:
        stat = path.stat()
    except OSError:
        return {"verified": False, "reason": "source_unavailable"}
    expected = json.loads(str(row["metadata_json"]))
    same = stat.st_size == expected.get("size_bytes") and stat.st_mtime_ns == row["modified_ns"]
    result = {
        "verified": bool(same),
        "reason": "metadata_match" if same else "source_changed",
        "path": str(path),
    }
    if not same and _is_volatile_source(source_path):
        result.update(
            _volatile_unavailable(source_path, row["modified_ns"], stat.st_mtime_ns)
        )
    return result


def read_verified_source(
    catalog: Path,
    source_path: str,
    *,
    max_bytes: int = CONTENT_MAX_BYTES,
) -> dict[str, object]:
    """Read one indexed source through an identity-stable, secret-safe boundary."""
    with _ro(catalog) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        row = connection.execute(
            "SELECT * FROM namespace WHERE source_path=? AND object_type='file' LIMIT 1",
            (source_path,),
        ).fetchone()
    if row is None:
        return {"verified": False, "reason": "not_a_file_object"}
    if row["status"] == "deleted":
        return {"verified": False, "reason": "source_deleted"}
    if SENSITIVE_PATH.search(source_path):
        return {"verified": False, "reason": "sensitive_path_refused"}
    path = Path(metadata["source_root"]) / source_path
    try:
        stat = path.stat()
    except OSError:
        return {"verified": False, "reason": "source_unavailable"}
    expected = json.loads(str(row["metadata_json"]))
    if stat.st_size != expected.get("size_bytes") or stat.st_mtime_ns != row["modified_ns"]:
        result = {"verified": False, "reason": "source_changed", "path": str(path)}
        if _is_volatile_source(source_path):
            result.update(
                _volatile_unavailable(
                    source_path, row["modified_ns"], stat.st_mtime_ns
                )
            )
        return result
    if stat.st_size > max_bytes:
        return {"verified": False, "reason": "source_too_large", "path": str(path)}
    payload, status = _read_stable_payload(path, stat, max_bytes=max_bytes)
    if payload is None:
        return {"verified": False, "reason": status, "path": str(path)}
    if b"\x00" in payload:
        return {"verified": False, "reason": "binary", "path": str(path)}
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {"verified": False, "reason": "non_utf8", "path": str(path)}
    if _contains_secret_pattern(content):
        return {"verified": False, "reason": "secret_pattern_refused", "path": str(path)}
    digest = hashlib.sha256(payload).hexdigest()
    if row["content_hash"] is not None and digest != row["content_hash"]:
        return {"verified": False, "reason": "content_hash_mismatch", "path": str(path)}
    return {
        "verified": True,
        "reason": "stable_content_match",
        "path": str(path),
        "source_path": source_path,
        "content": content,
        "content_sha256": digest,
        "bytes": len(payload),
    }


def _incremental_sync_unlocked(catalog: Path = DEFAULT_INDEX) -> dict[str, object]:
    """Refresh live file metadata/content/symbol overlays without touching GOYA.

    Derived typed projections that cannot be refreshed mechanically are marked
    invalid and their readers fail closed until a full rebuild.
    """
    started = time.perf_counter()
    started_ns = time.time_ns()
    connection = sqlite3.connect(catalog)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA synchronous=FULL")
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        root = Path(metadata["source_root"]).resolve(strict=True)
        live = _live_files(root)
        existing_rows = connection.execute(
            "SELECT source_path, status, modified_ns, metadata_json "
            "FROM namespace WHERE object_type='file' ORDER BY source_path"
        ).fetchall()
        existing = {str(row["source_path"]): row for row in existing_rows}
        changed: list[str] = []
        new: list[str] = []
        deleted = sorted(
            relative for relative, row in existing.items()
            if relative not in live and row["status"] != "deleted"
        )
        for relative, stat in live.items():
            row = existing.get(relative)
            if row is None:
                new.append(relative)
                continue
            expected = json.loads(str(row["metadata_json"]))
            if (
                row["status"] == "deleted"
                or stat.st_size != expected.get("size_bytes")
                or stat.st_mtime_ns != row["modified_ns"]
            ):
                changed.append(relative)

        refreshed: list[str] = []
        failed: list[dict[str, str]] = []
        vanished: list[str] = []
        symbols_refreshed = 0
        typed_invalidations: list[str] = []
        typed_refreshed: list[str] = []
        typed_relationships_refreshed = 0
        folders_refreshed: set[str] = set()
        for relative in sorted(changed + new):
            stat = live[relative]
            projection = _safe_file_projection(root, relative, stat)
            if projection["hash_status"] in {
                "unreadable", "changed_before_read", "changed_during_read"
            }:
                if relative in new and not (root / relative).exists():
                    vanished.append(relative)
                    continue
                failed.append({"path": relative, "reason": str(projection["hash_status"])})
                continue
            object_id = _id("file", relative)
            metadata_json = json.dumps(
                {
                    "size_bytes": projection["size_bytes"],
                    "extension": projection["extension"],
                    "hash_status": projection["hash_status"],
                    "content_status": projection["content_status"],
                    "incremental_sync": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO namespace(
                  object_id, namespace_path, object_type, name, source_path,
                  locator, status, project, content_hash, modified_ns,
                  authority, source_table, source_id, metadata_json
                ) VALUES (?, ?, 'file', ?, ?, '', 'current', NULL, ?, ?, NULL,
                          'sync.live', ?, ?)
                ON CONFLICT(object_id) DO UPDATE SET
                  name=excluded.name, status='current', content_hash=excluded.content_hash,
                  modified_ns=excluded.modified_ns, source_table='sync.live',
                  source_id=excluded.source_id, metadata_json=excluded.metadata_json
                """,
                (
                    object_id, _namespace_path("file", relative), Path(relative).name,
                    relative, projection["content_sha256"], projection["modified_ns"],
                    relative, metadata_json,
                ),
            )
            _refresh_namespace_fts(connection, object_id)
            connection.execute("DELETE FROM sync_fts WHERE source_path=?", (relative,))
            connection.execute(
                """
                INSERT INTO sync_overrides(
                  source_path, status, size_bytes, modified_ns, content_sha256,
                  content, updated_unix_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_path) DO UPDATE SET
                  status=excluded.status, size_bytes=excluded.size_bytes,
                  modified_ns=excluded.modified_ns,
                  content_sha256=excluded.content_sha256, content=excluded.content,
                  updated_unix_ns=excluded.updated_unix_ns
                """,
                (
                    relative, projection["content_status"], projection["size_bytes"],
                    projection["modified_ns"], projection["content_sha256"],
                    projection["content"], started_ns,
                ),
            )
            if projection["content"] is not None:
                connection.execute(
                    "INSERT INTO sync_fts(source_path, content) VALUES (?, ?)",
                    (relative, projection["content"]),
                )
            connection.execute("DELETE FROM sync_tombstones WHERE source_path=?", (relative,))
            if relative in new:
                folders_refreshed.update(
                    _refresh_parent_folder_projections(connection, root, relative)
                )
            _remove_symbols_for_path(connection, relative)
            if relative.endswith(".py") and isinstance(projection["content"], str):
                symbols_refreshed += _insert_symbols_for_content(
                    connection, relative, str(projection["content"])
                )
            handled_types = set(_advanced_projection_types(relative))
            capability_handled = False
            memory_handled = False
            if (
                relative == "ui/local_adapter/executor.py"
                and isinstance(projection["content"], str)
                and _table_exists(connection, "capabilities")
            ):
                _refresh_capabilities_projection(
                    connection, relative, str(projection["content"])
                )
                typed_refreshed.append(relative)
                capability_handled = True
                handled_types.update(("tool", "tool_schema"))
            elif (
                relative.startswith((
                    "artifacts/goya_memory/", "artifacts/goya_memory_clean/"
                ))
                and relative.endswith(".jsonl")
                and isinstance(projection["content"], str)
                and _table_exists(connection, "memories")
            ):
                _refresh_memory_projection(
                    connection, relative, str(projection["content"])
                )
                typed_refreshed.append(relative)
                memory_handled = True
                handled_types.add("memory")
            advanced_result = _refresh_advanced_file_projection(
                connection,
                relative,
                str(projection["content"])
                if isinstance(projection["content"], str)
                else None,
                int(projection["modified_ns"]),
            )
            typed_relationships_refreshed += int(advanced_result["relationships"])
            if (
                int(advanced_result["objects"]) > 0
                or int(advanced_result["relationships"]) > 0
                or relative.endswith((".json", ".py"))
                or ADVANCED_PROPOSAL.search(Path(relative).name)
                or ADVANCED_EVIDENCE_NAME.search(Path(relative).name)
                or ADVANCED_OUTPUT_NAME.search(Path(relative).name)
            ):
                typed_refreshed.append(relative)
            derived_types = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT object_type FROM namespace WHERE source_path=? "
                    "AND object_type NOT IN ('file','code_symbol')",
                    (relative,),
                )
            }
            unhandled_types = derived_types - handled_types
            if relative == "ui/local_adapter/executor.py" and not capability_handled:
                unhandled_types.update(("tool", "tool_schema"))
            memory_count = connection.execute(
                "SELECT count(*) FROM memories WHERE path=?", (relative,)
            ).fetchone()[0]
            if memory_count and not memory_handled:
                unhandled_types.add("memory")
            if not unhandled_types:
                connection.execute(
                    "DELETE FROM sync_invalidations WHERE source_path=?", (relative,)
                )
            else:
                typed_invalidations.append(relative)
                connection.execute(
                    """
                    INSERT INTO sync_invalidations VALUES (?, 'source_changed_requires_typed_rebuild', ?)
                    ON CONFLICT(source_path) DO UPDATE SET
                      reason=excluded.reason, invalidated_unix_ns=excluded.invalidated_unix_ns
                    """,
                    (relative, started_ns),
                )
                connection.execute(
                    "UPDATE namespace SET status='stale' WHERE source_path=? "
                    "AND object_type IN (" + ",".join("?" for _ in unhandled_types) + ")",
                    (relative, *sorted(unhandled_types)),
                )
            refreshed.append(relative)

        for relative in deleted:
            connection.execute(
                "UPDATE namespace SET status='deleted' WHERE source_path=?", (relative,)
            )
            connection.execute(
                """
                INSERT INTO sync_tombstones VALUES (?, ?)
                ON CONFLICT(source_path) DO UPDATE SET deleted_unix_ns=excluded.deleted_unix_ns
                """,
                (relative, started_ns),
            )
            connection.execute("DELETE FROM sync_fts WHERE source_path=?", (relative,))
            connection.execute("DELETE FROM sync_overrides WHERE source_path=?", (relative,))
            _delete_advanced_file_projection(connection, relative)
            if (
                relative.startswith((
                    "artifacts/goya_memory/", "artifacts/goya_memory_clean/"
                ))
                and relative.endswith(".jsonl")
                and _table_exists(connection, "memories")
            ):
                _refresh_memory_projection(connection, relative, "")
                typed_refreshed.append(relative)
            if relative == "ui/local_adapter/executor.py" and _table_exists(
                connection, "capabilities"
            ):
                _refresh_capabilities_projection(connection, relative, "")
                typed_refreshed.append(relative)
            ancestor = _experiment_ancestor(relative)
            if ancestor is not None and not (root / ancestor[0]).is_dir():
                experiment_id = _id("experiment", ancestor[0])
                _remove_relationships_for_ids(connection, [experiment_id])
                _delete_namespace_types(connection, ancestor[0], ("experiment",))
            folders_refreshed.update(
                _refresh_parent_folder_projections(connection, root, relative)
            )
            connection.execute(
                "DELETE FROM sync_invalidations WHERE source_path=?", (relative,)
            )
            _remove_symbols_for_path(connection, relative)

        completed_ns = time.time_ns()
        result = {
            "schema": "harness-index.incremental-sync/1",
            "changed_files": len(changed),
            "new_files": len(new),
            "deleted_files": len(deleted),
            "refreshed_files": len(refreshed),
            "failed_files": len(failed),
            "vanished_before_index": len(vanished),
            "symbols_refreshed": symbols_refreshed,
            "typed_projections_refreshed": len(set(typed_refreshed)),
            "typed_relationships_refreshed": typed_relationships_refreshed,
            "typed_invalidations": len(set(typed_invalidations)),
            "folders_refreshed": len(folders_refreshed),
            "changed_paths": sorted(changed)[:100],
            "new_paths": sorted(new)[:100],
            "deleted_paths": deleted[:100],
            "failed": failed[:100],
            "vanished_paths": vanished[:100],
            "authority_granted": "none",
            "source_writes": 0,
        }
        connection.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('last_incremental_sync_unix_ns', ?)",
            (str(completed_ns),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('generation_id', ?)",
            (f"sync:{completed_ns}",),
        )
        connection.execute(
            """
            INSERT INTO sync_runs(
              started_unix_ns, completed_unix_ns, changed_files, new_files,
              deleted_files, result_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                started_ns, completed_ns, len(changed), len(new), len(deleted),
                json.dumps(result, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.commit()
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError("catalog_integrity_failed_after_sync")
        result["integrity"] = integrity
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def incremental_sync(catalog: Path = DEFAULT_INDEX) -> dict[str, object]:
    """Run one maintenance transaction without mixing reader generations."""
    with _catalog_lock(catalog, exclusive=True):
        return _incremental_sync_unlocked(catalog)


def synchronization_audit(catalog: Path = DEFAULT_INDEX) -> dict[str, object]:
    """Measure a read-only no-op refresh and identify stale source locators."""
    started = time.perf_counter()
    with _ro(catalog) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        rows = connection.execute(
            "SELECT source_path, modified_ns, metadata_json FROM namespace WHERE object_type='file' AND COALESCE(status, '')!='deleted' ORDER BY source_path"
        ).fetchall()
    root = Path(metadata["source_root"])
    stale: list[str] = []
    volatile_stale: list[str] = []
    stable_stale: list[str] = []
    unavailable: list[str] = []
    for row in rows:
        path = root / str(row["source_path"])
        try:
            stat = path.stat()
        except OSError:
            unavailable.append(str(row["source_path"]))
            continue
        expected = json.loads(str(row["metadata_json"]))
        if stat.st_size != expected.get("size_bytes") or stat.st_mtime_ns != row["modified_ns"]:
            stale.append(str(row["source_path"]))
            if _is_volatile_source(str(row["source_path"])):
                volatile_stale.append(str(row["source_path"]))
            else:
                stable_stale.append(str(row["source_path"]))
    return {
        "checked_files": len(rows),
        "stale_files": len(stale),
        "stable_stale_files": len(stable_stale),
        "volatile_stale_files": len(volatile_stale),
        "unavailable_files": len(unavailable),
        "stale_paths": stale[:100],
        "stable_stale_paths": stable_stale[:100],
        "volatile_stale_paths": volatile_stale[:100],
        "unavailable_paths": unavailable[:100],
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "action": "stable_fail_closed;volatile_explicit_unavailable_with_age",
    }


def relationship_targets(catalog: Path, source_path: str, relation: str) -> list[str]:
    with _ro(catalog) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT target.source_path
            FROM namespace source
            JOIN relationships r ON r.source_id=source.object_id
            JOIN namespace target ON target.object_id=r.target_id
            WHERE source.source_path=? AND r.relation=?
              AND NOT EXISTS (
                SELECT 1 FROM sync_invalidations i WHERE i.source_path=source.source_path
              )
              AND NOT EXISTS (
                SELECT 1 FROM sync_invalidations i WHERE i.source_path=target.source_path
              )
            ORDER BY target.source_path
            """,
            (source_path, relation),
        ).fetchall()
    return [str(row[0]) for row in rows]


def current_temporal_value(catalog: Path, target: str) -> str | None:
    with _ro(catalog) as connection:
        rows = connection.execute(
            "SELECT value, status FROM temporal_events WHERE target=? ORDER BY sequence",
            (target,),
        ).fetchall()
    current = None
    for row in rows:
        if row["status"] == "APPROVED":
            current = str(row["value"])
        elif row["status"] == "REVOKED" and current == str(row["value"]):
            current = None
    return current


def capability_search(catalog: Path, query: str, limit: int = 5) -> list[dict[str, object]]:
    with _ro(catalog) as connection:
        invalid = connection.execute(
            "SELECT 1 FROM sync_invalidations WHERE source_path='ui/local_adapter/executor.py'"
        ).fetchone()
        if invalid is not None:
            return []
        rows = connection.execute(
            """
            SELECT c.*, bm25(capability_fts, 4.0, 1.0) AS lexical_score
            FROM capability_fts f JOIN capabilities c ON c.rowid=f.rowid
            WHERE capability_fts MATCH ?
            """,
            (_fts_expression(query),),
        ).fetchall()
    query_tokens = {token.lower() for token in TOKEN.findall(query)}
    ranked = []
    for row in rows:
        value = dict(row)
        arguments = json.loads(str(value["arguments_json"]))
        candidate_tokens = {
            token.lower()
            for token in TOKEN.findall(
                f"{value['name']} {value['description']} {' '.join(arguments)}"
            )
        }
        coverage = len(query_tokens & candidate_tokens)
        intent_bonus = 0
        if {"read", "file"}.issubset(query_tokens) and not ({"chunk", "offset", "continue"} & query_tokens):
            intent_bonus = 2 if value["name"] == "read_file" else 0
        ranked.append((coverage + intent_bonus, float(value.pop("lexical_score")), str(value["name"]), value))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in ranked[:limit]]


def latest_memory(catalog: Path, domain: str, task: str) -> dict[str, object] | None:
    with _ro(catalog) as connection:
        row = connection.execute(
            """
            SELECT m.* FROM memories m
            WHERE m.domain=? AND m.task=?
              AND NOT EXISTS (
                SELECT 1 FROM sync_invalidations i WHERE i.source_path=m.path
              )
            ORDER BY m.line_number DESC LIMIT 1
            """,
            (domain, task),
        ).fetchone()
    return dict(row) if row else None


def continuity_packet(catalog: Path) -> dict[str, object]:
    with _ro(catalog) as connection:
        rows = connection.execute("SELECT * FROM continuity_state ORDER BY phase").fetchall()
    completed = max(int(row["phase"]) for row in rows)
    failure = next((str(row["finding"]) for row in rows if "FAILED" in str(row["status"])), None)
    return {
        "completed_through": completed,
        "next_phase": completed + 1,
        "known_failure": failure,
        "phase_records": [dict(row) for row in rows],
        "authority_granted": "none",
    }


def compact_continuity_packet(catalog: Path) -> dict[str, object]:
    """Return the minimal automatically derived restart state."""
    packet = continuity_packet(catalog)
    return {
        "completed_through": packet["completed_through"],
        "next_phase": packet["next_phase"],
        "known_failure": packet["known_failure"],
        "authority_granted": "none",
    }


def inventory(catalog: Path = DEFAULT_INDEX) -> dict[str, object]:
    with _ro(catalog) as connection:
        counts = dict(connection.execute("SELECT object_type, count(*) FROM namespace GROUP BY object_type"))
        relation_counts = dict(connection.execute("SELECT relation, count(*) FROM relationships GROUP BY relation"))
        sources = [dict(row) for row in connection.execute("SELECT * FROM component_sources ORDER BY name")]
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        sync = {
            "overrides": connection.execute("SELECT count(*) FROM sync_overrides").fetchone()[0],
            "tombstones": connection.execute("SELECT count(*) FROM sync_tombstones").fetchone()[0],
            "typed_invalidations": connection.execute("SELECT count(*) FROM sync_invalidations").fetchone()[0],
            "runs": connection.execute("SELECT count(*) FROM sync_runs").fetchone()[0],
        }
    grouped = {"reality": 0, "history": 0, "knowledge": 0, "capabilities": 0, "authority": 0}
    for kind, count in counts.items():
        prefix = TYPE_NAMESPACE.get(kind, "/knowledge").split("/")[1]
        grouped[prefix] = grouped.get(prefix, 0) + int(count)
    return {"metadata": metadata, "groups": grouped, "object_types": counts, "relationships": relation_counts, "components": sources, "sync": sync}
