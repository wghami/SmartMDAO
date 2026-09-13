# Handoff

For whoever picks this up next — a contributor, a maintainer returning after a break, or a coding
agent. Read this before starting work.

**State as of v1.8.0:** `main` is clean. 239 tests, 100% coverage, 22/22 scripts passing.
Roadmap Phases 0–2 are merged; Phase 3 has not started and is **blocked on a decision** (below).

---

## Definition of Done

**A change is not done when the code works. It is done when all four of these hold.**

This is the contract. A branch that satisfies three of them is unfinished, not nearly finished.

### 1. `docs/` is updated

Every change that alters behaviour, adds capability, or teaches us something updates the docs in
the same commit:

- **[roadmap.md](roadmap.md)** — tick the boxes, move "current position", record what the phase
  cost and what it changed.
- **[known-issues.md](known-issues.md)** — new sharp edges get an entry with a severity and a fix
  direction. Existing entries get downgraded or closed when the work resolves them.
- **[architecture.md](architecture.md)** — when internals move.
- **[design/](design/)** — a new numbered record for a decision with alternatives worth
  remembering. Records are dated and append-only in intent: supersede by writing a new one, and by
  adding findings under the original, not by quietly rewriting history.

**Record what did not work, too.** The most valuable paragraphs in `docs/` are the ones admitting
a hypothesis was wrong ([002](design/002-agent-as-discipline.md) revised its own recommendation
after Phase 1 evidence). A doc that only records successes is marketing.

### 2. 100% test coverage

Not "high". **100%**, on every file under `smartmdao/`. This has held since before this work
started and it is load-bearing:

- It has already caught **dead code** — a guard in the initial-guess check that could never fire.
- It forces you to either test a branch or admit it should not exist.

```bash
uv run pytest          # pytest.ini already adds --cov with term-missing
```

If a line is genuinely unreachable, `# pragma: no cover` with a comment saying *why* is
acceptable — sparingly, and never for logic.

Full coverage needs the dev environment (`uv sync`), which installs both optional extras.

### 3. A didactic script

**Scripts are how this project is read.** Every feature gets one in [`scripts/`](../scripts),
written to teach rather than to exercise:

- Print what it is doing and, more importantly, **why it matters** — the `-> ...` commentary lines
  in the existing scripts are the pattern.
- Show the failure mode, not only the success. [`agent_as_discipline_demo.py`](../scripts/agent_as_discipline_demo.py)
  is worth more for its oscillation case than its converging one.
- Be deterministic and exit 0.

**Run the script before you write its narration.** Two of the demos in this repo initially claimed
things that were not true — one asserted a feedback loop its model did not contain, another
claimed four bugs and printed three. Both were caught by running them. Writing the assertion is
not the same as checking it.

### 4. `run_all.py` is green

```bash
uv run python run_all.py
```

It runs every script in `scripts/` and fails on any non-zero exit. **21/21 currently.** A script
that depends on an optional extra must *skip cleanly* (exit 0 with an explanatory message), not
fail — see [`sellar_benchmark_mdo_openturns.py`](../scripts/sellar_benchmark_mdo_openturns.py).

---

## Working conventions

```bash
uv sync                              # dev env, includes both extras
uv run pytest                        # 239 tests, 100% coverage
uv run python run_all.py             # 22 scripts
uv build                             # wheel + sdist
MPLBACKEND=Agg uv run pytest         # CI sets this; conftest.py also forces Agg
```

- **Branch, never commit to `main`.** PR, wait for CI, merge preserving history (`--merge`, not
  squash — the commits are written to be read individually).
- **Commit messages explain *why*.** Look at `git log` for the register: what changed, what it
  cost, what was found along the way that was not expected.
- **Verify claims against the running code.** Every behavioural claim in `docs/` was checked by
  executing it, not by reading the source. Several turned out to be wrong.

---

## Orientation

Read [architecture.md](architecture.md) for the internals. The short version:

| Module | Holds |
|---|---|
| `models.py` | `Step` — all function introspection |
| `graph.py` | Producer mapping, Tarjan SCC, `build_execution_plan` |
| `solvers.py` | `DAGSolver`, `IterativeSolver`, `HybridSolver`, convergence checkers |
| `analysis.py` | `analyze` / `validate` / `explain` — static, executes nothing |
| `mcp/` | MCP server; `handlers.py` has the behaviour, `server.py` only registers |

**Three invariants worth protecting:**

1. **Introspection never requires execution.** Order, cycles, types and outputs all come from
   signatures and annotations. This is what makes `analysis.py` and the MCP server possible.
2. **Missing type information is not an error.** It degrades to "unchecked", never to a failure.
3. **One planner, not two.** `graph.build_execution_plan` is shared by `HybridSolver` and
   `analysis`. A second implementation would drift, and the analysis would start describing a
   pipeline the solver would not actually run.

---

## Traps that have already bitten

All in [known-issues.md](known-issues.md) with detail. The ones that cost the most time:

- **Analysis must be solver-aware.** `IterativeSolver` sweeps in registration order and ignores
  the dependency graph; `HybridSolver` follows the graph and runs cyclic blocks **alphabetically**.
  The same steps need *different* variables seeded.
- **Which variable needs an initial guess depends on step names**, because of that alphabetical
  ordering. Renaming a step changes what you must seed, and getting it wrong is a `KeyError` from
  deep inside the solve.
- **Duplicate output names overwrite silently.** The earlier step still runs; its result is
  unreachable.
- **`distance(None, None)` is `0.0`, which means *converged*.** Any code path that can compare a
  variable a block does not produce will report convergence on the first sweep without iterating.
  This is why `HybridSolver` scopes `target_var` to the block producing it.
- **`@cached` functions are keyword-only.** Invisible inside a pipeline, surprising outside one.
- **A step returning `None` stores nothing**, leaving the previous value in place — which a
  convergence checker reads as *converged*. A discipline must be total.

---

## Open decisions

Not bugs — judgement calls left deliberately to the maintainer.

1. **Phase 3's execution model.** Running a pipeline means executing arbitrary user code.
   [001](design/001-mcp-connector.md) commits to subprocess + mandatory timeout, and to never
   shipping a remote transport without real sandboxing. **This needs explicit sign-off before
   anyone builds it.**
2. **Widening the `ConvergenceChecker` protocol.** It has no way to report "this will never
   converge"; `OscillationAwareConvergenceChecker` raises from inside `distance()` as a
   workaround, which costs the `residual_history`. A proper verdict type is a breaking change to
   a public `Protocol`.

### Settled

- ~~`ipykernel` in runtime dependencies~~ — moved to `dev` in **1.8.0**. It pulled 14 packages and
  nothing imported it.
- ~~`openturns` in runtime dependencies~~ — moved behind an `[openturns]` extra in **1.7.0**.
- ~~`HybridSolver` does not forward `target_var`~~ — fixed in **1.8.0**, scoped to the cyclic block
  that produces the variable.

---

## Next

[roadmap.md](roadmap.md) — Phase 3 (sandboxed execution), plus a deferred list of things recorded
so they are not lost. Nothing in Phase 3 should start before open decision #2 is settled.
