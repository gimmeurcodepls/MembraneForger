#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "ERROR: Python executable not found; set PYTHON_BIN" >&2
    exit 127
  fi
fi

usage() {
  cat <<'EOF'
Usage:
  bash setup.sh --check
  bash setup.sh --conda
  bash setup.sh --container
EOF
}

check_resources() {
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import hashlib, sys
root=Path('.')
manifest=root/'resources'/'RESOURCE_MANIFEST.tsv'
if not manifest.is_file():
    print('FAIL missing resources/RESOURCE_MANIFEST.tsv')
    sys.exit(1)
failed=False
for line in manifest.read_text().splitlines()[1:]:
    parts=line.split('\t')
    if len(parts) < 6:
        print('FAIL malformed manifest line', line)
        failed=True
        continue
    rel, expected=parts[0], parts[5]
    p=root/rel
    if not p.is_file():
        print(f'FAIL missing resource {rel}')
        failed=True
        continue
    got=hashlib.sha256(p.read_bytes()).hexdigest()
    if got != expected:
        print(f'FAIL checksum mismatch {rel}')
        failed=True
print('PASS resource manifest checksums' if not failed else 'FAIL resource manifest')
sys.exit(1 if failed else 0)
PY
}

mode="${1:---check}"
case "$mode" in
  --check)
    mkdir -p work outputs logs inputs
    check_resources
    "$PYTHON_BIN" scripts/check_python_environment.py
    "$PYTHON_BIN" scripts/dependency_resolver.py
    ;;
  --conda)
    cat <<'EOF'
Run this from the repository root with any Conda-compatible frontend:
  conda env create -f environments/environment.yml
  conda activate membraneforger
  python scripts/check_python_environment.py
EOF
    ;;
  --container)
    cat <<'EOF'
Container recipes are provided without licensed Rosetta/PyRosetta:
  docker build -f containers/Dockerfile -t membraneforger:portable .
  apptainer build MembraneForger.sif containers/Apptainer.def
Optional licensed dependencies must be bind-mounted and configured with ROSETTA_BIN, ROSETTA_DATABASE, or PYROSETTA_PYTHON.
EOF
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
