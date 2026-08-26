"""Does the app import cleanly with the path the container actually gives it?

The container runs `uvicorn api:app` from /app/webapp, and `api.py` puts exactly
one directory on sys.path: ROOT/src. Nothing puts ROOT itself there.

Every other test in this suite inserts ROOT so it can `import src.something`,
and a dev server started from the repo root picks ROOT up from the working
directory. Both are more permissive than production. A module that imports
`src.synthdata.fabric` therefore passes every test, runs fine locally, and
returns 500 on every page in the container -- which is exactly what shipped.

These run the import in a subprocess with only the paths the app itself sets, so
the permissiveness of this test process cannot mask the problem.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Every module the webapp imports at request time. A module missing from here
#: is not covered, so add to it when the app grows a dependency.
APP_MODULES = [
    "planning",
    "planning.recommend",
    "ontology.build",
    "synthdata.generate",
    "synthdata.fleet",
    "synthdata.fabric",
    "module1.threshold",
    "module5.ingest",
    "admission",
]


def _import_with_app_path(statement: str) -> subprocess.CompletedProcess:
    """Run a statement with only ROOT/src on the path, as the container has it.

    Deliberately not `sys.path.insert` in this process: pytest has already put
    ROOT on the path, and anything running here inherits that.
    """
    # A fresh interpreter started in webapp/ already has the container's path:
    # cwd first, then the standard library. All that is missing is the one
    # insert api.py performs. Rebuilding sys.path wholesale severed the stdlib
    # and made every case fail on `from __future__ import annotations`.
    code = f"import sys; sys.path.insert(0, r'{ROOT / 'src'}')\n{statement}"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=str(ROOT / "webapp"), env=env)


@pytest.mark.parametrize("module", APP_MODULES)
def test_module_imports_without_root_on_the_path(module):
    r = _import_with_app_path(f"import {module}")
    assert r.returncode == 0, (
        f"{module} does not import the way the app imports it.\n"
        f"Most likely an `import src.x` that needs ROOT on sys.path, where the "
        f"app only provides ROOT/src.\n{r.stderr.strip()[-400:]}")


def test_no_source_module_imports_itself_through_the_src_prefix():
    """`src.` inside src/ only resolves where ROOT happens to be on the path.

    Cheap to grep for and impossible to notice by reading, because it works
    everywhere a developer looks.
    """
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("from src.", "import src.")):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {stripped}")
    assert not offenders, (
        "modules under src/ importing through the `src.` prefix:\n  "
        + "\n  ".join(offenders))


def test_the_webapp_itself_starts_with_the_container_path():
    """The real check: does `import api` work where the container runs it?"""
    r = _import_with_app_path("import api; assert api.app")
    assert r.returncode == 0, f"webapp/api.py fails to import:\n{r.stderr.strip()[-500:]}"
