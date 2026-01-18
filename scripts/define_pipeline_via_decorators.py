import logging
from dataclasses import dataclass
from pathlib import Path
from smart_pipeline import Pipeline, configure_logging

# Initialize module-level logger
logger = logging.getLogger(__name__)

def run_decorators_usage():
    # 0. Configure logging
    configure_logging(level=logging.INFO)
    
    # 1. Initialize Pipeline FIRST
    pipe = Pipeline()

    # ==========================================
    # 2. Define Steps using Decorators
    # ==========================================

    # SCENARIO A: Standard Step (No arguments needed)
    # The decorator automatically adds this to 'pipe' with the output name 'step_1'
    @pipe.step
    def step_1(x: float):
        result = x + 1
        logger.debug(f"Executing step_1: {x} + 1 = {result}")
        return result

    # SCENARIO B: Tuple Unpacking (Needs explicit 'outputs')
    # We pass the outputs list just like we did in .add()
    @pipe.step(outputs=["y", "z"])
    def step_2(step_1: float):
        # Returns a tuple, so we need to tell the pipeline what the names are
        val_y = step_1 * 2
        val_z = step_1 * 3
        logger.debug(f"Executing step_2: Unpacking {step_1} -> y={val_y}, z={val_z}")
        return val_y, val_z

    # SCENARIO C: Dataclass for automatic inference
    @dataclass
    class FinalResult:
        total: float

    @pipe.step
    def step_3(y: float, z: float) -> FinalResult:
        logger.debug(f"Executing step_3: Summing {y} + {z}")
        return FinalResult(total=y + z)
    
    # Log the registered steps
    logger.info(f"Pipeline steps registered: {[s.name for s in pipe.steps]}")
    
    pipe.visualize(inputs=["x"],  # <-- if not provided, pipeline tries to infer it
                   output_path = str(Path("results") / f"{str(Path(__file__).stem)}.pdf"),
                   orientation = "LR",
                   graph_type = "bipartite",
                   view = False)
    
    # Run the pipeline
    logger.info("Starting pipeline execution with x=10...")
    result = pipe.run(x=10)
    
    logger.info(f"Final Result Dictionary: {result}")
    # Accessing the specific dataclass field if present in memory
    if 'total' in result:
        logger.info(f"Computed Total: {result['total']}")

# ==========================================
# 3. Execution
# ==========================================
if __name__ == "__main__":
    # This block ensures the code only runs if executed directly,
    # not if imported as a module.
    run_decorators_usage()