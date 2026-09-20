# Roadmap

Living document. Update the checkboxes as work lands; each phase states the condition under which
it is considered done.

**Current position:** Phases 0–4 complete. **Phase 5 (steps that touch the world) is next**, with
its design questions settled up front in [005](design/005-side-effecting-steps.md) and nothing built
yet.
**Baseline:** `v1.22.0` — 579 tests, 100% coverage, 29/29 scripts, 15 notebooks.

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

## Phase 4 — Rule-backed disciplines ⬜

Direction set by [003](design/003-determinism-and-the-engineer-in-the-loop.md); the last two design
questions settled in [004](design/004-rule-backed-disciplines.md).

A language model generates an ASP program (clingo) once, at authoring time, under engineer review.
That program — not the model — becomes the discipline: deterministic at run time, inspectable,
diffable, version-controlled. It targets the gap gradient-based MDO cannot reach (discrete
architectural choice) while satisfying the reproducibility and traceability this audience requires.

**Settled** in [003](design/003-determinism-and-the-engineer-in-the-loop.md): it ships as an
optional `[asp]` extra in this repository; the generated program lives in a **separate, reviewable
`.lp` file** (the program is the artifact an engineer reviews, so it has to be diffable); and the
symbolic/numeric discretisation is a **declared, validated object** rather than glue code, because
that is where the hypotheses hide.

**Settled** in [004](design/004-rule-backed-disciplines.md): the concept is a **rule-backed
discipline** (`RuleDiscipline`), named after what the engineer reviews rather than the engine; and
multiplicity is pinned in two halves — a static `unpinned-program` finding from `validate()`, which
**never grounds**, plus an `ambiguous-optimum` finding from a new budgeted rung on the Phase 3 cost
ladder, which does.

**This supersedes MCP sampling.** The library never calls a model.

### Phasing

Each sub-phase is its own branch and meets all four clauses of
[handoff.md](handoff.md)'s Definition of Done on its own.

- [x] **4.0 — Settle the decisions.** Docs only, no source changes: [004](design/004-rule-backed-disciplines.md),
      this section, and handoff's stale `Next`. Mirrors Phase 0, which is the pattern that worked.
- [x] **4.1 — The discretisation layer.** `mass_kg = 880 → mass_band = "heavy"` as a declared,
      inspectable object that `validate()` reports on. Shipped in **1.15.0**, deliberately before any
      clingo code: it is the sharpest risk in 003, it needs no extra installed to test, and building
      it second would mean retrofitting the bridge around whatever the first `.lp` happened to need.

      **What changed from the plan.** The design assumed discretisation-specific checks. Modelling a
      band as an ordinary `Step` instead — a band *is* a function from one variable to another —
      meant `missing-input` and `duplicate-output` already covered two of the four planned findings,
      and the solver ordered the band with no special case. Two checks shipped instead of four, and
      `effective_steps` is shared by `Pipeline.run` and `analysis` so the two cannot drift.

      **What it cost us.** Two sharp edges found by running it, both now in
      [known-issues.md](known-issues.md): a threshold inside a feedback loop gives the loop **more
      than one fixed point**, each converged and self-consistent, selected by the initial guess
      alone; and the synthetic step's *name* participates in the alphabetical seeding rule, so
      declaring a band changes which variable needs a seed. The first is the more serious — it is
      003's answer-set multiplicity arriving on the numeric side, before any rules engine exists.
- [x] **4.2 — The `[asp]` extra and `RuleDiscipline`.** Shipped in **1.16.0**: `.lp` loading,
      ground/solve, atoms → pipeline outputs, and **UNSAT as the honest `INFEASIBLE`** — which
      retires Phase 1's invented sentinel without breaking the rule that a discipline must be total.

      **The coupling question was answered first, as planned.** A `frozenset` of atoms converges
      under the existing non-numeric checker with **no change to `solvers.py`** — verified by spike
      before any of this was designed, which is what kept the phase the size the roadmap claimed.

      **Registered as an ordinary `Step`, like a band.** `Pipeline.add` accepts anything exposing
      `as_step()`, so the planner, the solver and every check in `analysis` handle a logic program
      without knowing it is one. No analysis code was added for it at all.

      **Ambiguity raises rather than resolves.** A program with two proven-optimal answer sets
      raises `AmbiguousProgramError` listing both. 4.3 turns this into a reported finding on the
      budgeted rung; refusing is the honest interim, since silently taking the first is what 003
      forbids.

      **Floats are refused as facts**, pointing at `Bands`. ASP has no floating point, so converting
      one inside the library would be precisely the undeclared threshold 4.1 exists to prevent.
