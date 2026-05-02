import numpy as np
from smartmdao.cache import (
    HistoryBackend, PickleDiskBackend, HDF5Backend, cached, generate_cache_key
)

def test_generate_cache_key():
    # Ensures kwargs order doesn't change the hash
    hash1 = generate_cache_key({"a": 1, "b": 2})
    hash2 = generate_cache_key({"b": 2, "a": 1})
    assert hash1 == hash2

def test_memory_and_history_backend():
    backend = HistoryBackend()
    
    @cached(backend)
    def compute(x):
        return x * 2

    assert compute(x=10) == 20 # Cache miss
    assert compute(x=10) == 20 # Cache hit
    
    # History backend specific check
    assert backend.history["compute"] == [20]

def test_pickle_backend(tmp_path):
    backend = PickleDiskBackend(directory=str(tmp_path))
    
    @cached(backend)
    def compute(data):
        return {"result": data}
        
    assert compute(data="hello") == {"result": "hello"}
    assert compute(data="hello") == {"result": "hello"}
    assert backend.has("compute", generate_cache_key({"data": "hello"}))

def test_hdf5_backend(tmp_path):
    # Tests both scalar and numpy array storage
    backend = HDF5Backend(filepath=str(tmp_path / "test.h5"))
    
    @cached(backend)
    def compute_array(size):
        return np.ones(size)
        
    @cached(backend)
    def compute_scalar(val):
        return val

    # Test arrays
    np.testing.assert_array_equal(compute_array(size=3), np.ones(3))
    np.testing.assert_array_equal(compute_array(size=3), np.ones(3)) # hit
    
    # Test scalars
    assert compute_scalar(val=42) == 42
    assert compute_scalar(val=42) == 42 # hit