# Software Dependencies

Executable resolution order is:

1. documented environment variable;
2. active `PATH` when appropriate;
3. actionable mode-specific failure.

Supported variables:

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
- `GMX_BIN`

`mstool` is resolved from `resources/vendor/mstool` by default after
bootstrap. `MEMBRANEFORGER_MSTOOL_ROOT` is an explicit override. Compiled
extensions are built into `${MEMBRANEFORGER_CACHE_DIR}` or the platform cache,
not into the repository.

DSSP is required only for DSSP-dependent modes. PyRosetta and Rosetta are
required only for modes that explicitly enable those integrations. Minimal
public dry-runs do not require Rosetta, PyRosetta, CHARMM/CGenFF, GLPA
parameters, or private regression data.

Run:

```bash
python scripts/dependency_resolver.py
bash setup.sh --check
```
