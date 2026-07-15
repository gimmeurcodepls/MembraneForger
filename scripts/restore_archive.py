#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore files from an archive manifest written before reversible archival.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not args.manifest.is_file():
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    with args.manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        src = ROOT / row["archive_relative_path"]
        dst = ROOT / row["original_relative_path"]
        expected = row.get("sha256", "")
        if not src.is_file():
            print(f"ERROR: archived file missing: {src}", file=sys.stderr)
            return 1
        if expected and sha256(src) != expected:
            print(f"ERROR: checksum mismatch for {src}", file=sys.stderr)
            return 1
        if dst.exists() and not args.overwrite:
            print(f"ERROR: destination exists: {dst}; use --overwrite", file=sys.stderr)
            return 1
    for row in rows:
        src = ROOT / row["archive_relative_path"]
        dst = ROOT / row["original_relative_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"RESTORED {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
