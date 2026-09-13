# Roadmap

Living document. Update the checkboxes as work lands; each phase states the condition under which
it is considered done.

**Current position:** Phase 0 complete. Phase 1 not started.
**Baseline:** `v1.6.0` — 118 tests, 100% coverage.

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

## Phase 1 — Prove agent-as-discipline ⬜

Make good on the claim the README and `StandardConvergenceChecker`'s docstring already make. No
MCP dependency: a stubbed model call is enough to prove the convergence mechanics, and the real
call swaps in later.

- [ ] `OscillationAwareConvergenceChecker` — stateful `ConvergenceChecker` retaining a short value
      history, detecting 2-cycles and reporting them distinctly from "still moving"
- [ ] Decide and document how a discipline signals *no feasible answer* (returning `None` currently
      means "store nothing", which silently freezes the previous value — see
      [known-issues.md](known-issues.md))
- [ ] `scripts/agent_as_discipline_demo.py` — discrete architecture proposed by a stubbed
      `ask_model()`, sized by a numeric discipline, violations fed back, converging on a frozen
      dataclass
- [ ] Tests for the new checker, holding the 100% bar
- [ ] Record the outcome in 002 — including if it does *not* work as hoped

**Exit criterion:** the demo converges deterministically with the stub, oscillation is detected
rather than burning `max_iterations`, and 002's open questions are answered from evidence rather
than argument.

**Risk:** this is the unproven part of the whole plan. If structural equality turns out to be
unusable for realistic model output, the honest outcome is to say so in 002 and let Phase 2 stand
on its own — it does not depend on this.

---

## Phase 2 — Analysis-only MCP server ⬜

The verification loop from [001-mcp-connector.md](design/001-mcp-connector.md). Nothing executes
a discipline function.

- [ ] `smartmdao/mcp/` package + `[mcp]` extra in `pyproject.toml`, lazy-imported
- [ ] `analyze_pipeline` — execution order, SCCs, feedback variables, recommended solver and why
- [ ] `validate_pipeline` — type-edge mismatches, unsatisfiable inputs, duplicate output names,
      orphaned outputs
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
