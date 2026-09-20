"""
The notebooks are documentation that GitHub renders **with its outputs**, so a
reader can see what each cell printed without installing anything. That is only
worth something if the outputs came from running the code.

These tests guard the committed artifact rather than re-running it:

  1. Every notebook was actually executed - no cell is missing its run.
  2. No cell carries an error output, so nothing shipped broken.
  3. Every name exported from `smartmdao` appears in at least one notebook.
  4. The index lists every notebook, and every notebook is indexed.

`run_notebooks.py` is what re-executes them, and CI runs it, so a notebook whose
code stops matching the library fails the build rather than sitting there
looking authoritative.
"""
import json
import pathlib
import re

import pytest

import smartmdao

REPO = pathlib.Path(__file__).resolve().parents[1]
NOTEBOOK_DIR = REPO / "notebooks"
INDEX = NOTEBOOK_DIR / "README.md"

NOTEBOOKS = sorted(NOTEBOOK_DIR.glob("*.ipynb"))


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def code_cells(notebook: dict):
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_there_are_notebooks():
    """Guards against the glob silently matching nothing."""
    assert len(NOTEBOOKS) >= 10


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_every_code_cell_was_executed(path):
    """
    An `execution_count` of None means the cell never ran, so whatever is
    written around it is a claim nobody checked.
    """
    unexecuted = [
        index
        for index, cell in enumerate(code_cells(load(path)))
        if cell.get("execution_count") is None
    ]
    assert not unexecuted, (
        f"{path.name} has unexecuted code cell(s) at {unexecuted}. "
        f"Run: uv run python run_notebooks.py {path.stem[:2]}"
    )


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_no_cell_errored(path):
    """A traceback in the committed output is a broken page shipped as docs."""
    failures = []
    for index, cell in enumerate(code_cells(load(path))):
        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                failures.append(f"cell {index}: {output.get('ename')}")

    assert not failures, f"{path.name} contains error output: {failures}"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_every_notebook_produced_some_output(path):
    """
    A notebook where nothing printed renders as a wall of code on GitHub, which
    defeats the reason for committing outputs at all.
    """
    produced = sum(len(cell.get("outputs", [])) for cell in code_cells(load(path)))
    assert produced > 0, f"{path.name} produced no output at all"


def test_every_public_name_appears_somewhere():
    """
    The same guarantee `tests/test_cookbook.py` makes for the cookbook. A new
    export with no worked example is a gap a reader falls into.
    """
    text = "\n".join(path.read_text() for path in NOTEBOOKS)
    uncovered = sorted(name for name in smartmdao.__all__ if name not in text)

    assert not uncovered, (
        "exported from smartmdao but never mentioned in any notebook: "
        f"{uncovered}"
    )


def test_the_index_lists_every_notebook():
    index_text = INDEX.read_text()
    linked = set(re.findall(r"\(([^)]+\.ipynb)\)", index_text))
    present = {path.name for path in NOTEBOOKS}

    assert linked == present, (
        f"only in the index: {sorted(linked - present)}; "
        f"only on disk: {sorted(present - linked)}"
    )


def test_every_notebook_starts_with_a_title():
    """Each file opens with a markdown heading naming its concept."""
    for path in NOTEBOOKS:
        first = load(path)["cells"][0]
        assert first["cell_type"] == "markdown", f"{path.name} opens with code"
        assert "".join(first["source"]).lstrip().startswith("# "), (
            f"{path.name} does not open with a title"
        )
