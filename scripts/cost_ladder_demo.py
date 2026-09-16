import logging
import pathlib
import tempfile

from smartmdao import configure_logging
from smartmdao.mcp.handlers import run_pipeline

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# An engineer will ask the agent to run the model. Refusing is not available to
# us: the agent has a shell and will run `python model.py` instead - unsandboxed,
# untimed, with the results scraped out of stdout. Refusal does not create a
# boundary, it moves the work somewhere with LESS control.
#
# So `run_pipeline` exists, and what it adds is not safety:
#
#   - a HARD KILL for a solve that will not stop
#   - TYPED results instead of scraped text
#   - CRASH ISOLATION, so a segfaulting discipline does not take the server down
#
# And one thing more, which is the point of the rungs below: an unbounded run
# is not something to commit someone to without a number. The cheapest rung
# measures the unit cost, so the estimate for every larger run falls out of it.
# ==============================================================================

MODEL = '''
"""A coupled sizing loop with a deliberately slow discipline."""
import time

from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(max_iterations=60, tolerance=1e-9))


@pipeline.step(outputs=["battery_kg"])
def size_battery(total_kg: float) -> float:
    time.sleep(0.02)            # stand-in for a real analysis
    return 0.25 * total_kg


@pipeline.step(outputs=["total_kg"])
def size_airframe(battery_kg: float, payload_kg: float) -> float:
    time.sleep(0.02)
    return 500.0 + battery_kg + payload_kg


pipeline.run(battery_kg=100.0, payload_kg=200.0)
'''

RUNAWAY = '''
import time

from smartmdao import Pipeline, IterativeSolver

pipeline = Pipeline(solver=IterativeSolver(max_iterations=100_000, target_var="x"))


@pipeline.step(outputs=["x"])
def never_settles(x: float) -> float:
    time.sleep(0.05)
    return x + 1.0            # walks away forever
'''

CRASHER = '''
import os

from smartmdao import Pipeline

pipeline = Pipeline()


@pipeline.step(outputs=["b"])
def hard_exit(a: float) -> float:
    os._exit(139)             # dies the way a segfault does: no exception
'''


def run_cost_ladder_demo():
    configure_logging(level=logging.CRITICAL)
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="smartmdao_ladder_"))

    model = workspace / "sizing.py"
    model.write_text(MODEL)

    # ==========================================================================
    # CASE 1: the cheapest rung, which is also the measurement
    # ==========================================================================
    print("=== CASE 1: rung='smoke' - does it run, and what does one sweep cost? ===")
    smoke = run_pipeline(str(model))          # smoke is the default, deliberately

    print(f"  rung        : {smoke['rung']}")
    print(f"  ok          : {smoke['ok']}   converged: {smoke['converged']}")
    print(f"  solver caps : {smoke['rung_detail']}")
    print(f"  elapsed     : {smoke['elapsed_seconds']}s")
    print(f"  inputs      : found in the file -> {smoke['inputs_used']['found_in_source']}")
    print()
    print("  -> converged=False is CORRECT here: one sweep is not convergence.")
    print("     The point of this rung is that the code executes, and what it costs.")

    # ==========================================================================
    # CASE 2: the estimate that came free with it
    # ==========================================================================
    print("\n=== CASE 2: the number to quote before spending anything ===")
    estimate = smoke["cost_estimate"]
    for key, value in estimate.items():
        print(f"  {key:<28} {value}")
    print()
    print("  -> This is the difference between 'this might take a while' and a")
    print("     figure the engineer can consent to. Worst case only - how many")
    print("     sweeps a loop actually needs is not knowable in advance, and")
    print("     pretending otherwise would be a confident guess.")

    # ==========================================================================
    # CASE 3: a capped run, then the real one
    # ==========================================================================
    print("\n=== CASE 3: budgeted, then full ===")
    budgeted = run_pipeline(str(model), rung="budgeted", budget_sweeps=3)
    print(f"  budgeted(3) : converged={budgeted['converged']} "
          f"after {budgeted['convergence_reports'][0]['iterations']} sweeps, "
          f"{budgeted['elapsed_seconds']}s")

    full = run_pipeline(str(model), rung="full")
    report = full["convergence_reports"][0]
    print(f"  full        : converged={full['converged']} "
          f"after {report['iterations']} sweeps, {full['elapsed_seconds']}s")
    print(f"  answer      : total_kg = {full['state']['total_kg']:.2f} kg")
    print(f"  per step    : "
          f"{ {k: v['calls'] for k, v in full['per_step'].items()} }")
    print()
    print("  -> The per-step call counts are the SCC decomposition made visible:")
    print("     only the cyclic block iterated.")

    # ==========================================================================
    # CASE 4: the two failures the child process exists for
    # ==========================================================================
    print("\n=== CASE 4: what happens when it goes wrong ===")

    runaway = workspace / "runaway.py"
    runaway.write_text(RUNAWAY)
    killed = run_pipeline(str(runaway), inputs={"x": 0.0}, rung="full", timeout_seconds=1.0)
    print(f"  a solve that will not stop -> ok={killed['ok']}, "
          f"timed_out={killed.get('timed_out')}")
    print(f"    {killed['error'][:96]}...")

    crasher = workspace / "crasher.py"
    crasher.write_text(CRASHER)
    crashed = run_pipeline(str(crasher), inputs={"a": 1.0})
    print(f"\n  a discipline that dies without raising -> ok={crashed['ok']}")
    print(f"    {crashed['error'][:96]}...")

    print("\n  -> Both come back as DATA, not exceptions, so the agent can read")
    print("     them and act. And this process is still alive to print this line,")
    print("     which is the whole argument for running it somewhere else.")

    # ==========================================================================
    # CASE 5: results are summarised, not dumped
    # ==========================================================================
    print("\n=== CASE 5: results are sized for a context window ===")
    print(f"  state keys : {sorted(full['state'])}")
    print(f"  report     : {full['convergence_reports'][0]}")
    print()
    print("  -> A converged `memory` can hold large arrays and a residual history")
    print("     per cyclic block. Arrays become shape + min/max + a short preview;")
    print("     the full data stays on your machine, where it already is.")


if __name__ == "__main__":
    run_cost_ladder_demo()
