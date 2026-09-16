# Known issues and limitations

Things that are true about the current code, recorded so they are not rediscovered. Nothing here
is a bug report against behaviour that already works as documented — these are the sharp edges.

**Severity key:** 🔴 blocks planned work · 🟡 papercut · ⚪ by design, documented for visibility

---

## 🟡 `visualize()` hangs in a headless process

`PipelineVisualizer.render()` defaults to `view=True`, which calls `plt.show()`
([visualization.py:151](../smartmdao/visualization.py:151)). With an interactive matplotlib
backend and no display, this blocks.

CI works around it by setting `MPLBACKEND: Agg` in the workflow environment. **The library itself
does not.** Any server-side or headless consumer must force the Agg backend and pass
`view=False`.

*Severity downgraded:* `smartmdao.mcp.rendering` now forces Agg (with `force=True`, since
`pyplot` is already imported by the time it runs) and never passes `view=True`, so the server path
is safe. The trap remains for anyone calling `visualize()` directly in a headless process.

*Fix direction:* changing the library default is a behaviour change for existing interactive
users, so it needs its own decision rather than being folded in here.

---

## ✅ MOSTLY RESOLVED in Phase 2.5 — the MCP loader could only see module-level pipelines

`load_pipeline` used to require a `Pipeline` assigned to a module-level variable, which read **7 of
this repository's own 23 scripts**. Every failure was a factory function — normal, good Python.

**Fixed:** the loader now calls factories, either a module-level callable declaring `-> Pipeline`
or one the caller names explicitly. Ambiguity lists every candidate; a factory needing arguments is
reported with the argument names.

**Remaining, and unfixable:** a pipeline built inside a function body and never returned. There is
nothing to call and nothing to read; reaching it would mean running the function, which is the one
thing this layer promises not to do. Measured after the change: **9 auto-discovered, 1 more by
name, 14 unreachable** — where the 14 are demo scripts building pipelines inside `run_*_demo()`.
That shape is normal for a *script* and unusual for a *model*, so the figure understates the
picture for real engineering code.

*Guidance for anyone wanting their model readable:* expose it as a module-level instance, or as a
factory annotated `-> Pipeline` and callable with no arguments. See
[`scripts/pipeline_discovery_demo.py`](../scripts/pipeline_discovery_demo.py).

---

## ✅ RESOLVED in 1.11.0 — an agent had no reliable way to learn the API

The MCP server exposed two example scripts as *resources* and the `pipeline_from_prose` prompt
mentioned none of them. Since MCP resources must be explicitly fetched and many clients never
surface them, the practical grounding available to a coding agent was the README — which covers a
fraction of the API. Generated code was plausible against functions that do not exist.

**Fixed** by serving guidance as a **tool** rather than a resource (`smartmdao_cookbook`), because
tools get called and resources get ignored; by naming it in the server `instructions`, which are
always in context and are what actually drives the call; and by writing
[cookbook.md](cookbook.md), whose every snippet is executed by `tests/test_cookbook.py`.

Three tests keep it honest: every snippet runs, every referenced script exists, and every name in
`smartmdao.__all__` is covered. The last one immediately found 11 exported names with no guidance
at all.

`AGENTS.md` (symlinked as `CLAUDE.md`) covers agents working in the repository rather than through
MCP.

---

## ✅ RESOLVED in 1.13.0 — no way to run a pipeline cheaply, or to know what it would cost

An engineer will ask the agent to run the model. Refusing is not available to us: the agent has a
shell and will run `python model.py` instead — unsandboxed, untimed, with results scraped from
stdout. Refusal does not create a boundary, it moves the work somewhere with less control.

What is missing is the middle of the cost ladder:

| Rung | Cost | Status |
|---|---|---|
| `analyze` / `validate` | free, nothing runs | shipped |
| single sweep | one call per discipline | **expressible today** via `max_iterations=1`, not exposed |
| budgeted run | capped sweeps + wall clock | missing |
| full run | whatever it takes | missing |
| `optimize` | full run × 10²–10³ | missing |

The single sweep is the interesting rung twice over: it is the cheapest possible smoke test *and*
it measures the unit cost, so the estimate for every rung above it falls out of it. Measured on a
toy model with a 20 ms discipline: one sweep 0.04 s, full run 0.90 s — a 22× ratio the engineer
could have been told before committing.

