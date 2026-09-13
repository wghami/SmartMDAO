# 003 — Determinism, traceability, and the engineer in the loop

**Status:** accepted as direction; nothing implemented
**Date:** 2026-09-13
**Amends:** [002](002-agent-as-discipline.md) — supersedes its production path, not its mechanics

---

## The principle

> We do not have to control everything the engineer does, but we have to give them enough
> information for an informed decision.

This is the governing constraint for everything built on top of SmartMDAO. It is written down here
because it was implicit for three phases, and an implicit principle is one the next contributor
will violate without noticing.

Two things follow, and they are not negotiable for this audience:

- **Determinism and reproducibility.** An engineer has to be able to re-run a study next year and
  get the same answer, and to explain to a design review why the answer is what it is. A
  discipline that returns something different each time is not a discipline; it is a rumour.
- **Traceability.** Every number in a result must trace back to an assumption someone can point
  at. "The model said so" is not traceable.

## The principle already explains what exists

Read retroactively, most of the work so far is this principle applied:

| Built | What it surfaces |
|---|---|
| `validate()` | Assumptions before anything runs — what must be seeded, what is unsatisfiable |
| `analyze()` | The structure the engineer wrote but could not see, e.g. an unintended feedback loop |
| `ConvergenceReport` | What the solve actually did, instead of a residual list to interpret by hand |
| `OscillationDetectedError` → abandonment | *Why* it failed, not just that it did |
| Faithful-vs-idiomatic translation | A choice the engineer makes, not a default chosen quietly |

None of these stop the engineer doing anything. All of them make the consequences visible first.
That is the pattern to keep.

---

## The problem with 002 as a production path

[002](002-agent-as-discipline.md) showed that a language model *can* be a discipline inside an MDA
feedback loop, and the mechanics hold up: `HybridSolver` finds the cycle, structural equality
converges it, oscillation is caught. Those findings stand.

What it could not answer is the question this audience asks first: **how do you reproduce this
run?** 002 lists it under "where it breaks" and leaves it there —

> Nondeterminism in a discipline is a hard sell. Cache aggressively, pin the model version, log
> every call.

Caching and logging make a run *replayable*. They do not make it *reproducible*, and they do not
make it reviewable: the artifact under review is a prompt and a model version, neither of which an
engineer can reason about the way they reason about an equation.

## Decision: ASP-backed disciplines

A language model generates an **Answer Set Programming** program (clingo), once, at authoring
time. That program — not the model — becomes the discipline.

```
authoring time (once, reviewed)      run time (every time, deterministic)
┌──────────────────────────┐         ┌────────────────────────────────┐
│ engineer states intent   │         │ facts derived from pipeline    │
│ agent generates ASP      │  ────▶  │ inputs + the fixed ASP program │
│ ENGINEER REVIEWS RULES   │         │ → clingo → atoms → discipline  │
└──────────────────────────┘         └────────────────────────────────┘
```

The nondeterminism is **relocated, not tolerated**. It moves out of the execution loop and into an
authoring step where a human reviews the result — and the thing under review is a set of logical
rules, which is exactly the kind of artifact engineers already review.

### Why it fits this library specifically

- **ASP output is low-entropy by construction.** 002's hard-won rule — *couple on the decision,
  never on the explanation of it* — is satisfied automatically. A set of atoms has no prose in it
  to perturb structural equality.
- **UNSAT is the honest `INFEASIBLE`.** Phase 1 had to invent a sentinel value because a
  discipline must be total. With ASP, "no feasible architecture exists" is not a convention we
  made up: it is a proof that no model satisfies the constraints.
- **Unsat cores give machine-checkable "why not".** 002's requirements↔feasibility negotiation
  stops being a plausible story and becomes *these constraints are mutually unsatisfiable, here
  they are*. That is traceability an engineer can take into a design review.
- **It targets the real gap.** Gradient-based MDO cannot touch discrete architectural choice.
  ASP is built for exactly that, and SmartMDAO's non-numeric convergence is what lets it couple to
  the continuous side.

### What this removes from scope

**SmartMDAO never calls a language model.** The coding agent generates the program; the library
runs clingo on it. The MCP `sampling` integration proposed in 002 and carried in the roadmap is no
longer needed, and the library takes no model-provider dependency of any kind.

`clingo` is one package with zero transitive dependencies — cheaper than anything removed in 1.7.0
and 1.8.0. It would be an optional extra regardless.

---

## Risks, stated plainly

### 1. The symbolic/numeric bridge is where the hypotheses hide

Going from `mass_kg = 880.0` to `mass(heavy)` requires a threshold. That threshold is a
**hypothesis**, it is usually buried in a helper function, and it silently determines the answer.
A perfectly reviewed ASP program sitting on top of an unreviewed discretisation is not traceable.

*Implication:* the discretisation layer must be first-class, declared and inspectable — reported
by `validate()` alongside everything else — not glue code between the two halves.

### 2. Determinism is not free

An ASP program can have many stable models. Same program, same facts, different answer set, unless
it is pinned with an optimisation statement plus a total tie-break.

*Implication:* a generated program that admits more than one optimal model is a **finding**, not a
detail. Enumerate and report it rather than silently taking the first.

### 3. The authoring step is still untrusted

Determinism lives in execution, not in generation. A model writes syntactically valid, semantically
wrong clingo very easily. The trust problem has been *moved* somewhere a human can review it —
that is the entire win — but it has not been eliminated.

*Implication:* if an engineer rubber-stamps generated rules, they have bought a false sense of
rigour and nothing else. Tooling must make review easy and make *skipping* it visible. This is the
one place the principle bites hardest: we do not prevent the engineer skipping review, but we must
never let them do it by accident.

### 4. Grounding can explode

ASP grounding is worst-case exponential. A generated program can be accidentally intractable in a
way nobody notices until it hangs — the same shape as the execution-cost problem in
[001](001-mcp-connector.md), and it wants the same answer: a budget and an estimate, not a spinner.

---

## What the principle forbids

Concrete rules this record imposes on anything built here:

1. **Never run something expensive without saying what it will cost first.** An estimate with a
   number, not a disclaimer.
2. **Never pick a default that changes the answer without saying so.** Convergence criteria, seeds,
   discretisation thresholds, translation fidelity — all are the engineer's to choose, and all must
   be visible when we choose one for them.
3. **Never present a generated artifact as verified.** Generated code, generated rules and
   generated translations are proposals until the engineer accepts them.
4. **Report what actually happened, including failure.** An abandoned solve returns its trace. A
   non-converged run says so. A finding that was suppressed says it was suppressed.

## Open questions

- **Naming.** "ASP-backed discipline" is provisional and used throughout this document for want of
  a settled term.
- **Where the program lives.** Inline in the Python file, or a separate `.lp` reviewed and
  version-controlled on its own? The second is better for review and diffing; the first keeps a
  discipline in one place.
- **How far the discretisation is declared.** A thin declarative mapping covers thresholds, but not
  every bridge is a threshold.
- **Whether the ASP layer belongs in this repository at all**, or in a companion package that
  depends on it. The dependency is cheap, but the concern is distinct.

## Related

- [001](001-mcp-connector.md) — the MCP connector, and the execution-cost problem this shares.
- [002](002-agent-as-discipline.md) — the mechanics this builds on; its production path is
  superseded here.
- [../roadmap.md](../roadmap.md) — phasing.
