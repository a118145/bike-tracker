"""The one import boundary the architecture rests on (brief §3.3).

``metrics/`` must import neither ``reflex`` nor the models. That is not a
stylistic preference: it is the condition that lets the metric tests run in
milliseconds without an app, a database or fixtures, which is in turn what
makes it practical to check every derived number against a real FIT file.

A boundary that is only written down in a document erodes. This test is what
makes crossing it a build failure.
"""

import ast
from pathlib import Path

import pytest

METRICS_DIR = Path(__file__).resolve().parent.parent / "bike_tracker" / "metrics"
METRICS_MODULES = sorted(METRICS_DIR.glob("*.py"))

FORBIDDEN_PREFIXES = ("reflex", "sqlmodel", "sqlalchemy", "fitdecode")
FORBIDDEN_RELATIVE = ("models", "ingest", "states", "components", "pages", "ui")


def _imported_names(path: Path) -> list[str]:
    """Every module name imported by ``path``, relative imports included."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # A relative import: ".." escapes the package.
                names.append("." * node.level + (node.module or ""))
            elif node.module:
                names.append(node.module)
    return names


def test_there_are_metrics_modules_to_check() -> None:
    """Guards against the glob silently matching nothing."""
    assert len(METRICS_MODULES) >= 5


@pytest.mark.parametrize("path", METRICS_MODULES, ids=lambda p: p.name)
def test_metrics_imports_no_framework(path: Path) -> None:
    for name in _imported_names(path):
        assert not name.startswith(FORBIDDEN_PREFIXES), (
            f"{path.name} imports {name!r} — metrics/ must stay framework-free"
        )


@pytest.mark.parametrize("path", METRICS_MODULES, ids=lambda p: p.name)
def test_metrics_reaches_no_other_package(path: Path) -> None:
    """Relative imports may not climb out of ``metrics/``."""
    for name in _imported_names(path):
        assert not name.startswith(".."), f"{path.name} imports {name!r} from outside metrics/"
        assert not any(name.startswith(f".{pkg}") for pkg in FORBIDDEN_RELATIVE), (
            f"{path.name} imports {name!r}"
        )


def test_metrics_is_importable_without_reflex_loaded() -> None:
    """Importing metrics must not drag the whole framework in with it.

    Run in a subprocess so an already-imported reflex in this process cannot
    mask the dependency.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import bike_tracker.metrics; "
            "assert 'reflex' not in sys.modules, "
            "'importing metrics pulled in reflex'; print('ok')",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
