#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from membraneforger_workflow import stage_main


if __name__ == "__main__":
    raise SystemExit(stage_main("stage3", sys.argv[1:]))
