import logging
from dataclasses import dataclass
from smart_pipeline import Pipeline, configure_logging
from smart_pipeline.cache import MemoryBackend, cached

# --- Setup Logging ---
# Initialize module-level logger
logger = logging.getLogger(__name__)

# ==============================================================================
# PART 1: The "Unchangeable" External Libraries
# ==============================================================================
# In real life, these are in separate files. You cannot change them.
# They return raw 'ints', not Dataclasses.

class LibAnalytics:
    @staticmethod
    def analyze(data):
        # Returns a raw int. We can't change this.
        return data * 2

class LibFinance:
    @staticmethod
    def analyze(money):
        # Returns a raw int. Same name 'analyze'. We can't change this.
        return money + 1000

# ==============================================================================
# PART 2: Your Pipeline "Glue" Code (The Adapter Pattern)
# ==============================================================================
# We define clean Dataclasses for our pipeline to use internally.

@dataclass
class AnalyticsResult:
    score: int

@dataclass
class FinanceResult:
    budget: int

# Setup Backend
mem_cache = MemoryBackend()

# --- ADAPTER 1: Analytics ---
# We write a wrapper function.
# 1. We name it uniquely ('run_analytics_step') so the CACHE works perfectly.
# 2. We wrap the raw result in a Dataclass so the PIPELINE works perfectly.

@cached(mem_cache)
def run_analytics_step(data: int) -> AnalyticsResult:
    """Adapts LibAnalytics for the pipeline."""
    logger.debug(f"Executing real logic for Analytics with data={data}...")
    
    # Call the external 'raw' function
    raw_value = LibAnalytics.analyze(data)
    
    # Wrap it nicely
    return AnalyticsResult(score=raw_value)


# --- ADAPTER 2: Finance ---

@cached(mem_cache)
def run_finance_step(money: int) -> FinanceResult:
    """Adapts LibFinance for the pipeline."""
    logger.debug(f"Executing real logic for Finance with money={money}...")
    
    # Call the external 'raw' function
    raw_value = LibFinance.analyze(money)
    
    # Wrap it nicely
    return FinanceResult(budget=raw_value)


# ==============================================================================
# PART 3: Execution
# ==============================================================================
def run_adapter_demo():
    # Initialize the centralized logging configuration
    # PRO TIP: Change to logging.DEBUG to see the cache hits/misses in action!
    configure_logging(level=logging.INFO)
    
    pipe = Pipeline()

    # We add our ADAPTERS, not the raw library functions.
    # The pipeline infers:
    # 1. run_analytics_step produces 'score' (from AnalyticsResult)
    # 2. run_finance_step produces 'budget' (from FinanceResult)
    pipe.add(run_analytics_step)
    pipe.add(run_finance_step)

    logger.info("--- Run 1: Cold Start ---")
    # 'run_analytics_step' calls LibAnalytics
    # 'run_finance_step' calls LibFinance
    res1 = pipe.run(data=10, money=50)
    logger.info(f"Analytics Score: {res1['score']}")
    logger.info(f"Finance Budget:  {res1['budget']}")

    logger.info("--- Run 2: Cached ---")
    # The adapters are cached. They don't even call the library functions.
    res2 = pipe.run(data=10, money=50)
    logger.info(f"Results match: {res1 == res2}")

    logger.info("--- Run 3: Partial Change ---")
    # We change 'money'.
    # - run_analytics_step: Inputs same -> Returns cached result immediately.
    # - run_finance_step:   Inputs changed -> Calls LibFinance.analyze again.
    res3 = pipe.run(data=10, money=9999)
    logger.info(f"Analytics Score: {res3['score']} (Cached)")
    logger.info(f"Finance Budget:  {res3['budget']} (Recalculated)")

if __name__ == "__main__":
    run_adapter_demo()