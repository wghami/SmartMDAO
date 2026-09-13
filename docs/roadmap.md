# Roadmap

Living document. Update the checkboxes as work lands; each phase states the condition under which
it is considered done.

**Current position:** Phases 0, 1 and 2 complete. Phase 3 not started.
**Baseline:** `v1.6.0` — 225 tests, 100% coverage.

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
- [x] `render_pipeline_diagram` — Agg forced, `view=False`
- [x] `explain_pipeline` — prose description
- [x] Resources: architecture doc, known-issues doc, two worked examples
- [x] Prompts: `pipeline_from_prose`, `review_pipeline` — both routing through the verify loop
- [x] Response truncation (`MAX_ITEMS`)
- [x] 79 new tests, 100% coverage held
- [x] `smartmdao-mcp` console entry point

**Exit criterion met.** Verified against Sellar: the tools identify the `y1↔y2` loop, recommend
`HybridSolver`, name `y2` as the variable needing a seed, and render a diagram — with no
discipline ever called.

**What changed from the plan:** the analysis is not MCP-specific, so it ships as a first-class
`smartmdao.analysis` module (`analyze` / `validate` / `explain`, exported from the package root)
with the MCP server as a thin adapter. The SCC decomposition was extracted out of `HybridSolver`
into `graph.build_execution_plan` so analysis and execution share one code path instead of
drifting.

**Finding:** the analysis has to be **solver-aware**. `IterativeSolver` runs steps in registration
order and ignores the dependency graph entirely, so the variable needing a seed differs from what
`HybridSolver` would need. The first implementation reported HybridSolver's answer for every
pipeline and was wrong — caught by running it against the Phase 1 demo.

---

## Phase 3 — Sandboxed execution ⬜

- [ ] Subprocess runner with a mandatory timeout and a JSON boundary
- [ ] `run_pipeline` — final state, `residual_history`, iteration count, convergence status
- [ ] `optimize` — normalised `OptimizationResult` plus evaluation count
- [ ] `sweep` — design-variable sweep table for what-if reasoning
- [ ] Execution model documented prominently at the install surface

**Exit criterion:** a non-converging pipeline is killed by timeout and reported as such rather than
hanging the server; no code path executes user disciplines in the server process.

**Constraint:** no remote transport without real sandboxing. A local stdio server executing user
code is defensible because the coding agent already runs code on that machine; the same server
behind HTTP is not.

---

## Deferred — not scheduled

Recorded so they are not lost, with no commitment.

- **Dependency diet.** Move `ipykernel` to dev, `openturns` behind an extra with a lazy import.
  Breaking change; needs its own version decision. See [known-issues.md](known-issues.md).
- **OpenMDAO / GEMSEO importer.** Translate competitor definitions into SmartMDAO. An adoption
  lever rather than a capability.
- **MCP sampling integration.** The Phase 1 stub replaced by a real call back into the client's
  model, making 002 live rather than demonstrative.
- **Duplicate-output detection in core.** Currently silent; would be a behaviour change to raise.
- **Positional-argument support in `@cached`.**
