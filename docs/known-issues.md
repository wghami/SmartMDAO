# Known issues and limitations

Things that are true about the current code, recorded so they are not rediscovered. Nothing here
is a bug report against behaviour that already works as documented — these are the sharp edges.

**Severity key:** 🔴 blocks planned work · 🟡 papercut · ⚪ by design, documented for visibility

---

## 🔴 `visualize()` hangs in a headless process

`PipelineVisualizer.render()` defaults to `view=True`, which calls `plt.show()`
([visualization.py:151](../smartmdao/visualization.py:151)). With an interactive matplotlib
backend and no display, this blocks.

CI works around it by setting `MPLBACKEND: Agg` in the workflow environment. **The library itself
does not.** Any server-side or headless consumer must force the Agg backend and pass
`view=False`.

*Blocks:* `render_xdsm` in [001-mcp-connector.md](design/001-mcp-connector.md).
*Fix direction:* have the MCP layer set the backend and never pass `view=True`. Changing the
library default is a behaviour change for existing interactive users and should be considered
separately.

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

## 🟡 Oscillation detection cannot be combined with `HybridSolver`

`OscillationAwareConvergenceChecker` is stateful and needs `distance()` called exactly once per
iteration. That only holds when `IterativeSolver(target_var=...)` is set; otherwise the residual is
a `max()` across every produced variable ([solvers.py:161](../smartmdao/solvers.py:161)), iterating
a `set` in arbitrary order, and one history cannot separate those interleaved calls.

`HybridSolver` builds its sub-solvers without forwarding `target_var`
([solvers.py:244](../smartmdao/solvers.py:244)), so a pipeline cannot currently have both
automatic cycle detection and oscillation detection. See
[002-agent-as-discipline.md](design/002-agent-as-discipline.md).

*Severity downgraded* from blocking: there is a clean way around it. Keeping the model on the
*linear* part of the pipeline and driving feedback from an outer Python loop avoids `target_var`
altogether, works with `HybridSolver`'s automatic cycle detection, and costs one model call per
outer iteration instead of one per sweep. See the Topologies section of
[002-agent-as-discipline.md](design/002-agent-as-discipline.md); Case 4 of the demo runs it.

*Fix direction:* let `HybridSolver` accept and forward `target_var`, or give the checker the
variable name. The latter means widening the `ConvergenceChecker` protocol, which is a bigger
change than it first appears.

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

## 🟡 `ConvergenceChecker` has no way to signal "this will never converge"

`distance()` returns a float, and `IterativeSolver` exits only on tolerance or exhausted
iterations. There is no third verdict. `OscillationAwareConvergenceChecker` therefore *raises*
from inside `distance()` to abort a hopeless run — effective, but a poor fit for a function whose
contract is to return a magnitude.

Side effect: an aborted run raises rather than returning, so `memory['residual_history']` is lost.
The exception carries the detected cycle, which is more useful, but the trace does not survive.

*Fix direction:* a richer verdict type (moving / at rest / hopeless) rather than a bare float.
Breaking change to a public `Protocol`; deferred.

---

## 🟡 `ipykernel` is a hard runtime dependency

[pyproject.toml](../pyproject.toml) lists `ipykernel` in `dependencies`. A library has no business
pulling a Jupyter kernel into every install. `openturns` is also heavy and is only needed by one
optional backend.

Together they make `uvx`-style installation of anything built on SmartMDAO slow.

*Not fixed:* removing a dependency is a breaking change for anyone relying on the transitive
install, and warrants its own version decision rather than being folded into a docs pass.
*Fix direction:* move `ipykernel` to the dev group, move `openturns` behind an `[openturns]`
extra, and lazily import it in `OpenTURNSBackend` the way `HDF5Backend` already treats `h5py`
([cache.py:68](../smartmdao/cache.py:68)).

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
