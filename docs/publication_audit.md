# Publication Audit

Audit date: 2026-07-15.

The public snapshot contains project-authored workflow code, documentation,
minimal synthetic examples, verified Apache-2.0 Martini files, and bootstrap
metadata for mstool. Restricted and unverifiable resources are excluded.

| path or pattern | disposition | public status |
|---|---|---|
| `resources/vendor/mstool/` | installed by user bootstrap from pinned GPL upstream | EXTERNAL - NOT REDISTRIBUTED |
| `resources/forcefields/martini/` | retained byte-identical from official Apache-2.0 upstream | KEEP_VERIFIED |
| `resources/forcefields/charmm36.ff/` | removed; user supplies licensed directory | EXTERNAL - NOT REDISTRIBUTED |
| `resources/forcefields/toppar/` | removed; user supplies licensed CHARMM/CGenFF/toppar files | EXTERNAL - NOT REDISTRIBUTED |
| `resources/ligand_params/GLPA/` parameter assets | removed; user supplies reproducible licensed parameters | EXTERNAL - NOT REDISTRIBUTED |
| `examples/legacy_kor/` | removed from public snapshot | EXTERNAL - NOT REDISTRIBUTED |
| `examples/test_membrane/` | removed from public snapshot | EXTERNAL - NOT REDISTRIBUTED |
| Rosetta, PyRosetta, Rosetta database, `molfile_to_params.py` | user-supplied licensed installations only | EXTERNAL - NOT REDISTRIBUTED |
| DSSP/mkdssp | external executable, installable in public CI | EXTERNAL - NOT REDISTRIBUTED |
| `examples/minimal/` | project-authored synthetic public dry-run fixture | KEEP_VERIFIED |

All public-snapshot entries are resolved by removal, bootstrap installation,
verified retention, or user-supplied external configuration.
