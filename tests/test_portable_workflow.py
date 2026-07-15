from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "membraneforger_workflow.py"
SPEC = importlib.util.spec_from_file_location("membraneforger_workflow", MODULE_PATH)
workflow = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules["membraneforger_workflow"] = workflow
SPEC.loader.exec_module(workflow)


def test_default_stage1_has_no_system_specific_chain() -> None:
    config = workflow.load_config(ROOT / "config" / "workflow.yaml")
    assert config["stage1"]["orientation_chain"] is None
    assert config["stage1"]["membrane_protein_mode"] == "whole"
    assert workflow.check_stage1(config, "pytest") == []


def test_private_legacy_configs_are_not_public() -> None:
    assert not (ROOT / "examples" / "legacy_kor").exists()
    assert not (ROOT / "examples" / "test_membrane").exists()


def test_opm_reference_required_only_when_enabled() -> None:
    cfg = workflow.load_config(ROOT / "config" / "workflow.yaml")
    cfg["stage3"]["placement"]["use_opm"] = False
    assert workflow.check_stage3(cfg, "pytest") == []
    cfg["stage3"]["placement"]["use_opm"] = True
    errors = workflow.check_stage3(cfg, "pytest")
    assert any("OPM reference" in err for err in errors)


def test_stage3_fails_on_enabled_plugin_missing_target() -> None:
    cfg = workflow.load_config(ROOT / "config" / "workflow.yaml")
    plugin = cfg["stage3"]["plugins"]["glycolipid_template_finalize"]
    plugin["enabled"] = True
    plugin["target_itp"] = "resources/ligand_params/GLPA/DOES_NOT_EXIST.itp"
    errors = workflow.check_stage3(cfg, "pytest")
    assert any("glycolipid_template_finalize target_itp" in err for err in errors)


def test_pipeline_check_command_passes() -> None:
    result = subprocess.run(
        ["bash", "run_pipeline.sh", "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS STAGE1 STATIC CHECK" in result.stdout
    assert "PASS STAGE4 STATIC CHECK" in result.stdout
