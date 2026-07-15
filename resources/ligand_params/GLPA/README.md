# GLPA Ligand Parameters

GLPA ligand parameter files are not redistributed in the public snapshot.

The previous local files could not be retained without complete provenance for
Rosetta/PyRosetta, CGenFF, Martini-derived, and generated parameter content.
Users who enable a GLPA or glycolipid workflow must provide their own licensed
and reproducible parameter directory with:

- `MEMBRANEFORGER_LIGAND_PARAMS_ROOT`, or
- the equivalent workflow configuration value.

MembraneForger validates this directory only when the selected workflow mode
requires GLPA ligand parameters.
