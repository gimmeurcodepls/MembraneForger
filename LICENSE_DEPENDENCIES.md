# Licensed And External Dependencies

MembraneForger distinguishes redistributable public files from dependencies
that users must install or provide themselves.

## Open Resources

- Martini 3 parameter files retained under `resources/forcefields/martini/`
  are byte-identical to the official Apache-2.0 upstream repository at commit
  `784591ebdc91d762ed4df986c4650546c938f776`.
- `mstool` is a GPL-3.0 dependency installed separately with
  `python scripts/bootstrap_resources.py --component mstool`. It is not
  tracked in the public repository.

## EXTERNAL - NOT REDISTRIBUTED

- CHARMM36: set `MEMBRANEFORGER_CHARMM36_ROOT`.
- CGenFF/toppar resources: set `MEMBRANEFORGER_CGENFF_ROOT`.
- GLPA or other ligand parameters: set `MEMBRANEFORGER_LIGAND_PARAMS_ROOT`.
- DSSP/mkdssp: install a package that provides `mkdssp` or set `DSSP_BIN`.
- PyRosetta: set `PYROSETTA_PYTHON` to a licensed Python environment.
- Rosetta utilities: set `ROSETTA_BIN`.
- Rosetta database: set `ROSETTA_DATABASE`.
- Rosetta `molfile_to_params.py`: set `MOLFILE_TO_PARAMS`.

Rosetta and PyRosetta are never downloaded, installed, cached, vendored,
uploaded, or redistributed by public CI.
