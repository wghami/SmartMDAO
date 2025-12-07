from pathlib import Path
import numpy as np
from dataclasses import dataclass
from smart_pipeline.core import Pipeline
from smart_pipeline.cache import (
    MemoryBackend,
    HDF5Backend,
    PickleDiskBackend,
    cached
)


# ==========================================
# 0. Setup Paths with Pathlib
# ==========================================

# Define the directory relative to your current working directory (Root)
# This creates a Path object: my_project/results/cache
cache_dir = Path("results/cache")

# Create the directory if it doesn't exist.
# parents=True -> creates 'results' if it's missing too.
# exist_ok=True -> doesn't crash if the folder already exists.
cache_dir.mkdir(parents=True, exist_ok=True)

print(f"--- Caching Location: {cache_dir.resolve()} ---")

# ==========================================
# 1. Define Data Contracts
# ==========================================
# By using Dataclasses, we don't need to manually type 
# outputs=["val_sq"] or outputs=["arr"] later.

@dataclass
class ComputeResult:
    val_sq: float

@dataclass
class SummaryResult:
    sum_val: float

@dataclass
class MetadataResult:
    meta_info: dict


# ==========================================
# 2. Configure Cache Backends
# ==========================================
# We define these outside the functions so the decorators can access them.

# MemoryBackend: Extremely fast, but data is lost when script ends.
# Good for small variables or immediate re-use.
ram_cache = MemoryBackend()

# HDF5Backend: Stores large arrays on disk. 
# Good for avoiding re-computation of large datasets between different runs.
# (Requires 'h5py' installed)
h5_path = cache_dir / "pipeline_cache.h5"
h5_cache = HDF5Backend(str(h5_path)) 

# This creates a folder named 'pipeline_pickle_cache' on your disk
pickle_path = cache_dir / "pipeline_pickle_cache"
pickle_cache = PickleDiskBackend(str(pickle_path))

# ==========================================
# 3. Define Steps with Caching
# ==========================================

@cached(ram_cache)
def heavy_computation(x: int, size: int) -> ComputeResult:
    """
    Simulates a heavy math operation.
    Cached in RAM: If run again with same inputs in this session, 
    it returns immediately without executing the function body.
    """
    print(f"  [Calculating] heavy_computation with x={x}, size={size}")
    return ComputeResult(val_sq=x * size)


@cached(h5_cache)
def generate_array(size: int) -> np.ndarray:
    # --- FIX IS HERE ---
    # HDF5 only accepts raw arrays. We cannot return a Dataclass here.
    # We return the numpy array directly.
    print(f"  [Generating]  Array size {size} (HDF5)")
    return np.ones((size, size)) * 0.5


def summarize(arr: np.ndarray) -> SummaryResult:
    """
    Aggregates the results.
    Not cached: This runs every time.
    """
    print("  [Summarizing] Running summary (Not Cached)")
    total = float(np.sum(arr))
    return SummaryResult(sum_val=total)


@cached(pickle_cache)
def generate_metadata(x: int, val_sq: float) -> MetadataResult:
    """
    Creates a complex Python object (dictionary).
    
    Why Pickle?
    HDF5 hates dictionaries and strings. Memory forgets them on exit.
    Pickle serializes this structure to disk so it persists.
    """
    print(f"  [Pickling]    Generating metadata for x={x}")
    
    # A complex object that isn't just a numpy array
    complex_data = {
        "experiment_id": f"EXP-{x}",
        "parameters": [x, val_sq],
        "valid": True,
        "tags": {"type": "demo", "backend": "pickle"}
    }
    
    return MetadataResult(meta_info=complex_data)

# ==========================================
# 4. Execution Logic
# ==========================================
def run_caching_demo():
    pipe = Pipeline()

    # Clean Pipeline definition without manual string names
    (
        pipe
        .add(summarize)  # <-- to illustrate that order doesn't matter
        .add(heavy_computation)
        .add(generate_metadata)
        .add(generate_array, outputs=["arr"]) # Manually name the raw array
    )

    print("--- Run 1: Cold Start ---")
    print("The cache is empty (or input is new), so functions must execute.")
    # 'heavy_computation' depends on x, size
    # 'generate_array' depends on size
    res1 = pipe.run(x=10, size=5)
    print(f"Metadata: {res1['meta_info']}") 
    print(f"Result: {res1['sum_val']}\n")


    print("--- Run 2: Hot Cache (Identical Inputs) ---")
    print("Inputs are exactly the same (x=10, size=5).")
    print("You should NOT see '[Calculating]' or '[Generating]' prints below.")
    res2 = pipe.run(x=10, size=5)
    print(f"Metadata: {res2['meta_info']} (Loaded from disk)")
    print(f"Result: {res2['sum_val']}\n")


    print("--- Run 3: Cache Miss (Changed Inputs) ---")
    print("We changed 'x'. 'heavy_computation' must recalculate.")
    print("However, 'size' is still 5. Does 'generate_array' run?")
    # Note: 'generate_array' only depends on 'size'. Since 'size' didn't change,
    # the pipeline might still pull the array from HDF5!
    res3 = pipe.run(x=5, size=5)
    print(f"Result: {res3['sum_val']}\n")


if __name__ == "__main__":
    run_caching_demo()