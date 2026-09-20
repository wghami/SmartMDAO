# Handoff

For whoever picks this up next — a contributor, a maintainer returning after a break, or a coding
agent. Read this before starting work.

**State as of v1.21.0:** `main` is clean. 558 tests, 100% coverage, 29/29 scripts, 15 notebooks.
Roadmap Phases 0–3 are complete. **Phase 4 (rule-backed disciplines) is under way** — 4.0, 4.1
and 4.2 are merged; **4.3 is next**. Design questions all settled, see
[004](design/004-rule-backed-disciplines.md).

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

Full coverage needs the dev environment (`uv sync`), which installs every optional extra.

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

### 3b. A notebook, if the change introduces a *concept*

[`notebooks/`](../notebooks) is one concept per file, committed **with outputs** so GitHub renders
them. A new public concept needs one; a bug fix does not.

```bash
uv run python run_notebooks.py          # execute all, rewrite in place
```

CI re-executes them, so a notebook that drifts from the library fails the build.
`tests/test_notebooks.py` additionally asserts every name in `smartmdao.__all__` appears in at
least one — the same guarantee the cookbook has.

### 4. `run_all.py` is green

```bash
uv run python run_all.py
```

It runs every script in `scripts/` and fails on any non-zero exit. **29/29 currently.** A script
that depends on an optional extra must *skip cleanly* (exit 0 with an explanatory message), not
fail — see [`sellar_benchmark_mdo_openturns.py`](../scripts/sellar_benchmark_mdo_openturns.py).

---

## Working conventions

New to the project? **[testing.md](testing.md)** walks through all of this step by step,
explaining what each command proves.

```bash
uv sync                              # dev env, includes every extra
uv run pytest                        # 558 tests, 100% coverage
uv run python run_all.py             # 29 scripts
uv run python run_notebooks.py       # 15 notebooks, rewritten with outputs
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
| `discretisation.py` | `Bands` / `Discretisation` — declared thresholds; each becomes a `Step` |
| `rules.py` | `RuleDiscipline` — an `.lp` program as a discipline; lazy `clingo` import |
| `mcp/` | MCP server; `handlers.py` has the behaviour, `server.py` only registers |

Documentation, and what each part is for:

| Where | For |
|---|---|
| [`notebooks/`](../notebooks) | Learning a concept properly, with real output to look at |
| [`docs/cookbook.md`](cookbook.md) | Task-indexed snippets, executed by the test suite |
| [`scripts/`](../scripts) | Runnable demonstrations, each teaching one failure mode |

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
- **Declared bands and rule-backed disciplines register as real steps**, so their *names* join the
  alphabetical ordering and can change which variable needs a seed. Adding either to a working
  pipeline can change what `run()` requires without any discipline being touched.
- **A threshold inside a loop can give the loop two converged answers**, chosen by the initial
  guess alone, with nothing reporting it.

---

## Open decisions

Not bugs — judgement calls left deliberately to the maintainer.

**None currently open.** Both of Phase 4's remaining questions closed in
[004](design/004-rule-backed-disciplines.md); the phase is unblocked.

### Settled

- ~~A settled name for an "ASP-backed discipline"~~ — **004**: the concept is a **rule-backed
  discipline**, `RuleDiscipline`. Named after what the engineer reviews, not the engine that runs
  it; `ASP`/`clingo` stay confined to the extra and the implementation docs.

- ~~How answer-set multiplicity gets pinned~~ — **004**: in two halves. `validate()` reports
  `unpinned-program` statically and **never grounds**, because grounding is worst-case exponential
  and an analysis that can hang is one nobody runs. Actual multiplicity is an `ambiguous-optimum`
  finding from a new budgeted rung on the Phase 3 cost ladder. Verified against clingo 5.8.2 before
  being written down — `--opt-mode=optN` does enumerate tied optima, and a tie-break written the
  obvious way (`#minimize { 1@0,A : selected(A) }`) turned out to separate nothing, which is why the
  static check tests that a tie-break is *well-formed* rather than *present*.

- ~~Whether to build `compare_runs`~~ — built in **1.14.0**. Two translations differing only in
  their convergence criterion both reported success while one answer was 95% out; nothing that
  reads structure could have caught it.
- ~~Where the ASP layer lives, what form the program takes, how far the discretisation is
  declared~~ — all three settled in **003**: an `[asp]` extra here, a separate reviewable `.lp`
  file, and a declared validated discretisation layer.

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

**Phase 4 is complete. Phase 5 is designed and unstarted.**

Phase 4's history is in [roadmap.md](roadmap.md); the short version is that 4.0–4.3 landed across
1.15.0–1.19.0, 4.4 was *discharged rather than written* (recorded as a decision, not ticked
silently), and 4.5 added the notebooks.

**Phase 5 — steps that touch the world.** A step inside a cyclic block runs once per sweep; for one
that writes a file or launches a subprocess that is a different thing entirely, and nothing
currently treats re-running as *unsafe* rather than wasteful. The three design questions are settled
in [005](design/005-side-effecting-steps.md) before any code exists — the shape Phase 4 used.

Read 005 before starting. Two things in it are easy to get wrong:

- **The refusal is deliberate, not an inconsistency.** Every other finding in this project is a
  warning, because every other failure is recoverable. You cannot un-send an email, so a warning
  printed alongside thirty created tickets is a post-mortem rather than information. Nothing stops
  `effects="every-sweep"`; the engineer is simply required to say so.
- **The check is solver-aware.** `IterativeSolver` sweeps every step, so the question is *would this
  run more than once*, not *is it in a cycle*.

The **optimizer case is left open on purpose** (005, risk 1): `optimize()` calls `run()` hundreds of
times, and `"once"` is scoped per run, so a declared step still fires once per evaluation. It is
sharper than the loop case and should not be designed in passing.

Smaller, and worth doing at some point:

- **The count guard has a hole.** `test_the_documented_counts_match_reality` only matches the
  `N/N scripts` form, which is how four stale lines survived in this file through several releases.
- **[known-issues.md](known-issues.md)** holds the rest, each with a severity and a fix direction.

One thing that will **not** be fixed, and should stop being attempted: a grounding blow-up cannot
be interrupted in-process. `budget_seconds` bounds *searching* only. Measured against clingo 5.8.2
and recorded — do not let "budget" grow into a claim of protection it does not give, which is the
same drift Phase 3 records about the subprocess.

*A note on this section's own history: it once pointed the next contributor at Phase 3 as upcoming
work, months after Phase 3 shipped. A doc that points at finished work is the same failure as the
Phase 2 box-ticking correction in [roadmap.md](roadmap.md), which is why the counts in this file are
now asserted by a test.*
