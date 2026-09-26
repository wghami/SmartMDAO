"""
Execute every notebook in `notebooks/` in place, so the committed outputs are real.

The notebooks are the long-form documentation: one concept per file, rendered by
GitHub with their outputs visible, so a reader can see what each cell actually
printed without installing anything. That only works if the outputs came from
running the code rather than from someone typing what they expected - the same
reason every snippet in `docs/cookbook.md` is executed by the test suite.

    uv run python run_notebooks.py            # execute all, rewrite what changed
    uv run python run_notebooks.py 09         # just the ones matching "09"
    uv run python run_notebooks.py --check    # CI: fail if a committed output is stale

A cell that raises stops the notebook and fails the run, with the traceback.
`tests/test_notebooks.py` then guards the committed artifact: every code cell
carries output, no cell carries an error, and every name exported from
`smartmdao` appears somewhere.

REPRODUCIBLE, NOT FROZEN. Some outputs are measurements - how long a sweep
took, the clock in a log line, a temporary directory's name - and differ on
every run by nature. Rewriting all sixteen files for those alone buried every
real change in noise, so a notebook is compared with its committed copy after
`fingerprint()` masks exactly those values, and is written back only when
something else changed. The committed outputs therefore always come from a
real run; they are just not replaced by an equivalent one. `--check` turns the
same comparison into a failure, so an output that no longer matches its code
is caught in CI instead of shipping.
"""
import json
import pathlib
import re
import sys
import time

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

NOTEBOOKS = pathlib.Path(__file__).parent / "notebooks"

# Generous, because one notebook runs an optimizer. Still bounded: a notebook
# that hangs should fail the build rather than the build waiting forever.
CELL_TIMEOUT_SECONDS = 300


#: What differs between two honest runs, and nothing else. Each is replaced by
#: a placeholder before comparing; adding a pattern here is a claim that the
#: value is a measurement, so keep it narrow.
VOLATILE = [
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "<clock>"),                    # log timestamps
    (re.compile(r"/tmp/(?:ipykernel_\d+/\d+\.py|tmp[\w-]+)"), "<tmp>"),   # temp dirs, cell files
    (re.compile(r"\d+(?:\.\d+)?(?:e-?\d+)?\s?(?:s|ms)\b"), "<duration>"),    # "0.0293s", "12 ms"
    (re.compile(r"('(?:\w+_)?seconds'|\w+_seconds)(['\"]?:\s*)[\d.e-]+"), r"\1\2<duration>"),
    (re.compile(r"\b0x[0-9a-f]{6,}\b"), "<address>"),
    # Where the checkout lives: /home/<you>/... here, /home/runner/work/... in CI.
    (re.compile(r"(?:/[^\s\"'/]+)+?/(?=(?:smartmdao|notebooks|scripts|\.venv)/)"), "<checkout>/"),
]


def _mask(text: str) -> str:
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


def _cell_prints(notebook, images: bool = True):
    """One comparable entry per cell. See `fingerprint`."""
    cells = []
    for cell in notebook.cells:
        outputs = []
        for output in cell.get("outputs", []):
            if output.get("output_type") == "stream":
                text = "".join(output.get("text", ""))
                if outputs and outputs[-1][0] == ("stream", output.get("name")):
                    outputs[-1] = (outputs[-1][0], outputs[-1][1] + text)
                else:
                    outputs.append((("stream", output.get("name")), text))
            else:
                data = {
                    key: (value if images or not key.startswith("image/") else "<image>")
                    for key, value in output.get("data", {}).items()
                }
                outputs.append(((output.get("output_type"), None),
                                json.dumps(data, sort_keys=True) + "".join(output.get("traceback", []))))
        cells.append(json.dumps(
            {"source": cell.source, "outputs": [(kind, _mask(text)) for kind, text in outputs]},
            sort_keys=True,
        ))
    return cells


def fingerprint(notebook, images: bool = True) -> str:
    """
    The notebook's sources and outputs, with measurements masked.

    Consecutive stream outputs are joined first: when stdout and stderr
    interleave, the kernel splits the same text into a different number of
    chunks from run to run.

    `images`: on one machine, matplotlib's Agg output is identical run to run,
    so a changed picture is a real change and is compared byte for byte. Across
    machines it is not - CI's fonts rasterise differently, which failed the two
    notebooks with diagrams on the first CI run while all fourteen others
    matched. So `--check` compares that an image is there, and every word of
    text exactly, but not the pixels.
    """
    return json.dumps(_cell_prints(notebook, images))


def first_difference(new, old, images: bool = True) -> str:
    """Where two runs part company, for a failure message that explains itself."""
    ours, theirs = _cell_prints(new, images), _cell_prints(old, images)
    for index, (a, b) in enumerate(zip(ours, theirs)):
        if a != b:
            return f"cell {index} ({new.cells[index].source.splitlines()[0][:50]!r}...)"
    return f"cell count {len(ours)} vs {len(theirs)}"


def execute(path: pathlib.Path, check: bool = False) -> tuple[bool, str]:
    """
    Runs one notebook. Returns (ok, detail); detail says what happened to the
    file - "unchanged", "updated", or, under `check`, "stale".
    """
    committed = nbformat.read(path, as_version=4)
    notebook = nbformat.read(path, as_version=4)

    client = NotebookClient(
        notebook,
        timeout=CELL_TIMEOUT_SECONDS,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
        # Per-cell start and end times are metadata, not output, and differed
        # on every run of every cell.
        record_timing=False,
    )

    try:
        client.execute()
    except CellExecutionError as error:
        if not check:
            # Write back whatever was produced, including a partial run. A
            # notebook left half-executed is easier to debug than one reverted.
            nbformat.write(notebook, path)
        return False, str(error).strip().splitlines()[-1]

    images = not check
    if fingerprint(notebook, images) == fingerprint(committed, images):
        return True, "unchanged"
    if check:
        where = first_difference(notebook, committed, images)
        return False, f"stale at {where}: committed outputs differ from what the code prints now"
    nbformat.write(notebook, path)
    return True, "updated"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    patterns = [arg for arg in argv[1:] if arg != "--check"]
    pattern = patterns[0] if patterns else ""
    paths = sorted(p for p in NOTEBOOKS.glob("*.ipynb") if pattern in p.name)

    if not paths:
        print(f"No notebooks matching {pattern!r} in {NOTEBOOKS}")
        return 1

    print(f"Executing {len(paths)} notebook(s)\n")
    failures = []

    for path in paths:
        started = time.perf_counter()
        ok, detail = execute(path, check=check)
        elapsed = time.perf_counter() - started

        if ok:
            print(f"  PASS  {path.name:44} {elapsed:6.1f}s  {detail}")
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
