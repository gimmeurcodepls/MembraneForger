# Stage 1

Stage 1 prepares the configured `stage1.input_pdb` for CG membrane setup. Public runs can set this through `run_pipeline.sh --pdb`; the original input is staged as `input/original.pdb` under the run output directory and is never modified in place.

Generic config does not assume a chain ID. If orientation is enabled, `stage1.orientation_chain` must be explicit and present in the input.
