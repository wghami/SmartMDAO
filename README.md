# SmartPipeline 🚀

**SmartPipeline** is a robust, Pythonic framework for building modular computational workflows. It moves beyond simple DAGs (Directed Acyclic Graphs) by supporting **automatic dependency injection**, **cyclic/iterative solving**, and **integrated caching**, all while keeping your code clean and readable using standard Python type hints.

## 🌟 Key Features

* **Type-Hint Driven Dependency Injection**: No need to manually define edges. If `Step B` needs a variable `x` and `Step A` returns a Dataclass containing `x`, the pipeline connects them automatically.

* **Hybrid Solver**: Automatically detects whether your pipeline is linear or contains feedback loops (cycles). It solves linear parts topologically and iterates over cyclic parts until convergence.

* **Modular Caching**: Built-in decorators (`@cached`) to cache step results in RAM, HDF5 (for large arrays), or Pickle (for complex objects) with zero boilerplate.

* **Visualization**: One-line generation of professional Graphviz diagrams (Flow charts or Data-Flow diagrams).

* **Integrated Logging**: Unified, configurable logging for debugging execution flows and solver convergence.

## 📦 Installation

1. **Clone the repository:**

   ```
   git clone [https://github.com/yourusername/smart-pipeline.git](https://github.com/yourusername/smart-pipeline.git)
   cd smart-pipeline
   ```

2. **Install dependencies:**
   It is recommended to use a virtual environment.

   ```
   pip install numpy matplotlib graphviz h5py
   ```

   *(Note: `graphviz` also requires the system-level binary to be installed on your OS).*

## ⚡ Quick Start

### 1. Basic Linear Pipeline

Create steps using standard Python functions or Dataclasses.

```python
from dataclasses import dataclass
from smart_pipeline import Pipeline

@dataclass
class ProcessedData:
    clean_value: float

# Step 1: Returns a Dataclass (Pipeline infers output name 'clean_value')
def process(raw_val: float) -> ProcessedData:
    return ProcessedData(clean_value=raw_val * 1.5)

# Step 2: Requests 'clean_value' (Pipeline auto-connects to Step 1)
def analyze(clean_value: float):
    print(f"Analysis Result: {clean_value + 10}")

# Execution
pipe = Pipeline()
pipe.add(process).add(analyze)
pipe.run(raw_val=10.0)
```

### 2. Handling Cycles (Iterative Solving)

SmartPipeline shines with coupled systems (e.g., Physics simulations, economic models). Use the `HybridSolver` to auto-detect loops.

```python
from smart_pipeline import Pipeline, HybridSolver

# A system where X depends on Y, and Y depends on X
def calc_x(y, input_val):
    return (y + input_val) / 2

def calc_y(x):
    return x ** 0.5 + 2

pipe = Pipeline(solver=HybridSolver(tolerance=1e-5))
pipe.add(calc_x, outputs=["x"])
pipe.add(calc_y, outputs=["y"])

# Run with initial guesses
results = pipe.run(input_val=10.0, y=1.0, x=1.0)
print(f"Converged: x={results['x']:.4f}, y={results['y']:.4f}")
```

### 3. Caching

Persist results to disk automatically using decorators.

```python
from smart_pipeline.cache import HDF5Backend, cached

backend = HDF5Backend("cache/data.h5")

@cached(backend)
def expensive_simulation(param: int):
    # This runs once. Future calls with param=42 load from HDF5 instantly.
    return some_heavy_numpy_calculation(param)
```

## 🛠️ Architecture Overview

* **`core.py`**: The entry point. Manages steps and delegates execution to solvers.

* **`solvers.py`**: The brains.

  * `DAGSolver`: For standard linear pipelines.

  * `IterativeSolver`: For fixed-point iteration problems.

  * `HybridSolver`: Uses Tarjan's Algorithm to decompose graphs into linear and cyclic components dynamically.

* **`executor.py`**: Handles argument binding and runtime memory management.

* **`visualization.py`**: Generates high-quality PDF/PNG diagrams of your workflow.

## 🚀 Future Improvements & Roadmap

To make `SmartPipeline` even better, the following improvements are planned:

1. **Parallel Execution**:

   * Currently, the executor runs sequentially. Integrating `asyncio` or `ProcessPoolExecutor` would allow independent branches of the DAG to run in parallel.

2. **Pydantic Integration**:

   * Replace standard `dataclasses` with Pydantic models for robust runtime data validation and schema generation.

3. **Checkpointing**:

   * Allow the pipeline to pause and resume from a specific state in case of failure, serializing the entire memory dictionary.

4. **Web UI**:

   * A lightweight Flask/Streamlit dashboard to visualize pipeline progress and convergence plots in real-time.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.