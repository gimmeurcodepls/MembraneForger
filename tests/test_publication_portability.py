from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DIRS = ["config", "stages", "scripts", "resources", "tests", "examples", "inputs", "environments", "containers", "docs", ".github"]
ACTIVE_FILES = ["run_pipeline.sh", "setup.sh", "README.md", "LICENSE", "CITATION.cff", "LICENSE_DEPENDENCIES.md", "THIRD_PARTY_NOTICES.md"]
FORBIDDEN_FRAGMENTS = ["/" + part + "/" for part in ("Users", "home", "scratch", "packages")]
FORBIDDEN_REVIEW_WORDS = {"REVIEW", "UNKNOWN", "UNRESOLVED"}


def iter_active_files():
    for rel in ACTIVE_FILES:
        p = ROOT / rel
        if p.is_file():
            yield p
    for dirname in ACTIVE_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            if any(part in {"__pycache__", ".pytest_cache"} for part in p.parts):
                continue
            yield p


def git_ls_files() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, text=True, stdout=subprocess.PIPE, check=True)
    return [line for line in result.stdout.splitlines() if line]


def test_required_publication_paths_exist() -> None:
    for rel in [
        "config", "inputs", "stages", "resources", "scripts", "tests", "examples/minimal",
        "environments", "containers", "docs", "LICENSES", "run_pipeline.sh", "setup.sh",
        "README.md", "LICENSE", "CITATION.cff", "LICENSE_DEPENDENCIES.md",
        "THIRD_PARTY_NOTICES.md", "docs/third_party_inventory.tsv",
    ]:
        assert (ROOT / rel).exists(), rel


def test_active_code_has_no_personal_or_hpc_absolute_paths() -> None:
    offenders: list[str] = []
    allowed = {"/opt/Gromacs/2022.1.sif", "/usr/local/gromacs/avx2_256/bin/gmx", "/usr/local/gromacs/bin/gmx"}
    for path in iter_active_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scrubbed = text
        for item in allowed:
            scrubbed = scrubbed.replace(item, "")
        if any(fragment in scrubbed for fragment in FORBIDDEN_FRAGMENTS):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_no_restricted_or_unresolved_public_snapshot_states() -> None:
    offenders = []
    checked_roots = [ROOT / "docs", ROOT / "resources", ROOT / "LICENSE_DEPENDENCIES.md", ROOT / "THIRD_PARTY_NOTICES.md"]
    files = []
    for root in checked_roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend(p for p in root.rglob("*") if p.is_file())
    for path in files:
        if path.name.endswith(".pyc"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(word in text for word in FORBIDDEN_REVIEW_WORDS):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_resource_manifest_covers_contained_resources() -> None:
    manifest = ROOT / "resources" / "RESOURCE_MANIFEST.tsv"
    rows = manifest.read_text(encoding="utf-8").splitlines()
    listed = {line.split("\t", 1)[0] for line in rows[1:] if line.strip()}
    missing = []
    for path in (ROOT / "resources").rglob("*"):
        if any(part == "__pycache__" for part in path.parts):
            continue
        if path.is_file() and path.name != "RESOURCE_MANIFEST.tsv":
            rel = path.relative_to(ROOT).as_posix()
            if rel not in listed:
                missing.append(rel)
    assert missing == []


def test_third_party_inventory_has_no_blank_retained_license_fields() -> None:
    inventory = ROOT / "docs" / "third_party_inventory.tsv"
    rows = inventory.read_text(encoding="utf-8").splitlines()
    header = rows[0].split("\t")
    required = ["path", "sha256", "license_spdx", "redistribution_verified", "upstream_commit_or_release", "final_action"]
    indexes = [header.index(name) for name in required]
    for line in rows[1:]:
        cols = line.split("\t")
        if cols[header.index("final_action")] == "KEEP_VERIFIED":
            assert all(cols[i] for i in indexes), line


def test_no_compiled_or_generated_artifacts_are_tracked() -> None:
    forbidden_suffixes = {".pyc", ".pyo", ".so", ".o", ".dylib", ".tpr", ".xtc", ".trr", ".edr", ".cpt"}
    offenders = [p for p in git_ls_files() if Path(p).suffix in forbidden_suffixes or Path(p).name == ".DS_Store"]
    assert offenders == []


def test_restricted_resource_paths_not_tracked() -> None:
    forbidden = [
        "resources/vendor/mstool/",
        "resources/forcefields/charmm36.ff/",
        "resources/forcefields/toppar/",
        "examples/legacy_kor/",
        "examples/test_membrane/",
    ]
    files = git_ls_files()
    offenders = [p for p in files if any(p.startswith(prefix) for prefix in forbidden)]
    assert offenders == []
    assert not any("rosetta" in p.lower() and not p.startswith(("docs/", "tests/", "scripts/", ".github/")) for p in files)
    assert not any("pyrosetta" in p.lower() and not p.startswith(("docs/", "tests/", "scripts/", ".github/", "config/")) for p in files)


def test_no_large_public_files() -> None:
    offenders = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and p.stat().st_size > 50 * 1024 * 1024]
    assert offenders == []


