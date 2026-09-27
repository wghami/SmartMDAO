"""
Constants shared by the server and the processes it starts.

They live here, not in `_runner` or `_worker`, because those two are started
with `python -m`. The package imports whatever the server needs when it is
itself imported, so a constant kept in `_runner` loaded `_runner` before runpy
could execute it, and every run's stderr opened with a RuntimeWarning that
hid the real ones (F-014). Nothing imports `_runner` or `_worker` now except
their own `python -m`.
"""

#: Cost rungs, cheapest first. `smoke` exists to answer "does this execute at
#: all" and to *measure the unit cost*, so the estimate for every larger run
#: falls out of one cheap run.
SMOKE = "smoke"
BUDGETED = "budgeted"
FULL = "full"
RUNGS = (SMOKE, BUDGETED, FULL)

#: Sweeps allowed on the `budgeted` rung unless the caller says otherwise.
DEFAULT_BUDGET_SWEEPS = 25

#: Worker protocol versions this SmartMDAO speaks, lowest and highest. Change
#: the request or response shape -> raise the upper bound, and keep answering
#: the old one for as long as servers speaking it are expected to be around.
PROTOCOL = (1, 1)

#: How a campaign point can end (docs/design/006). Shared by the coordinator
#: and the worker, so neither imports the other.
OK = "ok"
NOT_CONVERGED = "not_converged"
ERROR = "error"
TIMED_OUT = "timed_out"
CRASHED = "crashed"
#: Statuses a resumed campaign keeps; the others run again on request.
FINISHED = (OK, NOT_CONVERGED)
