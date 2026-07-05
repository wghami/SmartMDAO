"""
A tour of SmartMDAO's type validation layer.

SmartMDAO infers a step's inputs/outputs from ordinary Python type hints.
This layer enforces that the types a producer *declares* it returns are
actually compatible with what every consumer *declares* it expects - and
optionally, that what actually flows through the pipeline at runtime
matches those declarations too.

Two independent checks run automatically, and one is opt-in:
  1. Static structural validation - compares producer/consumer type hints
     against each other. Runs once per pipeline shape, before any step
     executes, so a contract mismatch never costs you a single computation.
  2. External input validation - compares the concrete values passed to
     `Pipeline.run(**inputs)` against the declared type of whichever step
     consumes them. Runs on every call, but it's just isinstance checks.
  3. Runtime validation (opt-in via `Pipeline(runtime_type_checks=True)`) -
     re-checks every input/output on every step call. Catches functions
     that "lie" about their own annotations, at the cost of doing real
     work on every call - so it's off by default.

All of this is built on a single extensible seam: `TypeChecker`, a
`Protocol` you can implement to change what "compatible" means.
"""
import logging
from typing import Optional, Union

from smartmdao import Pipeline, StandardTypeChecker, TypeMismatchError, configure_logging

logger = logging.getLogger(__name__)


# ==============================================================================
# PART 1: A well-typed pipeline - validation is silent when types line up
# ==============================================================================
def demo_well_typed_pipeline():
    logger.info("=== PART 1: A well-typed pipeline runs silently ===")

    def celsius_to_kelvin(celsius: float) -> float:
        return celsius + 273.15

    def classify(kelvin: float) -> str:
        return "boiling" if kelvin >= 373.15 else "not boiling"

    pipe = Pipeline()
    pipe.add(celsius_to_kelvin, outputs=["kelvin"])
    pipe.add(classify, outputs=["state"])

    result = pipe.run(celsius=100.0)
    logger.info(f"celsius=100.0 -> kelvin={result['kelvin']}, state='{result['state']}'")


# ==============================================================================
# PART 2: Static structural validation - caught before anything runs
# ==============================================================================
def demo_static_structural_mismatch():
    logger.info("=== PART 2: A producer/consumer type mismatch is caught before any step runs ===")

    def unreliable_sensor() -> str:
        # Bug: this should report a numeric reading, but returns its text form instead.
        return "21.5"

    def add_calibration_offset(sensor_reading: float) -> float:
        return sensor_reading + 1.0

    pipe = Pipeline()
    pipe.add(unreliable_sensor, outputs=["sensor_reading"])
    pipe.add(add_calibration_offset, outputs=["calibrated"])

    try:
        pipe.run()
    except TypeMismatchError as e:
        logger.info(f"Caught structural mismatch (nothing executed): {e}")


# ==============================================================================
# PART 3: External input validation - checked on every Pipeline.run() call
# ==============================================================================
def demo_external_input_mismatch():
    logger.info("=== PART 3: Values passed into .run() are checked against the consumer's type ===")

    def scale(factor: float) -> float:
        return factor * 2.0

    pipe = Pipeline()
    pipe.add(scale, outputs=["scaled"])

    try:
        pipe.run(factor="two")
    except TypeMismatchError as e:
        logger.info(f"Caught input mismatch: {e}")

    ok = pipe.run(factor=2.0)
    logger.info(f"Correct call succeeds: scaled={ok['scaled']}")


# ==============================================================================
# PART 4: Runtime validation is opt-in - it catches what static analysis can't
# ==============================================================================
def demo_runtime_opt_in_check():
    logger.info("=== PART 4: Runtime checks (opt-in) catch functions that lie about their own types ===")

    def buggy_normalize(value: float) -> float:
        # Bug: annotated to return a float, but a stray str() slipped in.
        return str(value / 100)

    # Default: runtime_type_checks=False. The annotation is trusted, so the bug slips through.
    lenient_pipe = Pipeline()
    lenient_pipe.add(buggy_normalize, outputs=["normalized"])
    sneaky_result = lenient_pipe.run(value=50.0)
    normalized = sneaky_result["normalized"]
    logger.info(
        f"Without runtime checks, the bug goes unnoticed: "
        f"normalized={normalized!r} (actually a {type(normalized).__name__}, not a float!)"
    )

    # Opt in: every input/output is now checked against its declared type on every call.
    strict_pipe = Pipeline(runtime_type_checks=True)
    strict_pipe.add(buggy_normalize, outputs=["normalized"])
    try:
        strict_pipe.run(value=50.0)
    except TypeMismatchError as e:
        logger.info(f"With runtime_type_checks=True, the same bug is caught immediately: {e}")


