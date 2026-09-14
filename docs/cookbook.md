# Cookbook

Task-indexed guidance for writing SmartMDAO pipelines, for humans and for coding agents.

**Every Python block here is executed by the test suite** (`tests/test_cookbook.py`). A snippet that
does not run fails CI, so nothing on this page can quietly rot into being wrong.

Each section names the scripts in [`scripts/`](../scripts) that go deeper. Those run in CI too.

| Topic | When you need it |
|---|---|
| [`quickstart`](#quickstart) | Writing your first pipeline |
| [`solvers`](#solvers) | Choosing between DAGSolver, IterativeSolver and HybridSolver |
| [`feedback-loops`](#feedback-loops) | Disciplines that depend on each other |
| [`convergence`](#convergence) | Controlling or inspecting how a loop settles |
| [`non-numeric`](#non-numeric) | Coupling on sets, dataclasses or decisions instead of floats |
| [`types`](#types) | Catching wiring mistakes before you run |
| [`caching`](#caching) | Expensive disciplines, repeated evaluations |
| [`optimization`](#optimization) | Driving a pipeline with an optimizer |
| [`analysis`](#analysis) | Inspecting a pipeline without running it |
| [`visualization`](#visualization) | XDSM diagrams |
| [`pitfalls`](#pitfalls) | Mistakes that fail silently — read this one |
| [`reference`](#reference) | Everything else exported from `smartmdao` |

---

## quickstart

A pipeline is a list of ordinary Python functions. `@pipeline.step` registers one; its
**parameter names** are its inputs and `outputs=[...]` names what it produces. Wiring is by name —
there is no explicit graph to build.

```python
from smartmdao import Pipeline

pipeline = Pipeline()

@pipeline.step(outputs=["area"])
def wing_area(span: float, chord: float) -> float:
    return span * chord

@pipeline.step(outputs=["lift"])
def lift(area: float, speed: float) -> float:
    return 0.5 * 1.225 * speed**2 * area * 1.2

result = pipeline.run(span=10.0, chord=1.5, speed=50.0)
assert abs(result["lift"] - 27562.5) < 0.1
```

`run()` returns a flat dict of everything: your inputs plus every produced variable.

**Deeper:** [`define_pipeline_via_decorators.py`](../scripts/define_pipeline_via_decorators.py),
[`ideal_pipeline_demo.py`](../scripts/ideal_pipeline_demo.py),
[`readme_quick_start.py`](../scripts/readme_quick_start.py)

---

## solvers

| Solver | Use when | Cost |
|---|---|---|
| `DAGSolver` *(default)* | No discipline consumes a value produced later. **Raises on a cycle.** | One pass |
| `HybridSolver` | Anything cyclic. Finds the loops itself, iterates only those. | One pass + iterations on the cyclic blocks |
| `IterativeSolver` | You want manual control of order and convergence | Sweeps *everything*, every iteration |

**Reach for `HybridSolver` whenever there is feedback.** It decomposes the graph, runs acyclic
steps once, and iterates only the strongly connected blocks.

```python
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(max_iterations=100, tolerance=1e-8))

@pipeline.step(outputs=["battery_kg"])
def battery(total_kg: float) -> float:
    return 0.25 * total_kg          # heavier aircraft needs more battery

@pipeline.step(outputs=["total_kg"])
def airframe(battery_kg: float) -> float:
    return 500.0 + battery_kg       # ...which makes it heavier

# Seed `battery_kg`, not `total_kg`: a cyclic block runs in ALPHABETICAL order,
# so `airframe` goes first and reads battery_kg before anything produces it.
# This is pitfall 2 below, and it caught the author of this page.
result = pipeline.run(battery_kg=100.0)
assert abs(result["total_kg"] - 666.67) < 0.01
```

**Deeper:** [`basic_solvers_demo.py`](../scripts/basic_solvers_demo.py),
[`three_level_iterative_solver.py`](../scripts/three_level_iterative_solver.py)

---

## feedback-loops

A cycle needs a **starting value** for whichever variable is read before it is produced. Pass it to
`run()` like any other input.

Which variable that is depends on the solver, and it is not always obvious — **ask rather than
guess**:

```python
from smartmdao import Pipeline, HybridSolver, analyze

pipeline = Pipeline(solver=HybridSolver())

@pipeline.step(outputs=["y1"])
def discipline_1(z: float, y2: float) -> float:
    return z**2 - 0.2 * y2

@pipeline.step(outputs=["y2"])
def discipline_2(y1: float) -> float:
    return abs(y1) ** 0.5

report = analyze(pipeline, inputs=["z"])
assert [g.variable for g in report.initial_guesses_required] == ["y2"]
assert report.cycles[0].feedback_variables == ("y1", "y2")

result = pipeline.run(z=2.0, y2=1.0)      # y2 is the seed analyze() asked for
```

**Deeper:** [`sellar_benchmark_mda.py`](../scripts/sellar_benchmark_mda.py),
[`pipeline_analysis_demo.py`](../scripts/pipeline_analysis_demo.py)

---

## convergence

Every iterative block returns a `ConvergenceReport` telling you what happened — you never have to
infer it from residuals.

```python
from smartmdao import Pipeline, IterativeSolver, CONVERGED

pipeline = Pipeline(solver=IterativeSolver(tolerance=1e-9, target_var="x"))

@pipeline.step(outputs=["x"])
def settle(x: float) -> float:
    return 0.5 * x + 1.0

result = pipeline.run(x=0.0)
report = result["convergence_reports"][-1]

assert report.status == CONVERGED
assert report.converged is True
assert abs(result["x"] - 2.0) < 1e-6
```

`status` is one of `CONVERGED`, `MAX_ITERATIONS` or `ABANDONED`. `target_var` narrows convergence
to one variable instead of a `max()` across everything produced — set it when one noisy variable
would otherwise hold the system back, and **always** when using a stateful checker.

To stop a hopeless loop early rather than burning every iteration:

```python
from smartmdao import (
    Pipeline, HybridSolver, OscillationAwareConvergenceChecker, ABANDONED,
)

pipeline = Pipeline(
    solver=HybridSolver(
        max_iterations=100,
        target_var="choice",
        convergence_checker=OscillationAwareConvergenceChecker(),
    )
)

@pipeline.step(outputs=["choice"])
def flip_flop(choice: str, nudge: int) -> str:
    return "B" if choice == "A" else "A"

@pipeline.step(outputs=["nudge"])
def observe(choice: str) -> int:
    return len(choice)

result = pipeline.run(choice="A", nudge=0)
report = result["convergence_reports"][-1]

assert report.status == ABANDONED
assert report.iterations == 4           # caught at 2 repetitions, not 100
assert "period 2" in report.reason
```

**Deeper:** [`convergence_report_demo.py`](../scripts/convergence_report_demo.py),
[`hybrid_target_var_demo.py`](../scripts/hybrid_target_var_demo.py)

---

## non-numeric

Convergence does not require floats. Anything supporting `==` converges on **structural equality**
— unchanged since the last sweep means at rest. This is what OpenMDAO and GEMSEO cannot express.

```python
from smartmdao import Pipeline, IterativeSolver

DEPENDS_ON = {"billing": {"auth", "db"}, "auth": {"db"}, "reporting": {"db"}}

pipeline = Pipeline(solver=IterativeSolver(max_iterations=10, target_var="enabled"))

@pipeline.step(outputs=["enabled"])
def resolve(requested: frozenset, enabled: frozenset) -> frozenset:
    expanded = set(enabled) | set(requested)
    for feature in list(expanded):
        expanded |= DEPENDS_ON.get(feature, set())
    return frozenset(expanded)

result = pipeline.run(requested=frozenset({"billing"}), enabled=frozenset())
assert result["enabled"] == frozenset({"billing", "auth", "db"})
```

Keep the coupling variable **low-entropy** — a frozenset, an enum, a small frozen dataclass. Never
prose: one reworded word reads as "still moving" and it will never converge.

**Deeper:** [`non_numeric_convergence_demo.py`](../scripts/non_numeric_convergence_demo.py),
[`agent_as_discipline_demo.py`](../scripts/agent_as_discipline_demo.py)

---

## types

Annotations are optional but earn their keep: `validate_structure` checks every producer/consumer
edge before anything runs. Missing annotations degrade to "unchecked", never to an error.

```python
from smartmdao import Pipeline, TypeMismatchError

pipeline = Pipeline()

@pipeline.step(outputs=["label"])
def produce(seed: int) -> str:
    return str(seed)

@pipeline.step(outputs=["doubled"])
def consume(label: int) -> int:        # declares int, gets str
    return label * 2

try:
    pipeline.run(seed=3)
    raise AssertionError("should have been rejected")
except TypeMismatchError as error:
    assert "label" in str(error)
```

`int` deliberately does **not** satisfy `float`. Pass `runtime_type_checks=True` to `Pipeline` to
also check actual values on every call.

**Deeper:** [`type_validation_demo.py`](../scripts/type_validation_demo.py)

---

## caching

`@cached` keys on a discipline's **inputs**, so a converging loop that revisits a state pays once.
Apply it *under* `@pipeline.step` — the step decorator sees through it.

```python
from smartmdao import Pipeline, cached, MemoryBackend

backend = MemoryBackend()
calls = []

pipeline = Pipeline()

@pipeline.step(outputs=["y"])
@cached(backend)
def expensive(x: float) -> float:
    calls.append(x)
    return x * 2

pipeline.run(x=3.0)
pipeline.run(x=3.0)          # same input: served from cache
assert len(calls) == 1
```

Backends: `MemoryBackend` (dict), `HistoryBackend` (memory plus every value ever produced, for
plotting or audit), `PickleDiskBackend` (anything picklable), `HDF5Backend` (arrays and scalars
only).

**Note:** a `@cached` function is keyword-only — `expensive(3.0)` raises, `expensive(x=3.0)` works.
Invisible inside a pipeline, surprising outside one.

**Deeper:** [`caching_linear_solver.py`](../scripts/caching_linear_solver.py),
[`caching_hybrid_solver.py`](../scripts/caching_hybrid_solver.py)

---

## optimization

`PipelineEvaluator` adapts a pipeline to the array-in/scalar-out interface optimizers want;
`OptimizationProblem` describes the problem backend-agnostically; `optimize()` runs it.

```python
from smartmdao import (
    Pipeline, PipelineEvaluator, OptimizationProblem, ConstraintSpec, optimize,
)

pipeline = Pipeline()

@pipeline.step(outputs=["objective"])
def objective(x: float) -> float:
    return x ** 2                       # unconstrained minimum at x = 0

@pipeline.step(outputs=["floor"])
def floor(x: float) -> float:
    return 2.0 - x                      # naturally x <= 2

problem = OptimizationProblem(
    evaluator=PipelineEvaluator(pipeline, design_vars=["x"]),
    initial_guess=[0.0],
    bounds=[(-10.0, 10.0)],
    objective="objective",
    constraints=[ConstraintSpec(name="floor", kind="ineq", multiplier=-1.0)],
)

result = optimize(problem, backend="scipy")
assert result.success
assert abs(result.x[0] - 2.0) < 1e-3
```

Both shipped backends use the convention **`ineq` means `h(x) >= 0`**. `multiplier=-1.0` flips a
constraint naturally written as `g(x) <= 0`.

`backend="openturns"` needs `pip install smartmdao[openturns]`. Register your own with
`@register_backend("name")`.

**Deeper:** [`optimizer_backends_demo.py`](../scripts/optimizer_backends_demo.py),
[`sellar_benchmark_mdo_scipy.py`](../scripts/sellar_benchmark_mdo_scipy.py),
[`golinsky_benchmark_mdo.py`](../scripts/golinsky_benchmark_mdo.py),
[`hs71_benchmark_mdo.py`](../scripts/hs71_benchmark_mdo.py),
[`ssbj_analytical_benchmark_mdo.py`](../scripts/ssbj_analytical_benchmark_mdo.py)

---

## analysis

`analyze`, `validate` and `explain` read structure **without executing any discipline**. Use them
before running anything, and always after generating a pipeline.

```python
from smartmdao import Pipeline, DAGSolver, analyze, validate, explain

pipeline = Pipeline(solver=DAGSolver())

@pipeline.step(outputs=["b"])
def first(a: float) -> float:
    return a * 2

@pipeline.step(outputs=["c"])
def second(b: float) -> float:
    return b + 1

report = analyze(pipeline, inputs=["a"])
assert report.execution_order == ("first", "second")
assert report.recommended_solver == "DAGSolver"
assert report.external_inputs == ("a",)
assert report.terminal_outputs == ("c",)

assert validate(pipeline, inputs=["a"]) == ()
assert "Recommended solver" in explain(pipeline, inputs=["a"])
```

`validate()` reports duplicate outputs, every bad type edge, missing inputs, unseeded feedback
variables and solver misconfiguration — worst first, all at once.

**Deeper:** [`pipeline_analysis_demo.py`](../scripts/pipeline_analysis_demo.py),
[`mcp_connector_demo.py`](../scripts/mcp_connector_demo.py),
[`pipeline_discovery_demo.py`](../scripts/pipeline_discovery_demo.py)

---

## visualization

```python
import matplotlib
matplotlib.use("Agg", force=True)          # required in a headless process

from smartmdao import Pipeline

pipeline = Pipeline()

@pipeline.step(outputs=["b"])
def first(a: float) -> float:
    return a * 2

pipeline.visualize(inputs=["a"], output_path="results/cookbook.png", view=False)
```

**Always pass `view=False` outside a notebook.** The default is `view=True`, which calls
`plt.show()` and blocks forever with no display.

---

## pitfalls

Six mistakes that produce a wrong answer rather than an error. All are in
[known-issues.md](known-issues.md) with detail.

**1. Duplicate output names overwrite silently.** Two steps declaring `outputs=["mass"]` — the last
registered wins, the first still runs, its result is discarded, and consumers wire to the wrong
producer. `validate()` catches it.

**2. Which variable needs a seed depends on step *names*.** A cyclic block runs in alphabetical
order, so renaming a step changes what you must pass to `run()`. Ask `analyze()`; do not guess.

**3. A step returning `None` stores nothing.** The previous value stays in memory, and a
convergence checker reads that as *converged*. A discipline must be **total** — return an explicit
sentinel for "no answer", never `None`.

**4. Step order matters for `IterativeSolver`.** It sweeps in registration order and ignores the
graph. Register a consumer before its producer and the first sweep sees a stale value; a step that
returns its input unchanged then makes the solver report convergence at iteration 1 on something
never evaluated. `HybridSolver` derives order from the graph and avoids this entirely.

**5. `int` does not satisfy `float`.** Deliberate. Implement `TypeChecker` if you want it looser.

**6. `@cached` functions are keyword-only.** `f(1.0)` raises; `f(x=1.0)` works.

**Deeper:** [known-issues.md](known-issues.md),
[`adapt_external_functions_with_same_name.py`](../scripts/adapt_external_functions_with_same_name.py),
[`basic_ml_orchestrator.py`](../scripts/basic_ml_orchestrator.py)

---

## reference

The rest of the public surface, for completeness. Everything here is importable from `smartmdao`
directly.

**Result objects** — returned to you, not constructed by you:

| Name | What it is |
|---|---|
| `PipelineAnalysis` | What `analyze()` returns: order, cycles, seeds, recommended solver |
| `CycleAnalysis` | One feedback loop inside a `PipelineAnalysis` |
| `InitialGuess` | A variable needing a seed, and the step that reads it first |
| `Finding` | One problem from `validate()` — `code`, `severity`, `message`, `step`, `variable` |
| `OptimizationResult` | Normalised optimizer outcome, with the backend's own object in `.raw` |

**Protocols** — implement these to extend the library. Structural typing throughout: no base class
to inherit, just the method.

| Name | Implement to |
|---|---|
| `Solver` | Replace the execution strategy entirely |
| `ConvergenceChecker` | Define what "close enough" means (`distance`) |
| `AbandonmentAware` | Let a checker stop a hopeless solve (`abandon_reason`) |
| `TypeChecker` | Change how declared types are compared |
| `OptimizerBackend` | Plug in an optimizer, then `@register_backend("name")` |

**Default implementations**, useful as a base or a reference:

| Name | Behaviour |
|---|---|
| `StandardConvergenceChecker` | Numeric `abs(Δ)`; structural equality for everything else |
| `StandardTypeChecker` | Strict — `int` does not satisfy `float` |

**Errors:**

| Name | Raised when |
|---|---|
| `TypeMismatchError` | A declared type on an edge, or an actual value, does not match |
| `OscillationDetectedError` | Only with `raise_on_detection=True`; otherwise a cycle is reported as an `ABANDONED` `ConvergenceReport` |

**Utility:**

```python
import logging
from smartmdao import configure_logging

configure_logging(level=logging.INFO)     # see solver decisions and iteration counts
```

Set `logging.DEBUG` to see every step invocation and each iteration's residual — the fastest way
to understand what a solve is actually doing.
