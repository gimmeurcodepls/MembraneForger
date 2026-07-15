#!/usr/bin/env python3
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


class PathResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class MstoolResolution:
    module: Any
    module_file: Path
    root: Path
    parent: Path
    mode: str
    version: str
    extension_cache: Path | None = None
    extension_mode: str = "not-built"
    extension_files: tuple[Path, ...] = ()
    commit: str = "unknown"

    def provenance(self, repo_root: Path = REPO_ROOT) -> dict[str, str]:
        data = {
            "mstool_source_mode": self.mode,
            "mstool_root": display_path(self.root, repo_root),
            "mstool_module_file": display_path(self.module_file, repo_root),
            "mstool_version": self.version,
            "mstool_commit": self.commit,
            "mstool_extension_mode": self.extension_mode,
        }
        if self.extension_cache is not None:
            data["mstool_extension_cache"] = display_path(self.extension_cache, repo_root)
        if self.extension_files:
            data["mstool_extension_files"] = ";".join(display_path(p, repo_root) for p in self.extension_files)
        return data


def display_path(path: Path, root: Path = REPO_ROOT) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def expand_path_value(value: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(value))
    if "$" in expanded:
        raise PathResolutionError(f"unresolved environment variable in path: {value!r}")
    return expanded


def resolve_cli_path(value: str | Path, *, base_dir: Path | None = None) -> Path:
    base = (base_dir or Path.cwd()).resolve()
    path = Path(expand_path_value(str(value)))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def resolve_config_path(value: str | Path, *, config_path: Path | None = None, root: Path = REPO_ROOT) -> Path:
    text = str(value)
    path = Path(expand_path_value(text))
    if path.is_absolute():
        return path.resolve()
    if text.startswith(("inputs/", "resources/", "work/", "outputs/", "logs/", "examples/", "config/", "stages/", "scripts/")):
        return (root / path).resolve()
    if config_path is not None:
        return (config_path.resolve().parent / path).resolve()
    return (root / path).resolve()


def _mstool_root_from_candidate(candidate: Path) -> Path:
    resolved = candidate.expanduser().resolve()
    if (resolved / "__init__.py").is_file() and resolved.name == "mstool":
        return resolved
    if (resolved / "mstool" / "__init__.py").is_file():
        return (resolved / "mstool").resolve()
    if resolved == (REPO_ROOT / "resources" / "vendor" / "mstool").resolve():
        raise PathResolutionError(
            "ERROR: repository-local mstool is not installed. Run: "
            "python scripts/bootstrap_resources.py --component mstool"
        )
    raise PathResolutionError(f"ERROR: repository-local mstool could not be found under {resolved}")


def _mstool_installed_commit(mstool_root: Path) -> str:
    metadata = mstool_root / ".membraneforger-install.json"
    if metadata.is_file():
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            return str(data.get("exact_commit", "unknown"))
        except Exception:
            return "unknown"
    return "unknown"


def _cache_root() -> Path:
    raw = os.environ.get("MEMBRANEFORGER_CACHE_DIR")
    source = "MEMBRANEFORGER_CACHE_DIR" if raw else "default cache"
    if raw:
        root = Path(expand_path_value(raw)).expanduser()
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        root = (Path(expand_path_value(xdg)).expanduser() if xdg else Path.home() / ".cache") / "membraneforger"
    root = root.resolve()
    if root.exists() and not root.is_dir():
        raise PathResolutionError(f"ERROR: {source} is not a directory: {root}")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise PathResolutionError(f"ERROR: could not create mstool cache root {root}: {exc}") from exc
    return root


def _python_tag() -> str:
    return str(sys.implementation.cache_tag or f"python-{sys.version_info.major}{sys.version_info.minor}")


def _platform_tag() -> str:
    return sysconfig.get_platform().replace("/", "_").replace(" ", "_")


def _extension_suffix() -> str:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise PathResolutionError("ERROR: Python EXT_SUFFIX is unavailable; cannot build mstool extensions")
    return str(suffix)


def mstool_extension_cache_dir(cache_root: Path | None = None) -> Path:
    root = cache_root or _cache_root()
    return root / "mstool" / _python_tag() / _platform_tag()


