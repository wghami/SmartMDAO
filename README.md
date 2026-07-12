# SmartMDAO 🚀

***Extensible MDAO framework with zero boilerplate integration and plug-and-play optimization.***

[![CI](https://github.com/wghami/SmartMDAO/actions/workflows/ci.yml/badge.svg)](https://github.com/wghami/SmartMDAO/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/smartmdao.svg)](https://pypi.org/project/smartmdao/)
[![Python versions](https://img.shields.io/pypi/pyversions/smartmdao.svg)](https://pypi.org/project/smartmdao/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/wghami/SmartMDAO/blob/main/LICENSE)

**SmartMDAO** is a lightweight, purely Pythonic framework for Multidisciplinary Design Analysis and Optimization (MDAO). Define your disciplines as plain Python functions, and SmartMDAO maps the dependency graph, converges cyclic feedback loops, caches expensive calls, and bridges straight into your optimizer of choice.

<p align="center">
  <img src="https://raw.githubusercontent.com/wghami/SmartMDAO/main/assets/sellar_mdao.svg" alt="Sellar Coupling Workflow" width="600"/>
</p>

## 🔍 See It In Action

**Without SmartMDAO** — you hand-write the convergence loop and track state yourself:

```python
y2 = 1.0  # initial guess
for _ in range(100):
    y1 = z1 ** 2 + z2 + x1 - 0.2 * y2
    y2_next = math.sqrt(abs(y1)) + z1 + z2
    if abs(y2_next - y2) < 1e-6:
        break
    y2 = y2_next
```

**With SmartMDAO** — declare each discipline once; the y1 ↔ y2 cycle is found and converged for you:

```python
@pipeline.step(outputs=["y1"])
def discipline_1(z1, z2, x1, y2): return z1 ** 2 + z2 + x1 - 0.2 * y2

@pipeline.step(outputs=["y2"])
def discipline_2(z1, z2, y1): return math.sqrt(abs(y1)) + z1 + z2

pipeline.run(z1=1.0, z2=1.0, x1=1.0, y2=1.0)
```

# 🌟 Why SmartMDAO?

- **Effortless MDA** — no DSL, just `@pipeline.step` and standard type hints. Works with plain types, dataclasses, or your own objects.
- **Convergence Beyond Numbers** — coupling variables don't have to be floats. Dicts, sets, dataclasses, or any equatable type converge too: SmartMDAO falls back to structural equality when there's no numeric residual to drive to zero, so a feedback loop over a negotiated plan or a resolved dependency set converges exactly like a numeric MDA loop does. See it in action below.
- **Built-in Caching** — layer `@cached` (RAM, HDF5, Pickle) onto expensive functions for instant speedups.
- **Agnostic MDO** — define your `OptimizationProblem` once, then run it through any backend with a single string: `optimize(problem, backend="scipy")` or `backend="openturns"`. Bring your own via `@register_backend`, or drop straight to `PipelineEvaluator` for full control.
- **Type-Safe** — static validation catches mismatched disciplines before a single step runs; opt-in runtime checks catch the rest.
- **Built Also for Researchers** — solvers are plain `Protocol` classes, so custom MDA convergence algorithms drop in without touching the core framework.

## 🔄 Convergence Beyond Numbers

OpenMDAO and GEMSEO converge coupled disciplines by driving a *numeric* residual (`|Δ|` between
successive floats) to zero. That assumption breaks the moment a discipline exchanges something
that isn't a number — a resolved set of dependencies, a negotiated plan, a piece of state an AI
agent is refining across a feedback loop. SmartMDAO's `StandardConvergenceChecker` falls back to
structural equality for non-numeric values: "did this value change since the last iteration?" is
a perfectly good convergence criterion when there's no derivative to speak of.

```python
from smartmdao import Pipeline, IterativeSolver

pipeline = Pipeline(solver=IterativeSolver(max_iterations=10))

depends_on = {
    "billing": frozenset({"database", "auth"}),
    "auth": frozenset({"database"}),
}

@pipeline.step(outputs=["enabled"])
def resolve_dependencies(requested: frozenset, enabled: frozenset) -> frozenset:
    expanded = set(enabled) | set(requested)
    for feature in list(expanded):
        expanded |= depends_on.get(feature, frozenset())
    return frozenset(expanded)

result = pipeline.run(requested=frozenset({"billing"}), enabled=frozenset())
# result["enabled"] -> frozenset({"billing", "auth", "database"}) - converged in
# 2 iterations, with not a single float anywhere in the coupling variable.
```

Bring your own equatable type — a `dict`, a `set`, a frozen `@dataclass` — and the same
`IterativeSolver`/`HybridSolver` machinery converges it, no numeric tolerance required.

<details>
<summary><strong>⚡ Full Quick Start: Caching, Constraints & Optimization (click to expand)</strong></summary>

Here is how easily you can solve the classic Sellar coupled problem end-to-end, from caching through optimization - swapping the solver backend with a single string.

``` python
import math
import logging
from smartmdao import (
    Pipeline,
    HybridSolver,
    PipelineEvaluator,
    OptimizationProblem,
    ConstraintSpec,
    optimize,
    cached,
    MemoryBackend,
    configure_logging
)

# --- Setup Logging and Cache ---
configure_logging(level=logging.WARNING)
mem_cache = MemoryBackend() # HDF5 and Pickle also available

# ==============================================================================
# PART 1: Initialize the Pipeline with the HybridSolver
# ==============================================================================
# The HybridSolver automatically detects and converges cyclic dependencies
pipeline = Pipeline(
    solver=HybridSolver(max_iterations=100, tolerance=1e-6)
)

# ==============================================================================
# PART 2: Define the Sellar Disciplines (MDA)
# ==============================================================================
@pipeline.step(outputs=["y1"])
@cached(mem_cache) # Instantly cache this discipline to speed up evaluations
def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
    return (z1 ** 2) + z2 + x1 - (0.2 * y2)

@pipeline.step(outputs=["y2"])
@cached(mem_cache) 
def discipline_2(z1: float, z2: float, y1: float) -> float:
    return math.sqrt(abs(y1)) + z1 + z2

@pipeline.step(outputs=["objective"])
@cached(mem_cache) 
def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
    return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)

@pipeline.step(outputs=["constraint_1"])
@cached(mem_cache) 
def compute_constraint_1(y1: float) -> float:
    """Constraint formulation: 3.16 - y1 <= 0"""
    return 3.16 - y1

@pipeline.step(outputs=["constraint_2"])
@cached(mem_cache) 
def compute_constraint_2(y2: float) -> float:
    """Constraint formulation: y2 - 24.0 <= 0"""
    return y2 - 24.0

# ==============================================================================
# PART 3: Bridge the Pipeline to an Optimizer-Agnostic Problem
# ==============================================================================
evaluator = PipelineEvaluator(
    pipeline=pipeline,
    design_vars=["z1", "z2", "x1"],
    constants={"y2": 1.0} # Initial guess to kick off the cycle
)

# Both backends expect h(x) >= 0; Sellar's constraints are naturally written
# as g(x) <= 0, so we flip the sign with multiplier=-1.0.
problem = OptimizationProblem(
    evaluator=evaluator,
    initial_guess=[1.0, 1.0, 1.0],
    bounds=[(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)],
    objective="objective",
    constraints=[
        ConstraintSpec(name="constraint_1", multiplier=-1.0),
        ConstraintSpec(name="constraint_2", multiplier=-1.0),
    ],
)

# ==============================================================================
# PART 4: Run the *same* problem through two different backends
# ==============================================================================
for backend_name in ("scipy", "openturns"):
    result = optimize(problem, backend=backend_name)
    print(f"[{backend_name:>9}] objective={result.objective_value:.4f}")
```

</details>

## 🧠 Advanced Examples

The Quick Start above just scratches the surface! Notably:

- **[`readme_quick_start.py`](https://github.com/wghami/SmartMDAO/blob/main/scripts/readme_quick_start.py)** — the complete version of the Quick Start above, including SciPy's raw `minimize()` call, full state extraction, and pipeline visualization.
- **[`optimizer_backends_demo.py`](https://github.com/wghami/SmartMDAO/blob/main/scripts/optimizer_backends_demo.py)** — define one `OptimizationProblem` and run the exact same Sellar problem through `scipy`, `openturns`, and a custom registered backend, just by swapping a string.
- **[`type_validation_demo.py`](https://github.com/wghami/SmartMDAO/blob/main/scripts/type_validation_demo.py)** — static and runtime type validation, `Optional`/`Union` support, and writing a custom `TypeChecker`.
- **[`non_numeric_convergence_demo.py`](https://github.com/wghami/SmartMDAO/blob/main/scripts/non_numeric_convergence_demo.py)** — two full non-numeric convergence cases: a single-discipline dependency closure over a `frozenset`, and a two-discipline negotiation over a shared `Plan` dataclass with an auto-detected `HybridSolver` cycle.

For deeper nesting, custom convergence solvers, or more complex multidisciplinary systems, check out the **[scripts folder in our GitHub repository](https://github.com/wghami/SmartMDAO/tree/main/scripts)**.

# 📦 Installation

SmartMDAO is available on PyPI. We recommend using uv for lightning-fast installation, but standard pip works perfectly.

Using `uv`:

``` bash
uv add smartmdao
```

Using `pip`:

``` bash
pip install smartmdao
```

*(Visualization is built in via matplotlib — no extra system packages required.)*

# 🤝 Contributing & License

Contributions are welcome! Please feel free to submit a Pull Request.This project is licensed under the MIT License - see the [LICENSE](https://github.com/wghami/SmartMDAO/blob/main/LICENSE) file for details.