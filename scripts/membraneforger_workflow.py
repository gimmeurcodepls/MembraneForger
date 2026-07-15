#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from membraneforger_paths import (  # noqa: E402
    PathResolutionError,
    display_path,
    gmx_command as resolve_gmx_command,
    resolve_cli_path,
    resolve_config_path,
    resolve_mstool,
)
from external_dependencies import DependencyError, resolve_stage4_dependencies  # noqa: E402

STAGE_NAMES = ("stage1", "stage2", "stage3", "stage4")
ATOM_RECORDS = ("ATOM  ", "HETATM")


class ContractError(RuntimeError):
    pass


def scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [scalar(part.strip()) for part in inner.split(",")]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def load_simple_yaml(path: Path) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise ContractError(f"{path}:{lineno}: indentation must use multiples of two spaces")
        text = raw.strip()
        if text.startswith("- "):
            raise ContractError(f"{path}:{lineno}: list items are not supported; use [] for this workflow config")
        if ":" not in text:
            raise ContractError(f"{path}:{lineno}: expected key: value")
        key, rest = text.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if rest == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = scalar(rest)
    return root


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    default = load_simple_yaml(ROOT / "config" / "workflow.yaml")
    if path == (ROOT / "workflow.yaml").resolve() or path == (ROOT / "config" / "workflow.yaml").resolve():
        loaded = default
    else:
        loaded = deep_merge(default, load_simple_yaml(path))
    loaded.setdefault("_meta", {})["config_path"] = str(path)
    return loaded


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def resolve_path(value: str | Path, config: dict[str, Any] | None = None) -> Path:
    path = Path(str(value).format(run_id="__RUN_ID__")).as_posix().replace("__RUN_ID__", "{run_id}")
    config_path = None
    if config is not None:
        raw = config.get("_meta", {}).get("config_path")
        config_path = Path(raw) if raw else None
    return resolve_config_path(path, config_path=config_path, root=ROOT)


def run_id_from(config: dict[str, Any], cli_run_id: str | None) -> str:
    run_id = cli_run_id or str(config.get("run", {}).get("id") or "default")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise ContractError(f"invalid run_id {run_id!r}; use letters, numbers, '.', '_' or '-'")
    return run_id


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    work: Path
    outputs: Path
    logs: Path

    def stage_work(self, stage: str) -> Path:
        return self.work / stage

    def stage_output(self, stage: str) -> Path:
        return self.outputs / stage


def run_paths(run_id: str, output_dir: Path | None = None) -> RunPaths:
    if output_dir is not None:
        return RunPaths(run_id, output_dir / "work", output_dir, output_dir / "logs")
    return RunPaths(run_id, ROOT / "work" / run_id, ROOT / "outputs" / run_id, ROOT / "logs" / run_id)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def chain_ids(path: Path) -> list[str]:
    seen: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(ATOM_RECORDS):
                chain = line[21:22].strip()
                if chain and chain not in seen:
                    seen.append(chain)
    return seen


def atom_counts(path: Path) -> dict[str, int]:
    heavy = 0
    hydrogens = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith(ATOM_RECORDS):
                continue
            name = line[12:16].strip().upper()
            elem = line[76:78].strip().upper() if len(line) >= 78 else ""
            if elem in {"H", "D"} or name.startswith(("H", "D")):
                hydrogens += 1
            else:
                heavy += 1
    return {"heavy": heavy, "hydrogens": hydrogens}


def require_path_if_selected(path: Path, label: str, selected: bool, errors: list[str]) -> None:
    if selected and not path.exists():
        errors.append(f"missing {label}: {relpath(path)}")


