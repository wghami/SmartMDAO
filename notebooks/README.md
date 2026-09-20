# Notebooks

Long-form documentation: **one concept per notebook, one notebook per concept.**

Every notebook here is committed **with its outputs**, so GitHub renders what each cell actually
printed — you can read the whole series without installing anything. The outputs are real: they come
from `run_notebooks.py` executing the files, not from anyone typing what they expected.

| # | Notebook | Concept |
|---|---|---|
| 1 | [Pipelines and steps](01-pipelines-and-steps.ipynb) | Functions become a graph, wired by name |
| 2 | [Solvers](02-solvers.ipynb) | `DAGSolver`, `HybridSolver`, `IterativeSolver` — and what each costs |
| 3 | [Feedback loops](03-feedback-loops.ipynb) | Cycles, and which variable needs a starting value |
| 4 | [Convergence](04-convergence.ipynb) | `ConvergenceReport`, `target_var`, oscillation, abandonment |
| 5 | [Non-numeric convergence](05-non-numeric-convergence.ipynb) | Converging on sets, decisions and dataclasses |
| 6 | [Type checking](06-type-checking.ipynb) | Catching wiring mistakes before anything runs |
| 7 | [Caching](07-caching.ipynb) | `@cached` and its four backends |
| 8 | [Optimization](08-optimization.ipynb) | Driving a pipeline with an optimizer |
| 9 | [Analysis](09-analysis.ipynb) | `analyze` / `validate` / `explain` — free, executes nothing |
| 10 | [Visualization](10-visualization.ipynb) | XDSM diagrams |
| 11 | [Discretisation](11-discretisation.ipynb) | Turning a number into a symbolic fact, with the threshold declared |
| 12 | [Rule-backed disciplines](12-rule-backed-disciplines.ipynb) | An ASP program as a discipline |
| 13 | [Execution and comparison](13-execution-and-comparison.ipynb) | The cost ladder, and proving a translation kept the answer |
| 14 | [Discovery and MCP](14-pipeline-discovery-and-mcp.ipynb) | Finding a pipeline in a file, and the connector |
| 15 | [Pitfalls](15-pitfalls.ipynb) | Mistakes that produce a wrong answer rather than an error |

## Reading order

1–5 are the core and are meant to be read in order. 6–14 are independent and can be read as needed.
**15 is the one to read twice** — everything in it runs, converges, and is wrong.

## Regenerating

```bash
uv run python run_notebooks.py          # execute all, rewrite in place
uv run python run_notebooks.py 09       # just the ones matching "09"
```

A cell that raises fails the run. `tests/test_notebooks.py` then guards the committed artifact:
every notebook was executed, no cell carries an error, every name exported from `smartmdao` appears
somewhere, and this index lists every file.

## How this relates to the other docs

| Where | What it is for |
|---|---|
| **`notebooks/`** | Learning a concept properly, with real output to look at |
| [`docs/cookbook.md`](../docs/cookbook.md) | Task-indexed snippets — the reference you reach for while writing code |
| [`scripts/`](../scripts) | Runnable demonstrations, each teaching one failure mode |
| [`docs/known-issues.md`](../docs/known-issues.md) | The sharp edges, with severity and fix direction |
