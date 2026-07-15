from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    merged["PYTHONPATH"] = str(ROOT / "scripts")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=merged, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_dssp_detection_through_dssp_bin(tmp_path: Path) -> None:
    exe = tmp_path / "mkdssp"
    exe.write_text("#!/bin/sh\necho mkdssp-test\n", encoding="utf-8")
    exe.chmod(0o755)
    result = run_python("from external_dependencies import resolve_dssp; print(resolve_dssp(required=True).status)", {"DSSP_BIN": str(exe)})
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def test_missing_required_dssp_fails(tmp_path: Path) -> None:
    result = run_python("from external_dependencies import resolve_dssp; resolve_dssp(required=True)", {"DSSP_BIN": str(tmp_path / "missing")})
    assert result.returncode != 0
    assert "DSSP" in result.stderr


def test_pyrosetta_import_through_configured_python(tmp_path: Path) -> None:
    py = tmp_path / "fake_py"
    py.write_text("#!/bin/sh\necho '{\"version\":\"fake-pyrosetta\"}'\n", encoding="utf-8")
    py.chmod(0o755)
    result = run_python("from external_dependencies import resolve_pyrosetta; print(resolve_pyrosetta(required=True).version)", {"PYROSETTA_PYTHON": str(py)})
    assert result.returncode == 0, result.stderr
    assert "fake-pyrosetta" in result.stdout


def test_missing_pyrosetta_only_blocks_pyrosetta_mode(tmp_path: Path) -> None:
    code = """
from membraneforger_workflow import load_config, check_stage4
from pathlib import Path
cfg = load_config(Path('config/workflow.example.yaml'))
print(check_stage4(cfg, 'minimal'))
cfg['stage4']['pyrosetta_plugins_enabled'] = True
print(check_stage4(cfg, 'pyro'))
"""
    result = run_python(code, {"PYROSETTA_PYTHON": str(tmp_path / "missing")})
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "[]"
    assert "PyRosetta is required" in lines[1]


def test_rosetta_executable_root_database_and_molfile_validation(tmp_path: Path) -> None:
    bin_dir = tmp_path / "rosetta bin"
    db = tmp_path / "database"
    bin_dir.mkdir()
    db.mkdir()
    exe = bin_dir / "score_jd2"
    exe.write_text("#!/bin/sh\necho rosetta\n", encoding="utf-8")
    exe.chmod(0o755)
    molfile = tmp_path / "molfile_to_params.py"
    molfile.write_text("#!/bin/sh\necho molfile\n", encoding="utf-8")
    molfile.chmod(0o755)
    code = """
from external_dependencies import resolve_rosetta_bin, resolve_rosetta_database, resolve_molfile_to_params
print(resolve_rosetta_bin(required=True).status)
print(resolve_rosetta_database(required=True).status)
print(resolve_molfile_to_params(required=True).status)
"""
    result = run_python(code, {"ROSETTA_BIN": str(bin_dir), "ROSETTA_DATABASE": str(db), "MOLFILE_TO_PARAMS": str(molfile)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["PASS", "PASS", "PASS"]


def test_rosetta_dependent_mode_fails_actionably_when_missing(tmp_path: Path) -> None:
    code = """
from membraneforger_workflow import load_config, check_stage4
from pathlib import Path
cfg = load_config(Path('config/workflow.example.yaml'))
cfg['stage4']['parameter_generation_enabled'] = True
print(check_stage4(cfg, 'rosetta'))
"""
    result = run_python(code, {"ROSETTA_BIN": str(tmp_path / "missing"), "ROSETTA_DATABASE": str(tmp_path / "missingdb"), "MOLFILE_TO_PARAMS": str(tmp_path / "missing.py")})
    assert result.returncode == 0, result.stderr
    assert "Rosetta utilities are required" in result.stdout


def test_dependency_resolver_json_has_external_rows() -> None:
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "dependency_resolver.py"), "--json"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    names = {row["name"] for row in rows}
    assert {"DSSP", "PyRosetta", "Rosetta utilities", "Rosetta database", "molfile_to_params"} <= names
