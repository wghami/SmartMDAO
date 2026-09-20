# Architecture

How SmartMDAO works internally. This is a reference for contributors — and for tooling (see
[001-mcp-connector.md](design/001-mcp-connector.md)) that needs to reason about a pipeline
without running it.

The whole library is ~1800 lines across 12 modules. Read it in this order:
`models` → `graph` → `validation` → `executor` → `solvers` → `core` → `optimization` → `cache`.

---

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | `Step` — one node, wrapping a callable. All function introspection lives here. |
| `graph.py` | Pure graph algorithms: producer mapping, adjacency building, Tarjan SCC. |
| `validation.py` | `TypeChecker` protocol, static edge validation, per-call input validation. |
| `executor.py` | `StepExecutor` — argument binding, invocation, memory updates. |
| `solvers.py` | `DAGSolver`, `IterativeSolver`, `HybridSolver`, convergence checking. |
| `discretisation.py` | `Bands` / `Discretisation` — declared thresholds turning a number into a symbolic fact. Each band becomes an ordinary `Step`. |
| `core.py` | `Pipeline` — the user-facing façade that wires the above together. |
| `optimization.py` | Bridge to external optimizers; backend registry. |
| `cache.py` | `@cached` and its four storage backends. |
| `visualization.py` | Pure-matplotlib XDSM diagram rendering. |
| `logging_config.py` | `configure_logging` helper. |

No module imports `core`, except `optimization` (for the `Pipeline` type). The dependency
direction is strictly one-way, which is why solvers and validation can be swapped without
touching the façade.

---

## The execution path

`Pipeline.run(**inputs)` ([core.py:51](../smartmdao/core.py:51)) does four things:

1. **Structural validation, once per pipeline shape.** `validate_structure` walks every
   producer→consumer edge and checks declared types are compatible. It executes nothing, so the
   result only depends on the pipeline's shape — hence the `_structure_validated` flag. Adding a
   step via `add()` resets it. This matters: inside an optimization loop the pipeline is run
   thousands of times and this cost is paid once.
2. **External input validation, every call.** `validate_external_inputs` type-checks the concrete
   values passed to `run()`, but only for variables *not* produced internally.
3. **Delegation to the solver.** `runtime_type_checks` (default `False`) decides whether a
   `type_checker` is threaded down into `StepExecutor` for per-invocation checks. It is opt-in
   because it adds overhead to every step call inside a convergence loop.
4. **Return `memory`** — a flat dict of every variable the run produced, plus the inputs.

There is no separate "compile" phase. A `Pipeline` is a list of `Step`s and a `Solver`; everything
else is derived on demand.

---

## `Step`: how a function becomes a node

A `Step` ([models.py:6](../smartmdao/models.py:6)) wraps a callable and nothing else. It is
`@dataclass(eq=False)` so identity-based hashing works — the graph algorithms use `Step` objects
as dict keys, and two steps wrapping identical functions must stay distinct.

**Seeing through decorators.** `get_signature()` calls `inspect.unwrap(self.fn)` before
`inspect.signature`. This is what makes `@pipeline.step` compose with `@cached` — without it the
graph would see the wrapper's `(**kwargs)` signature and infer no dependencies at all.

**Output names** (`resolve_output_names`), in priority order:

1. Explicit `outputs=[...]` passed to the decorator.
2. If the return annotation is a dataclass → its field names.
3. Otherwise → the function's own name.

**Output types** (`resolve_output_types`) mirror that:

- dataclass return → per-field types from `get_type_hints`;
- `tuple[...]` return matched against explicit `outputs=[...]` of the same length → per-position;
- single output → the return annotation itself;
- anything else → `{}`, meaning "no declared type", which validation treats as no constraint.

Every one of these paths is wrapped in `try/except` around `get_type_hints`. Unresolvable
annotations (forward refs, closures without global context) degrade to "skip validation for this
step" rather than failing pipeline construction. **Absence of a type is never an error.**

---

## The graph layer

Three functions, all pure, all in [graph.py](../smartmdao/graph.py):

- **`map_producers(steps)`** → `{variable_name: Step}`. Later steps overwrite earlier ones for the
  same name, so the *last* registered producer of a variable wins.
