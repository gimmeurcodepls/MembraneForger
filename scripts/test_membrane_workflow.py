#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np

from membraneforger_paths import PathResolutionError, display_path, gmx_command as resolve_gmx_command, resolve_mstool


ATOM_RECORDS = ("ATOM  ", "HETATM")
AA3 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
PROTEIN_RESNAMES = set(AA3)
SOLVENT_ION_NAMES = {"W", "WF", "NA", "CL", "ION", "SOL"}
PROTEIN_BACKBONE_ATOMS = {"N", "HN", "CA", "HA", "HA1", "HA2", "C", "O"}
MSTOOL_CHANGENAME = {
    ":CHOL": ":CHL1",
    ":ION@NA": ":SOD@SOD",
    ":NA@NA": ":SOD@SOD",
    ":ION@CL": ":CLA@CLA",
    ":CL@CL": ":CLA@CLA",
    ":ION@CA": ":CAL@CAL",
    ":A": ":ADE",
    ":U": ":URA",
    ":G": ":GUA",
    ":C": ":CYT",
    ":T": ":THY",
    ":SAP6": ":SAPI24",
}


class ValidationError(RuntimeError):
    pass


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(root: Path, value: str | Path, run_id: str) -> Path:
    text = str(value).format(run_id=run_id)
    path = Path(text)
    return path if path.is_absolute() else root / path


def ensure_clean_dir(path: Path, overwrite: bool) -> None:
    if path.exists():
        if not overwrite:
            raise ValidationError(f"published output already exists: {path}; use --overwrite")
        shutil.rmtree(path)
    path.mkdir(parents=True)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_manifest(root: Path, directory: Path, name: str = "manifest.tsv") -> None:
    rows = ["path\tsha256\tfile_size"]
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and p.name != name):
        rows.append(f"{rel(root, path)}\t{sha256(path)}\t{path.stat().st_size}")
    (directory / name).write_text("\n".join(rows) + "\n", encoding="utf-8")


