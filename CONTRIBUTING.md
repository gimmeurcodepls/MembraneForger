# Contributing

Keep changes stage-gated, reproducible, and conservative.

- Do not commit generated `work/`, `outputs/`, or `logs/` content.
- Do not add large scientific fixtures without provenance, checksum, license status, and a test that requires them.
- Do not change scientific defaults to satisfy tests; update tests only when the documented behavior changes.
- Use repository-relative path resolution and keep user inputs outside source-controlled examples unless they are redistributable fixtures.
- Record third-party resource provenance in `resources/RESOURCE_MANIFEST.tsv` and unresolved publication issues in `docs/publication_audit.md`.

Before opening a pull request:

```bash
bash -n setup.sh
bash -n run_pipeline.sh
find stages -type f -name '*.sh' -exec bash -n {} \;
python -m compileall scripts stages tests
python -m pytest -q
```