- **`build_dependency_graph(steps, input_keys, producers_map)`** → `(adj_list, indegree)`. For each
  step parameter it checks `producers_map` **first**, and only falls through to `input_keys`
  otherwise. That ordering is deliberate: a variable that is both passed to `run()` *and* produced
  by a step is treated as internally produced, which is exactly what makes feedback loops work —
  `run(y2=1.0)` supplies an initial guess without severing the `y2` edge.
- **`tarjan_scc(steps, adj_list)`** → list of strongly connected components. Recursive; a pipeline
  deeper than Python's recursion limit would need an iterative rewrite.

---

## Solvers

All three satisfy the `Solver` protocol: `solve(steps, inputs, type_checker=None) -> dict`.

### `DAGSolver` (default)

Kahn's algorithm over the dependency graph. If the sorted output is shorter than the step list, a
cycle exists and it raises `ValueError` pointing at `HybridSolver`/`IterativeSolver`. Cheapest
option; correct whenever there is no feedback.

### `IterativeSolver`

Runs *every* step in sequence, repeatedly, until the system stops moving.

Each iteration snapshots the current value of every produced variable, runs the sequence, then
computes a residual as the **maximum** `convergence_checker.distance()` across all produced
variables. It breaks when `diff != inf and diff < tolerance`.

Two consequences worth internalising:

- Because the residual is a `max()` over *all* produced variables
  ([solvers.py:161](../smartmdao/solvers.py:161)), one noisy variable holds the whole system
  hostage. `target_var` is the escape hatch — set it and convergence is judged on that variable
  alone.
- The `inf` guard means a non-numeric variable that is still changing can never accidentally
  satisfy the tolerance.

The residual list for each run is appended to `memory['residual_history']`, so a converged result
carries its own convergence trace.

Alongside it, each block appends a **`ConvergenceReport`** to `memory['convergence_reports']`,
recording `status` (`CONVERGED` / `MAX_ITERATIONS` / `ABANDONED`), the iteration count, the
residuals, a reason, and the block's step names. Without it a caller had to re-apply the tolerance
by hand to tell a converged run from an exhausted one.

### `AbandonmentAware`

`distance()` returns a magnitude, and a magnitude cannot express *"stop, this will never
converge"*. A checker that knows a system is hopeless — because the coupling variable is cycling,
say — therefore had only one option: raise from inside `distance()`, killing the run and taking
the residual history with it.

The optional companion protocol solves that without touching `ConvergenceChecker`:

```python
@runtime_checkable
class AbandonmentAware(Protocol):
    def abandon_reason(self) -> Optional[str]: ...   # None = keep iterating
```

`IterativeSolver` asks after each sweep, via `isinstance` — structural typing, so implementing the
one method is enough and checkers that do not are never asked. On a non-`None` answer the solve
stops cleanly with status `ABANDONED` and the reason recorded.

### `StandardConvergenceChecker`

The piece that makes non-numeric MDA possible ([solvers.py:37](../smartmdao/solvers.py:37)):

```python
if isinstance(previous, (int, float)) and isinstance(current, (int, float)):
    return abs(current - previous)      # numeric residual
return 0.0 if previous == current else float('inf')   # structural equality
```

A value that raises on `==` is treated as `inf` — never falsely claim convergence. Note the
distance for non-numeric values is **binary**: `0.0` or `inf`, with nothing in between. There is
no notion of "getting closer" for a dataclass.

`ConvergenceChecker` is a `Protocol`, so domain-specific residuals (relative error, array norms,
oscillation detection) drop in without touching the solvers.

### `HybridSolver`

The interesting one. It decomposes rather than brute-forcing:

1. Build the dependency graph.
2. Find SCCs with Tarjan.
3. Build the condensation graph (a DAG whose nodes are SCCs).
4. Topologically sort that DAG.
5. Walk the plan: a single step with no self-loop runs **exactly once**; any larger group is
   handed to a nested `IterativeSolver` ([solvers.py:232](../smartmdao/solvers.py:232)).

So only genuinely cyclic blocks iterate. Steps downstream of a feedback loop are evaluated once,
after it has converged. Within a cyclic block, steps are sorted alphabetically by name to keep
execution order deterministic.

`target_var` is forwarded to the cyclic block that **produces** that variable, and to no other.
This scoping is not a nicety: a block that does not produce the name has no entry for it in its
own snapshot, so the residual becomes `distance(None, None)` — which is `0.0`, meaning *converged*.
The block would report success on its first sweep having never iterated. A target matching no
cyclic block is ignored with a warning.

---

## `StepExecutor`

