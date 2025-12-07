from dataclasses import dataclass
import numpy as np
from smart_pipeline.core import Pipeline # Adjust import to your actual file

def run_decorators_usage():
    # 1. Initialize Pipeline FIRST
    pipe = Pipeline()

    # ==========================================
    # 2. Define Steps using Decorators
    # ==========================================

    # SCENARIO A: Standard Step (No arguments needed)
    # The decorator automatically adds this to 'pipe'
    @pipe.step
    def step_1(x: float):
        return x + 1

    # SCENARIO B: Tuple Unpacking (Needs explicit 'outputs')
    # We pass the outputs list just like we did in .add()
    @pipe.step(outputs=["y", "z"])
    def step_2(step_1: float):
        # Returns a tuple, so we need to tell the pipeline what the names are
        val_y = step_1 * 2
        val_z = step_1 * 3
        return val_y, val_z

    # SCENARIO C: Dataclass (Automatic inference)
    @dataclass
    class FinalResult:
        total: float

    @pipe.step
    def step_3(y: float, z: float) -> FinalResult:
        return FinalResult(total=y + z)
    
    print("Pipeline steps:", [s.name for s in pipe.steps])
    
    # pipe.visualize(inputs=["x"])
    
    result = pipe.run(x=10)
    print("Result:", result)

# ==========================================
# 3. Execution
# ==========================================
if __name__ == "__main__":
    # This block ensures the code only runs if executed directly,
    # not if imported as a module.
    run_decorators_usage()