def check_stage1(config: dict[str, Any], run_id: str) -> list[str]:
    errors: list[str] = []
    cfg = config.get("stage1", {})
    mode = cfg.get("membrane_protein_mode")
    role = cfg.get("role", "membrane_setup")
    if role not in {"membrane_setup", "replacement_protein_cg"}:
        errors.append("stage1.role must be 'membrane_setup' or 'replacement_protein_cg'")
    if mode not in {"whole", "selected"}:
        errors.append("stage1.membrane_protein_mode must be 'whole' or 'selected'")
    orientation_enabled = bool(cfg.get("orientation_enabled", False))
    orientation_chain = cfg.get("orientation_chain")
    input_pdb = resolve_path(cfg.get("input_pdb", "inputs/aa_protein.pdb"), config)
    require_inputs = bool(config.get("run", {}).get("require_inputs_for_check", False))
    require_path_if_selected(input_pdb, "stage1 input_pdb", require_inputs, errors)
    if orientation_enabled and not orientation_chain:
        errors.append("stage1.orientation_chain is required when stage1.orientation_enabled is true")
    if orientation_chain and input_pdb.exists():
        chains = chain_ids(input_pdb)
        if orientation_chain not in chains:
            errors.append(
                f"stage1.orientation_chain {orientation_chain!r} absent from {relpath(input_pdb)}; "
                f"available chains: {', '.join(chains) or '<none>'}"
            )
    if mode == "selected" and not orientation_chain:
        errors.append("stage1.orientation_chain is required when membrane_protein_mode is selected")
    return errors


def check_stage2(config: dict[str, Any], run_id: str) -> list[str]:
    errors: list[str] = []
    mode = config.get("stage2", {}).get("mode", "smoke")
    handoff_mode = config.get("stage2", {}).get("handoff", {}).get("mode")
    if mode not in {"smoke", "production", "replace-protein-handoff", "scaffold-cg-smoke", "run-cg-scaffold-smoke"}:
        errors.append("stage2.mode must be 'smoke', 'production', 'replace-protein-handoff', 'scaffold-cg-smoke', or 'run-cg-scaffold-smoke'")
    if handoff_mode and handoff_mode != "replace_protein_in_equilibrated_scaffold":
        errors.append("stage2.handoff.mode must be replace_protein_in_equilibrated_scaffold")
    if handoff_mode == "replace_protein_in_equilibrated_scaffold":
        cfg = config.get("stage2", {})
        require_inputs = bool(config.get("run", {}).get("require_inputs_for_check", False))
        require_path_if_selected(resolve_path(cfg.get("scaffold_coordinates", ""), config), "stage2 scaffold_coordinates", require_inputs, errors)
        require_path_if_selected(resolve_path(cfg.get("scaffold_topology", ""), config), "stage2 scaffold_topology", require_inputs, errors)
        require_path_if_selected(resolve_path(cfg.get("scaffold_toppar", ""), config), "stage2 scaffold_toppar", require_inputs, errors)
    return errors


def check_stage3(config: dict[str, Any], run_id: str) -> list[str]:
    errors: list[str] = []
    cfg = config.get("stage3", {})
    placement = cfg.get("placement", {})
    if placement.get("mode") != "aa_reference_to_backmapped":
        errors.append("stage3.placement.mode must be aa_reference_to_backmapped")
    if placement.get("use_opm"):
        require_path_if_selected(resolve_path(cfg.get("input_opm_reference", "inputs/opm_reference.pdb"), config), "OPM reference", True, errors)
    if not placement.get("fixed_selection"):
        errors.append("stage3.placement.fixed_selection is required")
    if not placement.get("moving_selection"):
        errors.append("stage3.placement.moving_selection is required")
    plugin = cfg.get("plugins", {}).get("glycolipid_template_finalize", {})
    if plugin.get("enabled"):
        target_itp = resolve_path(plugin.get("target_itp", ""), config)
        require_path_if_selected(target_itp, "glycolipid_template_finalize target_itp", True, errors)
        for key in ("source_residue", "target_molecule_type"):
            if not plugin.get(key):
                errors.append(f"stage3.plugins.glycolipid_template_finalize.{key} is required when enabled")
    fixed_chain = placement.get("fixed_chain")
    moving_chain = placement.get("moving_chain")
    if fixed_chain == "" or moving_chain == "":
        errors.append("stage3 fixed_chain/moving_chain must be null or a non-empty chain ID")
    return errors


