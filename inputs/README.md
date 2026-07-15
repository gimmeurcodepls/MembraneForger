# User Inputs

Place private or project-specific input structures here, or pass them from any other directory with `run_pipeline.sh --pdb /path/to/structure.pdb`.

Files in this directory are ignored by Git by default. Do not commit unpublished structures, licensed data, or user-specific inputs unless they are intentionally redistributable fixtures with documented provenance.

Path resolution does not depend on the shell's current directory:

- CLI paths such as `--pdb` and `--output-dir` are resolved relative to the caller's current directory when relative.
- Repository defaults such as `resources/` and `examples/` are resolved relative to the repository root.
- Example configuration paths are documented in their local README files.