def test_public_script_inventory_covers_bash_and_python_scripts() -> None:
    inventory = ROOT / "tests" / "public_script_inventory.tsv"
    rows = [line.split("\t") for line in inventory.read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    listed = {row[0]: row[1] for row in rows}
    valid = {"public_cli", "internal_helper", "library_module", "test_utility"}
    assert set(listed.values()) <= valid
    discovered = {
        str(path.relative_to(ROOT))
        for dirname in ("scripts", "stages")
        for path in (ROOT / dirname).rglob("*")
        if path.is_file() and path.suffix in {".py", ".sh"}
    }
    discovered.update({"run_pipeline.sh", "setup.sh"})
    assert discovered - set(listed) == set()


def test_public_cli_scripts_have_syntax_and_help() -> None:
    inventory = ROOT / "tests" / "public_script_inventory.tsv"
    rows = [line.split("\t") for line in inventory.read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    for rel, classification in rows:
        if classification != "public_cli":
            continue
        path = ROOT / rel
        if path.suffix == ".py":
            syntax = subprocess.run([sys.executable, "-m", "py_compile", str(path)], cwd=ROOT)
            assert syntax.returncode == 0, rel
            help_cmd = [sys.executable, str(path), "--help"]
        else:
            syntax = subprocess.run(["bash", "-n", str(path)], cwd=ROOT)
            assert syntax.returncode == 0, rel
            help_cmd = ["bash", str(path), "--help"]
        result = subprocess.run(help_cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode == 0, rel
        assert "Usage" in result.stdout or "usage:" in result.stdout.lower() or result.stdout.strip(), rel


def test_public_pdb_dry_run_from_outside_repo_with_spaces_without_private_resources(tmp_path: Path) -> None:
    outside = tmp_path / "outside dir"
    outside.mkdir()
    output = tmp_path / "output dir"
    cache = tmp_path / "empty cache dir"
    env = os.environ.copy()
    env.update({
        "MEMBRANEFORGER_CACHE_DIR": str(cache),
        "ROSETTA_BIN": "",
        "ROSETTA_DATABASE": "",
        "PYROSETTA_PYTHON": "",
        "MOLFILE_TO_PARAMS": "",
        "MEMBRANEFORGER_CHARMM36_ROOT": "",
        "MEMBRANEFORGER_CGENFF_ROOT": "",
        "MEMBRANEFORGER_LIGAND_PARAMS_ROOT": "",
    })
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "run_pipeline.sh"),
            "--pdb",
            str(ROOT / "examples" / "minimal" / "inputs" / "minimal.pdb"),
            "--config",
            str(ROOT / "config" / "workflow.example.yaml"),
            "--output-dir",
            str(output),
            "--dry-run",
        ],
        cwd=outside,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["input_sha256"]
    assert "mstool_resolution_warning" in provenance
    assert (output / "effective_config.yaml").is_file()
    assert not (output / "stage1").exists()


def test_public_ci_never_installs_or_caches_rosetta_assets() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").lower()
    forbidden = ["pyrosetta", "rosetta_database", "rosetta_bin", "molfile_to_params"]
    assert "apt-get install -y dssp" in ci
    assert "command -v mkdssp" in ci
    assert "actions/cache" not in ci
    for word in forbidden:
        assert f"pip install {word}" not in ci
        assert f"curl" not in ci or word not in ci


def test_containers_do_not_embed_restricted_resources() -> None:
    offenders = []
    for path in (ROOT / "containers").rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for token in ("pyrosetta", "rosetta", "charmm36.ff", "resources/forcefields/toppar", "resources/vendor/mstool"):
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_bootstrap_resource_verify_and_offline_dry_run() -> None:
    martini = subprocess.run([sys.executable, str(ROOT / "scripts" / "bootstrap_resources.py"), "--component", "martini", "--verify"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert martini.returncode == 0, martini.stderr
    mstool = subprocess.run([sys.executable, str(ROOT / "scripts" / "bootstrap_resources.py"), "--component", "mstool", "--dry-run", "--offline"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert mstool.returncode == 0, mstool.stderr
