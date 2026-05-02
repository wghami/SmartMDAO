import functools
import hashlib
import pickle
import os
import logging
from abc import ABC, abstractmethod
from collections import defaultdict

# Initialize module-level logger
logger = logging.getLogger(__name__)

# --- 1. Abstract Backend Interface ---
class CacheBackend(ABC):
    @abstractmethod
    def get(self, func_name, key):
        pass

    @abstractmethod
    def set(self, func_name, key, value):
        pass

    @abstractmethod
    def has(self, func_name, key):
        pass

# --- 2. In-Memory Backend (Dictionary) ---
class MemoryBackend(CacheBackend):
    def __init__(self):
        self.store = {}

    def _make_key(self, func_name, key):
        return f"{func_name}::{key}"

    def has(self, func_name, key):
        return self._make_key(func_name, key) in self.store

    def get(self, func_name, key):
        logger.debug(f"[Memory] Cache hit for {func_name}")
        return self.store[self._make_key(func_name, key)]

    def set(self, func_name, key, value):
        self.store[self._make_key(func_name, key)] = value

class HistoryBackend(MemoryBackend):
    """
    A simple extension of MemoryBackend that keeps a chronological 
    list of all values computed by the cached functions.
    """
    def __init__(self):
        super().__init__()
        # Dictionary mapping function_name -> list of values
        self.history = defaultdict(list)

    def set(self, func_name, key, value):
        # 1. Store in the standard cache (MemoryBackend logic)
        super().set(func_name, key, value)
        
        # 2. Append to our history list for plotting
        self.history[func_name].append(value)

# --- 3. HDF5 Backend ---
class HDF5Backend(CacheBackend):
    """
    Best for Large Numpy Arrays. 
    Limitation: Can only store data types HDF5 supports (scalars, strings, numpy arrays).
    For generic Python objects (classes, dicts), use Pickle instead.
    """
    def __init__(self, filepath):
        self.filepath = filepath
        import h5py 
        self.h5py = h5py # lazy import

    def has(self, func_name, key):
        if not os.path.exists(self.filepath):
            return False
        with self.h5py.File(self.filepath, 'r') as f:
            return f"{func_name}/{key}" in f

    def get(self, func_name, key):
        logger.debug(f"[HDF5] Cache hit for {func_name}")
        with self.h5py.File(self.filepath, 'r') as f:
            dataset = f[f"{func_name}/{key}"]
            # Convert back to numpy or scalar
            if dataset.shape == ():
                return dataset[()] # scalar
            return dataset[:] # array

    def set(self, func_name, key, value):
        with self.h5py.File(self.filepath, 'a') as f:
            group_path = f"{func_name}"
            if group_path not in f:
                f.create_group(group_path)
            
            # Delete if exists to overwrite
            if key in f[group_path]:
                del f[group_path][key]
            
            f[group_path].create_dataset(key, data=value)

class PickleDiskBackend(CacheBackend):
    def __init__(self, directory="cache_dir"):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, func_name, key):
        return os.path.join(self.directory, f"{func_name}_{key}.pkl")

    def has(self, func_name, key):
        return os.path.exists(self._path(func_name, key))

    def get(self, func_name, key):
        logger.debug(f"[Pickle] Cache hit for {func_name}")
        with open(self._path(func_name, key), 'rb') as f:
            return pickle.load(f)

    def set(self, func_name, key, value):
        with open(self._path(func_name, key), 'wb') as f:
            pickle.dump(value, f)

# --- 4. The Decorator ---
def generate_cache_key(kwargs):
    """
    Creates a stable hash of the input arguments.
    We use pickle to serialize args -> hash to handle complex types.
    """
    # Sort kwargs to ensure order doesn't matter: f(a=1, b=2) == f(b=2, a=1)
    sorted_items = sorted(kwargs.items())
    serialized = pickle.dumps(sorted_items)
    return hashlib.sha256(serialized).hexdigest()

def cached(backend: CacheBackend):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(**kwargs):
            # 1. Generate Key based on function input
            key = generate_cache_key(kwargs)
            
            # 2. Check Backend
            if backend.has(fn.__name__, key):
                return backend.get(fn.__name__, key)
            
            # 3. Run Function
            logger.debug(f"Cache miss for {fn.__name__}. Executing...")
            result = fn(**kwargs)
            
            # 4. Save Result
            backend.set(fn.__name__, key, result)
            return result
        return wrapper
    return decorator