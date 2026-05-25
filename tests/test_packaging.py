from __future__ import annotations

import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path


def test_pyproject_declares_package_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "pyfhircheck"' in pyproject
    assert "pyfhircheck.__version__" in pyproject
    assert (root / "LICENSE").is_file()
    assert (root / "src" / "pyfhircheck" / "py.typed").is_file()


def test_installed_distribution_exposes_entry_point() -> None:
    entry_points = importlib.metadata.entry_points(group="console_scripts")
    scripts = {entry.name: entry.value for entry in entry_points if entry.name == "pyfhircheck"}
    assert scripts["pyfhircheck"] == "pyfhircheck.cli:main"


def test_wheel_builds_from_source_tree(tmp_path: Path) -> None:
    if shutil.which("python") is None:
        return
    root = Path(__file__).resolve().parents[1]
    dist_dir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(dist_dir), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist_dir.glob("pyfhircheck-*.whl"))
    sdist = list(dist_dir.glob("pyfhircheck-*.tar.gz"))
    assert wheels
    assert sdist
