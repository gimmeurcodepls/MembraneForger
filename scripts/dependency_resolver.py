#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from membraneforger_workflow import load_simple_yaml
from membraneforger_paths import PathResolutionError, display_path, resolve_mstool
from external_dependencies import (
    resolve_dssp,
    resolve_molfile_to_params,
    resolve_pyrosetta,
    resolve_rosetta_bin,
    resolve_rosetta_database,
)


ROOT = Path(__file__).resolve().parents[1]

DEPENDENCIES: dict[str, dict[str, Any]] = {
    "Python": {"env": "PYTHON_BIN", "path": "python3", "required": True, "version": ["--version"]},
    "GROMACS": {"env": "GMX_BIN", "path": "gmx", "required": False, "version": ["--version"]},
    "martinize2": {"env": "MARTINIZE2_BIN", "path": "martinize2", "required": False, "version": ["--version"]},
    "Vermouth": {"module": "vermouth", "required": False},
    "INSANE": {"env": "INSANE_BIN", "path": "insane", "required": False},
    "mstool": {"module_path": "resources/vendor/mstool", "required": False},
    "OpenMM": {"module": "openmm", "required": False},
    "DSSP": {"special": "dssp", "required": False},
    "PyRosetta": {"special": "pyrosetta", "required": False},
    "Rosetta utilities": {"special": "rosetta_bin", "required": False},
    "Rosetta database": {"special": "rosetta_database", "required": False},
    "molfile_to_params": {"special": "molfile_to_params", "required": False},
    "Apptainer/Singularity": {"env": "APPTAINER_BIN", "path": "apptainer", "required": False, "version": ["--version"]},
    "Apptainer image": {"env": "APPTAINER_IMAGE", "required": False},
}


