# Roadmap

Living document. Update the checkboxes as work lands; each phase states the condition under which
it is considered done.

**Current position:** Phases 0–3 complete and merged to `main`. **Phase 4 (ASP) is next**, with
three of its four design questions now settled in [003](design/003-determinism-and-the-engineer-in-the-loop.md).
**Baseline:** `v1.14.0` — 389 tests, 100% coverage, 27/27 scripts.

New here? Read [handoff.md](handoff.md) first — it states what "done" means in this repo.

---

## Phase 0 — Documentation foundation ✅

Capture the design thinking behind the MCP connector before writing any of it, and clear two
pieces of dead configuration.

- [x] `docs/` structure created
- [x] [architecture.md](architecture.md) — internals reference
- [x] [design/001-mcp-connector.md](design/001-mcp-connector.md) — verification-first design record
- [x] [design/002-agent-as-discipline.md](design/002-agent-as-discipline.md) — agent-in-the-loop record
- [x] [known-issues.md](known-issues.md) — sharp edges, with severity
- [x] README: documentation section + pointer to 002
- [x] `pyproject.toml`: remove inert `[tool.setuptools]` and shadowed `[tool.pytest.ini_options]`

**Exit criterion:** tests still 118 passing at 100% coverage, `uv build` produces a wheel with an
unchanged module list, and every relative link in `docs/` resolves. No source changes.

---

## Phase 1 — Prove agent-as-discipline ✅

Make good on the claim the README and `StandardConvergenceChecker`'s docstring already make. No
MCP dependency: a stubbed model call is enough to prove the convergence mechanics, and the real
call swaps in later.

- [x] `OscillationAwareConvergenceChecker` — detects cycles up to `max_period`, raising
      `OscillationDetectedError` with the cycle itself
- [x] Decided: a discipline must be **total**. "No feasible answer" is an explicit sentinel value;
      returning `None` freezes the previous value and reads as converged
- [x] `scripts/agent_as_discipline_demo.py` — four scenarios: converges feasible, converges
      infeasible, oscillates and is caught, plus the model on the linear part with an outer loop
- [x] 28 new tests, 100% coverage held
- [x] Findings recorded in [002](design/002-agent-as-discipline.md)
- [x] **Topologies compared** — where the model sits turns out to matter more than anything else
      in this phase

**Exit criterion met.** Case 1 converges in 2 sweeps, Case 2 in 3, Case 3 is caught at sweep 4 of
100. All three of 002's open questions answered from evidence.

**What it cost us:** four limitations surfaced that were not in the hypothesis — the
`ConvergenceChecker` protocol has no "give up" verdict, oscillation detection is incompatible with
`HybridSolver`, silent premature convergence from step misordering, and initial guesses being tied
to alphabetical step names. All four are in [known-issues.md](known-issues.md); two became Phase 2
requirements.

**What it changed:** the recommended topology. Putting the model *inside* the cycle — what this
phase built — is the exception, not the default. Putting it on the **linear part** with an outer
Python loop costs one model call per outer iteration instead of one per sweep, and avoids three of
the four limitations above outright. 002 was revised accordingly.

**Still unproven:** everything about *real* model behaviour. The stub converges because it was
written to. Deferred to Phase 3.

---

## Phase 2 — Analysis-only MCP server ✅

The verification loop from [001-mcp-connector.md](design/001-mcp-connector.md). Nothing executes
a discipline function.

- [x] `smartmdao/mcp/` package + `[mcp]` extra, lazily imported — verified that a base install
      with no SDK present still imports the handlers and fails helpfully only at `create_server()`
- [x] `analyze_pipeline` — execution order, SCCs, feedback variables, recommended solver, and
      which variables need an initial guess
