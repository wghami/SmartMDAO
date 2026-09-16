# Handoff

For whoever picks this up next — a contributor, a maintainer returning after a break, or a coding
agent. Read this before starting work.

**State as of v1.13.0:** `main` is clean. 361 tests, 100% coverage, 25/25 scripts passing.
Roadmap Phases 0–3 are merged, bar the deferred `compare_runs`.

Two documents set the rules. This one says what *done* means. **[003](design/003-determinism-and-the-engineer-in-the-loop.md)**
says *why the project is built the way it is*: determinism, traceability, and giving the engineer
enough information to decide rather than deciding for them. Read both before designing anything.

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

It runs every script in `scripts/` and fails on any non-zero exit. **25/25 currently.** A script
that depends on an optional extra must *skip cleanly* (exit 0 with an explanatory message), not
fail — see [`sellar_benchmark_mdo_openturns.py`](../scripts/sellar_benchmark_mdo_openturns.py).

---

## Working conventions

New to the project? **[testing.md](testing.md)** walks through all of this step by step,
explaining what each command proves.

```bash
uv sync                              # dev env, includes both extras
uv run pytest                        # 361 tests, 100% coverage
uv run python run_all.py             # 25 scripts
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
- **A disconnected discipline converges happily.** If the graph falls into separate pieces, part
  of the pipeline cannot affect the answer and nothing fails — found in real use, where an agent's
  wing model ignored three of its five inputs. `validate()` reports `disconnected-graph`.
- **The MCP loader reads module-level instances and factories, nothing else.** A pipeline built
  inside a function body and never returned is unreachable by design. Verify against real files
  before assuming the connector can see a model.

---

## Open decisions

Not bugs — judgement calls left deliberately to the maintainer.

1. **Whether to build `compare_runs`** — run two pipelines on the same inputs and diff the state.
   It is what makes a hand-written-to-SmartMDAO translation trustworthy, and the one thing neither
   the agent nor static analysis can do alone. Deferred from Phase 3, not rejected.

2. **Where the ASP layer lives** ([003](design/003-determinism-and-the-engineer-in-the-loop.md)) —
   this repository or a companion package. The dependency is trivial (`clingo` is one package with
   no transitive deps); the concern is what is distinct.

### Settled

- ~~Phase 3's execution model~~ — settled and built in **1.13.0**: subprocess, mandatory wall
  clock, `smoke` rung by default. **Do not repeat the original safety framing**: the subprocess
  buys a hard kill, typed results and crash isolation, *not* protection, because the agent has a
  shell and will run the file itself if refused.

- ~~`ipykernel` in runtime dependencies~~ — moved to `dev` in **1.8.0**. It pulled 14 packages and
  nothing imported it.
- ~~`openturns` in runtime dependencies~~ — moved behind an `[openturns]` extra in **1.7.0**.
- ~~`HybridSolver` does not forward `target_var`~~ — fixed in **1.8.0**, scoped to the cyclic block
  that produces the variable.
- ~~Widening the `ConvergenceChecker` protocol~~ — solved in **1.9.0** *without* widening it. A
  companion `AbandonmentAware` protocol sits alongside the untouched `distance()`, so no existing
  checker broke. Worth remembering as a pattern: the instinct to widen an interface was wrong;
  adding a second optional one did the same job for free.

---

## Next

[roadmap.md](roadmap.md) — Phase 3 (sandboxed execution), plus a deferred list of things recorded
so they are not lost. Nothing in Phase 3 should start before open decision #2 is settled.
