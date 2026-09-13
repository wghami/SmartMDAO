# Roadmap

Living document. Update the checkboxes as work lands; each phase states the condition under which
it is considered done.

**Current position:** Phases 0 and 1 complete. Phase 2 not started.
**Baseline:** `v1.6.0` — 143 tests, 100% coverage.

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
- [x] `scripts/agent_as_discipline_demo.py` — three scenarios: converges feasible, converges
      infeasible, oscillates and is caught
- [x] 25 new tests, 100% coverage held
- [x] Findings recorded in [002](design/002-agent-as-discipline.md)

**Exit criterion met.** Case 1 converges in 2 sweeps, Case 2 in 3, Case 3 is caught at sweep 4 of
100. All three of 002's open questions answered from evidence.

**What it cost us:** three limitations surfaced that were not in the hypothesis — the
`ConvergenceChecker` protocol has no "give up" verdict, oscillation detection is incompatible with
`HybridSolver`, and silent premature convergence from step misordering. All three are in
[known-issues.md](known-issues.md); the third became a Phase 2 requirement.

**Still unproven:** everything about *real* model behaviour. The stub converges because it was
written to. Deferred to Phase 3.

---

## Phase 2 — Analysis-only MCP server ⬜

The verification loop from [001-mcp-connector.md](design/001-mcp-connector.md). Nothing executes
a discipline function.

- [ ] `smartmdao/mcp/` package + `[mcp]` extra in `pyproject.toml`, lazy-imported
- [ ] `analyze_pipeline` — execution order, SCCs, feedback variables, recommended solver and why
- [ ] `validate_pipeline` — type-edge mismatches, unsatisfiable inputs, duplicate output names,
      orphaned outputs, and **step misordering that causes silent premature convergence**
      (promoted from a Phase 1 finding — see [known-issues.md](known-issues.md))
- [ ] `render_xdsm` — Agg backend forced, `view=False`, returns a path or encoded image
- [ ] `explain_pipeline` — prose description of an existing pipeline
- [ ] Resources: architecture doc, `scripts/` examples, capability schema
- [ ] Prompts: authoring template, prose→pipeline template
- [ ] Response truncation so large state never floods the client's context
- [ ] Tests against handler functions directly, protocol layer kept thin, 100% coverage

**Exit criterion:** a coding agent can draft a Sellar pipeline, be told by `analyze_pipeline` that
it needs `HybridSolver`, fix it, and get a diagram — without the server ever calling a discipline.

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
