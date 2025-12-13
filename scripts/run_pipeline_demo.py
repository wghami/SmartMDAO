import logging
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from smart_pipeline import Pipeline, configure_logging

# Initialize module-level logger
logger = logging.getLogger(__name__)

# ==========================================
# 1. Define Data Contracts (The "What")
# ==========================================
# Using Dataclasses is Pythonic because it creates explicit "contracts" 
# for what data is being passed around. It enables autocomplete in IDEs
# and makes the code self-documenting.

@dataclass
class InitialProcessingOutput:
    """Output contract for the first step."""
    y: np.ndarray

@dataclass
class IntermediateCalculationOutput:
    """Output contract for the second step."""
    b: np.ndarray
    c: np.ndarray

@dataclass
class FinalCalculationOutput:
    """Output contract for the final step."""
    z: np.ndarray


# ==========================================
# 2. Define Processing Steps (The "How")
# ==========================================
# Notice the Type Hints (e.g., -> InitialProcessingOutput). 
# The pipeline uses these hints to understand that 'step_1' produces 'y', 
# and 'step_2' needs 'y'. This is often called "Dependency Injection" or "Inference".

def step_1(x: np.ndarray) -> InitialProcessingOutput:
    """
    Takes raw input 'x', process it, and packages it into a structured output.
    """
    logger.debug(f"Executing step_1 with input x={x}")
    processed_value = x + 1
    return InitialProcessingOutput(y=processed_value)

def step_2(a: np.ndarray) -> IntermediateCalculationOutput:
    """
    Takes raw input 'a', process it, and packages it into a structured output.
    """
    logger.debug(f"Executing step_2 with input a={a}")
    intermediate_value = a ** 2
    return IntermediateCalculationOutput(b=intermediate_value, c=intermediate_value + 10)

def step_3(y: np.ndarray, b: np.ndarray) -> FinalCalculationOutput:
    """
    Requires 'y'. The pipeline automatically looks for a previous step 
    that outputs an object containing 'y'.
    """
    logger.debug(f"Executing step_3 with inputs y={y}, b={b}")
    final_value = y * 2 + b
    return FinalCalculationOutput(z=final_value)


# ==========================================
# 3. Execution Logic
# ==========================================
def run_pipeline_demo():
    # 0. Configure Logging
    configure_logging(level=logging.INFO)

    logger.info("--- Starting Pipeline ---")

    # Initialize the pipeline
    pipe = Pipeline()

    # Add steps.
    # We don't need `outputs=["y"]` because the Dataclass return type 
    # already tells the pipeline what variables are being created.
    pipe.add(step_1).add(step_2).add(step_3)

    # Visualize diagram dependency (Pop-up default)
    # We pass the list of inputs we INTEND to provide to see how they connect
    logger.info("Generating interactive diagram...")
    pipe.visualize(inputs=["x", "a"],
                   output_path=str(Path("results") / "pipeline_diagram.pdf"),
                   graph_type="flow",
                   view=False)

    # Run the pipeline
    # The pipeline sees step_1 needs 'x', so we must provide 'x'.
    results = pipe.run(x=np.array([3.0]), a=np.array([4.0]))

    logger.info("--- Pipeline Finished ---")
    logger.info(f"Input (x): {results['x']}")
    logger.info(f"Input (a): {results['a']}")
    logger.info(f"Step 1 (y): {results['y']} (x + 1)")
    logger.info(f"Step 2 (b): {results['b']} (a ** 2)")
    logger.info(f"Step 3 (z): {results['z']} (y * 2 + b)")
    logger.info("-" * 25)
    logger.info(f"Full State: {results}")


if __name__ == "__main__":
    # This block ensures the code only runs if executed directly,
    # not if imported as a module.
    run_pipeline_demo()