# SmartPipeline 🚀

SmartPipeline is a robust, Pythonic framework for building modular computational workflows. It moves beyond simple DAGs (Directed Acyclic Graphs) by supporting automatic dependency injection, cyclic/iterative solving, and integrated caching, all while keeping your code clean and readable using standard Python type hints.

Whether you are running linear data processing or highly coupled Multidisciplinary Design Optimization (MDO) problems, SmartPipeline dynamically maps your functions and solves them efficiently.

# 🌟 Key Features

- **Type-Hint Driven Dependency Injection**: No need to manually define edges. If `Step B` needs a variable `x` and `Step A` returns a variable named `x`, the pipeline connects them automatically.

- **We do the MDA, you do the MDO**: We handle the heavy lifting of Multidisciplinary Analysis (MDA) — automatically isolating and converging feedback loops. Because our architecture is completely optimizer-agnostic, you can plug our dynamic evaluator into SciPy, OpenTURNS, PyOptSparse, or any algorithm you prefer.

- **Hybrid Solver**: Automatically detects whether your pipeline is linear or contains feedback loops (cycles). It solves linear parts topologically and iterates over cyclic parts until convergence using Tarjan's Algorithm.

- **Modular Caching**: Built-in decorators (`@cached`) to cache step results in RAM, HDF5 (for large arrays), or Pickle (for complex objects) with zero boilerplate.

- **Visualization**: One-line generation of Graphviz diagrams (Flow charts or Data-Flow diagrams).

# 📦 Installation

We recommend using `uv` for lightning-fast installation, but standard `pip` works perfectly as well.

Using `uv` (Recommended):

``` bash
uv pip install git+[https://github.com/wghami/smart-pipeline.git](https://github.com/wghami/smart-pipeline.git)
```

Using standard `pip`:

``` bash
pip install git+[https://github.com/wghami/smart-pipeline.git](https://github.com/wghami/smart-pipeline.git)
```

*Note*: The visualization features require the `graphviz` system binary to be installed on your OS.

# ⚡ The Agnostic MDO Approach

`SmartPipeline` is built to let you define complex physics or engineering problems using pure, readable Python, without being locked into a specific optimization suite.

Here is how easily you can solve the classic **Sellar coupled problem** (a 2-discipline feedback loop) and optimize it using SciPy.

``` python
import math
from scipy.optimize import minimize
from smart_pipeline import Pipeline, HybridSolver
from smart_pipeline.optimization import PipelineEvaluator

# 1. We handle the heavy lifting: Multidisciplinary Analysis (MDA)
# The HybridSolver automatically detects the cyclic dependency between y1 and y2!
pipe = Pipeline(solver=HybridSolver())

@pipe.step(outputs=["y1"])
def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
    return (z1 ** 2) + z2 + x1 - (0.2 * y2)

@pipe.step(outputs=["y2"])
def discipline_2(z1: float, z2: float, y1: float) -> float:
    return math.sqrt(abs(y1)) + z1 + z2

@pipe.step(outputs=["objective"])
def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
    return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)


# 2. You choose the optimizer: Agnostic Multidisciplinary Optimization (MDO)
# Map the optimizer's numeric array to our named design variables
evaluator = PipelineEvaluator(
    pipeline=pipe,
    design_vars=["z1", "z2", "x1"],
    constants={"y2": 1.0}  # Initial guess to kick off the cycle
)

# Pass the dynamically generated objective functions to ANY optimizer (SciPy, OpenTURNS, etc.)
result = minimize(
    evaluator.get_objective("objective"), 
    x0=[1.0, 1.0, 1.0], 
    method='SLSQP', 
    bounds=[(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)]
)

print(f"Optimization Success! Objective: {result.fun:.4f}")
```

The user retains full control over the Pythonic equations, while the `PipelineEvaluator` caches the complex cyclic MDA evaluations so the optimizer can request objectives and constraints independently without performance penalties.

# 🛠️ Architecture Overview

- `core.py`: The entry point. Manages steps and delegates execution to solvers.

- `solvers.py`: The brains.

    - `DAGSolver`: For standard linear pipelines.

    - `IterativeSolver`: For fixed-point iteration problems.

    - `HybridSolver`: Uses Tarjan's Algorithm to decompose graphs into linear and cyclic components dynamically.

- `optimization.py`: Contains the stateful `PipelineEvaluator` bridge, providing callable factories for external optimizers.

- `executor.py:` Handles argument binding and runtime memory management.

- `visualization.py`: Generates high-quality PDF/PNG diagrams of your workflow.

# 🚀 Future Improvements & Roadmap

To make `SmartPipeline` even better, the following improvements are planned:

- **Parallel Execution**: Integrating `asyncio` or `ProcessPoolExecutor` to allow independent branches of the DAG to run in parallel.

- **Pydantic Integration**: Replace standard `dataclasses` with Pydantic models for robust runtime data validation and schema generation.

- **Checkpointing**: Allow the pipeline to pause and resume from a specific state in case of failure, serializing the entire memory dictionary.

- **Web UI**: A lightweight Flask/Streamlit dashboard to visualize pipeline progress and convergence plots in real-time.

# 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.