def check_stage4(config: dict[str, Any], run_id: str) -> list[str]:
    errors: list[str] = []
    cfg = config.get("stage4", {})
    ligand_params = cfg.get("ligand_params") or []
    if not isinstance(ligand_params, list):
        errors.append("stage4.ligand_params must be a list")
        ligand_params = []
    for item in ligand_params:
        require_path_if_selected(resolve_path(str(item), config), "configured ligand params", True, errors)
    try:
        resolve_stage4_dependencies(config, required=True)
    except DependencyError as exc:
        errors.append(str(exc))
    return errors


CHECKERS = {
    "stage1": check_stage1,
    "stage2": check_stage2,
    "stage3": check_stage3,
    "stage4": check_stage4,
}


def check_contract(config: dict[str, Any], run_id: str, stages: Iterable[str]) -> int:
    errors: list[str] = []
    for stage in stages:
        errors.extend(CHECKERS[stage](config, run_id))
    if errors:
        for err in errors:
            print(f"FAIL {err}", file=sys.stderr)
        return 1
    for stage in stages:
        print(f"PASS {stage.upper()} STATIC CHECK")
    return 0


def dry_run(config: dict[str, Any], run_id: str, stages: Iterable[str], output_dir: Path | None = None) -> int:
    paths = run_paths(run_id, output_dir)
    print(f"DRY RUN run_id={run_id}")
    for stage in stages:
        stage_work = paths.stage_work(stage)
        stage_out = paths.stage_output(stage)
        print(f"{stage}: work={relpath(stage_work)} output={relpath(stage_out)} log={relpath(paths.logs)}")
        if stage == "stage1":
            s1 = config["stage1"]
            print(f"{stage}: copy {s1['input_pdb']} -> work/{run_id}/stage1/martinize_input.pdb")
            print(f"{stage}: martinize2 -ff {s1.get('martinize_forcefield', 'martini3001')} [preserve conditional -noscfix]")
        elif stage == "stage2":
            handoff = config["stage2"].get("handoff", {})
            if handoff.get("mode") == "replace_protein_in_equilibrated_scaffold":
                print(f"{stage}: remove original scaffold protein and insert outputs/{run_id}/stage1/replacement_protein_cg.pdb")
                print(f"{stage}: scaffold={config['stage2'].get('scaffold_coordinates')} topology={config['stage2'].get('scaffold_topology')}")
            elif config["stage2"].get("mode") in {"scaffold-cg-smoke", "run-cg-scaffold-smoke"}:
                print(f"{stage}: run_CG bounded validation from supplied equilibrated CG gro={config['stage2'].get('scaffold_coordinates')}")
                print(f"{stage}: topology={config['stage2'].get('scaffold_topology')} toppar={config['stage2'].get('scaffold_toppar')}")
            else:
                print(f"{stage}: consume outputs/{run_id}/stage1/cg_system.gro and cg_topology.top")
                print(f"{stage}: production outputs enabled={bool(config['stage2'].get('production', False))}")
        elif stage == "stage3":
            placement = config["stage3"]["placement"]
            print(f"{stage}: fixed=stage2 cg_backmap_input frame, moving={config['stage3']['input_aa_protlig']}")
            print(f"{stage}: use_opm={bool(placement.get('use_opm'))} fixed_selection={placement.get('fixed_selection')} moving_selection={placement.get('moving_selection')}")
        elif stage == "stage4":
            print(f"{stage}: pdb2gmx owns generated protein-chain ITPs and final AA topology")
    return 0


def gmx_command() -> list[str]:
    try:
        return resolve_gmx_command()
    except PathResolutionError as exc:
        raise ContractError(str(exc)) from exc


