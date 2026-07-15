# MembraneForger


<img width="1836" height="770" alt="Artboard 1@3x" src="https://github.com/user-attachments/assets/bc60169c-9c0e-4710-952a-9d64287fe7fa" />


MembraneForger is an end-to-end, stage-gated workflow for preparing membrane-protein systems for molecular dynamics simulations. It connects structure generation, Martini 3 coarse-graining, membrane construction, CG simulation handoff, CG-to-AA backmapping, and all-atom system preparation within a single reproducible framework. The workflow integrates Martinize2/Vermouth, INSANE, GROMACS, mstool while retaining intermediate outputs and validation checkpoints at each stage.[1–6]

By replacing a fragmented sequence of manual tools with a traceable pipeline, MembraneForger reduces setup overhead, improves reproducibility, and makes it easier to identify where structural or parameterization issues arise. Automation is applied to routine preparation steps, while users can decide protonation, termini, membrane orientation, ligands, glycans, chain assignments, structural completeness, and force-field coverage.

References
Souza et al. Martini 3: A General Purpose Force Field for Coarse-Grained Molecular Dynamics. Nature Methods 18, 382–388 (2021).
Kroon et al. Martinize2 and Vermouth Provide a Unified Framework for Topology Generation. eLife (2025).
Wassenaar et al. Computational Lipidomics with INSANE: A Versatile Tool for Generating Custom Membranes for Molecular Simulations. J. Chem. Theory Comput. 11, 2144–2155 (2015).
Kim. Backmapping with Mapping and Isomeric Information. J. Phys. Chem. B (2023).
Páll et al. Heterogeneous Parallelization and Acceleration of Molecular Dynamics Simulations in GROMACS. J. Chem. Phys. 153, 134110 (2020).

## Quick Start

Check dependencies:

```bash
./run_pipeline.sh --check-dependencies
```

Dry-run a user-owned PDB from any directory:

```bash
/absolute/path/to/MembraneForger/run_pipeline.sh \
  --pdb /absolute/or/relative/path/to/my_structure.pdb \
  --config /absolute/path/to/MembraneForger/config/workflow.example.yaml \
  --output-dir /absolute/or/relative/path/to/output_dir \
  --dry-run
```

The command stages `input/original.pdb`, writes `provenance.json`, records the SHA256 checksum of the original PDB, writes `effective_config.yaml` and `effective_config.json`, and prints the planned stage paths. Dry-run mode does not run molecular dynamics.

## Workflow Stages

1. Stage 1: prepare an AA protein input for coarse-graining with martinize2.
2. Stage 2: run or validate CG preprocessing/equilibration.
3. Stage 3: backmap accepted CG coordinates to AA coordinates with `mstool`.
4. Stage 4: prepare AA topology, solvation, index, and minimization files when all required parameters and tools are available.

Production runs should be reviewed one stage at a time:

```bash
bash stages/stage1_setup_cg/run.sh --config outputs/my_run/effective_config.yaml --run-id my_run
bash stages/stage2_run_cg/run.sh --config outputs/my_run/effective_config.yaml --run-id my_run
python stages/stage3_backmap/run.py --config outputs/my_run/effective_config.yaml --run-id my_run
bash stages/stage4_prepare_aa/run.sh --config outputs/my_run/effective_config.yaml --run-id my_run
```

## Inputs And Paths

- CLI paths such as `--pdb`, `--config`, and `--output-dir` are resolved relative to the caller's current directory when relative.
- Repository defaults such as `resources/`, `examples/`, `work/`, `outputs/`, and `logs/` are resolved from the repository root.
- User inputs belong in `inputs/` or any user-owned external path; `inputs/*` is ignored by Git by default.
- The original PDB is never modified in place.
- Output directories are not overwritten unless `--overwrite` is supplied.

## Repository-Local mstool

`resources/vendor/mstool/` is the canonical local `mstool` installation path,
but the upstream GPL source is not tracked in Git. Install the pinned version:

```bash
python scripts/bootstrap_resources.py --component mstool
```

MembraneForger inserts `resources/vendor` ahead of ambient Python paths and
verifies that `mstool.__file__` resolves below the selected repository-local
tree. The workflow must not depend on `work/runtime_vendor/mstool` or a
globally installed `mstool`.