- [x] **4.3 — Findings and the budget.** Landed across 1.17.0, 1.18.0 and 1.19.0.

      **Landed (1.17.0) — where the decision sits.** `rules-in-cycle` and
      `discretisation-in-cycle`, both decided statically from the SCC decomposition with nothing
      executed, plus the `facts` naming trap pinned by test. Prompted by the question of whether
      4.2's result licensed putting a decision inside a loop: it does not, and
      [002](design/002-agent-as-discipline.md) now carries the re-examination. **What it cost us:**
      `discretisation-unused`, shipped as a warning in 1.15.0, turned out to fire on the
      *recommended* topology — in B+D the caller reads the band from the result, so no step consumes
      it. Downgraded to info, because a warning that fires on the pattern the project recommends
      teaches people to ignore the finding list.

      **Landed (1.18.0) — the budget and `unpinned-program`.** `budget_seconds` bounds solving,
      answers are memoised on their facts, and `RuleDiscipline.cost` reports calls, cache hits,
      grounds and seconds so an engineer can be quoted a number. `validate()` reports
      `unpinned-program` for a program that generates candidates and either states no optimisation
      or carries only constant weights.

      **The honest boundary, measured rather than assumed.** A solve handle cancels promptly;
      `Control.interrupt()` during `ground()` is **ignored** and grounding runs to completion. So
      the budget bounds *search*, not grounding — which is the worst-case-exponential half. Calling
      it a budget without saying which half it covers would repeat the Phase 3 mistake of letting a
      reliability mechanism be described as a safety one. Recorded in
      [known-issues.md](known-issues.md) as still open.

      **What it cost us:** the UNSAT path returned before the memo store, so an infeasible fact set
      re-grounded on *every* call — the worst case, since that is the one a loop revisits. And the
      cache key was built from raw values before they were validated, so an unrepresentable fact
      raised `TypeError` on an unhashable key instead of the message explaining that facts must be
      symbols. Both found by tests, neither by reading the code.

      **Landed (1.19.0) — why there is no answer.** `explain_infeasible` returns a `Conflict`: the
      minimal set of facts that cannot hold together, or a statement that the rules contradict
      themselves regardless of input. On demand rather than automatic, since it costs one solve per
      fact.

      **The mechanism 004 warned about was dropped, and the warning understated it.**
      `SolveHandle.core()` returns solver literals *and is not minimal* — measured on a three-fact
      conflict it named all three, including one appearing in no rule. Dropping one fact at a time
      is minimal by construction and depends on nothing internal to clingo. An explanation that
      implicates an innocent constraint is worse than none, because it gets acted on.

      Also fixed here: clingo's own diagnostics went to stderr, and "atom does not occur in any rule
      head" fires for **every injected fact** — a correct program looked alarming, once per sweep.
      Routed to logging.

- [x] **4.4 — The didactic script. Discharged, not written.** Its stated exit criteria — *a
      program with two tied optimal answer sets caught as a finding, and an UNSAT with its core* —
      are sections 3 and 8 of [`rule_backed_discipline_demo.py`](../scripts/rule_backed_discipline_demo.py),
      which grew to eight sections across 4.2 and 4.3. A second script repeating them would be
      box-ticking, which is the failure this roadmap already records against its own author in
      Phase 2. **Recorded as a decision rather than absorbed silently.**

      What 4.4 was really reaching for — thorough, readable coverage of every concept — is better
      served by the notebooks below.

- [x] **4.5 — Notebooks, one concept per file.** Fifteen notebooks in [`notebooks/`](../notebooks),
      committed **with their outputs** so GitHub renders what each cell actually printed. Executed
      by `run_notebooks.py`, which CI runs, so a notebook that drifts from the library fails the
      build rather than sitting there looking authoritative.

      Guarded by `tests/test_notebooks.py`: every cell executed, no error output, every name in
      `smartmdao.__all__` mentioned somewhere, and the index matches what is on disk — the same
      guarantee `test_cookbook.py` makes for the cookbook.

      **What it cost us.** Writing them found two things nothing else had: `orientation` and
      `graph_type` on `visualize()` were **inert** (byte-identical output) — **removed in 1.21.0**
      rather than documented, since a `Literal` in a signature is a strong hint that choosing between
      the options does something, and a parameter that lies is worse than one that is absent; and the
      optimization API was written from recollection first and was wrong in every particular, which
      is exactly what the cookbook exists to prevent and what executing the notebooks caught.

      **Revised in 1.21.0.** Visualization moved from tenth to **second**, and the notebooks after it
      draw the pipeline they are describing — linear versus cyclic is the distinction the rest of the
      series turns on, and a reader should not have to reconstruct it from parameter names. Notebook
      12 now introduces ASP and clingo from scratch instead of assuming them. `visualize()` also
      draws declared bands and rule-backed disciplines now, because it renders `effective_steps`
      rather than only the registered ones.

