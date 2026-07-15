from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class DependencyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_executable(value: str | None, default_name: str | None = None) -> Path | None:
    if value:
        p = Path(os.path.expanduser(os.path.expandvars(value)))
        if p.is_absolute() or "/" in value:
            return p.resolve()
        found = shutil.which(value)
        return Path(found).resolve() if found else None
    if default_name:
        found = shutil.which(default_name)
        return Path(found).resolve() if found else None
    return None


def _version(cmd: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except Exception as exc:
        return f"version unavailable: {exc}"
    return (result.stdout or f"exit {result.returncode}").splitlines()[0]


@dataclass(frozen=True)
class DependencyRecord:
    name: str
    status: str
    path: str
    version: str = ""
    sha256: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "path": self.path,
            "version": self.version,
            "sha256": self.sha256,
            "detail": self.detail,
        }


def resolve_dssp(*, required: bool = False) -> DependencyRecord:
    path = _resolve_executable(os.environ.get("DSSP_BIN"), "mkdssp")
    if not path or not path.is_file():
        if required:
            raise DependencyError("DSSP/mkdssp is required for this mode; install mkdssp or set DSSP_BIN")
        return DependencyRecord("DSSP", "OPTIONAL_MISSING", os.environ.get("DSSP_BIN", "mkdssp"))
    return DependencyRecord("DSSP", "PASS", str(path), _version([str(path), "--version"]), _sha256(path))


def resolve_pyrosetta(*, required: bool = False) -> DependencyRecord:
    configured = os.environ.get("PYROSETTA_PYTHON")
    python = _resolve_executable(configured, None)
    if not python or not python.is_file():
        if required:
            raise DependencyError("PyRosetta is required for this mode; set PYROSETTA_PYTHON to a licensed Python environment")
        return DependencyRecord("PyRosetta", "OPTIONAL_MISSING", configured or "PYROSETTA_PYTHON")
    code = "import json, pyrosetta; print(json.dumps({'version': getattr(pyrosetta, '__version__', 'import-ok')}))"
    result = subprocess.run([str(python), "-c", code], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    if result.returncode != 0:
        if required:
            raise DependencyError("PyRosetta is required for this mode; configured PYROSETTA_PYTHON cannot import pyrosetta")
        return DependencyRecord("PyRosetta", "OPTIONAL_UNAVAILABLE", str(python), detail=(result.stderr or result.stdout).strip())
    try:
        version = json.loads(result.stdout.splitlines()[-1]).get("version", "import-ok")
    except Exception:
        version = "import-ok"
    return DependencyRecord("PyRosetta", "PASS", str(python), version, _sha256(python))


def resolve_rosetta_bin(*, required: bool = False) -> DependencyRecord:
    raw = os.environ.get("ROSETTA_BIN")
    path = Path(os.path.expanduser(os.path.expandvars(raw))).resolve() if raw else None
    if not path or not path.exists():
        if required:
            raise DependencyError("Rosetta utilities are required for this mode; set ROSETTA_BIN to a licensed Rosetta bin directory")
        return DependencyRecord("Rosetta utilities", "OPTIONAL_MISSING", raw or "ROSETTA_BIN")
    if path.is_file():
        return DependencyRecord("Rosetta utilities", "PASS", str(path), _version([str(path), "--help"]), _sha256(path))
    candidates = sorted(p for p in path.iterdir() if p.is_file() and os.access(p, os.X_OK))
    detail = f"{len(candidates)} executable files detected"
    return DependencyRecord("Rosetta utilities", "PASS", str(path), detail=detail)


def resolve_rosetta_database(*, required: bool = False) -> DependencyRecord:
    raw = os.environ.get("ROSETTA_DATABASE")
    path = Path(os.path.expanduser(os.path.expandvars(raw))).resolve() if raw else None
    if not path or not path.is_dir():
        if required:
            raise DependencyError("Rosetta database is required for this mode; set ROSETTA_DATABASE to a licensed database directory")
        return DependencyRecord("Rosetta database", "OPTIONAL_MISSING", raw or "ROSETTA_DATABASE")
    return DependencyRecord("Rosetta database", "PASS", str(path), detail="directory present")


def resolve_molfile_to_params(*, required: bool = False) -> DependencyRecord:
    path = _resolve_executable(os.environ.get("MOLFILE_TO_PARAMS"), "molfile_to_params.py")
    if not path or not path.is_file():
        if required:
            raise DependencyError("molfile_to_params.py is required for this mode; set MOLFILE_TO_PARAMS to the licensed Rosetta utility")
        return DependencyRecord("molfile_to_params", "OPTIONAL_MISSING", os.environ.get("MOLFILE_TO_PARAMS", "molfile_to_params.py"))
    return DependencyRecord("molfile_to_params", "PASS", str(path), _version([str(path), "--help"]), _sha256(path))


def resolve_directory_env(name: str, env: str, *, required: bool = False) -> DependencyRecord:
    raw = os.environ.get(env, "")
    path = Path(os.path.expanduser(os.path.expandvars(raw))).resolve() if raw else None
    if not path or not path.is_dir():
        if required:
            raise DependencyError(f"{name} is required for this mode; set {env} to a readable licensed directory")
        return DependencyRecord(name, "OPTIONAL_MISSING", raw or env)
    files = sorted(p for p in path.rglob("*") if p.is_file())
    digest = ""
    if files:
        h = hashlib.sha256()
        for p in files[:200]:
            h.update(str(p.relative_to(path)).encode())
            h.update(_sha256(p).encode())
        digest = h.hexdigest()
    return DependencyRecord(name, "PASS", str(path), sha256=digest, detail=f"{len(files)} files")


def resolve_stage4_dependencies(config: dict, *, required: bool = False) -> list[DependencyRecord]:
    stage4 = config.get("stage4", {})
    records: list[DependencyRecord] = []
    records.append(resolve_dssp(required=bool(stage4.get("dssp_required", False)) and required))
    records.append(resolve_directory_env("CHARMM36", "MEMBRANEFORGER_CHARMM36_ROOT", required=bool(stage4.get("charmm36_required", False)) and required))
    records.append(resolve_directory_env("CGenFF", "MEMBRANEFORGER_CGENFF_ROOT", required=bool(stage4.get("cgenff_required", False)) and required))
    records.append(resolve_directory_env("Ligand params", "MEMBRANEFORGER_LIGAND_PARAMS_ROOT", required=bool(stage4.get("ligand_params_required", False)) and required))
    records.append(resolve_pyrosetta(required=bool(stage4.get("pyrosetta_plugins_enabled", False)) and required))
    rosetta_required = bool(stage4.get("parameter_generation_enabled", False)) and required
    records.append(resolve_rosetta_bin(required=rosetta_required))
    records.append(resolve_rosetta_database(required=rosetta_required))
    records.append(resolve_molfile_to_params(required=rosetta_required))
    return records