def _source_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_fingerprint(mstool_root: Path) -> dict[str, Any]:
    lib = mstool_root / "lib"
    sources = {
        "distancelib.c": _source_sha256(lib / "distancelib.c"),
        "qcprot.c": _source_sha256(lib / "qcprot.c"),
    }
    setup_py = lib / "setup.py"
    if setup_py.is_file():
        sources["setup.py"] = _source_sha256(setup_py)
    return {
        "builder": "membraneforger-external-mstool-cache-v1",
        "python_executable": str(Path(sys.executable).resolve()),
        "python_tag": _python_tag(),
        "platform_tag": _platform_tag(),
        "soabi": str(sysconfig.get_config_var("SOABI") or ""),
        "ext_suffix": _extension_suffix(),
        "cc": str(sysconfig.get_config_var("CC") or ""),
        "sources": sources,
    }


def _expected_extension_files(cache_dir: Path) -> tuple[Path, Path]:
    suffix = _extension_suffix()
    return (cache_dir / f"distancelib{suffix}", cache_dir / f"qcprot{suffix}")


def _metadata_matches(cache_dir: Path, fingerprint: dict[str, Any]) -> bool:
    metadata = cache_dir / "build-metadata.json"
    if not metadata.is_file():
        return False
    try:
        recorded = json.loads(metadata.read_text(encoding="utf-8"))
    except Exception:
        return False
    return recorded.get("fingerprint") == fingerprint and all(path.is_file() for path in _expected_extension_files(cache_dir))


def _lock_dir(path: Path, timeout: float = 120.0) -> Path:
    lock = path.parent / f".{path.name}.lock"
    start = time.monotonic()
    while True:
        try:
            lock.mkdir(parents=True)
            return lock
        except FileExistsError:
            if time.monotonic() - start > timeout:
                raise PathResolutionError(f"ERROR: timed out waiting for mstool extension cache lock {lock}")
            time.sleep(0.1)


