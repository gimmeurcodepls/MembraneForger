# Reproducibility

The portable workflow derives paths from the repository root, config, CLI arguments, documented environment variables, or `PATH`.

CLI paths are resolved relative to the caller's current directory. Repository defaults are resolved relative to the repository root. Run-specific effective configuration and input provenance are written below `--output-dir`.

Static and dry-run checks validate path handling and mode-dependent requirements. They do not run scientific regression or full simulation.

There is intentionally no single full-pipeline production script because the CG equilibration stage must be run, monitored, and validated before backmapping and AA preparation.

Use copied-path validation:

```bash
tmpdir="$(mktemp -d)"
cp -R . "$tmpdir/MembraneForger"
cd "$tmpdir/MembraneForger"
bash setup.sh --check
bash run_pipeline.sh --check
bash run_pipeline.sh --pdb examples/minimal/inputs/minimal.pdb --config config/workflow.example.yaml --output-dir outputs/minimal --dry-run
python3 -m pytest -q
```

The public workflow records missing `mstool` as a bootstrap action during
minimal dry-runs. Full Stage 3 backmapping requires:

```bash
python scripts/bootstrap_resources.py --component mstool
```
