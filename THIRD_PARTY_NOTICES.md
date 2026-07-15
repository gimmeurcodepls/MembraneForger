# Third-Party Notices

Detailed file-level records are in `docs/third_party_inventory.tsv`.

## Martini Force Fields

- Copyright: upstream Martini contributors as recorded in the official source
- License: Apache-2.0
- Version/commit: `784591ebdc91d762ed4df986c4650546c938f776`
- Source: `https://github.com/marrink-lab/martini-forcefields.git`
- Required notice: retain Apache-2.0 license text in `LICENSES/Apache-2.0.txt`
- Required citations: Martini 3 general citation and molecule-specific
  citations from upstream documentation
- Local modifications: none
- Status: vendored public resource

## mstool

- License: GPL-3.0-only
- Version/commit: `2d37f9d3e89279ddd9125cc74da1f5e01153586c`
- Source: `https://github.com/ksy141/mstool.git`
- Local modifications: none
- Status: bootstrap installation into `resources/vendor/mstool/`

The public repository does not contain mstool source and the MembraneForger MIT
license does not relicense mstool.

## Restricted External Dependencies

CHARMM36, CGenFF, Rosetta, PyRosetta, Rosetta database files,
`molfile_to_params.py`, and user ligand parameters are external user-supplied
dependencies and are not redistributed.
