# 002 — An agent as an MDA discipline

**Status:** proposed, unproven
**Date:** 2026-09-13

---

## The observation

`StandardConvergenceChecker` ([solvers.py:37](../../smartmdao/solvers.py:37)) falls back to
structural equality for non-numeric coupling variables. Its docstring already names the use case:

> ...an agent-authored MDA negotiating a shared plan or a resolved set of dependencies...

Nothing in the repository demonstrates it. The README makes the same claim in prose. This document
works out what it would actually mean, what it is good for, and where it breaks.

The claim is narrow and specific: **a language model can be a discipline inside an MDA feedback
loop, and the loop can converge.** OpenMDAO and GEMSEO cannot express this, because they converge
by driving a numeric residual to zero and there is no derivative of a design decision.

## Mechanics

A step is any callable, so a step can call a model and return a typed value:

```python
@pipeline.step(outputs=["architecture"])
@cached(history)
def propose_architecture(requirements: frozenset, violations: frozenset) -> Architecture:
    return ask_model(requirements, violations)      # frozen dataclass

@pipeline.step(outputs=["mass", "violations"])
def size_and_check(architecture: Architecture) -> tuple[float, frozenset]:
    ...                                             # pure numerics
```

`architecture → violations → architecture` is a cycle. `HybridSolver` finds it with Tarjan,
isolates it as an SCC, and hands it to a nested `IterativeSolver`. Convergence is reached when the
proposed architecture stops changing between sweeps.

Note what this inverts. Normally the agent is the outer loop and calls tools. Here the **solver**
is the outer loop and the model is a subroutine.

## Why this is not just a while-loop with a model in it

That is the obvious objection and it deserves a direct answer. Four properties come from the
solver, not from the loop:

**1. Termination is external.** This is the one that matters. Agent loops today end when the model
declares itself finished — the model grading its own homework, which is exactly where agent
reliability is weakest. Here the model contributes a value and *the solver* decides whether the
system is at rest, against a criterion the model does not control and cannot see.

**2. Only the cycle iterates.** `HybridSolver` runs non-cyclic SCCs exactly once and iterates only
the strongly connected block ([solvers.py:232](../../smartmdao/solvers.py:232)). A hand-rolled
loop re-runs everything each pass; when "everything" includes model calls, that is money. The
decomposition is automatic, so nobody has to work out by hand which subset genuinely needs to
iterate.

**3. Contributions are typed and bounded.** `validate_structure` checks the step's declared return
type against what consumers expect *before anything runs*, and `runtime_type_checks` enforces it
per call. The model is a discipline with a contract, not a free agent — it cannot return prose
where a dataclass is required.

**4. It is auditable for free.** `residual_history` lands in the result dict
([solvers.py:144](../../smartmdao/solvers.py:144)) and `HistoryBackend`
([cache.py:44](../../smartmdao/cache.py:44)) already keeps a chronological list of every value each
function produced. The full record of what the model proposed at each iteration exists with no
additional machinery.

## What it is for

Ranked by how much the numeric coupling actually earns its place.

### Discrete architectural choice — the strongest case

MDO has a well-known hole. Gradient-based optimization handles continuous sizing beautifully and
cannot touch **discrete architecture**: how many engines, wing-mounted or fuselage-mounted, which
material, which topology. In practice that part is decided by a human in a meeting, or by
brute-force enumeration over a combinatorial space.

Put the model in that role. It proposes an architecture; numeric disciplines size it and return
constraint violations; those feed back; it revises. The numerics **ground** it — it cannot
hallucinate a mass budget, because `scipy` computes the mass — and it covers the combinatorial
part gradients cannot reach. That is a real gap in practice, not a restatement of something that
already works.

### Requirements ↔ feasibility negotiation

Requirements flow down, sizing flows back, some turn out infeasible. Today that is a meeting. As a
loop: the model holds the requirement set, numeric disciplines size against it, infeasibility
feeds back, it reallocates margin. Converges on a self-consistent requirement set — and the
`HistoryBackend` trace *is* the traceable derivation that systems engineers currently assemble by
hand.

### Optimization self-repair

An `optimize()` run fails: bad scaling, a flipped constraint sign, bounds too tight.
`OptimizationResult` already carries `success`, `message`, and `state`. Feed those to a model step
that proposes a reformulation, re-run, feed back. The loop converges when the *problem statement*
stops changing. Attractive because it improves the formulation rather than the design.

### Weak cases, stated plainly

- **Any loop with no numeric discipline in it.** You are using SmartMDAO as a DAG runner and
  purpose-built agent frameworks have more ecosystem. The value here comes from *coupling* symbolic
  and numeric disciplines in one convergence criterion; without the numerics there is no argument.
- **Free-text coupling variables.** See below — these essentially never converge.

## Where it breaks

**Structural equality is a brutal criterion.** The distance function returns `0.0` or `inf` and
nothing between. One reworded sentence means "still moving". The coupling variable must therefore
be **low-entropy**: a frozenset of discrete choices, an enum, a small frozen dataclass of
decisions. Never prose. Normalise before returning — sort collections, round floats, canonicalise
— and run at temperature 0. Design rule: *couple on the decision, not on the explanation of it.*

**Oscillation is undetected.** A model that flip-flops A→B→A→B never satisfies structural equality
and will burn every one of `max_iterations`, each costing model calls. Nothing in the current
solver notices. The fix fits the existing architecture cleanly: `ConvergenceChecker` is a
`Protocol` and the instance persists across iterations, so a stateful checker that retains a value
history and reports a detected 2-cycle is a small addition. **This is a prerequisite, not a
nice-to-have** — see Phase 1 in [../roadmap.md](../roadmap.md).

**Mixed convergence is stricter than it looks.** With no `target_var`, the residual is a `max()`
across *every* produced variable ([solvers.py:161](../../smartmdao/solvers.py:161)). The whole
system is hostage to the model's output being byte-identical between sweeps. `target_var` is the
necessary escape hatch: judge convergence on the architectural variable and let the numerics settle
underneath it.

**Cost is multiplicative.** MDA iterations × model calls per iteration — and wrapping the whole
thing in `optimize()` multiplies again by evaluation count. A model inside an optimizer's inner
loop is not affordable. The workable topology is the inverse: **model in the outer architectural
loop, `optimize()` inner.** The model proposes an architecture, the optimizer sizes it optimally,
the result feeds back. `@cached` matters a great deal here, since a converging system revisits
near-identical states.

**Reproducibility.** MDO practitioners care about this more than most software audiences, and a
nondeterministic discipline is a hard sell. Cache aggressively, pin the model version, log every
call, and treat the `HistoryBackend` record as part of the result rather than as debug output.

## Open questions

- Does structural equality on a frozen dataclass behave well enough in practice, or does every
  real use need a custom `ConvergenceChecker` with domain-specific normalisation?
- Is "converged" even the right target, or is the useful signal *where* it failed to converge —
  i.e. which requirement the model could not satisfy? A non-converged run may be the more
  informative result.
- How should a model step declare a *retraction* — "no feasible architecture exists"? Returning
  `None` currently means "store nothing", which would silently freeze the previous value.

## Next step

Phase 1 in [../roadmap.md](../roadmap.md): an oscillation-aware `ConvergenceChecker` plus a demo
script in `scripts/` using a stubbed `ask_model()`. No MCP dependency — the stub proves the
convergence mechanics, and the real model call is swapped in later. Until that exists, everything
above is a hypothesis.
