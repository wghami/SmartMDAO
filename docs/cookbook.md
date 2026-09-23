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
| [`discretisation`](#discretisation) | Turning a number into a symbolic fact, with the threshold declared |
| [`rules`](#rules) | Disciplines backed by a reviewed ASP program |
| [`side-effects`](#side-effects) | Steps that write a file, launch a subprocess or post to an API |
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

## discretisation

Turning a number into a symbolic fact — `mass_kg = 880` into `mass_band = "heavy"` — needs a
threshold, and **that threshold decides the answer**. Declare it with `Bands` so it is visible,
diffable and checkable, instead of burying it in a helper function.

```python
from smartmdao import Bands, Discretisation, Pipeline, explain, validate

pipeline = Pipeline(
    discretisation=Discretisation(
        mass_band=Bands("mass_kg", edges=[800, 1200],
                        names=["light", "medium", "heavy"]),
    ),
)

@pipeline.step(outputs=["mass_kg"])
def size_airframe(span: float) -> float:
    return 120.0 * span

@pipeline.step(outputs=["margin"])
def pick_margin(mass_band: str) -> float:
    return {"light": 0.05, "medium": 0.10, "heavy": 0.20}[mass_band]

# The band is an ordinary step: the solver orders it, and it runs in between.
result = pipeline.run(span=7.0)
assert result["mass_kg"] == 840.0
assert result["mass_band"] == "medium"
assert result["margin"] == 0.10

assert validate(pipeline, inputs=["span"]) == ()
assert "light=(-inf, 800); medium=[800, 1200); heavy=[1200, +inf)" in explain(
    pipeline, inputs=["span"]
)
```

**An exact edge value has a declared side.** `closed="left"` (the default) means each band is
`[lower, upper)`, so `800.0` is `"medium"`, not `"light"`. Pass `closed="right"` for the other
convention. This is a field rather than a convention because it changes answers quietly:

```python
from smartmdao import Bands

left = Bands("mass_kg", edges=[800], names=["light", "heavy"])
right = Bands("mass_kg", edges=[800], names=["light", "heavy"], closed="right")

assert left.classify(800) == "heavy"
assert right.classify(800) == "light"
assert left.classify(799.9) == right.classify(799.9) == "light"   # agree away from the edge
```

Bands must be **strictly ascending** and carry **one more name than edges**; anything else raises
`DiscretisationError` at construction, because it cannot classify at all. Judgement calls are
reported by `validate()` instead:

| Finding | Means |
|---|---|
| `discretisation-unused` | The band is derived and nothing consumes it — a declared threshold that cannot affect the answer |
| `discretisation-non-numeric` | The source variable is annotated `str`/`bool`, so classification will raise at run time |
| `missing-input` | The source variable is neither produced nor supplied — reported against the band's own step, `discretise_<name>` |
| `duplicate-output` | A step also declares the band's name |

**Two traps.** A band inside a feedback loop can give the loop **more than one fixed point** —
seeded light it settles light, seeded heavy it settles heavy, and both report `converged`. And the
synthetic step is named `discretise_<band>`, which usually sorts first alphabetically, so **it**
decides which variable needs a seed. Ask `analyze()`.

**Deeper:** [`discretisation_demo.py`](../scripts/discretisation_demo.py)

---

## rules

A **rule-backed discipline** is an ASP program (`.lp` file) wired in as a step. A language model may
have drafted it, but the file is what runs — reviewed, diffed, version-controlled. Nothing is
generated at run time, so the same facts give the same answer next year.

Needs the extra: `pip install 'smartmdao[asp]'`.

```python
import pathlib, tempfile
from smartmdao import Pipeline, Bands, Discretisation, RuleDiscipline, INFEASIBLE

program = pathlib.Path(tempfile.mkdtemp()) / "spar.lp"
program.write_text("""
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
:- spar(aluminium), mass_band(heavy).
cost(aluminium, 1). cost(cfrp, 3).
#minimize { C@1,M : spar(M), cost(M,C) }.
rank(aluminium,1). rank(cfrp,2).
#minimize { R@0,M : spar(M), rank(M,R) }.      % total tie-break: DISTINCT values
#show spar/1.
""")

pipeline = Pipeline(
    discretisation=Discretisation(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
    ),
)

@pipeline.step(outputs=["mass_kg"])
def size_airframe(span_m: float) -> float:
    return 120.0 * span_m

pipeline.add(RuleDiscipline(program, facts=["mass_band"], produces="decisions"))

assert pipeline.run(span_m=5.0)["decisions"] == frozenset({"spar(aluminium)"})
assert pipeline.run(span_m=10.0)["decisions"] == frozenset({"spar(cfrp)"})
```

**The output is a `frozenset` of atoms**, which is why it couples to a feedback loop with no solver
change — structural equality over a set of decisions, with no prose in it to perturb.

**UNSAT is the honest `INFEASIBLE`.** Not a sentinel anyone invented: clingo proved no model
satisfies the rules. It is an explicit value, never `None`, because a step returning `None` stores
nothing and reads as converged.

```python
from smartmdao import RuleDiscipline, INFEASIBLE
import pathlib, tempfile

impossible = pathlib.Path(tempfile.mkdtemp()) / "none.lp"
impossible.write_text("""
1 { spar(aluminium); spar(cfrp) } 1.
:- spar(aluminium), mass_band(heavy).
:- spar(cfrp), mass_band(heavy).
#show spar/1.
""")

rules = RuleDiscipline(impossible, facts=["mass_band"], produces="decisions")
assert rules.solve(mass_band="heavy") is INFEASIBLE
assert rules.solve(mass_band="heavy") is not None
```

### Why there is no answer

When the rules are unsatisfiable, ask. `explain_infeasible` returns a **`Conflict`** naming the
smallest set of facts that cannot hold together — minimal in the strong sense that removing any one
of them makes the rules satisfiable.

```python
from smartmdao import RuleDiscipline, INFEASIBLE
import pathlib, tempfile

program = pathlib.Path(tempfile.mkdtemp()) / "spar.lp"
program.write_text(
    "material(aluminium; cfrp).\n"
    "1 { spar(M) : material(M) } 1.\n"
    ":- spar(aluminium), mass_band(heavy).\n"
    ":- spar(cfrp), certification(part25).\n"
    "#show spar/1.\n"
)

rules = RuleDiscipline(program, facts=["mass_band", "certification", "site"],
                       produces="decisions")

facts = dict(mass_band="heavy", certification="part25", site="toulouse")
assert rules.solve(**facts) is INFEASIBLE

conflict = rules.explain_infeasible(**facts)
assert conflict.facts == {"mass_band": "heavy", "certification": "part25"}
assert "site" not in conflict.facts          # it has nothing to do with it
assert conflict.rules_alone is False
```

If the program contradicts itself regardless of input, `conflict.rules_alone` is `True` and
`conflict.facts` is empty — meaning no input could have worked, so read the program rather than
your requirements.

**It is on demand, not automatic.** An explanation costs one solve per fact, so computing it on
every sweep of a loop would charge for something nobody read. `solve()` stays cheap;
`conflict.solves` tells you what the explanation cost.

**Three things that will bite you:**

1. **A program with two equally optimal answer sets raises `AmbiguousProgramError`** rather than
   picking one. Pin it with an optimisation statement *plus* a total tie-break that ranks over a
   **distinct value per candidate** — `#minimize { 1@0,M : spar(M) }` looks like a tie-break and
   separates nothing, because the weight is the same constant for every candidate.
2. **Floats are refused** (`RuleProgramError`). ASP has no floating point, so injecting one would
   mean choosing a threshold invisibly. Declare a [`Bands`](#discretisation) and pass the band.
3. **The step name decides seeding inside a loop.** It defaults to `rules_<program stem>`; pass
   `name=` deliberately if the discipline sits in a cycle, and ask `analyze()`.

### What it costs, and what the budget covers

Solving is **memoised on the facts**. The program is fixed and clingo is deterministic, so the same
facts cannot give a different answer — the cache is exact, not an approximation. That matters inside
a loop, where the facts repeat as it converges.

```python
from smartmdao import RuleDiscipline
import pathlib, tempfile

program = pathlib.Path(tempfile.mkdtemp()) / "pick.lp"
program.write_text("spar(aluminium) :- mass_band(light).\nspar(cfrp) :- mass_band(heavy).\n#show spar/1.\n")

rules = RuleDiscipline(program, facts=["mass_band"], produces="decisions",
                       budget_seconds=5.0)

rules.solve(mass_band="light")
rules.solve(mass_band="light")          # same facts -> no re-grounding

assert rules.cost.calls == 2
assert rules.cost.grounds == 1
assert rules.cost.cache_hits == 1
print(rules.cost.projected_seconds(30))  # quote this before a long run
```

`rules.cost` is a `RuleCost`: calls, cache hits, grounds, and the seconds spent grounding versus
searching. `projected_seconds(n)` assumes **no** cache hits, which is the pessimistic reading on
purpose — quoting the optimistic number is how someone gets committed to a run that does not end.

**`budget_seconds` bounds searching, not grounding.** Verified against clingo 5.8.2: a solve handle
cancels promptly, while `interrupt()` during grounding is ignored and grounding runs to completion.
Since grounding is the worst-case-exponential half, the budget catches a hard *search* — it is not
protection against a grounding blow-up. For a hard kill around everything, run the pipeline through
`run_pipeline`, which uses a subprocess.

Exceeding the budget raises `RuleBudgetExceeded`. **That is not `INFEASIBLE`** — UNSAT is a proof
that no model exists, while a timeout proves nothing at all, and treating them alike would turn "we
gave up" into "your architecture is impossible".

`validate()` also reports `unpinned-program` when a program generates candidates and either states
no optimisation, or has only constant weights. It is a syntactic check, not a proof; solving is what
actually establishes ambiguity, and only for the facts it was given.

### Where to put the decision

**Keep the rules off the feedback loop.** Inside a cycle they are applied once per sweep, on values
that have not settled — so the choice depends on the solver's path rather than on a result, and a
discrete choice inside a loop can leave it oscillating or give it several stable answers that each
report success. `validate()` reports this as `rules-in-cycle`.

What decides the topology is **one word**: whether a fact is named after the loop's own variable.

```python
facts=["mass_band"]          # edge back into the loop -> rules run every sweep
facts=["prior_mass_band"]    # separate input -> rules sit on the linear part
```

The recommended shape is *decide → evaluate completely → revise*: run the pipeline with the
decision fixed, read the new facts off the converged result, and loop in ordinary Python. Because
the decision set is finite and the rules are deterministic, **a repeated decision set is a cycle** —
so memoising what you have seen gives you termination, not just a retry cap.

**Deeper:** [`rule_backed_discipline_demo.py`](../scripts/rule_backed_discipline_demo.py) section 6,
and [design/002](design/002-agent-as-discipline.md).

**Deeper:** [`rule_backed_discipline_demo.py`](../scripts/rule_backed_discipline_demo.py)

---

## side-effects

A step inside a cyclic block runs **once per sweep**. For a numeric discipline that is the point;
for one that writes a file, launches a subprocess or posts to an API it is something else — and
unlike every other mistake here, you cannot undo it. So declare it:

```python
from smartmdao import Pipeline, HybridSolver, SideEffectError, validate

def loop(effects):
    sent = []
    pipeline = Pipeline(solver=HybridSolver(max_iterations=80))

    @pipeline.step(outputs=["notified"], effects=effects)
    def notify(mass: float) -> float:
        sent.append(mass)                    # imagine: email.send(...)
        return mass

    @pipeline.step(outputs=["mass"])
    def size(notified: float) -> float:
        return notified * 0.5 + 10.0

    return pipeline, sent

pipeline, sent = loop(effects=True)
assert "side-effect-in-cycle" in [f.code for f in validate(pipeline)]

try:
    pipeline.run(mass=0.0)                   # refused BEFORE anything runs
except SideEffectError:
    pass
assert sent == []
```

| Value | Meaning | In a loop |
|---|---|---|
| *absent* | Undeclared; assumed pure | runs every sweep, silently |
| `effects=True` | Touches the world, loop behaviour **not stated** | **refused** (`SideEffectError`) |
| `effects="once"` | Run on the first sweep of a `run()`, reuse the result | latched — see below |
| `effects="every-sweep"` | Run every sweep — you meant it | runs every sweep |

**`"once"` changes the answer.** A step inside a cyclic block feeds the loop back — that is what
being in the cycle means — so freezing its output freezes a coupling. The loop converges against
the first-sweep value, not its own fixed point, **and still reports `converged`**. `validate()`
reports it as `side-effect-latched`.

```python
from smartmdao import Pipeline, HybridSolver, validate

def loop(effects):                           # each snippet here runs on its own
    sent = []
    pipeline = Pipeline(solver=HybridSolver(max_iterations=80))

    @pipeline.step(outputs=["notified"], effects=effects)
    def notify(mass: float) -> float:
        sent.append(mass)
        return mass

    @pipeline.step(outputs=["mass"])
    def size(notified: float) -> float:
        return notified * 0.5 + 10.0

    return pipeline, sent

every, _ = loop(effects="every-sweep")
once, sent = loop(effects="once")

assert abs(every.run(mass=0.0)["mass"] - 20.0) < 1e-5   # the real fixed point
assert once.run(mass=0.0)["mass"] == 10.0           # latched: different, and "converged"
assert len(sent) == 1
assert "side-effect-latched" in [f.code for f in validate(once)]
```

**The fix is structural: keep the loop pure, put the effect after it.** Split the step — the pure
part iterates, the side effect sits on the linear part and runs once on the converged result:

```python
from smartmdao import Pipeline, HybridSolver, validate

sent = []
split = Pipeline(solver=HybridSolver(max_iterations=80))

@split.step(outputs=["relayed"])
def relay(mass: float) -> float:
    return mass

@split.step(outputs=["mass"])
def size(relayed: float) -> float:
    return relayed * 0.5 + 10.0

@split.step(outputs=["receipt"], effects=True)      # after the loop: runs once
def notify(mass: float) -> str:
    sent.append(mass)
    return "sent"

assert abs(split.run(mass=0.0)["mass"] - 20.0) < 1e-5 and len(sent) == 1
assert validate(split, inputs=["mass"]) == ()
```

**Anything that multiplies runs refuses any declared effect** unless you pass `allow_effects=True`:
`PipelineEvaluator` (an optimizer runs the pipeline once per evaluation, and `"once"` is scoped to a
single run) and `compare_runs` (it executes both files, so everything would run twice).

```python
from smartmdao import Pipeline, PipelineEvaluator, SideEffectError

pipeline = Pipeline()

@pipeline.step(outputs=["y"], effects="every-sweep")
def post_result(x: float) -> float:
    return x

try:
    PipelineEvaluator(pipeline, design_vars=["x"])
except SideEffectError as error:
    assert "allow_effects=True" in str(error)

PipelineEvaluator(pipeline, design_vars=["x"], allow_effects=True)      # the explicit yes
```

Four more things worth knowing:

- **The check is solver-aware.** `IterativeSolver` sweeps every step whether or not there is a
  cycle, so an acyclic pipeline still repeats — and is still refused.
- **It is declared, never inferred.** A step returning `None` looks like a free signal and is not:
  a step can write a file *and* return a float.
- **A typo raises** rather than being reported: `effects="sometimes"` would silently declare a
  destructive step pure.
- **Analysing a file does not run it.** The MCP tools import the file to find the pipeline; a bare
  `pipeline.run(...)` at module level is suspended during that import. Put real runs under
  `if __name__ == "__main__":` anyway — it is what keeps importing a file free.

`explain()` lists what a pipeline touches outside itself. Full reasoning:
[design/005](design/005-side-effecting-steps.md); worked through with diagrams in
[`notebooks/15-side-effects.ipynb`](../notebooks/15-side-effects.ipynb).

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

**7. A translation can converge on a different rule than the original.** A hand-written loop that
tests one variable is not the same as a solver testing all of them, even though both "converge".
Set `target_var` to be faithful, or accept the difference knowingly — and prove which you got with
`compare_runs`. See [`scripts/translation_equivalence_demo.py`](../scripts/translation_equivalence_demo.py).

**8. A discipline wired to nothing is invisible at run time.** If your graph falls into separate
pieces, part of the pipeline cannot influence the result — and it will still converge, with correct
arithmetic, quietly ignoring whole inputs. This is the single most expensive mistake in this list
because everything *looks* fine. `validate()` reports it as `disconnected-graph`. It is a real
mistake made by a real agent asked for a wing model: it computed lift from span, chord and speed,
never compared it to the required lift, and returned a mass that was the same for any wing.

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
