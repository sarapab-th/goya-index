"""Reproducible synthetic smoke benchmark, not a blind or model benchmark."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sqlite3
import statistics
import tempfile
import time

import goya_index


def benchmark(files=100, queries=30):
    if not 1 <= files <= 2000 or not 1 <= queries <= 1000:
        raise ValueError("files must be 1..2000 and queries 1..1000")
    elapsed = []
    correct = verified = packet_verified = 0
    with tempfile.TemporaryDirectory(prefix="goya-index-benchmark-") as temporary:
        root = Path(temporary)
        source = root / "synthetic-source"
        source.mkdir()
        expected = {}
        for number in range(files):
            name = f"module_{number:04d}.py"
            content = (
                '"""Generated benchmark fixture, no customer data."""\n'
                f"def synthetic_metric_{number:04d}(value=1):\n"
                f"    return value + {number}\n"
            ).encode()
            (source / name).write_bytes(content)
            expected[name] = hashlib.sha256(content).hexdigest()
        data = root / "catalog"
        start = time.perf_counter()
        goya_index.build(source, data)
        build_ms = (time.perf_counter() - start) * 1000
        for number in range(queries):
            target = (number * 37) % files
            query = f"synthetic_metric_{target:04d}"
            name = f"module_{target:04d}.py"
            start = time.perf_counter()
            matches = goya_index.search(data, query, limit=5)
            elapsed.append((time.perf_counter() - start) * 1000)
            correct += bool(matches and matches[0]["source_path"] == name)
            result = goya_index.read(data, name)
            verified += bool(result["verified"] and result["content_sha256"] == expected[name])
            packet = goya_index.context(data, query, max_chars=2000)
            packet_verified += bool(
                packet["candidates"]
                and packet["candidates"][0]["source_path"] == name
                and packet["packet_chars"] <= 2000
                and packet["packet_sha256"] == hashlib.sha256(packet["packet"].encode()).hexdigest()
            )
        actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
        source_unchanged = actual == expected
        with closing(sqlite3.connect(data / "catalog.sqlite3")) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        result = {
            "schema": "goya-index.public-synthetic-benchmark/1",
            "recorded_utc": datetime.now(timezone.utc).isoformat(),
            "package_version": goya_index.__version__,
            "platform": {"system": platform.system(), "release": platform.release(),
                         "machine": platform.machine(), "python": platform.python_version(),
                         "sqlite": sqlite3.sqlite_version},
            "method": "author-designed deterministic exact-symbol smoke; normal filesystem cache",
            "files": files, "queries": queries,
            "distinct_query_targets": len({(n * 37) % files for n in range(queries)}),
            "build_ms": round(build_ms, 3),
            "search_median_ms": round(statistics.median(elapsed), 3),
            "search_p95_ms": round(sorted(elapsed)[math.ceil(len(elapsed) * .95) - 1], 3),
            "top1_correct": correct, "verified_reads": verified,
            "verified_packets": packet_verified, "source_unchanged": source_unchanged,
            "sqlite_integrity": integrity,
            "model_calls": 0,
            "token_cost_or_competitor_comparison": "not measured",
            "passed": correct == queries and verified == queries and packet_verified == queries
                      and source_unchanged and integrity == "ok",
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=100)
    parser.add_argument("--queries", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and (args.output.exists() or args.output.is_symlink()):
        parser.error("output already exists; choose a new file")
    try:
        result = benchmark(args.files, args.queries)
    except ValueError as exc:
        parser.error(str(exc))
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    print(payload, end="")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