`run_step` ([executor.py:17](../smartmdao/executor.py:17)) binds arguments from `memory` by
parameter name. A parameter missing from memory is an error *only* if it has no default —
defaults make a step's input optional. Missing required inputs raise `KeyError` listing both what
was missing and what was available.

Invocation is always `step.fn(**params)` — keyword-only. This is why `@cached`'s wrapper can
declare `def wrapper(**kwargs)` and still work for every step.

Exceptions from user code are wrapped in `RuntimeError` naming the step, with the original
preserved via `from e`.

**Memory updates** (`_update_memory`) have six cases:

| Return value | Behaviour |
|---|---|
| `None` | Nothing stored. A step may be pure side-effect. |
| explicit `outputs=[one]` | Whole result stored under that name. |
| explicit `outputs=[...]` + `dict` | Keys picked out by name; missing key → `KeyError`. |
| explicit `outputs=[...]` + tuple/list | Zipped positionally; length mismatch → `ValueError`. |
| dataclass, no explicit outputs | Expanded via `asdict()` into one variable per field. |
| anything else | Stored under the single resolved output name. |

Note the dataclass case uses a **runtime** `is_dataclass(result)` check, so it fires even when the
function carries no return annotation.

---

## The optimizer bridge

`PipelineEvaluator` ([optimization.py:9](../smartmdao/optimization.py:9)) adapts a `Pipeline` to
the array-in/scalar-out interface optimizers expect. It maps an ordered `design_vars` list onto
the array `x`, merges in fixed `constants`, and runs the pipeline.

Its one piece of cleverness is memoising the last evaluation: optimizers typically ask for the
objective and each constraint separately at the *same* `x`, and without this the pipeline would
run once per question. `get_objective(name)` and `get_constraint(name, multiplier)` are factories
returning plain callables closed over the evaluator.

`OptimizationProblem` describes the problem backend-agnostically; `OptimizationResult` normalises
the outcome and keeps the backend's native object in `raw`.

**The backend registry** is the same "name it once" pattern as `@pipeline.step`:
`@register_backend("scipy")` populates a module-level dict, and `optimize(problem, backend=...)`
accepts either a registered name or any object satisfying the `OptimizerBackend` protocol.
Unknown names raise listing what *is* registered.

Both shipped backends follow one convention: `ineq` means `h(x) >= 0`. `ConstraintSpec.multiplier`
exists to flip disciplines naturally written as `g(x) <= 0`. `OpenTURNSBackend` resolves `method`
via `getattr(ot, method)`, so any algorithm OpenTURNS ships is selectable with no new branch.

---

## Caching

`@cached(backend)` keys on the **inputs**, not the call site.
`generate_cache_key` ([cache.py:121](../smartmdao/cache.py:121)) sorts the kwargs, pickles them,
and SHA-256s the bytes — so argument order never affects the key, but every argument must be
picklable.

Four backends behind one ABC: `MemoryBackend` (dict), `HistoryBackend` (adds a chronological list
of every value each function produced — useful for plotting convergence, and for auditing),
`HDF5Backend` (lazy `h5py` import; scalars/strings/arrays only), `PickleDiskBackend` (anything
picklable, one file per entry).

Caching matters most inside `IterativeSolver`, where the same step is re-evaluated every sweep and
a converging system revisits near-identical states.

---

## Validation

`StandardTypeChecker` reduces annotations to concrete classes via `_concrete_classes`, which
understands `Optional`/`Union` (flattened), generic containers (checked on origin only — `list[int]`
and `list[str]` are indistinguishable), and `typing.Any` (returns `()`, meaning no constraint).

It is deliberately strict: `int` does **not** satisfy `float`. Loosening that is a three-line
custom `TypeChecker`, which is the documented extension point.

`validate_structure` compares *declared* types on each edge and runs before execution.
`validate_external_inputs` compares *actual* values, per call, for external inputs only. Anything
the framework can't confidently infer is skipped rather than rejected.

---

## Invariants worth preserving

- **Introspection must never require execution.** Every structural fact — order, cycles, types,
  outputs — is derived from signatures and annotations alone. This is what makes static analysis
  tooling possible.
- **Missing type information is not an error.** It degrades to "unchecked", never to a failure.
- **The solver decides termination, not the steps.** Steps are pure contributors; convergence is
  judged externally. See [002-agent-as-discipline.md](design/002-agent-as-discipline.md) for why
  that property turns out to be valuable well beyond numerics.
