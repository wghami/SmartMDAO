# SmartMDAO documentation

Design records and internals reference. User-facing documentation — installation, quick start,
examples — lives in the [top-level README](../README.md) and the runnable scripts in
[`scripts/`](../scripts).

> **Contributing, or picking this up cold?** Start with **[handoff.md](handoff.md)**. It states
> what "done" means here — docs updated, 100% coverage, a didactic script, and `run_all.py`
> green — plus the traps that have already cost time.
>
> Then read **[003](design/003-determinism-and-the-engineer-in-the-loop.md)**. It states *why*
> this project is built the way it is: determinism, traceability, and giving the engineer enough
> information to decide rather than deciding for them.

## Start here if you prefer running things

Each design record has a runnable counterpart in [`scripts/`](../scripts). They print what they
do and why, and `python run_all.py` executes every one of them.

| Script | Shows |
|---|---|
| [`pipeline_analysis_demo.py`](../scripts/pipeline_analysis_demo.py) | `analyze` / `validate` / `explain` — including why the same steps need different initial guesses under different solvers |
| [`mcp_connector_demo.py`](../scripts/mcp_connector_demo.py) | The MCP connector end to end, without an MCP client — plus the tools, resources and prompts a client would see |
| [`agent_as_discipline_demo.py`](../scripts/agent_as_discipline_demo.py) | A model discipline inside an MDA loop, and the cheaper topology beside it |
| [`hybrid_target_var_demo.py`](../scripts/hybrid_target_var_demo.py) | Narrowing convergence to one variable — and why a target on the wrong block would silently fake convergence |
| [`convergence_report_demo.py`](../scripts/convergence_report_demo.py) | Telling converged from exhausted from abandoned, and writing a checker that can give up |
| [`pipeline_discovery_demo.py`](../scripts/pipeline_discovery_demo.py) | Which ways of defining a pipeline the MCP tools can reach, and which they refuse to guess at |
| [`non_numeric_convergence_demo.py`](../scripts/non_numeric_convergence_demo.py) | Convergence on frozensets and dataclasses, with no floats involved |

## Reading order

**To understand how the library works:**
[architecture.md](architecture.md) — the execution path from `Pipeline.run()` down through
validation, the graph layer, the solvers, and `StepExecutor`. Written to be read top to bottom.

**To understand where it is going:**
[roadmap.md](roadmap.md) — phased plan with exit criteria. The living document; checkboxes move as
work lands.

**Before filing a bug:**
[known-issues.md](known-issues.md) — sharp edges that are already known, each with a severity and
a fix direction. Several are deliberate.

## Design records

Numbered, dated, and immutable in intent: a record says why a decision was made at a point in
time. Superseding one means writing a new record, not editing the old.

| # | Record | Status |
|---|---|---|
| 001 | [MCP connector](design/001-mcp-connector.md) — why the server verifies rather than authors | accepted, not implemented |
| 002 | [An agent as an MDA discipline](design/002-agent-as-discipline.md) — a model inside an MDA feedback loop | mechanics proven; production path superseded by 003 |
| 003 | [Determinism, traceability, and the engineer in the loop](design/003-determinism-and-the-engineer-in-the-loop.md) — the governing principle, and ASP-backed disciplines | accepted as direction |

## Conventions

- Links between docs are relative, so they resolve on GitHub and on disk.
- References into source use `file:line` form and should be checked when the code moves.
- Claims about behaviour are verified against the running code before being written down, not
  inferred from reading. Where something is a hypothesis, it says so.
