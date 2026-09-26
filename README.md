# SmartMDAO

***Coupled pipelines of plain Python functions — converged, and checked before they run.***

[![CI](https://github.com/wghami/SmartMDAO/actions/workflows/ci.yml/badge.svg)](https://github.com/wghami/SmartMDAO/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/smartmdao.svg)](https://pypi.org/project/smartmdao/)
[![Python versions](https://img.shields.io/pypi/pyversions/smartmdao.svg)](https://pypi.org/project/smartmdao/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/wghami/SmartMDAO/blob/main/LICENSE)

Write each discipline as a function. SmartMDAO wires them by parameter name, finds the feedback
loops, converges them, and tells you what a run would do — and what is wrong with it — without
running anything. Built for multidisciplinary design analysis and optimization (MDAO), and for any
workflow whose steps feed each other.

<p align="center">
  <img src="https://raw.githubusercontent.com/wghami/SmartMDAO/main/assets/sellar_mdao.svg" alt="Sellar coupling workflow" width="560"/>
</p>

## In one example

**Without SmartMDAO**, you write the convergence loop and track the state yourself:

```python
y2 = 1.0  # initial guess
for _ in range(100):
    y1 = z1 ** 2 + z2 + x1 - 0.2 * y2
    y2_next = math.sqrt(abs(y1)) + z1 + z2
    if abs(y2_next - y2) < 1e-6:
        break
    y2 = y2_next
```

**With SmartMDAO**, you declare each discipline once; the `y1 ↔ y2` loop is found and converged:

```python
import math
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver())

@pipeline.step(outputs=["y1"])
def discipline_1(z1, z2, x1, y2): return z1 ** 2 + z2 + x1 - 0.2 * y2

@pipeline.step(outputs=["y2"])
def discipline_2(z1, z2, y1): return math.sqrt(abs(y1)) + z1 + z2

result = pipeline.run(z1=1.0, z2=1.0, x1=1.0, y2=1.0)
result["convergence_reports"][0].status      # 'converged'
```

And before running it, ask what it would do:

```python
from smartmdao import analyze, validate

analyze(pipeline, inputs=["z1", "z2", "x1"]).cycles[0].feedback_variables   # ('y1', 'y2')
validate(pipeline, inputs=["z1", "z2", "x1"])
# ERROR: 'discipline_1' consumes 'y2' before anything produces it ... Pass y2=... to run().
```

No discipline was called to find that out.

## What makes it different

| | |
|---|---|
| **Checked before it runs** | `analyze()` and `validate()` read signatures and the graph: order, loops, the variable that needs a starting value, every structural problem at once. Free, because nothing executes. |
| **Converges on more than numbers** | A coupling variable can be a set, a dataclass or a whole architecture; anything with `==` converges on structural equality. |
| **Disciplines can be rules** | A reviewed `.lp` file becomes a discipline. "No feasible design" becomes a proof, with the minimal set of conflicting requirements. |
| **Says what a run will cost** | One sweep proves the model runs *and* measures the unit cost, so you are quoted a number before a long run. |
| **Knows when a step touches the world** | A step that writes a file or calls an API is declared; repeating it inside a loop is refused before anything runs. |
| **Reports what happened** | Every loop returns a report — converged, exhausted, or abandoned — with the reason and the residual trace. |
| **No ceremony** | No component classes, no problem description files. A discipline is a function. |

The principle behind all of it: surface the consequences before you commit to them, and never let
a default quietly decide the answer
([design record 003](https://github.com/wghami/SmartMDAO/blob/main/docs/design/003-determinism-and-the-engineer-in-the-loop.md)).

## Install

```bash
pip install smartmdao              # or: uv add smartmdao
pip install "smartmdao[mcp]"       # the MCP server, for coding agents
pip install "smartmdao[asp]"       # rule-backed disciplines (clingo)
pip install "smartmdao[openturns]" # the OpenTURNS optimizer backend
```

Diagrams are drawn with matplotlib; no system packages are needed.

## For coding agents

`smartmdao-mcp` is an MCP server. It lets an agent check the pipelines it writes instead of guessing:
`analyze_pipeline`, `validate_pipeline`, `explain_pipeline` and `render_pipeline_diagram` read the
code without executing it. `run_pipeline` runs it in a child process under a wall clock, one sweep
by default. `compare_runs` proves a refactor did not change the answer. `smartmdao_cookbook` gives
the agent the real API before it writes a line. Setup:
[testing guide, step 6](https://github.com/wghami/SmartMDAO/blob/main/docs/testing.md).

## Learn more

- **[Notebooks](https://github.com/wghami/SmartMDAO/tree/main/notebooks)** — one concept each,
  committed with their outputs so GitHub shows what every cell printed. Start here.
- **[Cookbook](https://github.com/wghami/SmartMDAO/blob/main/docs/cookbook.md)** — task by task,
  every snippet executed by the test suite.
- **[Testing guide](https://github.com/wghami/SmartMDAO/blob/main/docs/testing.md)** — try
  everything yourself, step by step.
- **[Scripts](https://github.com/wghami/SmartMDAO/tree/main/scripts)** — runnable demos, including
  the [full Sellar optimization](https://github.com/wghami/SmartMDAO/blob/main/scripts/readme_quick_start.py).
- **[All documentation](https://github.com/wghami/SmartMDAO/tree/main/docs)** — architecture,
  roadmap, known issues and design records.

## Contributing

Contributions are welcome. Read
**[docs/handoff.md](https://github.com/wghami/SmartMDAO/blob/main/docs/handoff.md)** first: it
defines what "done" means here — docs updated, 100% coverage, something that teaches the change,
and every script green. MIT licensed; see
[LICENSE](https://github.com/wghami/SmartMDAO/blob/main/LICENSE).
