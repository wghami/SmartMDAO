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

## 🔴 No oscillation detection in the convergence loop

`IterativeSolver` compares only the previous sweep to the current one. A coupling variable that
alternates A→B→A→B never satisfies the tolerance and burns all `max_iterations`.

Harmless for well-posed numeric MDA, where oscillation usually shows up as a diverging residual.
Material for non-numeric convergence, where structural equality is binary and a 2-cycle is
indistinguishable from steady progress — and expensive if a step costs a model call.

*Blocks:* Phase 1 in [roadmap.md](roadmap.md).
*Fix direction:* `ConvergenceChecker` is a `Protocol` and the instance persists across iterations,
so a stateful checker can retain a short value history and report a detected cycle. No solver
changes needed.

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
