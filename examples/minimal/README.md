# Minimal Public Example

This example verifies the public input interface without running molecular dynamics.

Input:

- `inputs/minimal.pdb`: a tiny synthetic alanine dipeptide-like PDB fixture for CLI validation only.

Run from any directory:

```bash
/absolute/path/to/MembraneForger/run_pipeline.sh \
  --pdb /absolute/path/to/MembraneForger/examples/minimal/inputs/minimal.pdb \
  --config /absolute/path/to/MembraneForger/config/workflow.example.yaml \
  --output-dir /tmp/membraneforger-minimal \
  --dry-run
```

Expected output:

```text
output/
  effective_config.json
  effective_config.yaml
  input/original.pdb
  provenance.json
```

Generated output is not committed. A successful dry-run means file-format validation, repository-local `mstool` resolution, static workflow checks, input checksum recording, and path planning completed. It does not mean the structure is biochemically suitable for simulation.