- [x] `validate_pipeline` — seven finding types, worst first
- [ ] ~~orphaned outputs~~ — **planned, then dropped without saying so.** See the correction below
- [x] `render_pipeline_diagram` — Agg forced, `view=False`
- [x] `explain_pipeline` — prose description
- [x] Resources: architecture doc, known-issues doc, two worked examples
- [x] Prompts: `pipeline_from_prose`, `review_pipeline` — both routing through the verify loop
- [x] Response truncation (`MAX_ITEMS`)
- [x] 79 new tests, 100% coverage held
- [x] `smartmdao-mcp` console entry point
- [x] Runnable demos: [`scripts/pipeline_analysis_demo.py`](../scripts/pipeline_analysis_demo.py)
      and [`scripts/mcp_connector_demo.py`](../scripts/mcp_connector_demo.py)

**Exit criterion met.** Verified against Sellar: the tools identify the `y1↔y2` loop, recommend
`HybridSolver`, name `y2` as the variable needing a seed, and render a diagram — with no
discipline ever called.

**What changed from the plan:** the analysis is not MCP-specific, so it ships as a first-class
`smartmdao.analysis` module (`analyze` / `validate` / `explain`, exported from the package root)
with the MCP server as a thin adapter. The SCC decomposition was extracted out of `HybridSolver`
into `graph.build_execution_plan` so analysis and execution share one code path instead of
drifting.

**Correction (1.12.0).** The Phase 2 plan listed *orphaned outputs* as a `validate_pipeline`
check. It was never implemented, and the line was rewritten as "seven finding types" in the same
commit that marked the phase complete — so the record showed a finished phase and no trace of the
dropped item. `git log -S"orphaned outputs"` shows it entering in `ef53747` and leaving in
`b9cd492`.

It surfaced when a coding agent produced a wing model that validated clean, converged, and whose
answer ignored three of its five inputs because one discipline was wired to nothing. Addressed in
1.12.0 by `disconnected-graph` — a stricter and less noisy check than the original idea, since
every healthy pipeline has terminal outputs and flagging those would fire on almost everything.

This is exactly the failure [handoff.md](handoff.md) warns about, committed by its author. Ticking
a box is not the same as doing the work, and quietly rewriting the box is worse.

**Finding:** the analysis has to be **solver-aware**. `IterativeSolver` runs steps in registration
order and ignores the dependency graph entirely, so the variable needing a seed differs from what
`HybridSolver` would need. The first implementation reported HybridSolver's answer for every
pipeline and was wrong — caught by running it against the Phase 1 demo.

---

## Phase 2.5 — Make the connector reach real code ✅

Promoted ahead of execution after working through the concept of operations. The analysis tools
only saw a `Pipeline` assigned to a module-level variable — **7 of this repository's own 23
scripts.** Every failure was a pipeline built inside a factory function, which is normal Python.

- [x] `load_pipeline` calls factories: a module-level callable declaring `-> Pipeline`, or one the
      caller names explicitly
- [x] A factory needing arguments is reported **with the argument names**, not generically
- [x] Ambiguity lists every candidate, instances and factories alike
- [x] `LoadedPipeline.source` records whether a pipeline was *read* or *called*, and the MCP
      handlers return it — how a result was obtained is part of what the engineer needs to know
      ([003](design/003-determinism-and-the-engineer-in-the-loop.md))
- [x] [`scripts/pipeline_discovery_demo.py`](../scripts/pipeline_discovery_demo.py) — seven shapes
      and what the loader makes of each

**The safety rule, which matters more than the feature:** a function is only ever called when it
*declares* `-> Pipeline`, or when the caller names it. Nothing is called speculatively to see what
it returns — a module's `run_demo()` would execute the entire study, and *no discipline is ever
invoked* is the promise the whole analysis layer rests on. Calling a factory registers steps; it
does not evaluate them.

### Exit criterion, corrected

The original read: *"the number should be most of 23."* **That was not achievable, and writing it
without measuring was the mistake.** Measured after the change: **9 auto-discovered, 1 more by
name, 14 unreachable.**

The 14 build their pipelines inside `run_*_demo()` and never return them — there is nothing to call
and nothing to read. That is normal for a *script* and unusual for a *model*, so the number
understates the picture for real engineering code. It is still the honest number, and it is not 23.

The revised criterion — met — is that **every shape a model file plausibly uses is reachable**, and
every shape that is not says exactly why.

---

## Phase 3 — Execution as a cost ladder ✅

