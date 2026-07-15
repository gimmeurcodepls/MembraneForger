#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["numpy", "pytest"]
OPTIONAL = ["MDAnalysis", "pandas", "scipy", "networkx", "matplotlib", "openmm", "parmed", "rdkit", "vermouth"]


def available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Python imports used by the portable MembraneForger workflow.")
    parser.parse_args(argv)
    vendor = ROOT / "resources" / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    failed = False
    for module in REQUIRED:
        ok = available(module)
        print(f"{'PASS' if ok else 'FAIL'} required import {module}")
        failed = failed or not ok
    for module in OPTIONAL:
        print(f"{'PASS' if available(module) else 'OPTIONAL_MISSING'} optional import {module}")
    if (vendor / "mstool").is_dir():
        print("PASS bootstrapped mstool resources/vendor/mstool")
    else:
        print("OPTIONAL_BOOTSTRAP_REQUIRED mstool: python scripts/bootstrap_resources.py --component mstool")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
