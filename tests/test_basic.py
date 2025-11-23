from smart_pipeline import main

def test_import():
    assert main is not None

def test_math():
    assert 1 + 1 == 2
