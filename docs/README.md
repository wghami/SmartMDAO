# SmartMDAO documentation

Design records and internals reference. User-facing documentation — installation, quick start,
examples — lives in the [top-level README](../README.md), the [notebooks](../notebooks) and the
runnable scripts in [`scripts/`](../scripts).

> **Contributing, or picking this up cold?** Start with **[handoff.md](handoff.md)**. It states
> what "done" means here — docs updated, 100% coverage, didactic material (a notebook for a
> concept, a script for a failure mode), and `run_all.py` green — plus the traps that have already
> cost time.
>
> Then read **[003](design/003-determinism-and-the-engineer-in-the-loop.md)**. It states *why*
> this project is built the way it is: determinism, traceability, and giving the engineer enough
> information to decide rather than deciding for them.

## Writing SmartMDAO code?

**[cookbook.md](cookbook.md)** is the task-indexed guide — solvers, feedback loops, caching,
optimization, and the mistakes that fail silently. Every snippet in it is executed by the test
suite, so it cannot quietly become wrong. Coding agents get the same content from the
`smartmdao_cookbook` MCP tool; agents working in-tree should read [AGENTS.md](../AGENTS.md).

## Want to try it rather than read about it?

**[testing.md](testing.md)** is a step-by-step walkthrough: set up, run the suite, watch the
analysis catch a feedback loop nobody declared, connect the server to a coding agent, and — just
as important — see what it deliberately cannot do. Each step says what to run, what you should
see, and what it proves. About 20 minutes.

## Want to see it work before installing anything?

**[`notebooks/`](../notebooks)** — sixteen of them, one concept each, committed **with their
outputs** so GitHub renders what every cell printed, diagrams included. CI re-executes them, so they
cannot drift from the library.

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
| [`translation_equivalence_demo.py`](../scripts/translation_equivalence_demo.py) | Proving a translation of real hand-written code still gives the same answer — and what "the same" means |
| [`translation_drift_demo.py`](../scripts/translation_drift_demo.py) | The failure mode in isolation: two translations that both report success and disagree by 95% |
| [`cost_ladder_demo.py`](../scripts/cost_ladder_demo.py) | Running a pipeline under a wall clock, and being quoted the cost before you spend it |
| [`pipeline_discovery_demo.py`](../scripts/pipeline_discovery_demo.py) | Which ways of defining a pipeline the MCP tools can reach, and which they refuse to guess at |
| [`non_numeric_convergence_demo.py`](../scripts/non_numeric_convergence_demo.py) | Convergence on frozensets and dataclasses, with no floats involved |
| [`discretisation_demo.py`](../scripts/discretisation_demo.py) | A threshold that moves 1% and changes the answer — and one inside a loop that gives it two |
| [`rule_backed_discipline_demo.py`](../scripts/rule_backed_discipline_demo.py) | A reviewed `.lp` file as a discipline: UNSAT as a proof, ambiguity refused, and where the decision belongs |

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
time. Superseding one means writing a new record, or appending findings under the original — not
editing the decision. Only the status line moves.

| # | Record | Status |
|---|---|---|
| 001 | [MCP connector](design/001-mcp-connector.md) — why the server verifies rather than authors | implemented (Phases 2–3) |
| 002 | [An agent as an MDA discipline](design/002-agent-as-discipline.md) — a model inside an MDA feedback loop | mechanics proven; production path superseded by 003 |
| 003 | [Determinism, traceability, and the engineer in the loop](design/003-determinism-and-the-engineer-in-the-loop.md) — the governing principle, and rule-backed disciplines | implemented (Phase 4) |
| 004 | [Rule-backed disciplines](design/004-rule-backed-disciplines.md) — naming, and pinning answer-set multiplicity | implemented (1.15.0–1.19.0) |
| 005 | [Steps that touch the world](design/005-side-effecting-steps.md) — declaring side effects, and refusing what would repeat | implemented (1.22.0–1.23.0) |
| 007 | [Running each pipeline with its own project's Python](design/007-project-interpreter.md) — interpreter discovery, one versioned worker, version floors | implemented (1.26.0) |
| 008 | [Units, checked for consistency and never converted](design/008-units.md) — a `Unit` marker, exact match with a pluggable checker, where mismatches are found | proposed (Phase 6.8) |

## Requests

[requests/](requests/) keeps briefs from people using SmartMDAO, as evidence for the plans they
produced. Decisions live in the roadmap and the design records, not there.

- [2026-09 — paper-repro](requests/2026-09-paper-repro.md) → roadmap Phase 6

## Conventions

- Links between docs are relative, so they resolve on GitHub and on disk.
- References into source use `file:line` form and should be checked when the code moves.
- Claims about behaviour are verified against the running code before being written down, not
  inferred from reading. Where something is a hypothesis, it says so.
