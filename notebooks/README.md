# Notebooks

Long-form documentation: **one concept per notebook, one notebook per concept.**

Every notebook here is committed **with its outputs**, so GitHub renders what each cell actually
printed — you can read the whole series without installing anything. The outputs are real: they come
from `run_notebooks.py` executing the files, not from anyone typing what they expected.

| # | Notebook | Concept |
|---|---|---|
| 1 | [Pipelines and steps](01-pipelines-and-steps.ipynb) | Functions become a graph, wired by name |
| 2 | [Visualization](02-visualization.ipynb) | **Seeing** that graph — linear versus cyclic |
| 3 | [Solvers](03-solvers.ipynb) | `DAGSolver`, `HybridSolver`, `IterativeSolver` — and what each costs |
| 4 | [Feedback loops](04-feedback-loops.ipynb) | Cycles, and which variable needs a starting value |
| 5 | [Convergence](05-convergence.ipynb) | `ConvergenceReport`, `target_var`, oscillation, abandonment |
| 6 | [Non-numeric convergence](06-non-numeric-convergence.ipynb) | Converging on sets, decisions and dataclasses |
| 7 | [Type checking](07-type-checking.ipynb) | Catching wiring mistakes before anything runs |
| 8 | [Caching](08-caching.ipynb) | `@cached` and its four backends |
| 9 | [Optimization](09-optimization.ipynb) | Driving a pipeline with an optimizer |
| 10 | [Analysis](10-analysis.ipynb) | `analyze` / `validate` / `explain` — free, executes nothing |
| 11 | [Discretisation](11-discretisation.ipynb) | Turning a number into a symbolic fact, with the threshold declared |
| 12 | [Rule-backed disciplines](12-rule-backed-disciplines.ipynb) | An ASP program as a discipline — introduces ASP and clingo from scratch |
| 13 | [Execution and comparison](13-execution-and-comparison.ipynb) | The cost ladder, and proving a translation kept the answer |
| 14 | [Discovery and MCP](14-pipeline-discovery-and-mcp.ipynb) | Finding a pipeline in a file, and the connector |
| 15 | [Side effects](15-side-effects.ipynb) | Steps that write files, launch processes or call APIs — declared, refused, latched |
| 16 | [Pitfalls](16-pitfalls.ipynb) | Mistakes that produce a wrong answer rather than an error |
| 17 | [Units](17-units.ipynb) | Declaring units, and catching dB wired into linear — checked, never converted |

## Reading order

1–5 are the core and are meant to be read in order. **Visualization comes second on purpose**: from
there on, every notebook draws the pipeline it is describing, so you can see whether it is linear or
cyclic instead of reconstructing that from parameter names.

6–15 and 17 are independent and can be read as needed. **16 is the one to read twice** — everything in it
runs, converges, and is wrong.

## Regenerating

```bash
uv run python run_notebooks.py          # execute all, rewrite only what changed
uv run python run_notebooks.py 09       # just the ones matching "09"
uv run python run_notebooks.py --check  # CI: fail if a committed output is stale
```

A cell that raises fails the run. Each notebook is compared with its committed copy after masking
measurements — timings, log clocks, temp paths — and written back only when something else
changed, so the committed outputs always come from a real run without every re-run rewriting all
of them. `tests/test_notebooks.py` then guards the committed artifact:
every notebook was executed, no cell carries an error, every name exported from `smartmdao` appears
somewhere, and this index lists every file.

## How this relates to the other docs

| Where | What it is for |
|---|---|
| **`notebooks/`** | Learning a concept properly, with real output to look at |
| [`docs/cookbook.md`](../docs/cookbook.md) | Task-indexed snippets — the reference you reach for while writing code |
| [`scripts/`](../scripts) | Runnable demonstrations, each teaching one failure mode |
| [`docs/known-issues.md`](../docs/known-issues.md) | The sharp edges, with severity and fix direction |
