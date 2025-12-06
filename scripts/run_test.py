from dataclasses import dataclass
from smart_pipeline.pipeline_test import Pipeline, IterativeSolver

# ==============================================================================
# 5. EXAMPLE USAGE
# ==============================================================================

if __name__ == "__main__":
    print("\n=== CASE 1: Standard Linear Pipeline (DAG) ===")
    
    # 1. Dictionary return example
    def fetch_data(source): 
        return {"raw": [2, 4, 6], "meta": "raw_data"}
    
    @dataclass
    class Processed:
        clean_data: list
        avg: float

    # 2. IMPORTANT: Return Type Hint added! 
    # The pipeline reads '-> Processed' to know this function produces 'clean_data' and 'avg'
    def process(raw) -> Processed:
        return Processed(clean_data=[x*2 for x in raw], avg=sum(raw)/len(raw))

    def report(avg, clean_data):
        return f"Report: Average is {avg}, Data * 2: {clean_data}"

    pipe_linear = Pipeline()
    
    # fetch_data returns dict, mapped to manual outputs
    pipe_linear.add(fetch_data, outputs=['raw', 'meta']) 
    pipe_linear.add(report)
    # process returns dataclass, auto-mapped via type hint
    pipe_linear.add(process) 

    res = pipe_linear.run(source="DB_PROD")
    print("Result:", res['report'])

    print("\n=== CASE 2: Iterative Feedback (Babylonian Sqrt) ===")
    
    def update_guess(x, S):
        new_x = 0.5 * (x + S/x)
        print(f"  Guess: {new_x:.4f}")
        return new_x

    solver = IterativeSolver(max_iterations=10, tolerance=0.0001, target_var='x')
    pipe_loop = Pipeline(solver=solver)
    
    pipe_loop.add(update_guess, outputs=['x']) 
    
    initial_guess = 10.0
    S = 36.0
    result_loop = pipe_loop.run(x=initial_guess, S=S) 
    print(f"Square root of {S} = {result_loop['x']}")