def validate_user_pdb(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".pdb":
        raise ContractError(f"unsupported input format for --pdb: {path}; expected .pdb")
    if not path.exists():
        raise ContractError(f"input PDB does not exist: {path}")
    if not path.is_file():
        raise ContractError(f"input PDB is not a regular file: {path}")
    if path.stat().st_size == 0:
        raise ContractError(f"input PDB is empty: {path}")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise ContractError(f"input PDB is not readable: {path}: {exc}") from exc
    counts = atom_counts(path)
    if counts["heavy"] + counts["hydrogens"] == 0:
        raise ContractError(f"input PDB contains no ATOM/HETATM records: {path}")
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "file_size": path.stat().st_size,
        "atom_counts": counts,
    }


def scrub_effective_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key != "_meta"}


def simple_yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(simple_yaml_scalar(item) for item in value) + "]"
    text = str(value)
    if text == "" or any(char in text for char in ":#[]{}\",'") or text.strip() != text:
        return json.dumps(text)
    return text


def dump_simple_yaml(data: dict[str, Any], indent: int = 0) -> list[str]:
    rows: list[str] = []
    pad = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            rows.append(f"{pad}{key}:")
            rows.extend(dump_simple_yaml(value, indent + 2))
        else:
            rows.append(f"{pad}{key}: {simple_yaml_scalar(value)}")
    return rows


def write_public_run_metadata(
    config: dict[str, Any],
    *,
    run_id: str,
    pdb_path: Path,
    output_dir: Path,
    overwrite: bool,
    dry_run_mode: bool,
) -> dict[str, Any]:
    info = validate_user_pdb(pdb_path)
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise ContractError(f"output directory already exists and is not empty: {output_dir}; use --overwrite")
    output_dir.mkdir(parents=True, exist_ok=True)
    staged = output_dir / "input" / "original.pdb"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdb_path, staged)
    config.setdefault("run", {})["id"] = run_id
    config["stage1"]["input_pdb"] = str(staged)
    config["stage3"]["input_aa_protlig"] = str(staged)
    provenance = {
        "status": "DRY_RUN" if dry_run_mode else "STAGED",
        "run_id": run_id,
        "original_input_pdb": str(pdb_path),
        "staged_input_pdb": display_path(staged, ROOT),
        "input_sha256": info["sha256"],
        "input_file_size": info["file_size"],
        "input_atom_counts": info["atom_counts"],
        "output_dir": str(output_dir),
        "scientific_suitability": (
            "file-format checks passed; biochemical suitability, missing residues, ligands, chain identifiers, "
            "membrane orientation, and protonation require researcher review"
        ),
    }
    try:
        mstool_info = resolve_mstool(root=ROOT, import_module=False)
        provenance.update(mstool_info.provenance(ROOT))
    except Exception as exc:
        provenance["mstool_resolution_warning"] = str(exc)
    effective_config = scrub_effective_config(config)
    (output_dir / "effective_config.json").write_text(json.dumps(effective_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "effective_config.yaml").write_text("\n".join(dump_simple_yaml(effective_config)) + "\n", encoding="utf-8")
    (output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return provenance


def prepare_publish(output_dir: Path, overwrite: bool) -> Path:
    if output_dir.exists():
        if not overwrite:
            raise ContractError(f"published output already exists: {relpath(output_dir)}; use --overwrite")
        shutil.rmtree(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=str(output_dir.parent)))


def atomic_publish(staged_dir: Path, output_dir: Path, checksum_file: str = "checksums.sha256") -> None:
    checksums: list[str] = []
    for path in sorted(p for p in staged_dir.rglob("*") if p.is_file()):
        checksums.append(f"{file_sha256(path)}  {path.relative_to(staged_dir)}")
    (staged_dir / checksum_file).write_text("\n".join(checksums) + "\n", encoding="utf-8")
    os.replace(staged_dir, output_dir)


def stage_main(stage: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "workflow.yaml"))
    parser.add_argument("--run-id")
    parser.add_argument("--mode")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(resolve_cli_path(args.config))
    run_id = run_id_from(config, args.run_id)
    if args.overwrite:
        config.setdefault("run", {})["overwrite"] = True
    if args.check:
        return check_contract(config, run_id, [stage])
    if args.dry_run:
        rc = check_contract(config, run_id, [stage])
        return rc if rc else dry_run(config, run_id, [stage])
    from test_membrane_workflow import run_stage
    return run_stage(ROOT, stage, config, run_id, bool(config.get("run", {}).get("overwrite", False)), args.mode)