def executable_version(exe: str, args: list[str]) -> str:
    try:
        result = subprocess.run([exe, *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
    except Exception as exc:
        return f"version check failed: {exc}"
    line = (result.stdout or "").splitlines()
    return line[0] if line else f"exit {result.returncode}"


def executable_runs(exe: str, args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run([exe, *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
    except Exception as exc:
        return False, f"execution failed: {exc}"
    output = (result.stdout or "").splitlines()
    detail = "\n".join(output[:20]) if output else f"exit {result.returncode}"
    return result.returncode == 0, detail


def module_available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def module_imports(module: str, extra_path: Path | None = None) -> tuple[bool, str]:
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(extra_path)!r}) if {str(extra_path)!r} else None; "
        f"import {module}; "
        f"print(getattr({module}, '__version__', 'import-ok'))"
    )
    result = subprocess.run([sys.executable, "-c", code], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=12)
    output = (result.stdout or "").strip().splitlines()
    return result.returncode == 0, (output[-1] if output else f"exit {result.returncode}")


def normalize(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_")


def availability_status(ok: bool, required: bool) -> str:
    if ok:
        return "PASS"
    return "FAIL" if required else "OPTIONAL_UNAVAILABLE"


def resolve_one(name: str, spec: dict[str, Any], configured: dict[str, Any]) -> dict[str, Any]:
    if spec.get("special") == "dssp":
        return {**resolve_dssp(required=spec["required"]).as_dict(), "required": spec["required"], "source": "DSSP_BIN or PATH", "value": os.environ.get("DSSP_BIN", "mkdssp")}
    if spec.get("special") == "pyrosetta":
        return {**resolve_pyrosetta(required=spec["required"]).as_dict(), "required": spec["required"], "source": "PYROSETTA_PYTHON", "value": os.environ.get("PYROSETTA_PYTHON", "")}
    if spec.get("special") == "rosetta_bin":
        return {**resolve_rosetta_bin(required=spec["required"]).as_dict(), "required": spec["required"], "source": "ROSETTA_BIN", "value": os.environ.get("ROSETTA_BIN", "")}
    if spec.get("special") == "rosetta_database":
        return {**resolve_rosetta_database(required=spec["required"]).as_dict(), "required": spec["required"], "source": "ROSETTA_DATABASE", "value": os.environ.get("ROSETTA_DATABASE", "")}
    if spec.get("special") == "molfile_to_params":
        return {**resolve_molfile_to_params(required=spec["required"]).as_dict(), "required": spec["required"], "source": "MOLFILE_TO_PARAMS or PATH", "value": os.environ.get("MOLFILE_TO_PARAMS", "molfile_to_params.py")}
    config_entry = configured.get(normalize(name), {})
    if isinstance(config_entry, dict):
        for key in ("value", "bin", "path", "command"):
            config_value = config_entry.get(key)
            if config_value:
                p = Path(str(config_value))
                resolved = str(p) if ("/" in str(config_value) or str(config_value).startswith(".")) else shutil.which(str(config_value))
                exists = p.exists() if ("/" in str(config_value) or str(config_value).startswith(".")) else resolved is not None
                if name == "INSANE" and exists and resolved:
                    ok, detail = executable_runs(resolved, ["--help"])
                    return {"name": name, "status": availability_status(ok, spec["required"]), "source": f"config.{key} execution", "value": str(config_value), "version": detail, "required": spec["required"]}
                return {"name": name, "status": availability_status(exists, spec["required"]), "source": f"config.{key}", "value": str(config_value), "required": spec["required"]}
    env = spec.get("env")
    env_value = os.environ.get(env, "") if env else ""
    if env_value:
        p = Path(env_value)
        resolved = str(p) if ("/" in env_value or env_value.startswith(".")) else shutil.which(env_value)
        exists = p.exists() if ("/" in env_value or env_value.startswith(".")) else resolved is not None
        if name == "INSANE" and exists and resolved:
            ok, detail = executable_runs(resolved, ["--help"])
            return {"name": name, "status": availability_status(ok, spec["required"]), "source": f"{env} execution", "value": env_value, "version": detail, "required": spec["required"]}
        return {"name": name, "status": availability_status(exists, spec["required"]), "source": env, "value": env_value, "required": spec["required"]}
    if "module_path" in spec:
        path = ROOT / spec["module_path"]
        if not path.is_dir():
            return {"name": name, "status": availability_status(False, spec["required"]), "source": "repository", "value": str(path.relative_to(ROOT)), "required": spec["required"]}
        try:
            resolution = resolve_mstool(root=ROOT, configured=path, import_module=True)
            detail = f"{resolution.version} {display_path(resolution.module_file, ROOT)} extensions={resolution.extension_mode}"
            ok = True
        except (PathResolutionError, Exception) as exc:
            detail = str(exc)
            ok = False
        return {
            "name": name,
            "status": availability_status(ok, spec["required"]),
            "source": "repository import",
            "value": str(path.relative_to(ROOT)),
            "version": detail,
            "required": spec["required"],
        }
    if "module" in spec:
        ok = module_available(spec["module"])
        return {"name": name, "status": "PASS" if ok else "OPTIONAL_MISSING", "source": "python import", "value": spec["module"], "required": spec["required"]}
    path_name = spec.get("path")
    found = shutil.which(path_name) if path_name else None
    if found:
        if name == "INSANE":
            ok, detail = executable_runs(found, ["--help"])
            if not ok and "pkg_resources" in detail:
                detail = f"{detail}; install a setuptools-compatible INSANE environment that provides pkg_resources"
            return {"name": name, "status": availability_status(ok, spec["required"]), "source": "PATH execution", "value": found, "version": detail, "required": spec["required"]}
        version = executable_version(found, spec.get("version", [])) if spec.get("version") else ""
        return {"name": name, "status": "PASS", "source": "PATH", "value": found, "version": version, "required": spec["required"]}
    return {"name": name, "status": "OPTIONAL_MISSING" if not spec["required"] else "FAIL", "source": "PATH", "value": path_name or "", "required": spec["required"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "dependencies.yaml"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    configured = load_simple_yaml(Path(args.config)) if Path(args.config).is_file() else {}
    rows = [resolve_one(name, spec, configured) for name, spec in DEPENDENCIES.items()]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            extra = f" {row.get('version', '')}" if row.get("version") else ""
            print(f"{row['status']:<16} {row['name']:<22} {row['source']}={row['value']}{extra}")
    return 1 if any(row["required"] and row["status"] != "PASS" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
