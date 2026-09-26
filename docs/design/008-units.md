# 008 — Units, checked for consistency and never converted

**Status:** implemented — 1.27.0 (roadmap 6.8), as proposed; findings at the end
**Date:** 2026-09-26
**Relates to:** [003](003-determinism-and-the-engineer-in-the-loop.md) (a default must not decide
the answer); invariants 1 and 2 in [architecture.md](../architecture.md); request R7 in
[requests/2026-09-paper-repro.md](../requests/2026-09-paper-repro.md)

---

## The problem

A unit today lives only in a variable's name (`_db`, `_dbm`, `_hz`, `_mbps`, `_km`), where
nothing reads it. The requester's link budget is the textbook case. dB against linear, and Mbps
against bit/s, are the classic bugs, and 14 of their first 35 inputs carry a unit that matters to
the result. A slip does not fail: it produces a number, and that number converges.

Verified on 1.26.0. Every step below validates clean and runs:

```python
@p.step(outputs=["gain"])
def antenna(power_w: Annotated[float, "W"]) -> Annotated[float, "dB"]: ...

@p.step(outputs=["margin"])
def budget(gain: Annotated[float, "linear"], distance_km: Annotated[float, "km"]) -> float: ...
```

`validate(p)` returns nothing. `Annotated` is already safe to write, because `get_type_hints`
strips its metadata so the type checker sees plain `float`. But nothing reads what it says.

The problem is not limited to physics. Seconds against milliseconds, MB against MiB, EUR against
USD: any workflow passing numbers between steps can mismatch this way.

## The rule this record starts from

**SmartMDAO will never convert units.** It reports that two ends of a connection disagree and does
nothing else. A silent conversion is a default that changes the answer, which
[003](003-determinism-and-the-engineer-in-the-loop.md) forbids. It would also make SmartMDAO
responsible for every conversion factor in every domain it will ever meet. Everything below is
consistency checking. Two further rules come from the invariants:

- **Read without executing** (invariant 1): units come from annotations, through
  `get_type_hints(..., include_extras=True)`, and no step is called.
- **A missing unit is "unchecked", never an error** (invariant 2): a connection is checked only when
  **both** ends declare a unit.

---

## Proposal

### 1. How a unit is written: a marker, not a bare string

```python
from typing import Annotated
from smartmdao import Unit

dB = Annotated[float, Unit("dB")]            # aliases keep signatures readable

@p.step(outputs=["gain"])
def antenna(power_w: Annotated[float, Unit("W")]) -> dB: ...
```

`Annotated` metadata is shared ground. Pydantic, Typer and documentation tools put their own
objects there, and people write plain strings there as descriptions: `Annotated[float, "wing
span"]`. Reading any string as a unit would report "wing span" against "m" as a mismatch, which is
a guess. A `Unit` marker says what it is. Bare strings are ignored.

Where units are read:

| Place | From |
|---|---|
| A parameter (consumer end) | its annotation |
| A single output (producer end) | the return annotation |
| Dataclass outputs | each field's annotation (verified readable) |
| Tuple outputs with `outputs=[...]` | each element's annotation (verified readable) |

### 2. What "consistent" means: exactly the same unit, and a pluggable checker

The default `StandardUnitChecker` compares the two strings after trimming whitespace. `km` into
`m` is reported. So is `meter` into `m`, which is arguably a false alarm, but deciding that two
spellings are the same unit is exactly the kind of equivalence SmartMDAO should not guess. `dB`
and `dBm` look close and differ by a reference power.

Checking is pluggable, modelled on `TypeChecker`:

```python
class UnitChecker(Protocol):
    def consistent(self, produced: str, expected: str) -> bool: ...
```

A project that wants `m` and `meter` treated alike, or wants dimensional analysis from a library
like pint, supplies its own checker: `Pipeline(unit_checker=...)`. The checker still only answers
yes or no. There is no hook through which a value can be rescaled.

### 3. Where mismatches are found

| Connection | Checked when | Finding |
|---|---|---|
| producer output → consumer parameter | both declare a unit | `unit-mismatch` |
| one external input → several consumers | two consumers declare different units | `unit-mismatch` |
| either end undeclared | never | none (counted, see below) |

- **Severity: error**, like `type-mismatch`. An annotation saying dB feeding one saying linear
  is wrong by the pipeline's own account, and the connection is where the answer goes wrong.
- **The message names both ends and both units.** When one side is a logarithmic unit (`dB`,
  `dBm`, `dBW`, `dBi`) and the other is not, it adds that they differ by a logarithm, not a
  factor. That is a hint in the message, not a conversion.
- **Coverage is stated.** `explain()` says "units: 12 of 30 connections checked". A pipeline with
  no units is no worse than today, and the engineer can see how much of it is checked.

Nothing happens at run time. Values stay plain numbers, units cost nothing in a sweep, and there
is no runtime check to switch off.

### 4. On the diagram

A data cell shows its unit when the producer declares one: `gain [dB]`. A mismatched connection is
drawn in the missing-input style. This is optional, and could ship after the check itself.

---

## Found while writing this: an external input's *type* is not checked across consumers

The same probe declared `distance_km: float` in one step and `distance_km: str` in another. Both
consume the same external input. `validate()` reported nothing, and the conflict surfaced only at
`run()`, as a `TypeMismatchError`, once a value was passed. Two consumers disagreeing about an
input is visible statically, for types as for units. **Proposed:** fix it in 6.8 with the same
code path, as a `type-mismatch` on the external input.

## Alternatives rejected

- **Infer units from names** (`distance_km` → km). The requester's convention is consistent, but a
  convention is not a declaration. `_m` is metres in one model and months in another. A guess
  presented as a check is worse than no check.
- **A built-in unit library** (pint or similar) as a dependency. It would bring dimensional analysis
  and, inevitably, the temptation to convert. The pluggable checker lets a project use one without
  SmartMDAO depending on it.
- **Units as a runtime wrapper type** (a `Quantity` flowing through the pipeline). It changes every
  discipline's values, costs at run time and in every sweep, and puts conversion one method call
  away.
- **Warning instead of error.** A declared mismatch is not a possible problem; the declarations
  contradict each other. `type-mismatch` sets the precedent.

## Open questions for the maintainer — answered 2026-09-26

All five as proposed: a `Unit` marker, exact match with a pluggable checker, error, units on the
diagram now, and the external-input type gap fixed in 6.8.

## Exit criterion for 6.8

A `dB` output wired into a `linear` parameter, and one external input declared in `km` by one step
and `m` by another, are both reported by `validate()` without executing anything. A pipeline with
no `Unit` anywhere validates exactly as before. `explain()` states how many connections were
checked. No value is ever changed.

---

## Findings from building it (6.8)

- **"Two consumers disagree about a type" means that no single value could satisfy both, not that
  the annotations differ.** `float` and `Optional[float]` differ, and a float satisfies both. So
  two types conflict only when neither is accepted for the other by the pipeline's type checker,
  and no class in one is related to a class in the other. A custom `TypeChecker` that accepts
  `int` for `float` therefore silences the finding, as it should.
- **`B` is not treated as logarithmic.** It is a bel in acoustics, but a byte in every data
  workflow, and the hint would mislead far more often than it would help.
- **Coverage counts connections between steps only.** An external input has no producer to
  compare with, so "checked" means something different there. Disagreements between its
  consumers are reported, but not counted.

