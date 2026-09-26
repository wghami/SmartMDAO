# Handoff

For whoever picks this up next — a contributor, a maintainer returning after a break, or a coding
agent. Read this before starting work.

**State as of v1.26.0:** `main` is clean. 805 tests, 100% coverage, 29/29 scripts, 16 notebooks.
Roadmap **Phases 0–5 are complete** — the last two were rule-backed disciplines
([004](design/004-rule-backed-disciplines.md)) and steps that touch the world
([005](design/005-side-effecting-steps.md)). **Phase 6** — lessons from paper-repro, a downstream
project using the MCP connector — is under way in the [roadmap](roadmap.md): 6.1–6.4 shipped in 1.24.0–1.25.0 and 6.6 (each pipeline in its own environment) makes 1.26.0; the sweep waits for paper-repro's first real runs.

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

### How this is enforced

Item 1 was the one most often forgotten, so it is no longer left to memory. One script,
[`tools/docs_gate.py`](../tools/docs_gate.py), holds the rule — **if anything under `smartmdao/`
changed, something under `docs/` changed too** — and four layers call it, so they cannot disagree:

| Layer | When | Effect |
|---|---|---|
| CI `docs-gate` job | every pull request | **fails the PR** |
| `.githooks/pre-push` | before a push leaves the machine | blocks the push; also runs the prose and count guards |
| Claude Code `PreToolUse` hook | on `gh pr create` | blocks the command, with the checklist |
| Claude Code `Stop` hook | end of every turn | reminds once, including uncommitted and untracked files; also flags a version on `main` whose tag has not appeared |

When no document genuinely needs to change — a private rename, a test-only fix — say so in the PR
body (or a commit message, for the pre-push hook):

```text
Docs: not needed — <a reason of at least ten characters>
```

The reason is required because an empty waiver is the same as no gate. The git hook is opt-in per
clone: `git config core.hooksPath .githooks`. The Claude Code hooks live in the committed
`.claude/settings.json`.

The gate only sees *whether* docs moved. Whether they are *right* is guarded by
`tests/test_docs.py` — relative links resolve, the roadmap's current position matches its phase
headers and agrees with this file, no finished phase hides an unexplained open box, and each design
record's status agrees with the index — and by the count and coverage guards in
`tests/test_cookbook.py`.

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

### 3. Didactic material — a notebook for a concept, a script for a failure mode

**This is how the project is read.** Every feature teaches itself somewhere. Since 1.20.0 the
preferred vehicle for a new *concept* is a notebook (3b below), and [`scripts/`](../scripts) is kept
small — a script earns its place by demonstrating one failure mode end to end. Either way it is
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
uv run python run_notebooks.py          # execute all, rewrite only what changed
uv run python run_notebooks.py --check  # what CI runs: fail if an output is stale
```

A re-run compares each notebook with its committed copy after masking measurements (timings, log
clocks, temp paths) and writes it back only if something else changed — so an unchanged notebook
never shows up in a diff, and an output that no longer matches its code fails CI.
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
uv run pytest                        # 805 tests, 100% coverage
uv run python run_all.py             # 29 scripts
uv run python run_notebooks.py       # 16 notebooks; rewrites only the ones that changed
uv build                             # wheel + sdist
MPLBACKEND=Agg uv run pytest         # CI sets this; conftest.py also forces Agg
```

- **Branch, never commit to `main`.** PR, wait for CI, merge preserving history (`--merge`, not
  squash — the commits are written to be read individually).
