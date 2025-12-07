from dataclasses import dataclass
from smart_pipeline import Pipeline

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
    
    Concept: Implicit Naming. 
    Since we are not returning a Dataclass, the pipeline will default to 
    naming the output variable the same as the function name: 'load_data'.
    """
    return f"Data from {path}"


def preprocess(load_data: str) -> DatasetSplit:
    """
    Intermediate Step: Splits data.
    
    Concept: Structural Return.
    Instead of returning a tuple `(a, b)`, we return a `DatasetSplit`.
    The pipeline inspects this class and automatically knows:
    1. This step produces 'train_set'
    2. This step produces 'test_set'
    """
    # Simulate splitting the string "dataset_A dataset_B"
    parts = load_data.split() 
    return DatasetSplit(train_set=parts[0], test_set=parts[1])


def train_model(train_set: str, test_set: str) -> Metrics:
    """
    Complex Step: Consumes split data, produces metrics.
    
    Concept: Dependency Injection.
    The type hints tell the pipeline it needs 'train_set' and 'test_set'.
    The previous step (preprocess) provided exactly those fields.
    """
    print(f"Training on '{train_set}' vs '{test_set}'...")
    # Logic simulation...
    return Metrics(accuracy=0.95, loss=0.05)


def report(accuracy: float):
    """
    Final Step: Side effect (printing).
    
    It only asks for 'accuracy'. The pipeline extracts this from the 
    'Metrics' object returned by the previous step.
    """
    print(f"--- Final Report ---\nAccuracy: {accuracy:.2%}")


# ==========================================
# 3. Execution
# ==========================================
def run_ml_pipeline():
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
    results = pipe.run(path="dataset_A dataset_B")

    print("\n--- Memory Dump ---")
    # We expect to see 'load_data' (raw), 'train_set', 'test_set', 'accuracy', and 'loss'
    print(results)

if __name__ == "__main__":
    run_ml_pipeline()