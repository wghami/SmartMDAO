import logging
from typing import List, Dict, Any, Callable

from .core import Pipeline

logger = logging.getLogger(__name__)

class PipelineEvaluator:
    """
    A generic, stateful bridge to interface the Pipeline with external optimizers 
    (SciPy, OpenTURNS, PyOptSparse, etc.).
    
    It caches the last evaluation to prevent redundant pipeline runs when optimizers
    request objectives and constraints independently for the same state.
    """
    def __init__(self, 
                 pipeline: Pipeline, 
                 design_vars: List[str], 
                 constants: Dict[str, Any] = None):
        """
        :param pipeline: The instantiated smart_pipeline.
        :param design_vars: Ordered list of variable names corresponding to the optimizer's input array `x`.
        :param constants: Optional dictionary of variables that remain fixed during optimization.
        """
        self.pipeline = pipeline
        self.design_vars = design_vars
        self.constants = constants or {}
        
        self.last_x = None
        self.last_results = None
        self.eval_count = 0

    def evaluate(self, x) -> Dict[str, Any]:
        """Runs the pipeline if the design variables have changed."""
        # Convert to tuple for hashable comparison
        x_tuple = tuple(x)
        
        if self.last_x != x_tuple:
            self.eval_count += 1
            
            # 1. Map the numeric array 'x' back to named variables
            inputs = dict(zip(self.design_vars, x))
            
            # 2. Inject constants
            inputs.update(self.constants)
            
            # 3. Execute
            self.last_results = self.pipeline.run(**inputs)
            self.last_x = x_tuple
            
        return self.last_results

    def get_objective(self, output_name: str) -> Callable:
        """
        Factory method that returns a callable objective function for the optimizer.
        """
        def _objective(x):
            return self.evaluate(x)[output_name]
        return _objective

    def get_constraint(self, output_name: str, multiplier: float = 1.0) -> Callable:
        """
        Factory method that returns a callable constraint function.
        :param multiplier: Useful for flipping constraint signs. 
                           (e.g., SciPy expects f(x) >= 0. If pipeline outputs f(x) <= 0, use multiplier=-1.0)
        """
        def _constraint(x):
            return multiplier * self.evaluate(x)[output_name]
        return _constraint