# ==============================================================================
# PART 5: Optional[...] and Union[...] are understood natively
# ==============================================================================
def demo_optional_and_union_support():
    logger.info("=== PART 5: Optional[...] and Union[...] annotations are understood natively ===")

    def apply_discount(price: float, coupon: Optional[str] = None) -> float:
        return price * 0.9 if coupon else price

    def format_price(final_price: Union[int, float]) -> str:
        return f"${final_price:.2f}"

    pipe = Pipeline()
    pipe.add(apply_discount, outputs=["final_price"])
    pipe.add(format_price, outputs=["label"])

    with_coupon = pipe.run(price=100.0, coupon="SAVE10")
    logger.info(f"With coupon: {with_coupon['label']}")

    without_coupon = pipe.run(price=100.0, coupon=None)
    logger.info(f"Without coupon (coupon=None satisfies Optional[str]): {without_coupon['label']}")


# ==============================================================================
# PART 6: TypeChecker is a Protocol - swap in your own compatibility rules
# ==============================================================================
def demo_custom_type_checker():
    logger.info("=== PART 6: TypeChecker is a Protocol - swap in your own strictness rules ===")

    class NumericToleranceChecker(StandardTypeChecker):
        """
        Loosens the default strict `isinstance` check to also accept `int`
        wherever a `float` is expected - handy since numeric/scientific code
        constantly mixes the two. Everything else falls back to the
        strict default behavior (inherited via super()).
        """
        def check_value(self, value, expected):
            if expected is float and isinstance(value, int):
                return True
            return super().check_value(value, expected)

    def add_one(x: float) -> float:
        return x + 1

    strict_pipe = Pipeline()  # default StandardTypeChecker
    strict_pipe.add(add_one, outputs=["y"])
    try:
        strict_pipe.run(x=5)  # a plain int - rejected by strict isinstance semantics
    except TypeMismatchError as e:
        logger.info(f"Strict (default) checker rejects int where float is expected: {e}")

    lenient_pipe = Pipeline(type_checker=NumericToleranceChecker())
    lenient_pipe.add(add_one, outputs=["y"])
    result = lenient_pipe.run(x=5)  # same int, accepted by the custom checker
    logger.info(f"Custom lenient checker accepts int -> float: y={result['y']}")


# ==============================================================================
# PART 7: Structural validation runs once per pipeline shape, not per call
# ==============================================================================
def demo_structure_validation_is_cached():
    logger.info("=== PART 7: Structural validation is cached per pipeline shape ===")
    logger.info("(This matters because PipelineEvaluator may call .run() hundreds of times during optimization.)")

    def scale(x: float) -> float:
        return x * 2.0

    pipe = Pipeline()
    pipe.add(scale, outputs=["y"])

    for x in (1.0, 2.0, 3.0):
        result = pipe.run(x=x)
        logger.info(f"x={x} -> y={result['y']}  (re-validated on the first call only)")

    # Adding a step changes the pipeline's shape, so the next .run() re-validates once more.
    pipe.add(lambda y: y + 1.0, outputs=["z"])
    result = pipe.run(x=4.0)
    logger.info(f"After adding a step: z={result['z']}  (structure re-validated once, for the new shape)")


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def run_type_validation_demo():
    configure_logging(level=logging.INFO)
    demo_well_typed_pipeline()
    demo_static_structural_mismatch()
    demo_external_input_mismatch()
    demo_runtime_opt_in_check()
    demo_optional_and_union_support()
    demo_custom_type_checker()
    demo_structure_validation_is_cached()


if __name__ == "__main__":
    run_type_validation_demo()
