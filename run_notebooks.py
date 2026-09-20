"""
Execute every notebook in `notebooks/` in place, so the committed outputs are real.

The notebooks are the long-form documentation: one concept per file, rendered by
GitHub with their outputs visible, so a reader can see what each cell actually
printed without installing anything. That only works if the outputs came from
running the code rather than from someone typing what they expected - the same
reason every snippet in `docs/cookbook.md` is executed by the test suite.

    uv run python run_notebooks.py            # execute all, rewrite in place
    uv run python run_notebooks.py 09         # just the ones matching "09"

A cell that raises stops the notebook and fails the run, with the traceback.
`tests/test_notebooks.py` then guards the committed artifact: every code cell
carries output, no cell carries an error, and every name exported from
`smartmdao` appears somewhere.
"""
import pathlib
import sys
import time

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

NOTEBOOKS = pathlib.Path(__file__).parent / "notebooks"

# Generous, because one notebook runs an optimizer. Still bounded: a notebook
# that hangs should fail the build rather than the build waiting forever.
CELL_TIMEOUT_SECONDS = 300


def execute(path: pathlib.Path) -> tuple[bool, str]:
    notebook = nbformat.read(path, as_version=4)

    client = NotebookClient(
        notebook,
        timeout=CELL_TIMEOUT_SECONDS,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    )

    try:
        client.execute()
    except CellExecutionError as error:
        return False, str(error).strip().splitlines()[-1]
    finally:
        # Write back whatever was produced, including a partial run. A notebook
        # left half-executed is easier to debug than one silently reverted.
        nbformat.write(notebook, path)

    return True, ""


def main(argv: list[str]) -> int:
    pattern = argv[1] if len(argv) > 1 else ""
    paths = sorted(p for p in NOTEBOOKS.glob("*.ipynb") if pattern in p.name)

    if not paths:
        print(f"No notebooks matching {pattern!r} in {NOTEBOOKS}")
        return 1

    print(f"Executing {len(paths)} notebook(s)\n")
    failures = []

    for path in paths:
        started = time.perf_counter()
        ok, detail = execute(path)
        elapsed = time.perf_counter() - started

        if ok:
            print(f"  PASS  {path.name:44} {elapsed:6.1f}s")
        else:
            print(f"  FAIL  {path.name:44} {elapsed:6.1f}s  {detail}")
            failures.append(path.name)

    print()
    if failures:
        print(f"{len(failures)} notebook(s) failed: {', '.join(failures)}")
        return 1

    print(f"All {len(paths)} notebook(s) executed cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