**Fixed** by `run_pipeline`, which takes a `rung`: `smoke` (the default — one sweep per
discipline), `budgeted` (capped sweeps) or `full`. A smoke run returns a `cost_estimate` derived
from its own measured sweep, so the agent quotes a number instead of a disclaimer. Every rung runs
in a child process under a mandatory wall clock.

`smoke` is the default deliberately: an unbounded run should never be the thing that happens by
accident.

---

## ✅ RESOLVED in 1.14.0 — a translated pipeline could silently change the answer

Converting hand-written code to SmartMDAO is a stated use case, and the risk is semantic drift that
nothing detects. The README's own "without SmartMDAO" example is the illustration:

```python
if abs(y2_next - y2) < 1e-6: break     # converges on y2 ALONE
```

The obvious translation converges on a `max()` across **both** `y1` and `y2` — a different
criterion, which can stop at a different iteration or not at all. Faithfulness requires
`target_var="y2"`, and nothing warns you. Similarly, a hand loop using under-relaxation
(`y2 = 0.5*y2 + 0.5*y2_next`) loses its damping entirely, since SmartMDAO has no built-in
relaxation, and may diverge where the original converged.

A translation that quietly changes the answer is worse than no translation: it looks cleaner, so it
gets trusted.

**Fixed** by `compare_runs`: both pipelines, the same inputs, a diff of the resulting state.
Numbers compare within a tolerance so a different iteration count is absorbed; anything non-numeric
compares exactly, because there is no "nearly" for a frozenset of decisions. A different
*destination* counts too — close numbers are not a match when one side never converged.

Demonstrated on exactly the drift described above: two translations differing only in which
variable the solver watches, **both reporting `converged: True`**, with one answer 95% out. Nothing
that reads structure could catch it, because both files are perfectly well-formed.

