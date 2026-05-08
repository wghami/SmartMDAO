# SmartMDAO 🚀

***Extensible MDAO framework with zero boilerplate integration and plug-and-play optimization.***

**SmartMDAO** is a lightweight, purely Pythonic framework for Multidisciplinary Design Analysis and Optimization (MDAO).

Define your physics and engineering models, and let SmartMDAO handle the complex dependency mapping, cyclic feedback loops, and caching. Once your Multidisciplinary Analysis (MDA) is built, seamlessly plug it into your favorite optimization library.


# 🌟 Why SmartMDAO?

1. **Effortless MDA**: No heavy Domain-Specific Languages. Just use `@pipeline.step` and standard Python type hints. SmartMDAO automatically maps dependencies and resolves cyclic feedback loops. Compatible with **with any standard Python types, such as strings and user-specific objects**.

2. **Built-in Caching**: Speed up your pipelines instantly by layering `@cached` decorators (RAM, HDF5, Pickle) onto expensive functions.

3. **Agnostic MDO**: Bring your own optimizer. Our `PipelineEvaluator` bridge works flawlessly with SciPy, OpenTURNS, PyOptSparse, or any other algorithm you prefer.

4. **Also Built for Researchers (Highly Extensible)** SmartMDAO is designed to be a sandbox for innovation. Our solvers are built on standard Python `Protocol` interfaces. If you want to research and implement your own custom MDA convergence algorithms, you can easily write a new solver class and inject it into the pipeline without touching the core framework.

# ⚡ Quick Start: Caching, Constraints, and Optimization

Here is how easily you can solve the classic Sellar coupled problem.

<p align="center">
  <img src="assets/sellar_mdao.svg" alt="Sellar Coupling Workflow" width="600"/>
</p>

Notice how SmartMDAO automatically converges the $y_1 \leftrightarrow y_2$ cyclic dependency, caches the heavy lifting, and elegantly bridges to SciPy - even allowing you to flip constraint signs on the fly.

``` python
import math
import logging
from scipy.optimize import minimize
from smartmdao import (
    Pipeline,
    HybridSolver,
    PipelineEvaluator,
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
# PART 3: Setup the Evaluator Bridge
# ==============================================================================
# Map the optimizer's numeric array back to our named design variables
evaluator = PipelineEvaluator(
    pipeline=pipeline,
    design_vars=["z1", "z2", "x1"],
    constants={"y2": 1.0} # Initial guess to kick off the cycle
)

# Setup Optimizer parameters
initial_guess = [1.0, 1.0, 1.0] 
bounds = [(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)]

# Constraints for scipy.optimize
# SciPy expects f(x) >= 0. Since our pipeline outputs f(x) <= 0, we use multiplier=-1.0
# The evaluator returns a callable function so the optimizer can access it
cons = [
    {'type': 'ineq', 'fun': evaluator.get_constraint("constraint_1", multiplier=-1.0)},
    {'type': 'ineq', 'fun': evaluator.get_constraint("constraint_2", multiplier=-1.0)}
]

# ==============================================================================
# PART 4: Run Optimization (MDO)
# ==============================================================================
print(f"Starting scipy optimization from initial guess: {initial_guess}")
result = minimize(
    evaluator.get_objective("objective"), 
    initial_guess, 
    method='SLSQP', 
    bounds=bounds, 
    constraints=cons,
    options={'disp': True, 'ftol': 1e-6}
)

# ==============================================================================
# PART 5: Extract Full State at Optimum
# ==============================================================================
# By passing the optimal 'x' back to the evaluator, we retrieve the full dictionary
# of intermediate variables, constraints, and objective values.
# Results automatically recovered from cache (no additional run).
# Change log to DEBUG to see cache hit. 
optimal_state = evaluator.evaluate(result.x)

print("\nOptimization Success! Final State:")
for key, value in optimal_state.items():
    if isinstance(value, float):
        print(f"  {key}: {value:.4f}")
    else:
        print(f"  {key}: {value}")

# Optional - visualizing the workflow
pipeline.visualize(inputs=["z1", "z2", "x1"],  # <-- if not provided, pipeline tries to infer it
    output_path = str("sellar_mdao.svg"), # choose your format svg, pdf, png
    orientation = "LR",
    graph_type = "bipartite",
    view = False)
```

## 🧠 Advanced Examples

The Quick Start above just scratches the surface! If you want to see how SmartMDAO handles deeper nesting, custom optimization solvers, or more complex multidisciplinary systems, check out the **[scripts folder in our GitHub repository](https://github.com/wghami/SmartMDAO/tree/main/scripts)**.

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

# 🤝 Contributing & License

Contributions are welcome! Please feel free to submit a Pull Request.This project is licensed under the MIT License - see the [LICENSE](https://github.com/wghami/SmartMDAO/blob/main/LICENSE) file for details.