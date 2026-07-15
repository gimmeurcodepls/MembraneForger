#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def info(msg: str) -> None:
    print(f"INFO: {msg}")


def run_mstool(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(ROOT / "scripts" / "bootstrap_mstool.py")]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.verify:
        cmd.append("--verify")
    if args.offline:
        cmd.append("--offline")
    return subprocess.call(cmd)


def verify_martini() -> int:
    origin = ROOT / "resources" / "forcefields" / "martini" / "ORIGIN.yaml"
    if not origin.is_file():
        print("ERROR: missing Martini ORIGIN.yaml", file=sys.stderr)
        return 1
    required = [
        "martini_v3.0.0.itp",
        "martini_v3.0.0_ions_v1.itp",
        "martini_v3.0.0_nucleobases_v1.itp",
        "martini_v3.0.0_phospholipids_v1.itp",
        "martini_v3.0.0_small_molecules_v1.itp",
        "martini_v3.0.0_solvents_v1.itp",
        "martini_v3.0.0_sugars_v1.itp",
        "martini_v3.0.0_sugars_v2.itp",
    ]
    base = ROOT / "resources" / "forcefields" / "martini"
    missing = [name for name in required if not (base / name).is_file()]
    if missing:
        print(f"ERROR: missing Martini files: {', '.join(missing)}", file=sys.stderr)
        return 1
    info("Martini canonical resources present")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap verified open MembraneForger resources.")
    parser.add_argument("--component", choices=["mstool", "martini"], action="append")
    parser.add_argument("--all-open-resources", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    components = set(args.component or [])
    if args.all_open_resources:
        components.update({"mstool", "martini"})
    if not components:
        parser.error("select --component or --all-open-resources")
    rc = 0
    if "martini" in components:
        rc = verify_martini() or rc
    if "mstool" in components:
        rc = run_mstool(args) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
