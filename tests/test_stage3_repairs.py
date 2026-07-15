from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "test_membrane_workflow.py"
SPEC = importlib.util.spec_from_file_location("test_membrane_workflow", MODULE_PATH)
workflow = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules["test_membrane_workflow"] = workflow
SPEC.loader.exec_module(workflow)


ALA_EXPECTED = ["N", "HN", "CA", "HA", "C", "O", "CB", "HB1", "HB2", "HB3"]


def ala_duplicate_residue(second_sidechain_offset: float = 0.0) -> pd.DataFrame:
    names = ["N", "HN", "CA", "HA", "C", "O", "CB", "HB1", "HB2", "HB3", "CB", "HB1", "HB2", "HB3"]
    coords = [
        (0.0, 0.0, 0.0),
        (0.0, 0.1, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.1, 0.0),
        (2.0, 0.0, 0.0),
        (2.1, 0.0, 0.0),
        (1.1, 2.7, 0.0),
        (1.2, 3.7, 0.0),
        (1.3, 3.6, 0.0),
        (1.4, 3.5, 0.0),
        (3.0 + second_sidechain_offset, 0.0, 0.0),
        (3.8 + second_sidechain_offset, 0.0, 0.0),
        (3.7 + second_sidechain_offset, 0.2, 0.0),
        (3.7 + second_sidechain_offset, -0.2, 0.0),
    ]
    return pd.DataFrame(
        {
            "id": list(range(len(names))),
            "chain": ["A"] * len(names),
            "resid": [1] * len(names),
            "resname": ["ALA"] * len(names),
            "name": names,
            "x": [c[0] for c in coords],
            "y": [c[1] for c in coords],
            "z": [c[2] for c in coords],
        }
    )


def test_ala_duplicate_orphaned_fragment_keeps_sc1_connected_set() -> None:
    residue = ala_duplicate_residue()
    drop, reason = workflow.select_duplicate_repair_indices(residue, ALA_EXPECTED, np.array([3.05, 0.0, 0.0]))
    kept_names = residue[~residue.index.isin(drop)]["name"].tolist()
    assert kept_names.count("CB") == 1
    assert 10 not in drop
    assert "CG SC1 provenance" in reason


def test_ambiguous_duplicate_coordinates_refuse_to_guess() -> None:
    residue = ala_duplicate_residue(second_sidechain_offset=-1.85)
    with pytest.raises(workflow.ValidationError):
        workflow.select_duplicate_repair_indices(residue, ALA_EXPECTED, np.array([1.125, 1.35, 0.0]))


def test_duplicate_residue_ids_in_different_chains_are_not_duplicates() -> None:
    residue = ala_duplicate_residue().iloc[:10].copy()
    two_chains = pd.concat([residue, residue.assign(chain="B")], ignore_index=True)
    rows = workflow.duplicate_atom_rows(two_chains, [])
    assert rows == [
        "chain\tresid\tinsertion_code\tresname\tatom_name\tduplicate_count\tatom_indices\tcoordinates\tbonded_neighbors\tselected_action\treason"
    ]


def test_protein_fragment_break_is_not_joined() -> None:
    atoms = pd.DataFrame(
        {
            "chain": ["A", "A", "A", "A", "A", "A"],
            "resid": [1, 1, 1, 2, 2, 2],
            "resname": ["GLY"] * 6,
            "name": ["N", "CA", "C", "N", "CA", "C"],
            "x": [0.0, 1.0, 2.0, 10.0, 11.0, 12.0],
            "y": [0.0] * 6,
            "z": [0.0] * 6,
        }
    )
    fragments = workflow.protein_fragments(atoms)
    assert len(fragments) == 2
    assert fragments[0]["break_after"].startswith("1->2 C-N")


def test_clean_residue_has_no_duplicate_repair() -> None:
    residue = ala_duplicate_residue().iloc[:10].copy()
    drop, reason = workflow.select_duplicate_repair_indices(residue, ALA_EXPECTED)
    assert drop == set()
    assert reason == "clean"


def test_membrane_template_finalizer_is_not_protein_terminal_repair() -> None:
    source = MODULE_PATH.read_text()
    assert "finalize_membrane_templates" not in source
    assert hasattr(workflow, "terminalize_protein_fragments")
