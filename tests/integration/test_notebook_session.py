#!/usr/bin/env python3
# File: ./tests/integration/test_notebook_session.py

"""``@session`` inside a Jupyter kernel (scitex-session#77).

Real notebooks executed by real kernels: headless ``python -m nbconvert
--execute`` (the parent process names the notebook) and nbclient driven from
a plain Python script (it does not). Observers are real hook functions
registered by the notebook's own first cell and printing what they receive;
the cell outputs are read back from the executed notebook. No mocks. Each
notebook executes once per module (fixtures); each test checks one fact.

Problems covered:

1. the kernel's argv (``-f <connection file> ...``) is never parsed as the
   function's CLI — defaults apply; ``%run script.py --n 5`` still parses;
2. the run is filed under the NOTEBOOK (script, project root, ``<nb>_out/``),
   not under the kernel's ``/tmp/ipykernel_*`` cell file;
3. the notebook is recorded: ``entry`` = notebook path, how it was found,
   its code-cell hash (stable under re-execution), the executed cells;
   a ``%run`` script records its driver notebook as ``notebook_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
pytest.importorskip("nbclient")
pytest.importorskip("nbconvert")
pytest.importorskip("ipykernel")

import scitex_session  # noqa: E402
from scitex_session._lifecycle._notebook import notebook_code_sha256  # noqa: E402

#: Run the in-tree package in the kernel, not whatever is installed.
_SRC = str(Path(scitex_session.__file__).resolve().parent.parent)

OBSERVERS = textwrap.dedent(
    """
    import json, os, sys
    sys.path.insert(0, %r)
    import scitex_session
    from scitex_session import session, INJECTED
    import scitex_io as sio

    def on_start(session_id, script_path, metadata):
        print("START=" + json.dumps({"script_path": script_path, "metadata": metadata}))

    def on_close(status, exit_code, metadata=None):
        print("CLOSE=" + json.dumps({"status": status, "metadata": metadata}))

    scitex_session.register_session_start_hook(on_start)
    scitex_session.register_session_close_hook(on_close)
    """
    % _SRC
)

MAIN_CELL = textwrap.dedent(
    """
    @session(verbose=False)
    def main(n: int = 3, CONFIG=INJECTED):
        rows = sio.load("../data/x.csv")
        sio.save({"n": n, "rows": len(rows)}, os.path.abspath("../results/out.json"))
        return 0
    """
)

COMPUTE = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, %r)
    import scitex_io as sio
    from scitex_session import session, INJECTED

    @session(verbose=False)
    def main(n: int = 3, CONFIG=INJECTED):
        sio.save({"n": n}, os.path.abspath("../results/run.json"))
        return 0

    if __name__ == "__main__":
        main()
    """
    % _SRC
)

#: Executes the notebook named by $DRIVE_NB — NOT on its command line, so the
#: kernel's launcher names only what a test passes as extra arguments.
DRIVER = textwrap.dedent(
    """
    import os, sys, nbformat, nbclient
    nb = nbformat.read(os.environ["DRIVE_NB"], as_version=4)
    cwd = os.path.dirname(os.environ["DRIVE_NB"])
    nbclient.NotebookClient(nb, resources={"metadata": {"path": cwd}}).execute()
    nbformat.write(nb, os.environ["DRIVE_OUT"])
    """
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(root: Path) -> Path:
    """A non-VCS SciTeX research layout: config/ data/ notebooks/ scripts/."""
    for d in ("config", "data", "notebooks", "scripts", "results"):
        (root / d).mkdir(parents=True)
    (root / "config" / "PARAMS.yaml").write_text("SCALE: 3\n")
    (root / "data" / "x.csv").write_text("id,value\n1,1.5\n2,2.5\n")
    (root / "scripts" / "compute.py").write_text(COMPUTE)
    return root.resolve()


def _notebook(path: Path, *cells: str) -> Path:
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(c) for c in cells]
    nb.metadata["kernelspec"] = {
        "name": "python3",
        "display_name": "Python 3",
        "language": "python",
    }
    nbformat.write(nb, str(path))
    return path


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCITEX_NOTEBOOK")}
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _outputs(executed: dict) -> str:
    """The cells' STDOUT text, plus one ``ERROR ...`` line per failed cell.

    stdout and stderr arrive as separate stream outputs whose chunks need not
    end at a newline: concatenating both put a stderr ``WARN:`` in the middle
    of a ``START=`` JSON line (CI flake). The observers print to stdout.
    """
    text = []
    for cell in executed["cells"]:
        for out in cell.get("outputs", []):
            if out.get("output_type") == "error":
                text.append(f"\nERROR {out.get('ename')}: {out.get('evalue')}\n")
            elif out.get("output_type") == "stream" and out.get("name") == "stdout":
                text.append("".join(out.get("text", "")))
    return "".join(text)