def _numpy_include() -> str:
    code = "import numpy as np; print(np.get_include())"
    result = subprocess.run([sys.executable, "-c", code], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PathResolutionError(f"ERROR: numpy is required to build mstool extensions: {detail}")
    return result.stdout.strip().splitlines()[-1]


def _build_mstool_extensions(mstool_root: Path, cache_dir: Path, fingerprint: dict[str, Any]) -> None:
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_parent = Path(tempfile.mkdtemp(prefix=".mstool-build-", dir=str(cache_dir.parent)))
    build_lib = temp_parent / "lib"
    build_temp = temp_parent / "temp"
    setup_script = temp_parent / "build_mstool_extensions.py"
    lib = mstool_root / "lib"
    setup_script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "from setuptools import Extension, setup",
                f"include_dirs = [{_numpy_include()!r}]",
                "setup(",
                "    name='membraneforger-mstool-extensions',",
                "    ext_modules=[",
                f"        Extension('distancelib', [{str((lib / 'distancelib.c').resolve())!r}], include_dirs=include_dirs),",
                f"        Extension('qcprot', [{str((lib / 'qcprot.c').resolve())!r}], include_dirs=include_dirs),",
                "    ],",
                "    script_args=['build_ext', '--build-lib', " + repr(str(build_lib)) + ", '--build-temp', " + repr(str(build_temp)) + "],",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [sys.executable, str(setup_script)],
            cwd=str(temp_parent),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
        if result.returncode != 0:
            raise PathResolutionError("ERROR: mstool extension build failed:\n" + (result.stdout or "").strip())
        built = _expected_extension_files(build_lib)
        missing = [p.name for p in built if not p.is_file()]
        if missing:
            raise PathResolutionError(f"ERROR: mstool extension build did not produce: {', '.join(missing)}")
        metadata = {
            "fingerprint": fingerprint,
            "source_root": str(mstool_root),
            "cache_dir": str(cache_dir),
            "python_executable": str(Path(sys.executable).resolve()),
            "built_files": [p.name for p in built],
        }
        (build_lib / "build-metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        build_lib.rename(cache_dir)
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise
    shutil.rmtree(temp_parent, ignore_errors=True)


def ensure_mstool_extensions(mstool_root: Path, *, cache_root: Path | None = None) -> tuple[Path, str, tuple[Path, ...]]:
    for rel in ("lib/distancelib.c", "lib/qcprot.c"):
        if not (mstool_root / rel).is_file():
            raise PathResolutionError(f"ERROR: missing canonical mstool extension source {mstool_root / rel}")
    cache_dir = mstool_extension_cache_dir(cache_root)
    fingerprint = _build_fingerprint(mstool_root)
    if _metadata_matches(cache_dir, fingerprint):
        return cache_dir, "external-cache-reused", _expected_extension_files(cache_dir)
    lock = _lock_dir(cache_dir)
    try:
        if _metadata_matches(cache_dir, fingerprint):
            return cache_dir, "external-cache-reused", _expected_extension_files(cache_dir)
        _build_mstool_extensions(mstool_root, cache_dir, fingerprint)
        return cache_dir, "external-cache-built", _expected_extension_files(cache_dir)
    finally:
        shutil.rmtree(lock, ignore_errors=True)


class _MstoolExtensionFinder(importlib.abc.MetaPathFinder):
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        short = fullname.rsplit(".", 1)[-1]
        if fullname not in {"mstool.lib.distancelib", "mstool.lib.qcprot"}:
            return None
        candidate = self.cache_dir / f"{short}{_extension_suffix()}"
        if not candidate.is_file():
            return None
        loader = importlib.machinery.ExtensionFileLoader(fullname, str(candidate))
        return importlib.util.spec_from_file_location(fullname, str(candidate), loader=loader)


def _install_mstool_extension_finder(cache_dir: Path) -> None:
    sys.meta_path[:] = [
        finder
        for finder in sys.meta_path
        if not isinstance(finder, _MstoolExtensionFinder)
    ]
    sys.meta_path.insert(0, _MstoolExtensionFinder(cache_dir))


def resolve_mstool(
    *,
    root: Path = REPO_ROOT,
    configured: str | Path | None = None,
    import_module: bool = True,
    build_extensions: bool = True,
) -> MstoolResolution:
    override = os.environ.get("MEMBRANEFORGER_MSTOOL_ROOT")
    if override:
        mstool_root = _mstool_root_from_candidate(Path(expand_path_value(override)))
        mode = "override:MEMBRANEFORGER_MSTOOL_ROOT"
    else:
        candidate = Path(configured) if configured is not None else root / "resources" / "vendor" / "mstool"
        if not candidate.is_absolute():
            candidate = root / candidate
        mstool_root = _mstool_root_from_candidate(candidate)
        mode = "repository"
    parent = mstool_root.parent.resolve()
    extension_cache: Path | None = None
    extension_mode = "not-built"
    extension_files: tuple[Path, ...] = ()
    if build_extensions:
        extension_cache, extension_mode, extension_files = ensure_mstool_extensions(mstool_root)
        _install_mstool_extension_finder(extension_cache)
    if import_module:
        for name in list(sys.modules):
            if name == "mstool" or name.startswith("mstool."):
                del sys.modules[name]
        sys.path.insert(0, str(parent))
        module = importlib.import_module("mstool")
        module_file = Path(module.__file__).resolve()
        if module_file != mstool_root / "__init__.py" and mstool_root not in module_file.parents:
            raise PathResolutionError(
                f"Unexpected mstool import location: {module_file}; expected below {mstool_root}"
            )
        if extension_cache is not None:
            lib_pkg = importlib.import_module("mstool.lib")
            lib_path = getattr(lib_pkg, "__path__", None)
            if lib_path is not None and str(extension_cache) not in lib_path:
                lib_path.append(str(extension_cache))
        version = str(getattr(module, "__version__", getattr(module, "version", "unknown")))
    else:
        module = None
        module_file = (mstool_root / "__init__.py").resolve()
        version = "not-imported"
    return MstoolResolution(
        module=module,
        module_file=module_file,
        root=mstool_root,
        parent=parent,
        mode=mode,
        version=version,
        extension_cache=extension_cache,
        extension_mode=extension_mode,
        extension_files=extension_files,
        commit=_mstool_installed_commit(mstool_root),
    )


def gmx_command() -> list[str]:
    gmx_bin = os.environ.get("GMX_BIN")
    if gmx_bin:
        return [gmx_bin]
    sif = Path("/opt/Gromacs/2022.1.sif")
    singularity_gmx = Path("/usr/local/gromacs/avx2_256/bin/gmx")
    singularity = shutil.which("singularity")
    if sif.exists() and singularity_gmx.exists() and singularity:
        return [singularity, "exec", str(sif), str(singularity_gmx)]
    for candidate in ("/usr/local/gromacs/avx2_256/bin/gmx", "/usr/local/gromacs/bin/gmx"):
        if Path(candidate).exists():
            return [candidate]
    found = shutil.which("gmx")
    if found:
        return [found]
    raise PathResolutionError("GROMACS executable not found; set GMX_BIN or expose gmx on PATH")
