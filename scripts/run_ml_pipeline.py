import logging
from dataclasses import dataclass
from smart_pipeline import Pipeline, configure_logging

# Initialize module-level logger
logger = logging.getLogger(__name__)

# ==========================================
# 1. Define Data Contracts
# ==========================================
# Instead of returning a generic Tuple (which requires manual naming later),
# we define a clear structure for our split data.

@dataclass
class DatasetSplit:
    """Holds the separated training and testing data."""
    train_set: str
    test_set: str

@dataclass
class Metrics:
    """Holds the performance results of the model."""
    accuracy: float
    loss: float


# ==========================================
# 2. Define Steps
# ==========================================

def load_data(path: str) -> str:
    """
    Simple Step: Returns a raw string.
    """
    logger.debug(f"Loading data from path: {path}")
    return f"Data from {path}"


def preprocess(load_data: str) -> DatasetSplit:
    """
    Intermediate Step: Splits data.
    """
    logger.debug("Splitting dataset into Train/Test...")
    # Simulate splitting the string "dataset_A dataset_B"
    parts = load_data.split() 
    return DatasetSplit(train_set=parts[0], test_set=parts[1])


def train_model(train_set: str, test_set: str) -> Metrics:
    """
    Complex Step: Consumes split data, produces metrics.
    """
    logger.info(f"Training on '{train_set}' vs '{test_set}'...")
    # Logic simulation...
    return Metrics(accuracy=0.95, loss=0.05)


def report(accuracy: float):
    """
    Final Step: Side effect (logging).
    """
    logger.info(f"--- Final Report --- | Accuracy: {accuracy:.2%}")


# ==========================================
# 3. Execution
# ==========================================
def run_ml_pipeline():
    # 0. Configure Logging
    configure_logging(level=logging.INFO)

    pipe = Pipeline()

    # Notice how clean this block is now.
    # We removed `outputs=["train_set", "test_set"]` because the 
    # DatasetSplit dataclass handles that logic inside the code, not the config.
    (
        pipe
        .add(train_model) # <-- the order of the .add does not matter
        .add(load_data)
        .add(report)
        .add(preprocess)
    )

    # Run the pipeline
    # 'load_data' needs 'path', so we provide it here.
    logger.info("Starting ML Pipeline execution...")
    results = pipe.run(path="dataset_A dataset_B")

    logger.info("--- Memory Dump ---")
    # We expect to see 'load_data' (raw), 'train_set', 'test_set', 'accuracy', and 'loss'
    logger.info(results)

if __name__ == "__main__":
    run_ml_pipeline()