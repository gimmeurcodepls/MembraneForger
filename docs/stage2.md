# Stage 2

Stage 2 consumes run-scoped Stage 1 outputs and produces `cg_minimized.gro`, `cg_equilibrated.gro`, and `cg_backmap_input.gro`. Production trajectory artifacts are required only when production mode is configured.

The portable wrapper removes receptor arrays, module loads, and broad warning suppression from the generic control path.

Stage 2 is the long-running CG equilibration stage. It is not collapsed into a one-command full pipeline runner; downstream stages should consume only validated Stage 2 outputs.