**Exit criterion:** an engineer can point at a `.lp` file and a declared discretisation, re-run the
study and get the same answer, and be told — before committing to a run — what grounding will cost.
A program that admits more than one optimal model says so rather than picking one.

---

## Phase 5 — Steps that touch the world ⬜

Direction set by [005](design/005-side-effecting-steps.md), which settles the three design questions
before any code is written — the shape Phase 4 used, and the reason it stayed the size it claimed.

A step inside a cyclic block runs **once per sweep**. For a numeric discipline that is the point;
for one that writes a file, launches a subprocess or posts to an API it is something else. Nothing
currently treats re-running a step as *unsafe* rather than merely wasteful, and that gap is what
separates an MDAO engine from a workflow engine.

**Settled in [005](design/005-side-effecting-steps.md):** side effects are **declared per step**
(`@pipeline.step(outputs=[...], effects="once")`), so analysis sees them without executing anything;
`validate()` reports `side-effect-in-cycle` statically **and the solver refuses at run time** unless
the step said which behaviour it wants; and `"once"` means once per `run()`.

**Refusing breaks the project's own pattern on purpose.** Every other finding describes something
recoverable. You cannot un-send an email, so a warning printed alongside thirty created tickets is a
post-mortem rather than information. Nothing stops `effects="every-sweep"` — the engineer keeps the
choice and is simply required to make it explicitly.

- [x] **5.1 — Declaration and the static finding.** Shipped in **1.22.0**. `effects` on `Step`
      (`None` / `True` / `"once"` / `"every-sweep"`), plumbed through `Pipeline.add` and
      `@pipeline.step`; `side-effect-in-cycle` in `validate()`; and `explain()` now states what a
      pipeline touches outside itself, which settles one of 005's open questions — a reviewer wants
      it and it costs nothing to say.

      **Solver-aware, as designed.** `IterativeSolver` sweeps every registered step, so an *acyclic*
      pipeline still repeats and is still reported. Asking the graph alone would have said nothing.

      **A typo raises rather than reports**, which is the second deliberate departure from the
      project's warn-don't-decide pattern: `effects="sometimes"` would silently declare a
      destructive step pure, and there is no defensible reading of it to hand back.

      **`"once"` is still reported.** The latch is 5.2, so today `"once"` is a statement of intent
      that nothing enforces — and the finding says exactly that rather than implying protection.
      Letting it go quiet here would be the same drift Phase 3 records about the subprocess.
- [ ] **5.2 — Runtime refusal and the `"once"` latch.**
- [ ] **5.3 — A notebook, and the tool descriptions.** `run_pipeline` executes side effects for
      real, and `compare_runs` executes them **twice** — which "compare two translations" does not
      sound like.

**Left open deliberately:** the optimizer case. `optimize()` calls `run()` hundreds of times, and
`"once"` is scoped per run, so a declared step still fires once per *evaluation*. Sharper than the
loop case and not designed in passing — see 005, risk 1.

---

## Deferred — not scheduled

Recorded so they are not lost, with no commitment.

*(The dependency diet is done: `openturns` behind an extra in 1.7.0, `ipykernel` to `dev` in
1.8.0. `HybridSolver` forwarding `target_var` landed in 1.8.0. See
[known-issues.md](known-issues.md).)*
- ~~**OpenMDAO / GEMSEO importer.**~~ **Dropped (2026-09-20).** The idea was an adoption lever:
  translate a competitor's problem definition into SmartMDAO. It is dropped because it pulls the
  project toward *being* those frameworks. What SmartMDAO does that they do not — converge on a
  decision rather than a number, report what a solve would do without running it, make a threshold
  reviewable — has no counterpart to import from, so an importer would faithfully carry across only
  the part that is not the point. Recorded rather than deleted: the reasoning is the useful part.
- ~~**MCP sampling integration.**~~ Dropped — see Phase 4. The library will not call a model.
- **Under-relaxation in the solvers.** Absent today, which is a translation hazard: a hand-written
  loop that relied on damping loses it silently. See [known-issues.md](known-issues.md).
- **Duplicate-output detection in core.** Currently silent; would be a behaviour change to raise.
- **Positional-argument support in `@cached`.**
