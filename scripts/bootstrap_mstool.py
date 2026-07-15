#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "resources" / "vendor" / "mstool.lock.yaml"


def info(msg: str) -> None:
    print(f"INFO: {msg}")


def warning(msg: str) -> None:
    print(f"WARNING: {msg}")


def error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_lock(path: Path = LOCK) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("- "):
            continue
        if ":" in line and not raw.startswith(" "):
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"')
    return data


def safe_extract_tar(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        roots = {m.name.split("/", 1)[0] for m in members if m.name}
        if len(roots) != 1:
            raise RuntimeError("archive must contain exactly one top-level directory")
        root_name = next(iter(roots))
        dest_resolved = dest.resolve()
        for member in members:
            target = (dest / member.name).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise RuntimeError(f"archive path traversal rejected: {member.name}")
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                if dest_resolved not in link_target.parents and link_target != dest_resolved:
                    raise RuntimeError(f"archive link traversal rejected: {member.name}")
        tf.extractall(dest)
    return dest / root_name


def download(url: str, target: Path, offline: bool) -> None:
    if offline:
        raise RuntimeError("offline mode cannot download mstool; provide --source-archive")
    if "@" in url.split("://", 1)[-1].split("/", 1)[0]:
        raise RuntimeError("credential-bearing URLs are rejected")
    with urllib.request.urlopen(url, timeout=60) as response:
        with target.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def copy_package(extracted_root: Path, destination: Path) -> None:
    candidates = [
        extracted_root / "src" / "mstool",
        extracted_root / "mstool",
    ]
    source = next((p for p in candidates if (p / "__init__.py").is_file()), None)
    if source is None:
        raise RuntimeError("could not find mstool package in upstream archive")
    shutil.copytree(source, destination)
    for rel in ("LICENSE", "COPYING"):
        license_file = extracted_root / rel
        if license_file.is_file():
            shutil.copy2(license_file, destination / rel)
            break


def record_install(destination: Path, lock: dict[str, str], archive: Path) -> None:
    rows = []
    for path in sorted(p for p in destination.rglob("*") if p.is_file()):
        rows.append({
            "path": path.relative_to(destination).as_posix(),
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
        })
    metadata = {
        "project": lock["project"],
        "upstream_repository": lock["upstream_repository"],
        "exact_commit": lock["exact_commit"],
        "release_if_any": lock.get("release_if_any", ""),
        "license_spdx": lock["license_spdx"],
        "source_archive_sha256": sha256(archive),
        "installed_files": rows,
    }
    (destination / ".membraneforger-install.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git_verify(destination: Path, expected_commit: str) -> None:
    git = shutil.which("git")
    if not git or not (destination / ".git").exists():
        return
    result = subprocess.run([git, "-C", str(destination), "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0 or result.stdout.strip() != expected_commit:
        raise RuntimeError(f"unexpected mstool git revision: {result.stdout.strip() or result.stderr.strip()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned mstool dependency into resources/vendor/mstool.")
    parser.add_argument("--destination", default=str(ROOT / "resources" / "vendor" / "mstool"))
    parser.add_argument("--source-archive", help="Use a local tar.gz archive instead of downloading")
    parser.add_argument("--force", action="store_true", help="Replace an existing destination")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)

    lock = parse_lock()
    destination = Path(args.destination).expanduser().resolve()
    expected = lock["source_archive_sha256"]
    info(f"mstool destination: {destination}")
    info(f"pinned commit: {lock['exact_commit']}")

    if args.verify:
        metadata = destination / ".membraneforger-install.json"
        if not metadata.is_file():
            error(f"missing mstool install metadata: {metadata}")
            return 1
        data = json.loads(metadata.read_text(encoding="utf-8"))
        if data.get("exact_commit") != lock["exact_commit"]:
            error("installed mstool commit does not match lock")
            return 1
        info("mstool install metadata matches lock")
        return 0

    if destination.exists() and not args.force:
        error(f"refusing to overwrite existing mstool directory: {destination}; use --force")
        return 1

    if args.dry_run:
        info("dry-run: would fetch, verify, and install mstool")
        return 0

    temp_parent = Path(tempfile.mkdtemp(prefix="membraneforger-mstool-"))
    try:
        archive = temp_parent / "mstool.tar.gz"
        if args.source_archive:
            archive = Path(args.source_archive).expanduser().resolve()
            info(f"using local source archive: {archive}")
        else:
            download(lock["source_archive_url"], archive, args.offline)
        actual = sha256(archive)
        if actual != expected:
            raise RuntimeError(f"checksum mismatch for mstool archive: expected {expected}, got {actual}")
        extract_dir = temp_parent / "extract"
        extract_dir.mkdir()
        extracted = safe_extract_tar(archive, extract_dir)
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_package(extracted, destination)
        git_verify(destination, lock["exact_commit"])
        record_install(destination, lock, archive)
        info("mstool installed")
        return 0
    except Exception as exc:
        error(str(exc))
        return 1
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