def pipeline_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "MembraneForger public workflow control layer. This validates and stages user PDB inputs; "
            "long scientific stages remain explicit stage-gated commands."
        )
    )
    parser.add_argument("--config", default=str(ROOT / "config" / "workflow.yaml"))
    parser.add_argument("--pdb", help="User-supplied .pdb structure to stage for this run")
    parser.add_argument("--output-dir", help="Run-specific output directory")
    parser.add_argument("--run-id")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--check-dependencies", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json-status", action="store_true")
    args = parser.parse_args(argv)
    config_path = resolve_cli_path(args.config)
    config = load_config(config_path)
    run_id = run_id_from(config, args.run_id)
    if args.overwrite:
        config.setdefault("run", {})["overwrite"] = True
    output_dir = resolve_cli_path(args.output_dir) if args.output_dir else ROOT / "outputs" / run_id
    if args.pdb:
        pdb_path = resolve_cli_path(args.pdb)
        write_public_run_metadata(
            config,
            run_id=run_id,
            pdb_path=pdb_path,
            output_dir=output_dir,
            overwrite=bool(config.get("run", {}).get("overwrite", False)),
            dry_run_mode=bool(args.dry_run),
        )
        print(f"INFO: staged input PDB at {output_dir / 'input' / 'original.pdb'}")
        print(f"INFO: wrote effective config at {output_dir / 'effective_config.yaml'}")
        print(f"INFO: wrote provenance at {output_dir / 'provenance.json'}")
    if args.check_dependencies:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dependency_resolver.py"), "--config", str(ROOT / "config" / "dependencies.yaml")],
            text=True,
        )
        return result.returncode
    if args.check:
        rc = check_contract(config, run_id, STAGE_NAMES)
    elif args.dry_run:
        rc = check_contract(config, run_id, STAGE_NAMES)
        if rc == 0:
            rc = dry_run(config, run_id, STAGE_NAMES, output_dir if args.pdb or args.output_dir else None)
    elif args.pdb:
        rc = check_contract(config, run_id, STAGE_NAMES)
        if rc == 0:
            print("INFO: public input staging complete")
            print("INFO: run stages explicitly after reviewing effective_config.json and provenance.json")
            print(f"INFO: example next command: bash {ROOT / 'stages' / 'stage1_setup_cg' / 'run.sh'} --config {output_dir / 'effective_config.yaml'} --run-id {run_id}")
    else:
        print("ERROR: use --check, --check-dependencies, --dry-run, or --pdb for the portable workflow control layer", file=sys.stderr)
        rc = 2
    if args.json_status:
        print(json.dumps(completion_status(static_pass=(rc == 0 and args.check), dry_run_pass=(rc == 0 and args.dry_run)), indent=2))
    return rc


def completion_status(static_pass: bool = False, dry_run_pass: bool = False) -> dict[str, dict[str, str]]:
    status: dict[str, dict[str, str]] = {}
    for stage in STAGE_NAMES:
        status[stage.upper()] = {
            "STATIC": "PASS" if static_pass else "NOT RUN",
            "DRY-RUN": "PASS" if dry_run_pass else "NOT RUN",
            "MOCK": "NOT RUN",
            "REAL": "NOT RUN",
            "REGRESSION": "NOT RUN",
            "FULL": "NOT RUN",
        }
    return status


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in STAGE_NAMES:
            stage = argv.pop(0)
            return stage_main(stage, argv)
        return pipeline_main(argv)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except PathResolutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
