import logging
from dataclasses import dataclass
from smart_pipeline import Pipeline, IterativeSolver, configure_logging

# Initialize module-level logger
logger = logging.getLogger(__name__)

def run_iterative_solver_demo():
    # 0. Configure Logging
    configure_logging(level=logging.INFO)

    # ==============================================================================
    # CASE 1: Standard Linear Pipeline (DAG)
    # ==============================================================================
    logger.info("=== CASE 1: Standard Linear Pipeline (DAG) ===")
    
    # 1. Dictionary return example
    def fetch_data(source): 
        logger.debug(f"Fetching data from {source}")
        return {"raw": [2, 4, 6], "meta": "raw_data"}
    
    @dataclass
    class Processed:
        clean_data: list
        avg: float

    # 2. Return Type Hint added! 
    # The pipeline reads '-> Processed' to know this function produces 'clean_data' and 'avg'
    def process(raw) -> Processed:
        logger.debug(f"Processing raw data: {raw}")
        return Processed(clean_data=[x*2 for x in raw], avg=sum(raw)/len(raw))

    def report(avg, clean_data):
        msg = f"Report: Average is {avg}, Data * 2: {clean_data}"
        return msg

    pipe_linear = Pipeline()
    
    # fetch_data returns dict, mapped to manual outputs
    pipe_linear.add(fetch_data, outputs=['raw', 'meta']) 
    pipe_linear.add(report)
    # process returns dataclass, auto-mapped via type hint
    pipe_linear.add(process) 

    res = pipe_linear.run(source="DB_PROD")
    logger.info(f"Result: {res['report']}")

    # ==============================================================================
    # CASE 2: Iterative Feedback (Babylonian Sqrt)
    # ==============================================================================
    logger.info("=== CASE 2: Iterative Feedback (Babylonian Sqrt) ===")
    
    def update_guess(x, S):
        new_x = 0.5 * (x + S/x)
        # We log the iteration step. 
        # Using INFO here so you can see the convergence in standard run mode.
        logger.info(f"  Guess update: {new_x:.4f}")
        return new_x

    # Configure the solver specifically for this loop
    solver = IterativeSolver(max_iterations=10, tolerance=0.0001, target_var='x')
    pipe_loop = Pipeline(solver=solver)
    
    pipe_loop.add(update_guess, outputs=['x']) 
    
    initial_guess = 10.0
    S = 36.0
    
    logger.info(f"Starting calculation for Sqrt({S}) with initial guess {initial_guess}...")
    result_loop = pipe_loop.run(x=initial_guess, S=S) 
    
    logger.info(f"Square root of {S} = {result_loop['x']}")

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
if __name__ == "__main__":
    run_iterative_solver_demo()