def _events(text: str, tag: str) -> list:
    prefix = tag + "="
    return [json.loads(x[len(prefix):]) for x in text.splitlines() if x.startswith(prefix)]  # fmt: skip


def _nbconvert(nb: Path, cwd: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "nbconvert", "--to", "notebook", "--execute", *extra,
         os.path.relpath(nb, cwd)],
        cwd=cwd, env=_env(), capture_output=True, text=True, timeout=300,
    )  # fmt: skip


def _execute(nb: Path, cwd: Path) -> dict:
    """Execute headless with nbconvert; the observed start/close events."""
    proc = _nbconvert(nb, cwd, "--stdout")
    assert proc.returncode == 0, proc.stderr[-3000:]
    text = _outputs(json.loads(proc.stdout))
    return {
        "text": text,
        "start": _events(text, "START"),
        "close": _events(text, "CLOSE"),
    }


def _drive(project: Path, nb: Path, *extra_argv: str, **env) -> dict:
    """Execute ``nb`` with nbclient from a plain script (+ ``extra_argv``)."""
    driver = project.parent / "drive.py"
    driver.write_text(DRIVER)
    out = project.parent / f"executed-{nb.stem}.ipynb"
    proc = subprocess.run(
        [sys.executable, str(driver), *extra_argv],
        cwd=project, env=_env(DRIVE_NB=str(nb), DRIVE_OUT=str(out), **env),
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr[-3000:]
    text = _outputs(json.loads(out.read_text()))
    return {
        "text": text,
        "start": _events(text, "START"),
        "close": _events(text, "CLOSE"),
    }


# ---------------------------------------------------------------------------
# Executions (one per module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cell(tmp_path_factory):
    """``@session`` defined in one cell and called in the next (nbconvert)."""
    project = _project(tmp_path_factory.mktemp("cell") / "proj")
    nb = _notebook(project / "notebooks" / "a.ipynb", OBSERVERS, MAIN_CELL, "main()")
    return {"project": project, "nb": nb, **_execute(nb, project)}


@pytest.fixture(scope="module")
def run_script(tmp_path_factory):
    """A notebook that ``%run``s a project @session script with an argument."""
    project = _project(tmp_path_factory.mktemp("run") / "proj")
    nb = _notebook(
        project / "notebooks" / "driver.ipynb",
        OBSERVERS,
        "%run ../scripts/compute.py --n 5",
    )
    return {"project": project, "nb": nb, **_execute(nb, project)}


@pytest.fixture(scope="module")
def twice(tmp_path_factory):
    """Two sessions in one kernel, then an ordinary cell."""
    project = _project(tmp_path_factory.mktemp("twice") / "proj")
    nb = _notebook(
        project / "notebooks" / "twice.ipynb",
        OBSERVERS,
        MAIN_CELL,
        "main()",
        "main()",
        "print('AFTER=ok')",
    )
    return {"project": project, "nb": nb, **_execute(nb, project)}


@pytest.fixture(scope="module")
def decoy(tmp_path_factory):
    """nbclient from a script whose command line names ANOTHER notebook."""
    project = _project(tmp_path_factory.mktemp("decoy") / "proj")
    nb = _notebook(project / "notebooks" / "a.ipynb", OBSERVERS, MAIN_CELL, "main()")
    other = _notebook(project / "notebooks" / "other.ipynb", "print('other')")
    return {"project": project, "nb": nb, **_drive(project, nb, str(other))}


@pytest.fixture(scope="module")
def explicit(tmp_path_factory):
    """nbclient from a script, with ``$SCITEX_NOTEBOOK_PATH`` set."""
    project = _project(tmp_path_factory.mktemp("explicit") / "proj")
    nb = _notebook(project / "notebooks" / "a.ipynb", OBSERVERS, MAIN_CELL, "main()")
    return {"project": project, "nb": nb, **_drive(project, nb, SCITEX_NOTEBOOK_PATH=str(nb))}  # fmt: skip


@pytest.fixture(scope="module")
def jupyter(tmp_path_factory):
    """nbclient from a script, with a Jupyter server's ``$JPY_SESSION_NAME``
    (relative to the server root, as older servers set it)."""
    project = _project(tmp_path_factory.mktemp("jupyter") / "proj")
    nb = _notebook(project / "notebooks" / "a.ipynb", OBSERVERS, MAIN_CELL, "main()")
    return {"project": project, "nb": nb,
            **_drive(project, nb, JPY_SESSION_NAME="notebooks/a.ipynb")}  # fmt: skip


@pytest.fixture
def inplace(tmp_path):
    """Code hash before / after executing IN PLACE / after a code edit."""
    project = _project(tmp_path / "proj")
    nb = _notebook(project / "notebooks" / "a.ipynb", OBSERVERS, MAIN_CELL, "main()")
    before = notebook_code_sha256(nb)
    proc = _nbconvert(nb, project, "--inplace")
    assert proc.returncode == 0, proc.stderr[-3000:]
    executed = json.loads(nb.read_text())
    after_run = notebook_code_sha256(nb)
    edited = nbformat.read(str(nb), as_version=4)
    edited.cells[2].source = "main(4)"
    nbformat.write(edited, str(nb))
    return {"before": before, "after_run": after_run, "executed": executed,
            "after_edit": notebook_code_sha256(nb)}  # fmt: skip


def _meta(run: dict) -> dict:
    (start,) = run["start"]
    return start["metadata"]


# ---------------------------------------------------------------------------
# (1) kernel argv
# ---------------------------------------------------------------------------


def test_cell_session_runs_without_error(cell):
    # Arrange
    text = cell["text"]
    # Act
    errors = [x for x in text.splitlines() if x.startswith("ERROR")]
    # Assert
    assert errors == []


def test_cell_session_applies_function_defaults_not_kernel_argv(cell):
    # Arrange
    meta = _meta(cell)
    # Act
    args = meta["args"]
    # Assert
    assert args == {"n": 3}


def test_run_script_parses_its_own_arguments(run_script):
    # Arrange
    meta = _meta(run_script)
    # Act
    args = meta["args"]
    # Assert
    assert args == {"n": 5}


# ---------------------------------------------------------------------------
# (2) filed under the notebook
# ---------------------------------------------------------------------------


def test_cell_session_script_is_the_notebook(cell):
    # Arrange
    (start,) = cell["start"]
    # Act
    script = start["script_path"]
    # Assert
    assert script == str(cell["nb"])


def test_cell_session_project_root_is_the_project(cell):
    # Arrange
    meta = _meta(cell)
    # Act
    root = (meta["project_root"], meta["project_root_source"])
    # Assert
    assert root == (str(cell["project"]), "marker")


def test_cell_session_outputs_go_beside_the_notebook(cell):
    # Arrange
    meta = _meta(cell)
    # Act
    sdir_out = meta["sdir_out"]
    # Assert
    assert sdir_out == str(cell["project"] / "notebooks" / "a_out")


def test_cell_session_run_dir_is_not_the_kernel_temp_dir(cell):
    # Arrange
    meta = _meta(cell)
    # Act
    sdir_run = meta["sdir_run"]
    # Assert
    assert sdir_run.startswith(str(cell["project"] / "notebooks" / "a_out" / "RUNNING"))


def test_run_script_project_root_is_the_project(run_script):
    # Arrange
    meta = _meta(run_script)
    # Act
    root = meta["project_root"]
    # Assert
    assert root == str(run_script["project"])


# ---------------------------------------------------------------------------
# (3) the notebook is recorded
# ---------------------------------------------------------------------------


def test_cell_entry_names_the_notebook_and_how_it_was_found(cell):
    # Arrange
    entry = _meta(cell)["entry"]
    # Act
    got = {k: entry[k] for k in ("kind", "invocation", "path", "path_source")}
    # Assert
    assert got == {"kind": "notebook", "invocation": "cell",
                   "path": str(cell["nb"]), "path_source": "parent-cmdline"}  # fmt: skip


def test_cell_entry_code_hash_is_the_code_cells_hash(cell):
    # Arrange
    entry = _meta(cell)["entry"]
    # Act
    code = (entry["code_hash_scheme"], entry["code_sha256"], entry["code_cells"])
    # Assert
    assert code == ("ipynb-code-v1", notebook_code_sha256(cell["nb"]), 3)


def test_cell_entry_records_executed_cells_in_order(cell):
    # Arrange
    entry = _meta(cell)["entry"]
    # Act
    order = ([c["cell"] for c in entry["executed_cells"]], entry["executed_in_order"])
    # Assert
    assert order == ([0, 1, 2], True)


def test_cell_metadata_names_the_notebook(cell):
    # Arrange
    meta = _meta(cell)
    # Act
    notebook = meta["notebook_path"]
    # Assert
    assert notebook == str(cell["nb"])


def test_cell_session_closes_successfully(cell):
    # Arrange
    closes = cell["close"]
    # Act
    statuses = [c["status"] for c in closes]
    # Assert
    assert statuses == ["success"]


def test_cell_session_is_the_scitex_io_provenance_scope(cell):
    # Arrange
    (close,) = cell["close"]
    # Act
    scope = close["metadata"]["io_provenance"]
    # Assert
    assert (scope["scope"] in _meta(cell)["sdir_run"], scope["total"]) == (True, 0)


def test_run_script_is_the_script(run_script):
    # Arrange
    (start,) = run_script["start"]
    # Act
    script = start["script_path"]
    # Assert
    assert script == str(run_script["project"] / "scripts" / "compute.py")


def test_run_script_records_its_driver_notebook(run_script):
    # Arrange
    meta = _meta(run_script)
    # Act
    got = (meta["entry"]["invocation"], meta["entry"]["path"], meta["notebook_path"])
    # Assert
    assert got == ("run", str(run_script["nb"]), str(run_script["nb"]))


# ---------------------------------------------------------------------------
# A kernel is long-lived: several sessions, then ordinary cells
# ---------------------------------------------------------------------------


def test_two_sessions_in_one_kernel_get_two_run_dirs(twice):
    # Arrange
    starts = twice["start"]
    # Act
    run_dirs = {s["metadata"]["sdir_run"] for s in starts}
    # Assert
    assert len(run_dirs) == 2


def test_two_sessions_in_one_kernel_both_succeed(twice):
    # Arrange
    closes = twice["close"]
    # Act
    statuses = [c["status"] for c in closes]
    # Assert
    assert statuses == ["success", "success"]


def test_cell_output_still_works_after_sessions_closed(twice):
    # Arrange
    text = twice["text"]
    # Act
    printed = "AFTER=ok" in text
    # Assert
    assert printed


def test_second_session_history_lists_every_executed_cell(twice):
    # Arrange
    entry = twice["start"][1]["metadata"]["entry"]
    # Act
    cells = [c["cell"] for c in entry["executed_cells"]]
    # Assert
    assert cells == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Resolution: never a wrong notebook, never the cell file
# ---------------------------------------------------------------------------


def test_a_launcher_naming_another_notebook_is_not_trusted(decoy):
    # Arrange
    entry = _meta(decoy)["entry"]
    # Act
    reason = entry["path_unresolved"]
    # Assert
    assert "none of them contains the cell being executed" in reason


def test_unresolved_notebook_session_has_no_script(decoy):
    # Arrange
    (start,) = decoy["start"]
    # Act
    script = start["script_path"]
    # Assert
    assert script is None


def test_unresolved_notebook_outputs_go_to_cwd_notebook_out(decoy):
    # Arrange
    meta = _meta(decoy)
    # Act
    sdir_out = meta["sdir_out"]
    # Assert
    assert sdir_out == str(decoy["project"] / "notebooks" / "notebook_out")


def test_explicit_notebook_path_env_is_used(explicit):
    # Arrange
    entry = _meta(explicit)["entry"]
    # Act
    got = (entry["path"], entry["path_source"])
    # Assert
    assert got == (str(explicit["nb"]), "env")


def test_jupyter_session_name_is_used(jupyter):
    # Arrange
    entry = _meta(jupyter)["entry"]
    # Act
    got = (entry["path"], entry["path_source"])
    # Assert
    assert got == (str(jupyter["nb"]), "jupyter")


# ---------------------------------------------------------------------------
# Code identity
# ---------------------------------------------------------------------------


def test_inplace_execution_wrote_outputs(inplace):
    # Arrange
    cells = inplace["executed"]["cells"]
    # Act
    has_outputs = bool(cells[2]["outputs"])
    # Assert
    assert has_outputs


def test_code_hash_is_unchanged_by_execution_outputs(inplace):
    # Arrange
    before = inplace["before"]
    # Act
    after = inplace["after_run"]
    # Assert
    assert after == before


def test_code_hash_changes_when_a_code_cell_changes(inplace):
    # Arrange
    before = inplace["before"]
    # Act
    after = inplace["after_edit"]
    # Assert
    assert after != before


# EOF
