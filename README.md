# SmartMDAO 🚀

**SmartMDAO** is a lightweight, purely Pythonic framework for Multidisciplinary Design Analysis and Optimization (MDO).

Define your physics and engineering models, and let SmartMDAO handle the complex dependency mapping, cyclic feedback loops, and caching. Once your Multidisciplinary Analysis (MDA) is built, seamlessly plug it into your favorite optimization library.

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

*(Note: Visualization features require the graphviz system binary to be installed on your OS).*

### Installing Graphviz (Optional System Requirement)

While `smartmdao` works perfectly on its own, generating pipeline diagrams requires the `graphviz` system binary to be installed on your OS.

**macOS (Homebrew):**
```bash
brew install graphviz
```

**Linux (Ubuntu/Debian):**
``` bash
sudo apt-get install graphviz
```

**Windows (winget):**
``` bash
winget install graphviz
```

*(Alternatively, you can download the Windows installer directly from the [official Graphviz website](https://graphviz.org/download/))*

# 🌟 Why SmartMDAO? (The 3 Core Strengths)

1. **Effortless MDA via Decorators & Auto-Caching** No need to manually define complex graph edges or learn a heavy Domain-Specific Language. If `Step B` needs variable `x` and `Step A` returns `x`, SmartMDAO connects them automatically using standard Python type hints. Wrap your functions in `@pipe.step` and add our built-in `@cached` decorators (RAM, HDF5, Pickle) to effortlessly speed up evaluations.

2. **Plug-and-Play MDO (Bring Your Own Optimizer)** We do the MDA, you do the MDO. Because our architecture relies on standard Python types and dictionaries, it is completely optimizer-agnostic. Use our `PipelineEvaluator` bridge to cache cyclic MDA evaluations, and pass your objective functions, constraints, etc directly to SciPy, OpenTURNS, PyOptSparse, or any other algorithm you prefer.

3. **Also Built for Researchers (Highly Extensible)** SmartMDAO is designed to be a sandbox for innovation. Our solvers are built on standard Python `Protocol` interfaces. If you want to research and implement your own custom MDA convergence algorithms, you can easily write a new solver class and inject it into the pipeline without touching the core framework.

# ⚡ Quick Start: The Agnostic MDO Approach

Here is how easily you can solve the classic Sellar coupled problem (a 2-discipline feedback loop) and optimize it using standard `scipy`.

``` python
import math
from scipy.optimize import minimize
from smartmdao import Pipeline, HybridSolver, PipelineEvaluator

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

# Pass the dynamically generated objective functions to ANY optimizer
result = minimize(
    evaluator.get_objective("objective"), 
    x0=[1.0, 1.0, 1.0], 
    method='SLSQP', 
    bounds=[(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)]
)

print(f"Optimization Success! Objective: {result.fun:.4f}")
```

# 🛠️ Architecture Overview

- `core.py`: The entry point. Manages steps and delegates execution to solvers.
- `solvers.py`: The brains of the operation.
    - `DAGSolver`: For standard linear pipelines.
    - `IterativeSolver`: For fixed-point iteration problems.
    - `HybridSolver`: Uses Tarjan's Algorithm to dynamically decompose graphs into linear and cyclic components.
- `optimization.py`: Contains the stateful `PipelineEvaluator` bridge, providing callable factories for external optimizers.
- `visualization.py`: Generates high-quality Graphviz diagrams (Data-Flow or Process-Flow) of your workflow in one line of code.

# 🚀 Roadmap

**Parallel Execution**: Integrating `asyncio` or `ProcessPoolExecutor` to allow independent branches of the DAG to run in parallel.

**Pydantic Integration**: Replacing standard `dataclasses` with Pydantic models for robust runtime data validation.

**Checkpointing**: Allowing pipelines to pause and resume from a specific state in case of failure.

# 🤝 Contributing & License

Contributions are welcome! Please feel free to submit a Pull Request.This project is licensed under the MIT License - see the [LICENSE](https://github.com/wghami/SmartMDAO/blob/main/LICENSE) file for details.