Reframed. The original plan was a flat `run_pipeline` / `optimize` / `sweep`, justified by
sandboxing. Two things changed that — see [001](design/001-mcp-connector.md):

**The safety argument was wrong for the local case.** The agent already has a shell. Refusing to
execute does not create a boundary; it pushes the run somewhere unsandboxed, untimed, and scraped
from stdout. Subprocess + timeout buys **reliability, structure and a kill switch** — not
protection. We should stop claiming otherwise.

**Engineers need to choose a rung and be told the cost.** An unbounded run is not a reasonable
thing to ask someone to consent to blindly.

- [x] Subprocess runner with a mandatory timeout and a JSON boundary
- [x] **Single sweep** (`rung="smoke"`, the default) — one call per discipline. Cheapest smoke
      test, and it measures the unit cost so every estimate above it falls out of it
- [x] **Budgeted run** — capped sweeps plus wall clock, with the estimate quoted first
- [x] Result summarisation — arrays become shape, min/max and a short preview
- [x] Literal input values recovered from the file, so `run_pipeline(path)` behaves the way
      running the script does
- [x] [`scripts/cost_ladder_demo.py`](../scripts/cost_ladder_demo.py)
- [x] **`compare_runs`** — same inputs, two pipelines, diff the state. The thing that makes a
      translation from hand-written code trustworthy
- [ ] `optimize` / `sweep` — deferred until someone asks for them

**Exit criterion met.** An engineer is told what a run will cost before it starts, a non-converging
pipeline is killed by timeout and reported as such, and a translation can be checked against its
original.

`compare_runs` earns its place on one demonstration: two translations of the same model, differing
only in which variable the solver watches, **both reporting `converged: True`** — and one answer
95% out. Nothing that reads structure can catch that, because both files are perfectly well-formed.
See [`scripts/translation_drift_demo.py`](../scripts/translation_drift_demo.py).

**The safety claim, restated once more because it keeps wanting to creep back.** The subprocess
buys a hard kill, typed results and crash isolation. It does **not** buy safety: the agent has a
shell and will run the file itself if refused. Verified in real use, where an agent asked to build
a wing model simply ran `python` when the connector could not.

**Constraint unchanged:** no remote transport without real sandboxing.

---

## Phase 4 — ASP-backed disciplines ⬜

Direction set by [003](design/003-determinism-and-the-engineer-in-the-loop.md). Not scheduled, and
deliberately not designed in detail yet.

A language model generates an ASP program (clingo) once, at authoring time, under engineer review.
That program — not the model — becomes the discipline: deterministic at run time, inspectable,
diffable, version-controlled. It targets the gap gradient-based MDO cannot reach (discrete
architectural choice) while satisfying the reproducibility and traceability this audience requires.

**Settled** in [003](design/003-determinism-and-the-engineer-in-the-loop.md): it ships as an
optional `[asp]` extra in this repository; the generated program lives in a **separate, reviewable
`.lp` file** (the program is the artifact an engineer reviews, so it has to be diffable); and the
symbolic/numeric discretisation is a **declared, validated object** rather than glue code, because
that is where the hypotheses hide.

**Still open:** a settled name for the concept, and how answer-set multiplicity is pinned in
practice.

**This supersedes MCP sampling.** The library never calls a model.

---

## Deferred — not scheduled

Recorded so they are not lost, with no commitment.

*(The dependency diet is done: `openturns` behind an extra in 1.7.0, `ipykernel` to `dev` in
1.8.0. `HybridSolver` forwarding `target_var` landed in 1.8.0. See
[known-issues.md](known-issues.md).)*
- **OpenMDAO / GEMSEO importer.** Translate competitor definitions into SmartMDAO. An adoption
  lever rather than a capability.
- ~~**MCP sampling integration.**~~ Dropped — see Phase 4. The library will not call a model.
- **Under-relaxation in the solvers.** Absent today, which is a translation hazard: a hand-written
  loop that relied on damping loses it silently. See [known-issues.md](known-issues.md).
- **Duplicate-output detection in core.** Currently silent; would be a behaviour change to raise.
- **Positional-argument support in `@cached`.**
