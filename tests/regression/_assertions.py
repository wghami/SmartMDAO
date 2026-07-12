import pytest


def assert_state_close(actual: dict, expected: dict, *, rel: float = 1e-3, abs: float = 1e-4) -> None:
    """Assert that every key in `expected` is present in `actual` and numerically close.

    Used by the benchmarks in tests/regression/ to compare a real pipeline run
    against a known-good reference (a published optimum or a pinned baseline),
    as opposed to the mocked unit tests elsewhere in tests/ which only check wiring.
    """
    for key, expected_value in expected.items():
        actual_value = actual[key]
        assert actual_value == pytest.approx(expected_value, rel=rel, abs=abs), (
            f"{key}: expected {expected_value}, got {actual_value}"
        )
