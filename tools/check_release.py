"""Audit built artifacts and test the wheel in an isolated, temporary environment.

Run after building: python3 -B tools/check_release.py
Uses no package index and no model/provider calls. No credentials are needed.
"""

from pathlib import Path, PurePosixPath
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".pyc", ".sqlite", ".sqlite3", ".db", ".seed", ".pem", ".key", ".p12", ".pfx"}
PRIVATE_PATH = re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+/")


def verify_entry(name, payload):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.suffix in FORBIDDEN_SUFFIXES:
        raise RuntimeError(f"Unexpected release entry: {name}")
    if any(part in {".git", ".env", "__pycache__", "node_modules"} for part in path.parts):
        raise RuntimeError(f"Unexpected generated/private entry: {name}")
    if PRIVATE_PATH.search(payload):
        raise RuntimeError(f"Personal absolute path in release: {name}")
    if b"-----BEGIN " + b"PRIVATE KEY-----" in payload or b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----" in payload:
        raise RuntimeError(f"Private-key armor in release: {name}")


def check():
    wheels = list((ROOT / "dist").glob("*.whl"))
    sdists = list((ROOT / "dist").glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("Expected exactly one wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        package_sources = {str(path.relative_to(ROOT)) for path in (ROOT / "goya_index").rglob("*.py")}
        if {name for name in names if name.startswith("goya_index/")} != package_sources:
            raise RuntimeError("Wheel source set differs from the public package")
        for name in names:
            verify_entry(name, wheel.read(name))
            if name in package_sources and wheel.read(name) != (ROOT / name).read_bytes():
                raise RuntimeError(f"Stale wheel source: {name}")
        metadata = next(name for name in names if name.endswith(".dist-info/METADATA"))
        payload = wheel.read(metadata)
        if b"License-Expression: MIT" not in payload or b"Requires-Dist:" in payload:
            raise RuntimeError("Incorrect license or unexpected runtime dependency")
    with tarfile.open(sdists[0], "r:gz") as archive:
        names = set()
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError(f"Non-regular source archive member: {member.name}")
            with archive.extractfile(member) as stream:
                verify_entry(member.name, stream.read())
            names.add("/".join(PurePosixPath(member.name).parts[1:]))
        for required in ("LICENSE", "SECURITY.md", "docs/EVIDENCE.md", "examples/source/inventory.py", "tests/test_public_package.py"):
            if required not in names:
                raise RuntimeError(f"Missing source archive file: {required}")

    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    with tempfile.TemporaryDirectory(prefix="goya-index-wheel-") as temporary:
        scratch = Path(temporary)
        virtual = scratch / "venv"
        venv.EnvBuilder(with_pip=True).create(virtual)
        python = virtual / "bin/python"
        subprocess.run([str(python), "-B", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", "--no-compile", str(wheels[0])], check=True, cwd=scratch, env=environment)
        subprocess.run([str(python), "-B", "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"], check=True, cwd=scratch, env=environment)
        subprocess.run([str(virtual / "bin/goya-index"), "--help"], check=True, cwd=scratch, env=environment, stdout=subprocess.DEVNULL)
        imported = subprocess.check_output([str(python), "-I", "-B", "-c", "import goya_index; print(goya_index.__file__)"], cwd=scratch, env=environment, text=True).strip()
        if not Path(imported).resolve().is_relative_to(virtual.resolve()):
            raise RuntimeError("Wheel test imported from outside its isolated environment")
    print(json.dumps({"wheel": wheels[0].name, "sdist": sdists[0].name, "archive_checks": "passed", "offline_wheel_install_tests": "passed"}))


if __name__ == "__main__":
    check()
