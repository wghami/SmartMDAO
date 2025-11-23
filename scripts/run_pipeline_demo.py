from dataclasses import dataclass
import numpy as np
from smart_pipeline.pipeline import Pipeline


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
class FinalCalculationOutput:
    """Output contract for the second step."""
    z: np.ndarray


# ==========================================
# 2. Define Processing Steps (The "How")
# ==========================================
# Notice the Type Hints (e.g., -> InitialProcessingOutput). 
# The pipeline uses these hints to understand that 'step_1' produces 'y', 
# and 'step_2' needs 'y'. This is often called "Dependency Injection" or "Inference".

def step_1(x: np.ndarray) -> InitialProcessingOutput:
    """
    Takes raw input 'x', adds 1, and packages it into a structured output.
    """
    processed_value = x + 1
    return InitialProcessingOutput(y=processed_value)


def step_2(y: np.ndarray) -> FinalCalculationOutput:
    """
    Requires 'y'. The pipeline automatically looks for a previous step 
    that outputs an object containing 'y'.
    """
    final_value = y * 2
    return FinalCalculationOutput(z=final_value)


# ==========================================
# 3. Execution Logic
# ==========================================
def run_pipeline_demo():
    print("--- Starting Pipeline ---")

    # Initialize the pipeline
    pipe = Pipeline()

    # Add steps.
    # We don't need `outputs=["y"]` because the Dataclass return type 
    # already tells the pipeline what variables are being created.
    pipe.add(step_1).add(step_2)

    # Create input data
    # Using numpy array as per the strict typing in the example
    input_data = np.array([3.0])

    # Run the pipeline
    # The pipeline sees step_1 needs 'x', so we must provide 'x'.
    results = pipe.run(x=input_data)

    print("--- Pipeline Finished ---")
    print(f"Input (x): {results['x']}")
    print(f"Step 1 (y): {results['y']} (x + 1)")
    print(f"Step 2 (z): {results['z']} (y * 2)")
    print("-" * 25)
    print(f"Full State: {results}")


if __name__ == "__main__":
    # This block ensures the code only runs if executed directly,
    # not if imported as a module.
    run_pipeline_demo()