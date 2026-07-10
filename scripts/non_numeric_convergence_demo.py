import logging
from dataclasses import dataclass
from smartmdao import Pipeline, IterativeSolver, HybridSolver, configure_logging

# Initialize module-level logger
logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# OpenMDAO and GEMSEO converge coupled disciplines by driving a *numeric*
# residual (|delta| between successive floats) to zero. That assumption
# breaks the moment a discipline exchanges something that isn't a number -
# a resolved set of dependencies, a negotiated plan, a piece of state an
# AI agent is refining across a feedback loop.
#
# SmartMDAO's StandardConvergenceChecker falls back to structural equality
# for non-numeric values: "did this value change since the last iteration?"
# is a perfectly good convergence criterion when there's no derivative to
# speak of. Both cases below have zero floats in their coupling variables.
# ==============================================================================

def run_non_numeric_convergence_demo():
    configure_logging(level=logging.INFO)

    # ==============================================================================
    # CASE 1: Single-discipline fixed point over a frozenset (dependency closure)
    # ==============================================================================
    logger.info("=== CASE 1: Dependency closure over a frozenset ===")

    depends_on = {
        "reporting": frozenset({"database"}),
        "billing": frozenset({"database", "auth"}),
        "auth": frozenset({"database"}),
    }

    def resolve_dependencies(requested: frozenset, enabled: frozenset) -> frozenset:
        expanded = set(enabled) | set(requested)
        for feature in list(expanded):
            expanded |= set(depends_on.get(feature, frozenset()))
        return frozenset(expanded)

    pipe_deps = Pipeline(solver=IterativeSolver(max_iterations=10))
    pipe_deps.add(resolve_dependencies, outputs=["enabled"])

    result1 = pipe_deps.run(requested=frozenset({"billing", "reporting"}), enabled=frozenset())
    logger.info(f"Converged feature set: {sorted(result1['enabled'])}")
    logger.info(f"Iterations to fixed point: {len(result1['residual_history'][-1])}")

    # ==============================================================================
    # CASE 2: Two disciplines negotiating a shared Plan (auto-detected cycle)
    # ==============================================================================
    logger.info("=== CASE 2: Two disciplines negotiate a shared Plan (HybridSolver) ===")

    @dataclass(frozen=True)
    class Plan:
        tasks: frozenset

    roster = ["design", "build", "test", "deploy"]
    blocked_by = {
        "build": frozenset({"design"}),
        "test": frozenset({"build"}),
        "deploy": frozenset({"test"}),
    }

    def propose(reviewed_plan: Plan) -> Plan:
        # "Planner" discipline: stage the next task whose prerequisites are
        # already in place. Adds at most one task per call, so convergence
        # genuinely takes several iterations to reach the fixed point.
        staged = set(reviewed_plan.tasks)
        for task in roster:
            if task not in staged and blocked_by.get(task, frozenset()) <= staged:
                return Plan(tasks=frozenset(staged | {task}))
        return Plan(tasks=frozenset(staged))

    def review(plan: Plan) -> Plan:
        # "Critic" discipline: passes the plan through - the cycle converges
        # the moment `propose` has nothing left to add.
        return plan

    negotiation = Pipeline(solver=HybridSolver(max_iterations=10))
    negotiation.add(propose, outputs=["plan"])
    negotiation.add(review, outputs=["reviewed_plan"])

    result2 = negotiation.run(reviewed_plan=Plan(tasks=frozenset()))
    logger.info(f"Converged plan: {sorted(result2['plan'].tasks)}")
    logger.info(f"Iterations to fixed point: {len(result2['residual_history'][-1])}")

if __name__ == "__main__":
    run_non_numeric_convergence_demo()
