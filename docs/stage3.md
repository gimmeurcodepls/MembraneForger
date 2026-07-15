# Stage 3

Stage 3 consumes `outputs/<run_id>/stage2/cg_backmap_input.gro` and `inputs/aa_protlig.pdb`. `inputs/opm_reference.pdb` is required only when `stage3.placement.use_opm: true`.

Stage 3 imports `mstool` from `resources/vendor/mstool` by default and records the resolved module path in provenance. It must not rely on `work/runtime_vendor/mstool`.

`fixed` means the protein coordinates already present in the backmapped CG-to-AA structure. `moving` means the AA protein/ligand reference coordinates. The transform is applied to the appropriate moving AA atoms and is not silently swapped.
