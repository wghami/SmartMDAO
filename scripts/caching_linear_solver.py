import logging
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from smartmdao import Pipeline, configure_logging
from smartmdao.cache import (
    MemoryBackend,
    HDF5Backend,
    PickleDiskBackend,
    cached
)

# --- Setup Logging ---
logger = logging.getLogger(__name__)

# ==========================================
# 0. Global Configuration
# ==========================================
# We define paths and backends globally so decorators can access them at import time.
# However, we defer the creation of folders/files to the main execution function
# to avoid side effects during simple imports.

CACHE_DIR = Path("results/cache")

# MemoryBackend: Extremely fast, but data is lost when script ends.
ram_cache = MemoryBackend()

# HDF5Backend: Stores large arrays on disk.
# We define the path, but the file is created only when used.
h5_path = CACHE_DIR / "pipeline_cache.h5"
h5_cache = HDF5Backend(str(h5_path)) 

# PickleBackend: Stores complex objects.
pickle_path = CACHE_DIR / "pipeline_pickle_cache"
pickle_cache = PickleDiskBackend(str(pickle_path))


# ==========================================
# 1. Define Data Contracts
# ==========================================
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
# 2. Define Steps with Caching
# ==========================================

@cached(ram_cache)
def heavy_computation(x: int, size: int) -> ComputeResult:
    """
    Simulates a heavy math operation.
    """
    logger.debug(f"[Calculating] heavy_computation with x={x}, size={size}")
    return ComputeResult(val_sq=x * size)


@cached(h5_cache)
def generate_array(size: int) -> np.ndarray:
    # HDF5 only accepts raw arrays. We cannot return a Dataclass here.
    logger.debug(f"[Generating]  Array size {size} (HDF5)")
    return np.ones((size, size)) * 0.5


def summarize(arr: np.ndarray) -> SummaryResult:
    """
    Aggregates the results. Not cached.
    """
    logger.debug("[Summarizing] Running summary (Not Cached)")
    total = float(np.sum(arr))
    return SummaryResult(sum_val=total)


@cached(pickle_cache)
def generate_metadata(x: int, val_sq: float) -> MetadataResult:
    """
    Creates a complex Python object (dictionary).
    """
    logger.debug(f"[Pickling]    Generating metadata for x={x}")
    
    complex_data = {
        "experiment_id": f"EXP-{x}",
        "parameters": [x, val_sq],
        "valid": True,
        "tags": {"type": "demo", "backend": "pickle"}
    }
    
    return MetadataResult(meta_info=complex_data)

# ==========================================
# 3. Main Execution Function
# ==========================================
def run_caching_demo():
    # 1. Configure Logging first
    # Try changing to logging.DEBUG to see the "[Calculating]" messages!
    configure_logging(level=logging.INFO)
    
    # 2. Setup Infrastructure (Side Effects)
    # We create directories here, not at the top level
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"--- Caching Location: {CACHE_DIR.resolve()} ---")

    # 3. Build Pipeline
    pipe = Pipeline()
    (
        pipe
        .add(summarize)
        .add(heavy_computation)
        .add(generate_metadata)
        .add(generate_array, outputs=["arr"]) 
    )

    # 4. Run Scenarios
    logger.info("--- Run 1: Cold Start ---")
    logger.info("The cache is empty (or input is new), so functions must execute.")
    
    res1 = pipe.run(x=10, size=5)
    logger.info(f"Metadata: {res1['meta_info']}") 
    logger.info(f"Result: {res1['sum_val']}")


    logger.info("--- Run 2: Hot Cache (Identical Inputs) ---")
    logger.info("Inputs are exactly the same (x=10, size=5).")
    logger.info("You should NOT see '[Calculating]' or '[Generating]' logs below (unless DEBUG is on).")
    
    res2 = pipe.run(x=10, size=5)
    logger.info(f"Metadata: {res2['meta_info']} (Loaded from disk)")
    logger.info(f"Result: {res2['sum_val']}")


    logger.info("--- Run 3: Cache Miss (Changed Inputs) ---")
    logger.info("We changed 'x'. 'heavy_computation' must recalculate.")
    logger.info("However, 'size' is still 5. Does 'generate_array' run?")
    
    res3 = pipe.run(x=5, size=5)
    logger.info(f"Result: {res3['sum_val']}")


if __name__ == "__main__":
    run_caching_demo()