- **Releasing is bumping the version.** Change `version` in `pyproject.toml` in the PR. When it
  merges and the tests pass on `main`, CI's `release` job ([`tools/release.py`](../tools/release.py))
  tags that merge commit `v<version>` and creates the GitHub Release. Do not tag by hand. The
  docs cite behaviour by release ("fixed in 1.12.0") and downstream projects pin by tag, so a
  version that is not a release is a reference nobody can check. Until 1.24.0 this was manual,
  and it drifted: the latest Release stayed at 1.14.0 through nine tagged versions, and
  1.7.0–1.13.0 were never tagged. The `publish` job then uploads the new version to PyPI by
  trusted publishing — no token exists to leak — and **waits for approval** in the `pypi`
  environment: an upload is the one step that cannot be taken back. PyPI skips 1.7.0–1.23.0 by
  choice; the first automated upload is 1.24.0.
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
| `effects.py` | Side effects at run time: the refusal, the `"once"` latch, `repeating_step_names` |
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
  guess alone. `validate()` reports it as `discretisation-in-cycle`.
- **A side-effecting step in a loop runs once per sweep.** Declare it with `effects=`; `run()`
  refuses `effects=True` there, and `effects="once"` freezes a coupling so the loop converges
  somewhere else. Keep loops pure and put effects after them.
- **Loading a file used to run it.** Importing executed any top-level `pipeline.run(...)`. Fixed in
  1.23.0, but it is why real runs belong under `if __name__ == "__main__":`.

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
  and an analysis that can hang is one nobody runs. Actual multiplicity is caught when solving:
  `RuleDiscipline.solve()` raises `AmbiguousProgramError` listing every tied model, under the
  discipline's `budget_seconds`. (004 planned an `ambiguous-optimum` finding on a cost-ladder rung;
  it shipped as an exception instead — see 004's findings.) Verified against clingo 5.8.2 before
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

**Phases 0–5 are complete. Phase 6 is under way; 6.1–6.4 and 6.6 are done. The sweep (006, 6.7) waits for paper-repro's first real runs; units (6.8) can start any time.** Phase 6 comes from
[a brief](requests/2026-09-paper-repro.md) written by paper-repro, a downstream project that uses the
MCP connector on a model outside aerospace. Every request was checked against the code before being
planned, and the roadmap records where SmartMDAO pushed back: one declared-input mechanism rather
than two, group ordering only *between* independent blocks, and units checked for consistency but
**never converted**. The sweep (R3) and the project interpreter (R4) share one worker and are
designed together in 006 and 007; the sweep's final shape waits for paper-repro's first real runs.

[roadmap.md](roadmap.md) has the history, including what each phase cost; its deferred list holds
the remaining ideas with no commitment.

Phase 5 is the one to read before extending the engine toward workflows. Three of its lessons will
recur:

- **Refusing is sometimes the informed choice.** Every finding here is a warning, because every
  other failure is recoverable. Side effects are not — you cannot un-send an email — so `run()`
  refuses `effects=True` in a loop, and anything that multiplies runs refuses any declared effect
  unless `allow_effects=True`. That is a reasoned exception to [003](design/003-determinism-and-the-engineer-in-the-loop.md),
  recorded in [005](design/005-side-effecting-steps.md); do not let it read as a drift in standards.
- **A declaration can be honoured and the answer still be wrong.** `effects="once"` freezes a
  coupling, so a loop settles somewhere other than its fixed point and reports success. Measure the
  consequence of a mechanism, not just that it works.
- **"Introspection never requires execution" was not true of files.** The loader imported them, and
  importing ran any top-level `pipeline.run(...)`. It is true now — run() is suspended during the
  import — but it went unnoticed through five phases because nothing had a side effect to show it.

Smaller things worth doing at some point are in [known-issues.md](known-issues.md), each with a
severity and a fix direction.

One thing that will **not** be fixed, and should stop being attempted: a grounding blow-up cannot
be interrupted in-process. `budget_seconds` bounds *searching* only. Measured against clingo 5.8.2
and recorded — do not let "budget" grow into a claim of protection it does not give, which is the
same drift Phase 3 records about the subprocess.

*A note on this section's own history: it once pointed the next contributor at Phase 3 as upcoming
work, months after Phase 3 shipped. The counts in this file are now asserted by a test that also
fails if the lines it checks disappear.*