Advanced users may set `MEMBRANEFORGER_MSTOOL_ROOT` to an intentional override. The override is validated and recorded in provenance.

## Installation

Conda:

```bash
conda env create -f environments/environment.yml
conda activate membraneforger
python scripts/check_python_environment.py
```

Locked Python requirements are listed in:

```text
environments/requirements-lock.txt
environments/constraints.txt
```

Docker:

```bash
docker build -f containers/Dockerfile -t membraneforger:portable .
```

Apptainer:

```bash
apptainer build MembraneForger.sif containers/Apptainer.def
```

External tools used by enabled stages may include GROMACS, martinize2/Vermouth,
INSANE, OpenMM, DSSP, Rosetta, and PyRosetta. DSSP is installable in public CI
and configurable with `DSSP_BIN`. Rosetta, PyRosetta, Rosetta databases,
`molfile_to_params.py`, CHARMM36, CGenFF, and ligand parameter directories are
user-supplied licensed resources and are not bundled.

Relevant variables:

- `MEMBRANEFORGER_MSTOOL_ROOT`
- `MEMBRANEFORGER_CACHE_DIR`
- `MEMBRANEFORGER_CHARMM36_ROOT`
- `MEMBRANEFORGER_CGENFF_ROOT`
- `MEMBRANEFORGER_LIGAND_PARAMS_ROOT`
- `DSSP_BIN`
- `PYROSETTA_PYTHON`
- `ROSETTA_BIN`
- `ROSETTA_DATABASE`
- `MOLFILE_TO_PARAMS`

MembraneForger builds required `mstool` compiled extensions into an external
cache under `${MEMBRANEFORGER_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/membraneforger}/mstool/<python-tag>/<platform-tag>/`;
compiled binaries should not appear in the source tree.

## Outputs

For `--output-dir outputs/my_run`, dry-run/staging writes:

```text
outputs/my_run/
  effective_config.json
  effective_config.yaml
  input/original.pdb
  provenance.json
```

Stage execution may additionally create:

```text
outputs/my_run/stage1/
outputs/my_run/stage2/
outputs/my_run/stage3/
outputs/my_run/stage4/
outputs/my_run/work/
outputs/my_run/logs/
```

Generated runtime products are ignored by Git. Preserve selected outputs outside the repository or document them as immutable references before publication.

## Examples

Minimal public dry-run:

```bash
./run_pipeline.sh \
  --pdb examples/minimal/inputs/minimal.pdb \
  --config config/workflow.example.yaml \
  --output-dir outputs/minimal \
  --dry-run
```

## Tests

```bash
bash -n setup.sh
bash -n run_pipeline.sh
find stages -type f -name '*.sh' -exec bash -n {} \;
python -m compileall scripts stages tests
python -m pytest -q
```

Optional when installed:

```bash
shellcheck setup.sh run_pipeline.sh stages/*/*.sh
ruff check scripts stages tests
```

## Citation And Licensing

If you use MembraneForger, cite this repository and the upstream tools listed in `THIRD_PARTY_NOTICES.md`. Repository source is covered by `LICENSE`. Optional licensed dependencies are documented in `LICENSE_DEPENDENCIES.md`.

Resource provenance is tracked in `resources/RESOURCE_MANIFEST.tsv`,
`docs/third_party_inventory.tsv`, and `docs/publication_audit.md`.

## Known Limitations

- The public controller performs staging, validation, and dry-run planning; long MD stages remain explicit and stage-gated.
- Full production modes require user-supplied external scientific resources when
  the selected mode depends on CHARMM/CGenFF, GLPA ligand parameters, DSSP,
  Rosetta, or PyRosetta.

## Troubleshooting

- `ERROR: input PDB does not exist`: check the `--pdb` path relative to the directory where you launched the command.
- `ERROR: output directory already exists`: choose a new `--output-dir` or pass `--overwrite`.
- `repository-local mstool is not installed`: run `python scripts/bootstrap_resources.py --component mstool`.
- `GROMACS executable not found`: set `GMX_BIN` or expose `gmx` on `PATH`.