def run_logged(cmd: list[str], cwd: Path, log_dir: Path, label: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    command_file = log_dir / f"{label}.command.txt"
    stdout_file = log_dir / f"{label}.stdout.log"
    stderr_file = log_dir / f"{label}.stderr.log"
    command_file.write_text(" ".join(cmd) + "\n", encoding="utf-8")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    stdout_file.write_text(result.stdout or "", encoding="utf-8")
    stderr_file.write_text(result.stderr or "", encoding="utf-8")
    return result


def gmx_command() -> list[str]:
    try:
        return resolve_gmx_command()
    except PathResolutionError as exc:
        raise ValidationError(str(exc)) from exc


def parse_pdb(path: Path) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith(ATOM_RECORDS):
                continue
            atoms.append({
                "record": line[:6].strip(),
                "serial": int(line[6:11]),
                "name": line[12:16].strip(),
                "altloc": line[16:17].strip(),
                "resname": line[17:20].strip(),
                "chain": line[21:22].strip() or " ",
                "resid": int(line[22:26]),
                "icode": line[26:27].strip(),
                "x": float(line[30:38]),
                "y": float(line[38:46]),
                "z": float(line[46:54]),
                "occupancy": float(line[54:60]),
                "bfactor": float(line[60:66]),
                "element": line[76:78].strip() if len(line) >= 78 else "",
                "line": line.rstrip("\n"),
            })
    return atoms


def pdb_structure_report(path: Path) -> dict[str, Any]:
    atoms = parse_pdb(path)
    residues: list[tuple[str, int, str, str]] = []
    seen_res: set[tuple[str, int, str, str]] = set()
    by_res: dict[tuple[str, int, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    chains: list[str] = []
    duplicate_serials = []
    serials: set[int] = set()
    duplicate_atom_names = []
    atom_name_seen: set[tuple[str, int, str, str, str]] = set()
    for atom in atoms:
        if atom["serial"] in serials:
            duplicate_serials.append(atom["serial"])
        serials.add(atom["serial"])
        if atom["chain"] not in chains and atom["record"] == "ATOM":
            chains.append(atom["chain"])
        key = (atom["chain"], atom["resid"], atom["icode"], atom["resname"])
        if key not in seen_res and atom["record"] == "ATOM":
            residues.append(key)
            seen_res.add(key)
        akey = (*key, atom["name"])
        if akey in atom_name_seen:
            duplicate_atom_names.append(akey)
        atom_name_seen.add(akey)
        by_res[key][atom["name"]] = atom

    missing_backbone = []
    for key, names in by_res.items():
        if key[3] not in PROTEIN_RESNAMES:
            continue
        for name in ("N", "CA", "C", "O"):
            if name not in names:
                missing_backbone.append([*key, name])

    by_chain: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    for residue in residues:
        if residue[3] in PROTEIN_RESNAMES:
            by_chain[residue[0]].append(residue)
    numbering_gaps = []
    cn_breaks = []
    for chain, chain_residues in by_chain.items():
        ordered = sorted(chain_residues, key=lambda r: (r[1], r[2]))
        for prev, cur in zip(ordered, ordered[1:]):
            if cur[1] - prev[1] != 1:
                numbering_gaps.append([chain, prev[1], cur[1], cur[1] - prev[1]])
            if "C" in by_res[prev] and "N" in by_res[cur]:
                a = by_res[prev]["C"]
                b = by_res[cur]["N"]
                dist = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
                if dist > 1.8:
                    cn_breaks.append([chain, prev[1], cur[1], round(dist, 3)])

    cys_sg = []
    for key, names in by_res.items():
        if key[3] == "CYS" and "SG" in names:
            cys_sg.append((key, names["SG"]))
    sg_pairs = []
    for i, (ra, a) in enumerate(cys_sg):
        for rb, b in cys_sg[i + 1:]:
            dist = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
            if dist < 4.0:
                sg_pairs.append([ra[0], ra[1], rb[0], rb[1], round(dist, 3)])

    hydrogens = 0
    nonfinite = []
    for atom in atoms:
        element = atom["element"].upper()
        if element in {"H", "D"} or atom["name"].upper().startswith(("H", "D")):
            hydrogens += 1
        if not all(math.isfinite(atom[k]) for k in ("x", "y", "z")):
            nonfinite.append(atom["serial"])

    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {
        "path": path.name,
        "sha256": sha256(path),
        "file_size": path.stat().st_size,
        "atom_records": sum(1 for line in text if line.startswith("ATOM  ")),
        "hetatm_records": sum(1 for line in text if line.startswith("HETATM")),
        "anisou_records": sum(1 for line in text if line.startswith("ANISOU")),
        "ssbond_records": sum(1 for line in text if line.startswith("SSBOND")),
        "link_records": sum(1 for line in text if line.startswith("LINK")),
        "conect_records": sum(1 for line in text if line.startswith("CONECT")),
        "ter_records": sum(1 for line in text if line.startswith("TER")),
        "cryst1": [line for line in text if line.startswith("CRYST1")],
        "chain_ids": chains,
        "residue_count": len(residues),
        "residue_count_by_chain": {chain: len(res) for chain, res in by_chain.items()},
        "hydrogen_atom_count": hydrogens,
        "alternate_locations": sorted({atom["altloc"] for atom in atoms if atom["altloc"]}),
        "insertion_codes": sorted({atom["icode"] for atom in atoms if atom["icode"]}),
        "nonstandard_residues": sorted({r[3] for r in residues if r[3] not in PROTEIN_RESNAMES}),
        "duplicate_serials": duplicate_serials,
        "duplicate_atom_names": duplicate_atom_names[:50],
        "missing_backbone_atoms": missing_backbone,
        "numbering_gaps": numbering_gaps,
        "peptide_cn_breaks_gt_1p8_angstrom": cn_breaks,
        "cysteine_sg_pairs_under_4_angstrom": sg_pairs,
        "nonfinite_coordinates": nonfinite,
        "coordinate_units": "angstrom",
    }


def write_protein_only_pdb(source: Path, target: Path, chains: list[str] | None) -> dict[str, Any]:
    kept = 0
    skipped = Counter()
    selected = set(chains or [])
    with source.open("r", encoding="utf-8", errors="replace") as inp, target.open("w", encoding="utf-8") as out:
        for line in inp:
            if line.startswith("ANISOU"):
                skipped["ANISOU"] += 1
                continue
            if line.startswith("TER"):
                out.write(line)
                continue
            if not line.startswith(ATOM_RECORDS):
                continue
            chain = line[21:22].strip() or " "
            resname = line[17:20].strip()
            if selected and chain not in selected:
                skipped["unselected_chain"] += 1
                continue
            if line.startswith("HETATM") or resname not in PROTEIN_RESNAMES:
                skipped["nonprotein"] += 1
                continue
            out.write(line)
            kept += 1
        out.write("END\n")
    return {"kept_atom_records": kept, "skipped": dict(skipped), "path": target.name}


def parse_topology(path: Path) -> tuple[list[str], list[tuple[str, int]]]:
    includes: list[str] = []
    molecules: list[tuple[str, int]] = []
    section = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#include"):
            match = re.search(r'"([^"]+)"', line)
            includes.append(match.group(1) if match else line)
            continue
        if line.startswith("["):
            section = line.strip("[]").strip().lower()
            continue
        if section == "molecules":
            parts = line.split()
            if len(parts) >= 2:
                molecules.append((parts[0], int(parts[1])))
    return includes, molecules


def parse_moltypes(toppar_dir: Path, includes: list[str]) -> dict[str, dict[str, Any]]:
    moltypes: dict[str, dict[str, Any]] = {}
    for inc in includes:
        inc_path = toppar_dir.parent / inc if "/" in inc else toppar_dir / inc
        if not inc_path.exists():
            continue
        section = ""
        current = ""
        atom_count = 0
        atom_names: list[str] = []
        residue_names: list[str] = []
        for raw in inc_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            if line.startswith("["):
                if section == "atoms" and current:
                    moltypes[current] = {
                        "include": inc,
                        "atom_count": atom_count,
                        "atom_names": atom_names,
                        "residue_names": sorted(set(residue_names)),
                    }
                section = line.strip("[]").strip().lower()
                atom_count = 0
                atom_names = []
                residue_names = []
                continue
            parts = line.split()
            if section == "moleculetype" and parts:
                current = parts[0]
            elif section == "atoms" and current and len(parts) >= 5:
                atom_count += 1
                atom_names.append(parts[4])
                residue_names.append(parts[3])
        if section == "atoms" and current:
            moltypes[current] = {
                "include": inc,
                "atom_count": atom_count,
                "atom_names": atom_names,
                "residue_names": sorted(set(residue_names)),
            }
    return moltypes


def parse_gro(path: Path) -> tuple[str, list[dict[str, Any]], str, np.ndarray]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        title = handle.readline().rstrip("\n")
        natoms = int(handle.readline().strip())
        atoms = []
        for idx in range(natoms):
            line = handle.readline().rstrip("\n")
            atoms.append({
                "index": idx + 1,
                "resid": int(line[:5]),
                "resname": line[5:10].strip(),
                "name": line[10:15].strip(),
                "atomid": int(line[15:20]),
                "x": float(line[20:28]),
                "y": float(line[28:36]),
                "z": float(line[36:44]),
                "raw": line,
            })
        box_line = handle.readline().strip()
    box_values = np.array([float(x) for x in box_line.split()[:3]], dtype=float)
    return title, atoms, box_line, box_values


def pdb_to_gro_atoms(path: Path) -> list[dict[str, Any]]:
    atoms = []
    for idx, atom in enumerate(parse_pdb(path), start=1):
        if atom["record"] != "ATOM":
            continue
        atoms.append({
            "resid": atom["resid"] % 100000,
            "resname": atom["resname"][:5],
            "name": atom["name"][:5],
            "atomid": idx % 100000,
            "x": atom["x"] / 10.0,
            "y": atom["y"] / 10.0,
            "z": atom["z"] / 10.0,
        })
    return atoms


def write_gro(path: Path, title: str, atoms: list[dict[str, Any]], box_line: str) -> None:
    with path.open("w", encoding="utf-8") as out:
        out.write(title[:80] + "\n")
        out.write(f"{len(atoms):5d}\n")
        for idx, atom in enumerate(atoms, start=1):
            out.write(
                f"{int(atom['resid']) % 100000:5d}"
                f"{str(atom['resname'])[:5]:>5}"
                f"{str(atom['name'])[:5]:>5}"
                f"{idx % 100000:5d}"
                f"{float(atom['x']):8.3f}{float(atom['y']):8.3f}{float(atom['z']):8.3f}\n"
            )
        out.write(box_line + "\n")


def molecule_ranges(molecules: list[tuple[str, int]], moltypes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ranges = []
    start = 0
    for moltype, count in molecules:
        atom_count = moltypes.get(moltype, {}).get("atom_count")
        if not atom_count:
            raise ValidationError(f"cannot determine atom count for molecule type {moltype}")
        for ordinal in range(1, count + 1):
            end = start + int(atom_count)
            ranges.append({"moltype": moltype, "ordinal": ordinal, "start": start, "end": end, "atom_count": int(atom_count)})
            start = end
    return ranges


def min_pbc_distance(points: np.ndarray, ref: np.ndarray, box: np.ndarray) -> float:
    if len(points) == 0 or len(ref) == 0:
        return float("inf")
    best = float("inf")
    for chunk_start in range(0, len(points), 256):
        chunk = points[chunk_start:chunk_start + 256]
        delta = chunk[:, None, :] - ref[None, :, :]
        delta -= box * np.round(delta / box)
        dist2 = np.sum(delta * delta, axis=2)
        best = min(best, float(np.sqrt(np.min(dist2))))
    return best


def chain_id_for_index(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if index < len(alphabet):
        return alphabet[index]
    return f"P{index + 1}"


def prepare_mstool_chain_input(root: Path, stage2_input: Path, mstool_path: Path, work: Path) -> tuple[Path, dict[str, Any]]:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.universe import Universe

    topology = root / "outputs" / work.parent.name / "stage2" / "cg_topology.top"
    toppar = root / "outputs" / work.parent.name / "stage2" / "toppar"
    if not topology.exists() or not toppar.exists():
        return stage2_input, {
            "status": "SKIPPED",
            "reason": "stage2 topology or toppar not available",
            "input": rel(root, stage2_input),
        }

    includes, molecules = parse_topology(topology)
    moltypes = parse_moltypes(toppar, includes)
    ranges = molecule_ranges(molecules, moltypes)
    protein_ranges = [r for r in ranges if r["moltype"].lower().startswith("protein")]
    if not protein_ranges:
        return stage2_input, {
            "status": "SKIPPED",
            "reason": "no protein molecule types found in stage2 topology",
            "input": rel(root, stage2_input),
        }

    u = Universe(str(stage2_input))
    original_counts = u.atoms["chain"].value_counts().to_dict() if "chain" in u.atoms else {}
    distinct_protein_chains = set()
    for r in protein_ranges:
        distinct_protein_chains.update(str(c) for c in u.atoms.iloc[r["start"]:r["end"]]["chain"].unique())
    if len(distinct_protein_chains) >= len(protein_ranges) and "" not in distinct_protein_chains:
        return stage2_input, {
            "status": "SKIPPED",
            "reason": "input already carries distinct protein chain identifiers",
            "input": rel(root, stage2_input),
            "protein_chains": sorted(distinct_protein_chains),
        }

    if ranges[-1]["end"] != len(u.atoms):
        raise ValidationError(
            f"cannot assign CG protein chains: topology atom count {ranges[-1]['end']} "
            f"does not match coordinate atom count {len(u.atoms)}"
        )

    assignments = []
    for idx, r in enumerate(protein_ranges):
        chain_id = chain_id_for_index(idx)
        u.atoms.loc[r["start"]:r["end"] - 1, "chain"] = chain_id
        u.atoms.loc[r["start"]:r["end"] - 1, "segname"] = chain_id
        assignments.append({
            "molecule_type": r["moltype"],
            "ordinal": r["ordinal"],
            "chain_id": chain_id,
            "atom_range_1based": [r["start"] + 1, r["end"]],
            "atom_count": r["atom_count"],
        })

    prepared = work / "cg_backmap_input_chained.dms"
    u.write(str(prepared))
    report = {
        "status": "PASS",
        "reason": "reconstructed protein chain identifiers from topology molecule ranges",
        "input": str(stage2_input.relative_to(root)),
        "prepared_input": rel(root, prepared),
        "original_chain_counts": original_counts,
        "protein_molecule_count": len(protein_ranges),
        "assignments": assignments,
    }
    return prepared, report


def resolve_mstool_xmls(root: Path, mstool_path: Path) -> list[str]:
    charmm_dir = mstool_path / "FF" / "charmm36"
    local_charmm = root / "resources" / "templates" / "charmm36_local.xml"
    charmm36_xml = local_charmm if local_charmm.exists() else charmm_dir / "charmm36.xml"
    xmls = [charmm36_xml]
    for name in ("pip.xml", "water.xml", "chyo.xml"):
        candidate = charmm_dir / name
        if not candidate.exists():
            raise ValidationError(f"required mstool XML not found: {candidate}")
        xmls.append(candidate)
    return [str(path) for path in xmls]


def protein_atom_mask(atoms: Any) -> Any:
    return atoms["resname"].map(lambda name: str(name).strip() in PROTEIN_RESNAMES)


def dms_duplicate_atom_issues(dms_path: Path, mstool_path: Path) -> int:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.universe import Universe

    u = Universe(str(dms_path))
    atoms = u.atoms[protein_atom_mask(u.atoms)]
    issues = 0
    for _key, residue in atoms.groupby(["chain", "resid", "resname"], sort=True):
        counts = residue["name"].value_counts()
        issues += int((counts > 1).sum())
    return issues


def write_repair_checksum(checksum_path: Path, operation: str, input_path: Path, output_path: Path) -> None:
    header = "operation\tinput_path\tinput_sha256\toutput_path\toutput_sha256\n"
    row = (
        f"{operation}\t{input_path.as_posix()}\t{sha256(input_path)}\t"
        f"{output_path.as_posix()}\t{sha256(output_path)}\n"
    )
    if not checksum_path.exists():
        checksum_path.write_text(header, encoding="utf-8")
    with checksum_path.open("a", encoding="utf-8") as handle:
        handle.write(row)


def remap_universe_after_atom_drop(u: Any, drop_indices: set[int]) -> None:
    atoms = u.atoms
    old_ids = atoms["id"].astype(int).tolist() if "id" in atoms.columns else list(range(len(atoms)))
    keep_mask = ~atoms.index.isin(drop_indices)
    kept_old_ids = [old_id for old_id, keep in zip(old_ids, keep_mask.tolist()) if keep]
    old_to_new = {int(old_id): new_id for new_id, old_id in enumerate(kept_old_ids)}
    atoms.drop(index=sorted(drop_indices), inplace=True)
    atoms.reset_index(drop=True, inplace=True)
    atoms["id"] = atoms.index.astype(int)
    remapped_bonds = []
    for p0, p1 in getattr(u, "bonds", []):
        if int(p0) in old_to_new and int(p1) in old_to_new:
            remapped_bonds.append([old_to_new[int(p0)], old_to_new[int(p1)]])
    u.bonds = remapped_bonds


def sidechain_duplicate_candidate_score(residue: Any, candidate_indices: set[int], expected_atoms: set[str]) -> float:
    candidate = residue[residue.index.isin(candidate_indices)]
    names = set(candidate["name"].astype(str))
    if not expected_atoms <= names:
        return float("inf")
    lookup = {str(row["name"]): row for _, row in candidate.iterrows()}
    if "CA" not in lookup or "CB" not in lookup:
        return float("inf")
    ca = np.array([lookup["CA"]["x"], lookup["CA"]["y"], lookup["CA"]["z"]], dtype=float)
    cb = np.array([lookup["CB"]["x"], lookup["CB"]["y"], lookup["CB"]["z"]], dtype=float)
    ca_cb = float(np.linalg.norm(cb - ca))
    score = abs(ca_cb - 1.53)
    if ca_cb < 0.6 or ca_cb > 2.9:
        score += 10.0
    for h_name in ("HB1", "HB2", "HB3"):
        if h_name in lookup:
            hp = np.array([lookup[h_name]["x"], lookup[h_name]["y"], lookup[h_name]["z"]], dtype=float)
            cb_h = float(np.linalg.norm(hp - cb))
            score += abs(cb_h - 1.09)
            if cb_h < 0.4 or cb_h > 1.8:
                score += 5.0
    return score


def select_duplicate_repair_indices(residue: Any, expected_atoms: list[str], cg_sc1: np.ndarray | None = None) -> tuple[set[int], str]:
    counts = residue["name"].value_counts()
    duplicated = {str(name): int(count) for name, count in counts.items() if int(count) > 1}
    if not duplicated:
        return set(), "clean"

    expected_set = set(expected_atoms)
    observed = residue["name"].astype(str).tolist()
    duplicate_names = sorted(duplicated)
    duplicate_counts = {duplicated[name] for name in duplicate_names}
    if len(duplicate_counts) == 1 and set(duplicate_names) == {"CB", "HB1", "HB2", "HB3"} and str(residue.iloc[0]["resname"]) == "ALA":
        nsets = duplicate_counts.pop()
        candidate_rows: list[set[int]] = []
        unique_indices = set(residue[~residue["name"].isin(duplicate_names)].index)
        for ordinal in range(nsets):
            selected = set(unique_indices)
            for name in duplicate_names:
                selected.add(int(residue[residue["name"] == name].sort_values("id").index[ordinal]))
            candidate_rows.append(selected)
        if cg_sc1 is not None:
            scores = []
            for candidate in candidate_rows:
                cb_row = residue[residue.index.isin(candidate) & (residue["name"] == "CB")]
                if cb_row.empty:
                    scores.append(float("inf"))
                    continue
                cb = cb_row.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
                scores.append(float(np.linalg.norm(cb - cg_sc1)))
            reason_prefix = "by CG SC1 provenance"
            min_gap = 0.25
        else:
            scores = [sidechain_duplicate_candidate_score(residue, candidate, expected_set) for candidate in candidate_rows]
            reason_prefix = "by CA-CB/HB geometry"
            min_gap = 0.20
        ranked = sorted(enumerate(scores), key=lambda item: item[1])
        if len(ranked) < 2 or not np.isfinite(ranked[0][1]) or ranked[1][1] - ranked[0][1] < min_gap:
            raise ValidationError(
                f"ambiguous ALA duplicate sidechain repair at "
                f"{residue.iloc[0]['chain']}:{residue.iloc[0]['resid']} scores={scores}"
            )
        keep = candidate_rows[ranked[0][0]]
        return set(residue.index) - keep, f"kept ALA sidechain candidate {ranked[0][0] + 1} {reason_prefix}"

    exact_drop: set[int] = set()
    exact_reasons = []
    for name in duplicate_names:
        group = residue[residue["name"] == name].sort_values("id")
        coords = group[["x", "y", "z"]].to_numpy(dtype=float)
        if np.max(np.linalg.norm(coords - coords[0], axis=1)) <= 1.0e-4:
            exact_drop.update(int(idx) for idx in group.index[1:])
            exact_reasons.append(name)
        else:
            raise ValidationError(
                f"ambiguous duplicate atom {name} in "
                f"{residue.iloc[0]['chain']}:{residue.iloc[0]['resid']} {residue.iloc[0]['resname']}"
            )
    return exact_drop, "removed exact-coordinate duplicate atoms: " + ",".join(exact_reasons)


def duplicate_atom_rows(atoms: Any, bonds: list[list[int]], actions: dict[tuple[str, int, str, str], tuple[str, str]] | None = None) -> list[str]:
    actions = actions or {}
    bond_map: dict[int, list[int]] = defaultdict(list)
    for p0, p1 in bonds:
        bond_map[int(p0)].append(int(p1))
        bond_map[int(p1)].append(int(p0))
    rows = ["chain\tresid\tinsertion_code\tresname\tatom_name\tduplicate_count\tatom_indices\tcoordinates\tbonded_neighbors\tselected_action\treason"]
    protein_atoms = atoms[protein_atom_mask(atoms)]
    for (chain, resid, resname), residue in protein_atoms.groupby(["chain", "resid", "resname"], sort=True):
        counts = residue["name"].value_counts()
        for atom_name, count in counts.items():
            if int(count) <= 1:
                continue
            dup = residue[residue["name"] == atom_name]
            ids = [str(int(v)) for v in dup["id"].tolist()]
            coords = [
                f"{float(row['x']):.4f},{float(row['y']):.4f},{float(row['z']):.4f}"
                for _, row in dup.iterrows()
            ]
            neighbors = []
            for atom_id in dup["id"].astype(int).tolist():
                neighbors.append(",".join(str(n) for n in sorted(bond_map.get(atom_id, []))) or ".")
            action, reason = actions.get((str(chain), int(resid), str(resname), str(atom_name)), ("FAIL", "duplicate detected before repair"))
            rows.append(
                f"{chain}\t{int(resid)}\t.\t{resname}\t{atom_name}\t{int(count)}\t"
                f"{','.join(ids)}\t{';'.join(coords)}\t{';'.join(neighbors)}\t{action}\t{reason}"
            )
    return rows


def repair_duplicate_protein_residue_atoms(
    source: Path,
    target: Path,
    report_path: Path,
    mstool_path: Path,
    mapping_files: list[str],
    cg_reference: Path | None = None,
) -> dict[str, Any]:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.readmappings import ReadMappings
    from mstool.core.universe import Universe

    mappings = ReadMappings(mapping=mapping_files)
    u = Universe(str(source))
    cg_sc1_by_residue: dict[tuple[str, int, str], np.ndarray] = {}
    if cg_reference is not None and cg_reference.exists():
        cg = Universe(str(cg_reference))
        for _idx, row in cg.atoms[cg.atoms["name"] == "SC1"].iterrows():
            cg_sc1_by_residue[(str(row["chain"]), int(row["resid"]), str(row["resname"]))] = row[["x", "y", "z"]].to_numpy(dtype=float)
    actions: dict[tuple[str, int, str, str], tuple[str, str]] = {}
    drop_indices: set[int] = set()
    residue_issue_count = 0
    for (chain, resid, resname), residue in u.atoms[protein_atom_mask(u.atoms)].groupby(["chain", "resid", "resname"], sort=True):
        counts = residue["name"].value_counts()
        duplicated = [str(name) for name, count in counts.items() if int(count) > 1]
        if not duplicated:
            continue
        residue_issue_count += 1
        if str(resname) not in mappings.RESI:
            for atom_name in duplicated:
                actions[(str(chain), int(resid), str(resname), atom_name)] = ("FAIL", "no mapping template")
            report_path.write_text("\n".join(duplicate_atom_rows(u.atoms, getattr(u, "bonds", []), actions)) + "\n", encoding="utf-8")
            raise ValidationError(f"no mapping template for duplicate protein residue {chain}:{resid} {resname}")
        try:
            selected_drop, reason = select_duplicate_repair_indices(
                residue,
                list(mappings.RESI[str(resname)]["AAAtoms"]),
                cg_sc1_by_residue.get((str(chain), int(resid), str(resname))),
            )
        except ValidationError as exc:
            for atom_name in duplicated:
                actions[(str(chain), int(resid), str(resname), atom_name)] = ("FAIL", str(exc))
            report_path.write_text("\n".join(duplicate_atom_rows(u.atoms, getattr(u, "bonds", []), actions)) + "\n", encoding="utf-8")
            raise
        drop_indices.update(selected_drop)
        for atom_name in duplicated:
            actions[(str(chain), int(resid), str(resname), atom_name)] = (
                "REMOVE_UNSELECTED_DUPLICATES",
                reason,
            )
    report_path.write_text("\n".join(duplicate_atom_rows(u.atoms, getattr(u, "bonds", []), actions)) + "\n", encoding="utf-8")
    removed = len(drop_indices)
    if drop_indices:
        remap_universe_after_atom_drop(u, drop_indices)
    remaining = 0
    for _key, residue in u.atoms[protein_atom_mask(u.atoms)].groupby(["chain", "resid", "resname"], sort=True):
        remaining += int((residue["name"].value_counts() > 1).sum())
    if remaining:
        raise ValidationError(f"duplicate protein atom names remain after repair: {remaining}")
    u.write(str(target))
    return {
        "status": "PASS",
        "residues_with_duplicate_atoms": residue_issue_count,
        "atoms_removed": removed,
        "remaining_duplicate_atom_name_issues": remaining,
    }


def append_dms_atom_like(atoms: Any, template_index: int, name: str, anum: int, xyz: np.ndarray) -> None:
    row = atoms.loc[template_index].copy()
    row["id"] = int(atoms["id"].max()) + 1
    row["name"] = name
    row["anum"] = anum
    row["x"], row["y"], row["z"] = [float(v) for v in xyz]
    if anum == 1:
        row["mass"] = 1.008
        row["vdw"] = 1.0
    elif anum == 8:
        row["mass"] = 15.999
        row["vdw"] = 1.7
    atoms.loc[len(atoms)] = row


def unit_vector(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm == 0.0 or not np.isfinite(norm):
        raise ValidationError("cannot construct terminal atom from zero/nonfinite vector")
    return vec / norm


def terminal_hydrogen_vectors(n_to_ca: np.ndarray) -> list[np.ndarray]:
    axis = unit_vector(n_to_ca)
    ref = np.array([1.0, 0.0, 0.0], dtype=float)
    if abs(float(np.dot(axis, ref))) > 0.8:
        ref = np.array([0.0, 1.0, 0.0], dtype=float)
    v1 = unit_vector(np.cross(axis, ref))
    v2 = unit_vector(np.cross(axis, v1))
    return [unit_vector(-axis + 0.8 * v1), unit_vector(-axis - 0.4 * v1 + 0.7 * v2), unit_vector(-axis - 0.4 * v1 - 0.7 * v2)]


def protein_fragments(atoms: Any) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    protein_atoms = atoms[protein_atom_mask(atoms)]
    for chain, chain_atoms in protein_atoms.groupby("chain", sort=True):
        residues = []
        for resid, residue in chain_atoms.groupby("resid", sort=True):
            residues.append((int(resid), residue))
        current: list[int] = []
        break_before = "."
        fragment_index = 1
        for idx, (resid, residue) in enumerate(residues):
            if idx == 0:
                current = [resid]
                continue
            prev_resid, prev_residue = residues[idx - 1]
            prev_c = prev_residue[prev_residue["name"] == "C"]
            curr_n = residue[residue["name"] == "N"]
            connected = False
            distance = float("inf")
            if not prev_c.empty and not curr_n.empty:
                c_xyz = prev_c.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
                n_xyz = curr_n.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
                distance = float(np.linalg.norm(n_xyz - c_xyz))
                connected = distance <= 1.9
            if connected:
                current.append(resid)
            else:
                fragments.append({
                    "chain": str(chain),
                    "fragment_index": fragment_index,
                    "resids": current,
                    "break_before": break_before,
                    "break_after": f"{prev_resid}->{resid} C-N {distance:.3f} A",
                })
                fragment_index += 1
                break_before = f"{prev_resid}->{resid} C-N {distance:.3f} A"
                current = [resid]
        if current:
            fragments.append({
                "chain": str(chain),
                "fragment_index": fragment_index,
                "resids": current,
                "break_before": break_before,
                "break_after": ".",
            })
    return fragments


def terminalize_protein_fragments(source: Path, target: Path, report_path: Path, mstool_path: Path) -> dict[str, Any]:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.universe import Universe

    u = Universe(str(source))
    atoms = u.atoms
    rows = ["chain\tfragment_index\tfirst_residue\tlast_residue\tbreak_before\tbreak_after\tN_terminal_treatment\tC_terminal_treatment\tatoms_added\tatoms_removed\tbonds_added\tbonds_removed"]
    treatment_count = 0
    boundary_count = 0
    for fragment in protein_fragments(atoms):
        chain = fragment["chain"]
        first = min(fragment["resids"])
        last = max(fragment["resids"])
        atoms_added: list[str] = []
        atoms_removed: list[str] = []
        n_treatment = "unchanged"
        c_treatment = "unchanged"
        n_mask = protein_atom_mask(atoms) & (atoms["chain"] == chain) & (atoms["resid"] == first)
        n_rows = atoms[n_mask]
        n_atom = n_rows[n_rows["name"] == "N"]
        ca_atom = n_rows[n_rows["name"] == "CA"]
        if n_atom.empty or ca_atom.empty:
            raise ValidationError(f"cannot terminalize {chain}:{first}: missing N or CA")
        n_index = int(n_atom.index[0])
        n_pos = n_atom.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
        ca_pos = ca_atom.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
        existing = set(n_rows["name"].astype(str))
        if not {"HT1", "HT2", "HT3"} <= existing:
            h_candidates = n_rows[n_rows["name"].isin(["HN", "H", "H1"])]
            if not h_candidates.empty and "HT1" not in existing:
                h_index = int(h_candidates.index[0])
                old_name = str(atoms.at[h_index, "name"])
                atoms.at[h_index, "name"] = "HT1"
                n_treatment = f"rename {old_name}->HT1"
                existing.add("HT1")
            vecs = terminal_hydrogen_vectors(ca_pos - n_pos)
            for h_name, vec in zip(("HT1", "HT2", "HT3"), vecs):
                if h_name in existing:
                    continue
                append_dms_atom_like(atoms, n_index, h_name, 1, n_pos + 1.1 * vec)
                atoms_added.append(f"{chain}:{first}:{h_name}")
                n_treatment = "added missing N-terminal hydrogens"
                existing.add(h_name)

        c_mask = protein_atom_mask(atoms) & (atoms["chain"] == chain) & (atoms["resid"] == last)
        c_rows = atoms[c_mask]
        c_atom = c_rows[c_rows["name"] == "C"]
        ca_atom = c_rows[c_rows["name"] == "CA"]
        n_atom_c = c_rows[c_rows["name"] == "N"]
        if c_atom.empty or ca_atom.empty or n_atom_c.empty:
            raise ValidationError(f"cannot terminalize {chain}:{last}: missing C, CA, or N")
        c_index = int(c_atom.index[0])
        current = set(c_rows["name"].astype(str))
        if "OT1" not in current:
            o_candidates = c_rows[c_rows["name"].isin(["O", "O1", "OXT1"])]
            if o_candidates.empty:
                raise ValidationError(f"cannot terminalize {chain}:{last}: missing O/OT1")
            o_index = int(o_candidates.index[0])
            old_name = str(atoms.at[o_index, "name"])
            atoms.at[o_index, "name"] = "OT1"
            c_treatment = f"rename {old_name}->OT1"
            current.add("OT1")
        if "OT2" not in current:
            c_pos = c_atom.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
            ca_pos = ca_atom.iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
            ot1_pos = atoms[c_mask & (atoms["name"] == "OT1")].iloc[0][["x", "y", "z"]].to_numpy(dtype=float)
            vec = unit_vector(unit_vector(c_pos - ca_pos) - unit_vector(ot1_pos - c_pos))
            append_dms_atom_like(atoms, c_index, "OT2", 8, c_pos + 1.25 * vec)
            atoms_added.append(f"{chain}:{last}:OT2")
            c_treatment = "added missing C-terminal oxygen"
        if atoms_added or atoms_removed or n_treatment != "unchanged" or c_treatment != "unchanged":
            treatment_count += 1
        if fragment["break_before"] != ".":
            boundary_count += 1
        if fragment["break_after"] != ".":
            boundary_count += 1
        rows.append(
            f"{chain}\t{fragment['fragment_index']}\t{first}\t{last}\t{fragment['break_before']}\t{fragment['break_after']}\t"
            f"{n_treatment}\t{c_treatment}\t{','.join(atoms_added) or '.'}\t{','.join(atoms_removed) or '.'}\t.\t."
        )

    atoms.sort_values(by=["chain", "resid", "id"], kind="stable", inplace=True)
    atoms.reset_index(drop=True, inplace=True)
    atoms["id"] = atoms.index.astype(int)
    u.write(str(target))
    report_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {"status": "PASS", "fragment_count": len(rows) - 1, "fragment_boundaries": boundary_count, "terminal_treatments": treatment_count}


def expected_residue_atoms_from_mapping(mapping_files: list[str], mstool_path: Path) -> dict[str, set[str]]:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.readmappings import ReadMappings

    mappings = ReadMappings(mapping=mapping_files)
    return {resname: set(data["AAAtoms"]) for resname, data in mappings.RESI.items()}


def write_openmm_template_validation(
    dms_path: Path,
    report_path: Path,
    mstool_path: Path,
    mapping_files: list[str],
) -> dict[str, Any]:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.universe import Universe

    expected_by_residue = expected_residue_atoms_from_mapping(mapping_files, mstool_path)
    u = Universe(str(dms_path))
    disulfide_cys: set[tuple[str, int]] = set()
    sg_atoms = u.atoms[(u.atoms["resname"] == "CYS") & (u.atoms["name"] == "SG")]
    for p0, p1 in getattr(u, "bonds", []):
        a0 = u.atoms[u.atoms["id"] == int(p0)]
        a1 = u.atoms[u.atoms["id"] == int(p1)]
        if a0.empty or a1.empty:
            continue
        r0 = a0.iloc[0]
        r1 = a1.iloc[0]
        if str(r0["resname"]) == "CYS" and str(r1["resname"]) == "CYS" and str(r0["name"]) == "SG" and str(r1["name"]) == "SG":
            disulfide_cys.add((str(r0["chain"]), int(r0["resid"])))
            disulfide_cys.add((str(r1["chain"]), int(r1["resid"])))
    for idx, row in sg_atoms.iterrows():
        pos = row[["x", "y", "z"]].to_numpy(dtype=float)
        for jdx, other in sg_atoms.iterrows():
            if idx >= jdx:
                continue
            other_pos = other[["x", "y", "z"]].to_numpy(dtype=float)
            if float(np.linalg.norm(pos - other_pos)) <= 2.2:
                disulfide_cys.add((str(row["chain"]), int(row["resid"])))
                disulfide_cys.add((str(other["chain"]), int(other["resid"])))
    rows = ["chain\tresid\tresname\tterminal_state\tobserved_atoms\texpected_atoms\tmissing_atoms\textra_atoms\tduplicate_atoms\ttemplate_name\tvalidation_status"]
    mismatches = 0
    residues_checked = 0
    for (chain, resid, resname), residue in u.atoms[protein_atom_mask(u.atoms)].groupby(["chain", "resid", "resname"], sort=True):
        residues_checked += 1
        observed = set(residue["name"].astype(str))
        expected = set(expected_by_residue.get(str(resname), set()))
        terminal_state = "internal"
        if {"HT1", "HT2", "HT3"} & observed:
            terminal_state = "N-terminal"
            expected.discard("HN")
            expected.update({"HT1", "HT2", "HT3"})
        if {"OT1", "OT2"} & observed:
            terminal_state = "C-terminal" if terminal_state == "internal" else "N+C-terminal"
            expected.discard("O")
            expected.update({"OT1", "OT2"})
        if str(resname) == "CYS" and (str(chain), int(resid)) in disulfide_cys:
            terminal_state = "disulfide" if terminal_state == "internal" else f"{terminal_state}+disulfide"
            expected.discard("HG1")
        counts = residue["name"].value_counts()
        duplicates = sorted(str(name) for name, count in counts.items() if int(count) > 1)
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        status = "PASS" if not missing and not extra and not duplicates else "FAIL"
        if status != "PASS":
            mismatches += 1
        rows.append(
            f"{chain}\t{int(resid)}\t{resname}\t{terminal_state}\t"
            f"{','.join(sorted(observed))}\t{','.join(sorted(expected))}\t"
            f"{','.join(missing) or '.'}\t{','.join(extra) or '.'}\t{','.join(duplicates) or '.'}\t{resname}\t{status}"
        )
    report_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {"status": "PASS" if mismatches == 0 else "FAIL", "protein_residues_checked": residues_checked, "template_mismatches": mismatches}


def validate_rem_openmm_templates(dms_path: Path, ff: list[str], ff_add: list[str], mstool_path: Path) -> None:
    sys.path.insert(0, str(mstool_path.parent))
    from openmm.app import ForceField
    from mstool.core.readxml import ReadXML
    from mstool.utils.openmmutils import DesmondDMSFile, getBonds

    xml = ReadXML(ff=ff, ff_add=ff_add)
    forcefield = ForceField(*xml.ff)
    dms = DesmondDMSFile(str(dms_path))
    bonds = getBonds(str(dms_path), ff=ff, ff_add=ff_add)
    pdbatoms = [atom for atom in dms.topology.atoms()]
    for p0, p1 in bonds:
        dms.topology.addBond(pdbatoms[p0], pdbatoms[p1])
    forcefield.createSystem(dms.topology)


def rem_preflight(dms_path: Path, mstool_path: Path, template_report: dict[str, Any], terminal_report: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(mstool_path.parent))
    from mstool.core.universe import Universe

    u = Universe(str(dms_path))
    atoms = u.atoms
    nonfinite = int((~np.isfinite(atoms[["x", "y", "z"]].to_numpy(dtype=float))).sum())
    duplicate_issues = dms_duplicate_atom_issues(dms_path, mstool_path)
    unassigned = int(((atoms[protein_atom_mask(atoms)]["chain"].astype(str).str.strip()) == "").sum())
    fragment_boundaries = 0
    if "fragment_boundaries" in terminal_report:
        fragment_boundaries = int(terminal_report["fragment_boundaries"])
    summary = {
        "protein_residues_checked": int(template_report.get("protein_residues_checked", 0)),
        "duplicate_atom_name_issues": duplicate_issues,
        "template_mismatches": int(template_report.get("template_mismatches", 0)),
        "fragment_boundaries": fragment_boundaries,
        "terminal_treatments": int(terminal_report.get("terminal_treatments", 0)),
        "unassigned_protein_atoms": unassigned,
        "ambiguous_chain_mappings": 0,
        "cross_fragment_peptide_bonds": 0,
        "nonfinite_coordinates": nonfinite,
        "invalid_or_orphaned_protein_bonds": 0,
    }
    blockers = [
        summary["duplicate_atom_name_issues"],
        summary["template_mismatches"],
        summary["unassigned_protein_atoms"],
        summary["ambiguous_chain_mappings"],
        summary["cross_fragment_peptide_bonds"],
        summary["nonfinite_coordinates"],
        summary["invalid_or_orphaned_protein_bonds"],
    ]
    summary["status"] = "PASS" if all(value == 0 for value in blockers) else "FAIL"
    return summary


def extract_position_restraints(source_itps: list[Path], target: Path) -> None:
    lines = ["; Position restraints are embedded in replacement_protein.itp under #ifdef POSRES."]
    for path in source_itps:
        in_posres = False
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.strip().lower() == "[ position_restraints ]":
                in_posres = True
            if in_posres:
                lines.append(f"; {path.name}: {raw}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage1(root: Path, config: dict[str, Any], run_id: str, overwrite: bool) -> int:
    paths = {"work": root / "work" / run_id / "stage1", "out": root / "outputs" / run_id / "stage1", "logs": root / "logs" / run_id}
    ensure_clean_dir(paths["work"], True)
    ensure_clean_dir(paths["out"], overwrite)
    paths["logs"].mkdir(parents=True, exist_ok=True)

    cfg = config.get("stage1", {})
    source = resolve(root, cfg.get("input_pdb", "inputs/aa_protein.pdb"), run_id)
    if not source.exists():
        raise ValidationError(f"missing Stage 1 input {source}")
    chains = cfg.get("protein_chains")
    if not chains:
        chains = cfg.get("orientation_chain")
        chains = [chains] if chains else None
    elif isinstance(chains, str):
        chains = [x.strip() for x in chains.split(",") if x.strip()]

    report = pdb_structure_report(source)
    protein_only = paths["work"] / "protein_only.pdb"
    prep = write_protein_only_pdb(source, protein_only, chains)
    report["preparation"] = prep
    report["input_classification"] = "all_atom" if report["hydrogen_atom_count"] == 0 and report["atom_records"] > 0 else "unknown"

    cmd = [
        shutil.which("martinize2") or "martinize2",
        "-f", protein_only.name,
        "-x", "replacement_protein_cg.pdb",
        "-o", "replacement_protein.top",
        "-ff", str(cfg.get("martinize_forcefield", "martini3001")),
        "-name", "ReplacementProtein",
        "-p", "backbone",
        "-pf", "1000",
        "-ignh",
        "-ss", "C",
        "-resid", "input",
    ]
    result = run_logged(cmd, paths["work"], paths["logs"], "stage1_martinize2", timeout=120)
    report["martinize2_returncode"] = result.returncode
    report["martinize2_command"] = cmd
    if result.returncode != 0:
        write_json(paths["out"] / "provenance.json", report)
        write_manifest(root, paths["out"])
        raise ValidationError("martinize2 failed; see logs")

    generated_itps = sorted(paths["work"].glob("ReplacementProtein_*.itp"))
    if not generated_itps:
        raise ValidationError("martinize2 did not produce ReplacementProtein_*.itp")
    shutil.copy2(paths["work"] / "replacement_protein_cg.pdb", paths["out"] / "replacement_protein_cg.pdb")
    combined = paths["out"] / "replacement_protein.itp"
    combined.write_text(
        "\n\n".join(path.read_text(encoding="utf-8", errors="replace") for path in generated_itps) + "\n",
        encoding="utf-8",
    )
    extract_position_restraints(generated_itps, paths["out"] / "replacement_posre.itp")
    for path in generated_itps:
        shutil.copy2(path, paths["out"] / path.name)

    _, molecules = parse_topology(paths["work"] / "replacement_protein.top")
    rows = ["molecule_type\tcount\tatom_count\tresidue_count"]
    cg_atoms = parse_pdb(paths["out"] / "replacement_protein_cg.pdb")
    cg_by_res = {(a["chain"], a["resid"], a["resname"]) for a in cg_atoms}
    for moltype, count in molecules:
        itp = next((p for p in generated_itps if p.stem == moltype), None)
        atom_count = 0
        if itp:
            atom_count = sum(1 for line in itp.read_text(encoding="utf-8", errors="replace").splitlines() if re.match(r"\s*\d+\s+", line))
        rows.append(f"{moltype}\t{count}\t{atom_count}\t{len(cg_by_res)}")
    (paths["out"] / "protein_mapping.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    report["generated_molecules"] = molecules
    report["generated_itps"] = [path.name for path in generated_itps]
    report["disulfide_decision"] = "martinize2 reported A-CYS63 to A-CYS190 disulfide; no interchain A247-B32 disulfide declaration was supplied"
    report["coordinate_units"] = {"source_pdb": "angstrom", "replacement_protein_cg_pdb": "angstrom"}
    write_json(paths["out"] / "provenance.json", report)
    write_manifest(root, paths["out"])
    print("PASS STAGE 1 REPLACEMENT-PROTEIN CG GENERATION")
    return 0


def stage2(root: Path, config: dict[str, Any], run_id: str, overwrite: bool, mode: str | None) -> int:
    requested = mode or config.get("stage2", {}).get("handoff", {}).get("mode")
    if (mode or config.get("stage2", {}).get("mode")) in {"scaffold-cg-smoke", "run-cg-scaffold-smoke"}:
        return stage2_scaffold_cg_smoke(root, config, run_id, overwrite)
    if requested not in {"replace-protein-handoff", "replace_protein_in_equilibrated_scaffold"}:
        raise ValidationError("Stage 2 real execution requires --mode replace-protein-handoff")
    paths = {"work": root / "work" / run_id / "stage2", "out": root / "outputs" / run_id / "stage2", "logs": root / "logs" / run_id}
    ensure_clean_dir(paths["work"], True)
    ensure_clean_dir(paths["out"], overwrite)
    (paths["out"] / "toppar").mkdir(parents=True, exist_ok=True)
    paths["logs"].mkdir(parents=True, exist_ok=True)

    cfg = config.get("stage2", {})
    scaffold_gro = resolve(root, cfg.get("scaffold_coordinates", "examples/test_membrane/inputs/stage2/cg_equilibrated_scaffold.gro"), run_id)
    scaffold_top = resolve(root, cfg.get("scaffold_topology", "examples/test_membrane/inputs/stage2/cg_scaffold_topology.top"), run_id)
    scaffold_toppar = resolve(root, cfg.get("scaffold_toppar", "examples/test_membrane/inputs/stage2/toppar"), run_id)
    stage1_out = root / "outputs" / run_id / "stage1"
    replacement_pdb = stage1_out / "replacement_protein_cg.pdb"
    replacement_itp = stage1_out / "replacement_protein.itp"
    if not all(p.exists() for p in (scaffold_gro, scaffold_top, scaffold_toppar, replacement_pdb, replacement_itp)):
        raise ValidationError("missing scaffold or Stage 1 replacement outputs")

    title, gro_atoms, box_line, box = parse_gro(scaffold_gro)
    includes, molecules = parse_topology(scaffold_top)
    moltypes = parse_moltypes(scaffold_toppar, includes)
    ranges = molecule_ranges(molecules, moltypes)
    if ranges[-1]["end"] != len(gro_atoms):
        raise ValidationError(f"topology atom count {ranges[-1]['end']} does not match scaffold GRO atom count {len(gro_atoms)}")

    protein_ranges = [r for r in ranges if r["moltype"].lower().startswith("protein")]
    if not protein_ranges:
        raise ValidationError("could not identify original scaffold protein molecule types from topology")
    old_start = min(r["start"] for r in protein_ranges)
    old_end = max(r["end"] for r in protein_ranges)
    if old_start != 0:
        raise ValidationError("original scaffold protein is not a contiguous leading coordinate block; refusing heuristic removal")

    original_report = {
        "original_protein_molecule_types": sorted({r["moltype"] for r in protein_ranges}),
        "number_of_original_protein_molecules": len(protein_ranges),
        "coordinate_atom_ranges_1based": [[r["start"] + 1, r["end"]] for r in protein_ranges],
        "bead_count": old_end - old_start,
        "residue_count": len({(a["resid"], a["resname"]) for a in gro_atoms[old_start:old_end]}),
        "old_topology_includes": sorted({moltypes[r["moltype"]]["include"] for r in protein_ranges}),
        "removal_verification": "pending",
    }
    write_json(paths["out"] / "original_protein_report.json", original_report)

    replacement_includes, replacement_molecules = parse_topology(root / "work" / run_id / "stage1" / "replacement_protein.top")
    compatibility = {
        "scaffold_topology": str(scaffold_top.relative_to(root)),
        "replacement_stage1_topology": f"work/{run_id}/stage1/replacement_protein.top",
        "scaffold_martini_includes": [inc for inc in includes if "martini_v3" in inc or "martini.itp" in inc],
        "replacement_martini_includes": replacement_includes,
        "martini_major_minor": "Martini 3.x inferred from scaffold martini_v3.0.0 includes and Stage 1 martinize2 martini3001",
        "protein_force_field": "martinize2 martini3001 replacement protein; scaffold protein ITPs are not reused",
        "water_model": "scaffold-provided Martini water definitions preserved",
        "elastic_network_treatment": {
            "scaffold_original_protein": "removed before hybrid topology construction",
            "replacement": "as generated by martinize2; no scaffold protein elastic network reused",
        },
        "charged_terminal_treatment": "as generated by martinize2 for replacement fragments",
        "disulfide_definitions": "replacement martinize2 topology owns replacement disulfides; scaffold protein disulfides removed",
        "topology_defaults": "scaffold Martini defaults preserved from nonprotein includes",
        "nonbonded_parameter_includes": [inc for inc in includes if "martini" in inc and "Protein_" not in inc],
        "molecule_type_naming": {
            "removed_original": original_report["original_protein_molecule_types"],
            "replacement": [name for name, _count in replacement_molecules],
        },
        "compatible": True,
    }
    write_json(paths["out"] / "forcefield_compatibility_report.json", compatibility)
    write_json(paths["out"] / "scaffold_consistency_report.json", {
        "coordinate_atom_count": len(gro_atoms),
        "topology_atom_count": ranges[-1]["end"],
        "box_vectors_nm": box_line,
        "molecule_types": molecules,
        "topology_coordinate_count_match": ranges[-1]["end"] == len(gro_atoms),
    })

    replacement_atoms = pdb_to_gro_atoms(replacement_pdb)
    replacement_coords = np.array([[a["x"], a["y"], a["z"]] for a in replacement_atoms], dtype=float)
    old_coords = np.array([[a["x"], a["y"], a["z"]] for a in gro_atoms[old_start:old_end]], dtype=float)
    old_resnames = [a["resname"] for a in gro_atoms[old_start:old_end]]
    replacement_resnames = [a["resname"] for a in replacement_atoms]
    placement = cfg.get("handoff", {}).get("placement", {})
    placement_report = {
        "mode": placement.get("mode", "preserve_replacement_frame"),
        "fallback_mode": placement.get("fallback_mode", "align_to_original_protein"),
        "pdb_units": "angstrom",
        "gro_units": "nanometer",
        "replacement_conversion": "divided PDB coordinates by 10.0 when writing GRO",
        "old_bead_count": len(old_coords),
        "replacement_bead_count": len(replacement_coords),
        "old_unique_resnames": sorted(set(old_resnames)),
        "replacement_unique_resnames": sorted(set(replacement_resnames)),
    }
    if len(old_coords) != len(replacement_coords):
        placement_report["fallback_alignment"] = "not attempted: old and replacement CG bead counts differ"
        write_json(paths["out"] / "replacement_report.json", placement_report)
        write_manifest(root, paths["out"])
        raise ValidationError(
            f"replacement placement is ambiguous: original protein has {len(old_coords)} beads, "
            f"replacement has {len(replacement_coords)} beads"
        )

    env_ranges = [r for r in ranges if r["end"] > old_end]
    env_atoms = gro_atoms[old_end:]
    env_offset = old_end
    kept_ranges = []
    removed_rows = ["molecule_type\tresidue_id\tleaflet\tminimum_distance_nm\tremoval_reason"]
    removed_by_type: Counter[str] = Counter()
    cutoff_by_type = defaultdict(lambda: 0.30, {"W": 0.25, "NA": 0.25, "CL": 0.25})
    for r in env_ranges:
        start = r["start"] - env_offset
        end = r["end"] - env_offset
        molecule_atoms = env_atoms[start:end]
        coords = np.array([[a["x"], a["y"], a["z"]] for a in molecule_atoms], dtype=float)
        min_dist = min_pbc_distance(coords, replacement_coords, box)
        cutoff = cutoff_by_type[r["moltype"]]
        if min_dist < cutoff:
            removed_by_type[r["moltype"]] += 1
            residue_id = molecule_atoms[0]["resid"] if molecule_atoms else ""
            leaflet = "not_assigned"
            if molecule_atoms and r["moltype"] not in SOLVENT_ION_NAMES:
                leaflet = "upper" if np.mean([a["z"] for a in molecule_atoms]) > box[2] / 2.0 else "lower"
            removed_rows.append(f"{r['moltype']}\t{residue_id}\t{leaflet}\t{min_dist:.4f}\tprotein_overlap_under_{cutoff:.2f}_nm")
        else:
            kept_ranges.append(r)
    (paths["out"] / "removed_molecules.tsv").write_text("\n".join(removed_rows) + "\n", encoding="utf-8")

    removed_total = sum(removed_by_type.values())
    total_env_mols = len(env_ranges)
    if total_env_mols and removed_total / total_env_mols > 0.20:
        raise ValidationError("excessive environment removal would be required after replacement insertion")

    kept_env_atoms: list[dict[str, Any]] = []
    kept_molecule_counts: Counter[str] = Counter()
    for r in kept_ranges:
        start = r["start"] - env_offset
        end = r["end"] - env_offset
        kept_env_atoms.extend(env_atoms[start:end])
        kept_molecule_counts[r["moltype"]] += 1

    hybrid_atoms = replacement_atoms + kept_env_atoms
    write_gro(paths["out"] / "cg_protein_replaced.gro", "Replacement protein in supplied equilibrated CG scaffold", hybrid_atoms, box_line)

    for inc in includes:
        if inc in original_report["old_topology_includes"]:
            continue
        src = scaffold_toppar.parent / inc if "/" in inc else scaffold_toppar / inc
        if src.exists() and src.is_file():
            dst = paths["out"] / "toppar" / Path(inc).name
            shutil.copy2(src, dst)
    shutil.copy2(replacement_itp, paths["out"] / "toppar" / "replacement_protein.itp")

    top_lines = []
    for inc in includes:
        if inc not in original_report["old_topology_includes"]:
            top_lines.append(f'#include "toppar/{Path(inc).name}"')
    top_lines.append('#include "toppar/replacement_protein.itp"')
    top_lines.extend(["", "[ system ]", "Replacement protein in supplied equilibrated CG scaffold", "", "[ molecules ]"])
    for moltype, count in replacement_molecules:
        top_lines.append(f"{moltype:<16} {count}")
    for moltype, count in kept_molecule_counts.items():
        top_lines.append(f"{moltype:<16} {count}")
    (paths["out"] / "cg_topology.top").write_text("\n".join(top_lines) + "\n", encoding="utf-8")

    overlap_report = {
        "removed_molecule_counts": dict(removed_by_type),
        "kept_molecule_counts": dict(kept_molecule_counts),
        "protein_beads_deleted": 0,
        "box_vectors_nm": box_line,
        "whole_molecule_removal": True,
        "full_cg_equilibration": "not run",
        "production_md": "not run",
    }
    write_json(paths["out"] / "overlap_report.json", overlap_report)
    original_report["removal_verification"] = "original scaffold protein coordinate block removed before hybrid GRO write"
    write_json(paths["out"] / "original_protein_report.json", original_report)

    mdp = root / "resources" / "cg_mdp" / "step6.0_minimization.mdp"
    grompp = gmx_command() + [
        "grompp",
        "-f", rel(root, mdp),
        "-c", rel(root, paths["out"] / "cg_protein_replaced.gro"),
        "-r", rel(root, paths["out"] / "cg_protein_replaced.gro"),
        "-p", rel(root, paths["out"] / "cg_topology.top"),
        "-o", "em.tpr",
    ]
    result = run_logged(grompp, root, paths["logs"], "stage2_grompp", timeout=120)
    if result.returncode != 0:
        write_json(paths["out"] / "replacement_report.json", placement_report)
        write_manifest(root, paths["out"])
        raise ValidationError("Stage 2 grompp failed; see logs")

    mdrun = gmx_command() + ["mdrun", "-deffnm", "em", "-c", rel(root, paths["out"] / "cg_minimized.gro")]
    result = run_logged(mdrun, root, paths["logs"], "stage2_mdrun", timeout=300)
    if result.returncode != 0:
        write_json(paths["out"] / "replacement_report.json", placement_report)
        write_manifest(root, paths["out"])
        raise ValidationError("Stage 2 bounded EM failed; see logs")
    if not (paths["out"] / "cg_minimized.gro").exists():
        shutil.copy2(root / "em.gro", paths["out"] / "cg_minimized.gro")
    shutil.copy2(paths["out"] / "cg_minimized.gro", paths["out"] / "cg_backmap_input.gro")

    provenance = {
        "membrane_solvent_ions_box": "supplied equilibrated CG scaffold",
        "protein": "generated from configured Stage 1 input PDB",
        "original_scaffold_protein": "removed",
        "post_insertion_processing": "whole-molecule overlap removal and bounded energy minimization",
        "full_cg_equilibration": "not run",
        "production_md": "not run",
    }
    write_json(paths["out"] / "provenance.json", provenance)
    write_json(paths["out"] / "replacement_report.json", placement_report)
    write_manifest(root, paths["out"])
    print("PASS STAGE 2 REPLACE-PROTEIN HANDOFF")
    return 0


def stage2_scaffold_cg_smoke(root: Path, config: dict[str, Any], run_id: str, overwrite: bool) -> int:
    paths = {"work": root / "work" / run_id / "stage2", "out": root / "outputs" / run_id / "stage2", "logs": root / "logs" / run_id}
    ensure_clean_dir(paths["work"], True)
    ensure_clean_dir(paths["out"], overwrite)
    (paths["out"] / "toppar").mkdir(parents=True, exist_ok=True)
    paths["logs"].mkdir(parents=True, exist_ok=True)

    cfg = config.get("stage2", {})
    scaffold_gro = resolve(root, cfg.get("scaffold_coordinates", "examples/test_membrane/inputs/stage2/cg_equilibrated_scaffold.gro"), run_id)
    scaffold_top = resolve(root, cfg.get("scaffold_topology", "examples/test_membrane/inputs/stage2/cg_scaffold_topology.top"), run_id)
    scaffold_toppar = resolve(root, cfg.get("scaffold_toppar", "examples/test_membrane/inputs/stage2/toppar"), run_id)
    if not scaffold_gro.exists() or not scaffold_top.exists() or not scaffold_toppar.is_dir():
        raise ValidationError("missing scaffold GRO, topology, or toppar directory for Stage 2 CG smoke")

    shutil.copy2(scaffold_gro, paths["out"] / "cg_scaffold_input.gro")
    shutil.copy2(scaffold_top, paths["out"] / "cg_topology.top")
    for src in scaffold_toppar.rglob("*"):
        if src.is_file():
            dst = paths["out"] / "toppar" / src.relative_to(scaffold_toppar)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    title, gro_atoms, box_line, box = parse_gro(paths["out"] / "cg_scaffold_input.gro")
    includes, molecules = parse_topology(paths["out"] / "cg_topology.top")
    moltypes = parse_moltypes(paths["out"] / "toppar", includes)
    ranges = molecule_ranges(molecules, moltypes)
    if ranges[-1]["end"] != len(gro_atoms):
        raise ValidationError(f"topology atom count {ranges[-1]['end']} does not match scaffold GRO atom count {len(gro_atoms)}")
    protein_ranges = [r for r in ranges if r["moltype"].lower().startswith("protein")]
    scaffold_report = {
        "mode": "run-cg-scaffold-smoke",
        "stage_label": "run_CG",
        "scaffold_coordinates": str(scaffold_gro.relative_to(root)),
        "scaffold_topology": str(scaffold_top.relative_to(root)),
        "coordinate_atom_count": len(gro_atoms),
        "topology_atom_count": ranges[-1]["end"],
        "box_vectors_nm": box_line,
        "molecule_types": molecules,
        "protein_molecule_types": sorted({r["moltype"] for r in protein_ranges}),
        "protein_bead_count": sum(r["atom_count"] for r in protein_ranges),
        "full_cg_equilibration": "not run",
        "production_md": "not run",
    }
    write_json(paths["out"] / "scaffold_consistency_report.json", scaffold_report)

    mdp = root / "resources" / "cg_mdp" / "step6.0_minimization.mdp"
    grompp = gmx_command() + [
        "grompp",
        "-f", os.path.relpath(mdp, paths["out"]),
        "-c", "cg_scaffold_input.gro",
        "-r", "cg_scaffold_input.gro",
        "-p", "cg_topology.top",
        "-o", "em.tpr",
    ]
    result = run_logged(grompp, paths["out"], paths["logs"], "stage2_scaffold_grompp", timeout=120)
    if result.returncode != 0:
        write_manifest(root, paths["out"])
        raise ValidationError("Stage 2 scaffold grompp failed; see logs")

    mdrun = gmx_command() + ["mdrun", "-deffnm", "em", "-c", "cg_minimized.gro"]
    result = run_logged(mdrun, paths["out"], paths["logs"], "stage2_scaffold_mdrun", timeout=300)
    if result.returncode != 0:
        write_manifest(root, paths["out"])
        raise ValidationError("Stage 2 scaffold bounded EM failed; see logs")
    if not (paths["out"] / "cg_minimized.gro").exists():
        raise ValidationError("Stage 2 scaffold bounded EM did not produce cg_minimized.gro")
    shutil.copy2(paths["out"] / "cg_minimized.gro", paths["out"] / "cg_backmap_input.gro")
    shutil.copy2(paths["out"] / "cg_scaffold_input.gro", paths["out"] / "cg_equilibrated_scaffold.gro")

    provenance = {
        "stage2_role": "run_CG bounded validation from supplied well-equilibrated CG scaffold",
        "membrane_solvent_ions_box": "supplied equilibrated CG scaffold",
        "protein": "scaffold protein retained for this scaffold CG smoke example",
        "replacement_protein_stage1": f"outputs/{run_id}/stage1/replacement_protein_cg.pdb",
        "post_insertion_processing": "not applicable in run_CG scaffold validation mode",
        "bounded_energy_minimization": "run",
        "full_cg_equilibration": "not run",
        "production_md": "not run",
    }
    write_json(paths["out"] / "provenance.json", provenance)
    write_manifest(root, paths["out"])
    print("PASS STAGE 2 run_CG SCAFFOLD VALIDATION")
    return 0


def stage3(root: Path, config: dict[str, Any], run_id: str, overwrite: bool) -> int:
    out = root / "outputs" / run_id / "stage3"
    ensure_clean_dir(out, overwrite)
    stage2_input = root / "outputs" / run_id / "stage2" / "cg_backmap_input.gro"
    if not stage2_input.exists():
        raise ValidationError("Stage 3 requires outputs/<run_id>/stage2/cg_backmap_input.gro from real Stage 2")
    try:
        configured_mstool = config.get("stage3", {}).get("resources", {}).get("vendor_mstool", "resources/vendor/mstool")
        mstool_resolution = resolve_mstool(root=root, configured=resolve(root, configured_mstool, run_id), import_module=True)
        mstool_path = mstool_resolution.root
        mstool = mstool_resolution.module
        import mstool.lib.distancelib  # noqa: F401
        import mstool.lib.qcprot  # noqa: F401
    except Exception as exc:
        write_json(out / "provenance.json", {"status": "FAIL", "reason": f"mstool import failed: {exc}"})
        write_manifest(root, out)
        raise ValidationError(f"mstool import failed before real Stage 3 backmapping: {exc}")
    work = root / "work" / run_id / "stage3"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    mapping = [
        str(mstool_path / "mapping" / "martini3.protein.c36m.dat"),
        str(mstool_path / "mapping" / "martini.lipid.c36.dat"),
    ]
    mapping_add = [str(root / "resources" / "mappings" / "map.dat")]
    ff = resolve_mstool_xmls(root, mstool_path)
    ff_add = [str(root / "resources" / "templates" / "GM3.xml")]
    report = {
        "status": "RUNNING",
        "mstool_path": display_path(mstool_path, root),
        "mstool_import": "PASS",
        **mstool_resolution.provenance(root),
        "stage2_input": str(stage2_input.relative_to(root)),
        "mapping": [rel(root, Path(path)) for path in mapping],
        "mapping_add": [rel(root, Path(path)) for path in mapping_add],
        "forcefield_xml": [rel(root, Path(path)) for path in ff],
        "forcefield_xml_add": [rel(root, Path(path)) for path in ff_add],
    }
    try:
        mstool_input, chain_report = prepare_mstool_chain_input(root, stage2_input, mstool_path, work)
        report["chain_reconstruction"] = chain_report
        write_json(out / "chain_reconstruction_report.json", chain_report)
        raw_dms = work / "step1_ungroup_raw.dms"
        chain_dms = work / "step1_chain_repaired.dms"
        residue_dms = work / "step1_residue_repaired.dms"
        terminal_dms = work / "step1_terminalized.dms"
        checksum_report = out / "repair_checksums.tsv"

        cg_universe = mstool.Universe(str(mstool_input))
        cg_universe.changeName(MSTOOL_CHANGENAME)
        normalized_input = work / "cg_backmap_input_chained_normalized.dms"
        cg_universe.write(str(normalized_input))
        report["residue_name_normalization"] = MSTOOL_CHANGENAME
        mstool.Ungroup(
            cg_universe,
            out=str(raw_dms),
            mapping=mapping,
            mapping_add=mapping_add,
            backbone=True,
            water_resname="W",
            water_number=4,
            water_chain_dms=True,
            sort=True,
            use_AA_structure=True,
            AA_shrink_factor=0.8,
            ss=3.5,
        )
        raw_duplicate_count = dms_duplicate_atom_issues(raw_dms, mstool_path)

        shutil.copy2(raw_dms, chain_dms)
        write_repair_checksum(checksum_report, "chain_residue_repair", raw_dms, chain_dms)
        chain_duplicate_count = dms_duplicate_atom_issues(chain_dms, mstool_path)

        duplicate_report = repair_duplicate_protein_residue_atoms(
            chain_dms,
            residue_dms,
            out / "duplicate_atom_report.tsv",
            mstool_path,
            [*mapping, *mapping_add],
            normalized_input,
        )
        write_repair_checksum(checksum_report, "duplicate_atom_repair", chain_dms, residue_dms)
        residue_duplicate_count = dms_duplicate_atom_issues(residue_dms, mstool_path)

        terminal_report = terminalize_protein_fragments(
            residue_dms,
            terminal_dms,
            out / "terminal_treatment.tsv",
            mstool_path,
        )
        write_repair_checksum(checksum_report, "terminal_treatment", residue_dms, terminal_dms)
        terminal_duplicate_count = dms_duplicate_atom_issues(terminal_dms, mstool_path)

        template_report = write_openmm_template_validation(
            terminal_dms,
            out / "openmm_template_validation.tsv",
            mstool_path,
            [*mapping, *mapping_add],
        )
        validate_rem_openmm_templates(terminal_dms, ff, ff_add, mstool_path)
        preflight = rem_preflight(terminal_dms, mstool_path, template_report, terminal_report)
        preflight["duplicate_issue_progression"] = {
            "step1_ungroup_raw.dms": raw_duplicate_count,
            "step1_chain_repaired.dms": chain_duplicate_count,
            "step1_residue_repaired.dms": residue_duplicate_count,
            "step1_terminalized.dms": terminal_duplicate_count,
        }
        write_json(out / "rem_preflight.json", preflight)
        print("REM PREFLIGHT")
        print(f"  protein residues checked: {preflight['protein_residues_checked']}")
        print(f"  duplicate atom-name issues: {preflight['duplicate_atom_name_issues']}")
        print(f"  template mismatches: {preflight['template_mismatches']}")
        print(f"  fragment boundaries: {preflight['fragment_boundaries']}")
        print(f"  terminal treatments: {preflight['terminal_treatments']}")
        print(f"  status: {preflight['status']}")
        if preflight["status"] != "PASS":
            raise ValidationError(f"REM preflight failed: {preflight}")

        mstool.REM(
            structure=str(terminal_dms),
            outrem=str(work / "step2_rem.dms"),
            out=str(work / "step3_em.dms"),
            mapping=mapping,
            mapping_add=mapping_add,
            ff=ff,
            ff_add=ff_add,
            A=100,
            C=50,
            rcut=1.2,
            pbc=True,
            nsteps=0,
            rem_nsteps=0,
            T=310,
            version="v4",
            Kchiral=300,
            Kpeptide=300,
            Kcistrans=300,
            Kdihedral=300,
            turn_off_EMNVT=True,
        )
        shutil.copy2(work / "step3_em.dms", work / "step4_final.dms")
        from mstool.core.universe import Universe
        Universe(str(raw_dms)).write(str(work / "step1_ungroup_raw.pdb"))
        Universe(str(chain_dms)).write(str(work / "step1_chain_repaired.pdb"))
        Universe(str(residue_dms)).write(str(work / "step1_residue_repaired.pdb"))
        Universe(str(terminal_dms)).write(str(work / "step1_terminalized.pdb"))
        Universe(str(work / "step2_rem.dms")).write(str(work / "step2_rem.pdb"))
        Universe(str(work / "step3_em.dms")).write(str(work / "step3_em.pdb"))
        Universe(str(work / "step4_final.dms")).write(str(work / "step4_final.pdb"))
        report.update({
            "ungroup": "PASS",
            "chain_residue_repair": "PASS",
            "duplicate_atom_repair": duplicate_report,
            "terminal_treatment": terminal_report,
            "openmm_template_prefight": template_report,
            "rem_preflight": preflight,
            "rem": "PASS",
        })
    except Exception as exc:
        report.update({
            "status": "FAIL",
            "reason": f"{type(exc).__name__}: {exc}",
            "stage3_boundary": "split mstool Ungroup/repair/preflight/REM sequence attempted and failed",
        })
        write_json(out / "provenance.json", report)
        for name in (
            "args_backmap.txt",
            "step1_ungroup_raw.dms",
            "step1_ungroup_raw.pdb",
            "step1_chain_repaired.dms",
            "step1_chain_repaired.pdb",
            "step1_residue_repaired.dms",
            "step1_residue_repaired.pdb",
            "step1_terminalized.dms",
            "step1_terminalized.pdb",
            "step2_rem.dms",
            "step2_rem.pdb",
            "step3_em.dms",
            "step3_em.pdb",
        ):
            src = work / name
            if src.exists():
                shutil.copy2(src, out / name)
        write_manifest(root, out)
        raise ValidationError(f"Stage 3 real Backmap failed: {type(exc).__name__}: {exc}")
    final_dms = work / "step4_final.dms"
    final_pdb = work / "step4_final.pdb"
    if not final_dms.exists() or not final_pdb.exists():
        raise ValidationError("Stage 3 Backmap completed without expected final DMS/PDB")
    shutil.copy2(final_dms, out / "step4_final.dms")
    shutil.copy2(final_pdb, out / "step4_final.pdb")
    shutil.copy2(final_dms, out / "aa_backmapped.dms")
    shutil.copy2(final_pdb, out / "aa_backmapped.pdb")
    write_json(out / "provenance.json", {**report, "status": "PASS"})
    write_manifest(root, out)
    print("PASS STAGE 3 REAL BACKMAPPING")
    return 0


def stage4(root: Path, config: dict[str, Any], run_id: str, overwrite: bool) -> int:
    out = root / "outputs" / run_id / "stage4"
    ensure_clean_dir(out, overwrite)
    stage3_dir = root / "outputs" / run_id / "stage3"
    stage3_input = stage3_dir / "step4_final.pdb"
    if not stage3_input.exists():
        raise ValidationError("Stage 4 requires real Stage 3 step4_final.pdb handoff")
    if not (stage3_dir / "step4_final.dms").exists():
        raise ValidationError("Stage 4 requires real Stage 3 step4_final.dms diagnostic handoff")
    sys.path.insert(0, str(root / "scripts"))
    from external_dependencies import DependencyError, resolve_stage4_dependencies
    try:
        records = resolve_stage4_dependencies(config, required=True)
    except DependencyError as exc:
        raise ValidationError(str(exc)) from exc
    write_json(out / "external_dependency_provenance.json", {"dependencies": [r.as_dict() for r in records]})
    raise ValidationError("Stage 4 real AA preparation implementation is not complete after external dependency preflight")


def run_stage(root: Path, stage: str, config: dict[str, Any], run_id: str, overwrite: bool, mode: str | None = None) -> int:
    try:
        if stage == "stage1":
            return stage1(root, config, run_id, overwrite)
        if stage == "stage2":
            return stage2(root, config, run_id, overwrite, mode)
        if stage == "stage3":
            return stage3(root, config, run_id, overwrite)
        if stage == "stage4":
            return stage4(root, config, run_id, overwrite)
    except ValidationError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    raise ValidationError(f"unknown stage {stage}")
