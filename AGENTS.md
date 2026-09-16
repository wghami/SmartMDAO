# Notes for coding agents

Read this before writing SmartMDAO code or changing this repository. Humans are welcome too.

*(If you reached SmartMDAO through the MCP connector instead, call `smartmdao_cookbook` — same
content, served as a tool.)*

---

## Writing SmartMDAO code

**Read [`docs/cookbook.md`](docs/cookbook.md) first.** The README covers a fraction of the API, so
writing from recollection produces plausible code against functions that do not exist. Every
snippet in the cookbook is executed by the test suite, so it reflects this version rather than
whatever you remember.

The shortest useful summary:

```python
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver())      # HybridSolver whenever there is feedback

@pipeline.step(outputs=["lift"])                # parameter names ARE the inputs
def compute_lift(span: float, speed: float) -> float:
    return 0.5 * 1.225 * speed**2 * span

result = pipeline.run(span=10.0, speed=50.0)    # flat dict of everything
```

Then **verify before presenting it**:

```python
from smartmdao import analyze, validate

analyze(pipeline, inputs=["span", "speed"])     # order, cycles, which var needs a seed
validate(pipeline, inputs=["span", "speed"])    # every structural problem at once
```

Neither executes a discipline. Both are cheap. Run them.

**If you translate or refactor a pipeline, prove it with `compare_runs`.** Two convergence criteria
that look equivalent can settle in different places and both report success — a translation that
quietly changes the answer is worse than none, because it looks cleaner and gets trusted.

**To run it, prefer `run_pipeline` over a shell.** It enforces a wall clock, returns typed results,
and survives a discipline that crashes. It defaults to one sweep, which proves the code executes
and measures the unit cost — quote that to the engineer before asking for a full run.

**Write generated models in the user's own working directory, not `scripts/`.** That folder holds
curated examples and `run_all.py` executes every file in it, so a model dropped there joins the
project's test surface.

### Five traps that produce a wrong answer rather than an error

1. **Duplicate output names overwrite silently.** Last registered wins; the earlier step still runs
   and its result is discarded.
2. **Which variable needs an initial guess depends on step *names*** — cyclic blocks run in
   alphabetical order. Ask `analyze()`; do not reason it out.
3. **A step returning `None` stores nothing**, so the previous value persists and the solver reads
   that as *converged*. A discipline must be total: return an explicit sentinel for "no answer".
4. **`IterativeSolver` ignores the dependency graph**, sweeping in registration order. Prefer
   `HybridSolver` unless you specifically want manual control.
5. **A discipline wired to nothing still converges.** If the graph falls into separate pieces, part
   of the pipeline cannot affect the answer and nothing fails. `validate()` reports it as
   `disconnected-graph` — the most expensive mistake here, because everything looks fine.

Full list with detail: [`docs/known-issues.md`](docs/known-issues.md).

---

## Changing this repository

**A change is not done when the code works.** It is done when all four of these hold — a branch
satisfying three is unfinished, not nearly finished:

1. **`docs/` is updated** — roadmap ticked, known-issues amended, a numbered design record if the
   decision had alternatives worth remembering. Record what did *not* work too.
2. **100% test coverage.** Not "high". It has already caught genuinely dead code.
3. **A didactic script** in `scripts/` that teaches rather than exercises — and **run it before
   writing its narration.** Several demos here shipped claims that were untrue until executed.
4. **`run_all.py` is green.**

The full contract, with rationale, is [`docs/handoff.md`](docs/handoff.md). The *why* behind the
project is [`docs/design/003`](docs/design/003-determinism-and-the-engineer-in-the-loop.md):
determinism, traceability, and giving the engineer enough information to decide rather than
deciding for them.

```bash
uv sync                          # dev env, includes both optional extras
uv run pytest                    # must be 100%
uv run python run_all.py         # every script, must be green
```

Branch, PR, wait for CI, merge with `--merge` rather than squash — the commits are written to be
read individually. Never commit to `main`.

### Three invariants worth protecting

1. **Introspection never requires execution.** Order, cycles, types and outputs come from
   signatures and annotations alone. This is what makes `analysis.py` and the MCP server possible.
2. **Missing type information is not an error.** It degrades to "unchecked", never to a failure.
3. **One planner, not two.** `graph.build_execution_plan` is shared by `HybridSolver` and
   `analysis`. A second implementation would drift, and the analysis would start describing a
   pipeline the solver would not run.

### Verify claims before writing them down

Every behavioural statement in `docs/` was checked by executing it. Several turned out to be wrong
— including two demo narrations and one exit criterion that was unachievable. If you are about to
assert what the code does, run it first.
