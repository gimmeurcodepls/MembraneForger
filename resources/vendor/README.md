# Vendor Resource Policy

`resources/vendor/mstool/` is intentionally not tracked in Git.

MembraneForger resolves `mstool` from this repository-specific path by default,
or from `MEMBRANEFORGER_MSTOOL_ROOT` when a user explicitly supplies an
external installation. To install the pinned GPL dependency into the expected
repository-local location, run:

```bash
python scripts/bootstrap_resources.py --component mstool
```

The top-level MembraneForger MIT license does not relicense `mstool`.