Whether a translation should be *faithful* (preserve the original's convergence semantics) or
*idiomatic* (use the default and accept small numerical differences) remains the engineer's
explicit choice, per [003](design/003-determinism-and-the-engineer-in-the-loop.md) — but now it is
a choice made with the difference in front of them.

---

## ✅ RESOLVED in 1.12.0 — a disconnected discipline was invisible

Found in real use. A coding agent asked for a wing sized from span, chord and speed produced a
pipeline that **validated clean, converged, and whose arithmetic was correct** — and whose answer
did not depend on span, chord or speed at all. `compute_lift` consumed them and produced `lift`,
which nothing read; the mass loop underneath was closed on itself.

The graph was in two disconnected pieces. Nothing reported it.

**Fixed** by `disconnected-graph` (warning): if the dependency graph falls into more than one
weakly-connected piece, every piece is named along with the outputs nothing consumes. Measured
against all nine loadable pipelines in `scripts/`: **nine clean, and it isolates the bad one.**

Deliberately *not* a general "orphaned output" check — every healthy pipeline has terminal outputs
(`objective`, `constraint_1`), so flagging unused variables alone would fire on almost everything.
The *disconnection* is what is diagnostic. The original Phase 2 plan had it the other way round;
see the correction in [roadmap.md](roadmap.md).

The file is kept verbatim as `tests/fixtures/wing_mda_disconnected.py`.

---

## ✅ RESOLVED in 1.12.0 — a seed the file already passed was reported as missing

Also found in real use, and worse: the tool reported a **working file as broken**.

An agent called `analyze_pipeline` on `scripts/sellar_benchmark_mda.py` with
`inputs=["z1","z2","x1"]`. The script already passes `y2=1.0`, but the agent had to *guess* what
the file supplies, guessed the design variables, and omitted the seed — so the tool flagged as
missing precisely the thing the agent forgot to mention. The agent then reported the pipeline as
invalid and offered to patch a file that was fine.

Circular: you had to know the answer to ask the question correctly.

**Fixed** by reading the file's own `run()` call statically (AST, nothing executed) and merging
those names with the caller's. Handles `run(z1=..., y2=...)` and the
`inputs = {...}; run(**inputs)` shape the benchmarks use. The response reports `inputs_used` split
into `requested` and `found_in_source`, so the agent can say *where* a value came from rather than
asserting. Dynamic construction is still invisible — this narrows the guessing, it does not
eliminate it.

---

## 🔴 Step registration order is load-bearing, and getting it wrong is silent

`IterativeSolver` runs steps in registration order. If a step that *consumes* a feedback variable
is registered before the step that *produces* it, the first sweep sees that variable's initial
value only. A step that returns its input unchanged in that situation makes the solver report
**convergence at iteration 1** — on a state that was never evaluated.

No error, no warning. Demonstrated by
`tests/regression/test_agent_as_discipline.py::test_registering_the_model_first_converges_prematurely`,
where a pipeline confidently converges on an architecture that violates its own requirements.

*Fix direction:* this is exactly what static analysis should catch, and it is a listed target for
`validate_pipeline` in Phase 2. `HybridSolver` avoids the trap entirely by deriving order from the
graph — it is specific to hand-ordered `IterativeSolver` use.

---

## ✅ RESOLVED in 1.8.0 — oscillation detection could not be combined with `HybridSolver`

`OscillationAwareConvergenceChecker` is stateful and needs `distance()` called exactly once per
iteration. That only holds when a `target_var` is set; otherwise the residual is a `max()` across
every produced variable ([solvers.py:161](../smartmdao/solvers.py:161)), iterating a `set` in
arbitrary order, and one history cannot separate those interleaved calls.

`HybridSolver` used to build its sub-solvers without forwarding `target_var`, so a pipeline could
have automatic cycle detection *or* oscillation detection, never both.

**Fixed:** `HybridSolver(target_var=...)` now forwards to the cyclic block that produces that
variable. `scripts/hybrid_target_var_demo.py` shows the same pipeline burning all 30 sweeps without
a target and being caught at sweep 4 with one.

**The guard matters more than the feature.** A target is only ever given to the block that
*produces* it. Handing it to any other block would be silently catastrophic: that block's snapshot
has no entry for the name, so the residual becomes `distance(None, None)` — which is `0.0`, i.e.
*converged*. The block would report success on its first sweep without iterating at all. A target
matching no cyclic block is ignored, with a warning.

`validate()` now reports two related mistakes statically: `checker-needs-target-var` (a custom
checker with no target) and `target-var-not-produced`. Deliberately *not* reported: `HybridSolver`
with the standard checker and no target — that is the idiomatic default and flagging it would fire
on almost every correct pipeline.

---

## 🟡 Which variable needs an initial guess depends on step *names*

`HybridSolver` sorts the steps inside a cyclic block alphabetically for deterministic execution
([solvers.py:241](../smartmdao/solvers.py:241)). The alphabetically-first step therefore runs
first, and whichever of its inputs the cycle has not produced yet must be supplied to `run()` as
an initial guess.

The consequence is surprising: **renaming a step can change which variable you have to seed**, and
getting it wrong is a `KeyError` from deep inside the solve rather than an up-front complaint. In
the demo's mass-growth loop, `size_airframe` sorts before `size_battery`, so the guess must be
`battery_mass_kg` — not the `total_mass_kg` you would reach for by intuition. Widening a cycle
widens the set of variables needing seeds, for the same reason.

*Fix direction:* a natural target for `analyze_pipeline` in Phase 2 — "this cycle needs an initial
guess for X" is exactly the sort of thing static analysis can state up front, and it is derivable
without executing anything.

---

## ✅ RESOLVED in 1.9.0 — `ConvergenceChecker` could not signal "this will never converge"

`distance()` returns a float, and `IterativeSolver` exited only on tolerance or exhausted
iterations. There was no third verdict, so `OscillationAwareConvergenceChecker` *raised* from
inside `distance()` to abort a hopeless run — effective, but a poor fit for a function whose
contract is to return a magnitude, and it destroyed `memory['residual_history']` on the way out.

**Fixed without breaking `distance()`.** Rather than widening the existing protocol (which would
have broken every checker in existence), 1.9.0 adds a *companion* protocol:

```python
@runtime_checkable
class AbandonmentAware(Protocol):
    def abandon_reason(self) -> Optional[str]: ...   # None = keep iterating
```

Structural typing, so implementing one method is enough — no inheritance, and checkers that do not
implement it are asked nothing and behave exactly as before.

Every iterative block now also records a `ConvergenceReport` in
`memory['convergence_reports']`, with `status` of `CONVERGED`, `MAX_ITERATIONS` or `ABANDONED`,
the iteration count, the full residuals, a reason, and the block's step names. Previously a caller
got only `residual_history` and had to re-apply the tolerance by hand to tell convergence from
exhaustion.

**Behaviour change:** `OscillationAwareConvergenceChecker.raise_on_detection` now defaults to
`False`. A detected oscillation stops the solve and returns a report with the trace intact, rather
than raising. Pass `raise_on_detection=True` for the old behaviour — at the cost of losing the
residual history, which is what prompted the change.

---

## ✅ RESOLVED in 1.7.0 / 1.8.0 — heavy runtime dependencies

The base install used to pull `openturns` (needed by exactly one optimizer backend) and
`ipykernel` (needed by nothing at all).

- **1.7.0** moved `openturns` behind an `[openturns]` extra.
- **1.8.0** moved `ipykernel` to the `dev` group. Investigation found the only reference to it
  anywhere in the repository was the `pyproject.toml` line itself, added in the first commit — and
  it pulled **14 packages**, including a debugger, a Jupyter kernel, ZeroMQ and Tornado.

Both are breaking for anyone who relied on the transitive install, hence the version bumps.
`OpenTURNSBackend` still registers without OpenTURNS present and raises an actionable `ImportError`
at the call rather than at import.

---

## 🟡 Duplicate output names silently overwrite

`map_producers` ([graph.py:48](../smartmdao/graph.py:48)) builds a `{name: Step}` dict, so if two
steps declare the same output name the last one registered wins — with no warning. The first
step still executes; its output is simply unreachable, and the dependency graph wires consumers to
the wrong producer.

*Fix direction:* detect collisions in `validate_structure` and raise, or at minimum warn. This is
a genuine candidate for `validate_pipeline` in the MCP tool surface, since it is exactly the class
of mistake an agent generating many steps would make.

---

## 🟡 `@cached` functions are keyword-only

The decorator's wrapper is `def wrapper(**kwargs)` ([cache.py:134](../smartmdao/cache.py:134)).
Inside a pipeline this is invisible, because `StepExecutor` always invokes
`step.fn(**params)`. But calling a cached discipline *directly* with positional arguments fails:

```python
discipline_1(1.0, 1.0, 1.0, 1.0)      # TypeError
discipline_1(z1=1.0, z2=1.0, ...)     # works
```

Surprising when testing a discipline in isolation, or reusing one outside a pipeline.

*Fix direction:* accept `*args`, bind them against the unwrapped signature, and normalise to
kwargs before hashing — the key must stay order-independent.

---

## 🟡 `tarjan_scc` is recursive

`strongconnect` ([graph.py:16](../smartmdao/graph.py:16)) recurses per node, so a pipeline with a
chain longer than Python's recursion limit (~1000 by default) raises `RecursionError`.

Not reachable for any realistic MDO problem, but it is a hard ceiling rather than a soft one, and
a generated pipeline could plausibly get large.

*Fix direction:* iterative Tarjan with an explicit stack, if it ever matters.

---

## ⚪ Generic container types are checked on origin only

`_concrete_classes` ([validation.py:34](../smartmdao/validation.py:34)) reduces `list[int]` to
`list`, so `list[int]` and `list[str]` are indistinguishable to the type checker. Element types
are never inspected.

Deliberate — deep structural checking would be expensive on every call inside a convergence loop.
Documented so nobody reads a passing validation as stronger than it is.

---

## ⚪ `int` does not satisfy `float`

`StandardTypeChecker` is strict by design; `int` is not accepted where `float` is declared. This
surprises people, and the answer is the documented extension point: implement `TypeChecker` and
loosen `_concrete_classes`. Recorded here because it is the single most likely "is this a bug?"
question.

---

## ⚪ `HDF5Backend` cannot store arbitrary Python objects

Scalars, strings, and numpy arrays only — dicts, dataclasses, and custom objects need
`PickleDiskBackend`. Already stated in the class docstring; surfaced here because the failure
happens at write time, deep inside a converging loop, which is an unpleasant place to discover it.

---

## ⚪ A step returning `None` silently stores nothing

`_update_memory` returns early on `None` ([executor.py:91](../smartmdao/executor.py:91)),
which is what makes side-effect-only steps possible. The consequence inside a feedback loop is
that the previous value of that variable persists unchanged — which the convergence checker will
read as "at rest".

Relevant to [002-agent-as-discipline.md](design/002-agent-as-discipline.md), where a model step
needs some way to signal "no feasible answer" and `None` is the obvious-but-wrong choice.
