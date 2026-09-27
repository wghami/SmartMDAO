# 009 — Inputs across the JSON boundary

**Status:** implemented — 1.28.0 (roadmap 6.9–6.13)
**Date:** 2026-09-27
**Relates to:** [003](003-determinism-and-the-engineer-in-the-loop.md) (say what was done, never
decide quietly); invariant 1 in [architecture.md](../architecture.md);
[007](007-project-interpreter.md) (the worker); F-011, F-016 and F-017 in
[requests/2026-09-27-paper-repro-f011.md](../requests/2026-09-27-paper-repro-f011.md)

---

## The problem

Every MCP tool call is JSON, and JSON cannot carry what Python pipelines take:

| Python value | After JSON | On 1.27.0 |
|---|---|---|
| `2.0` | `2` — many clients send a whole-number float as an integer | refused: `int` does not satisfy `float` |
| `(0.0, 0.0, 2.0, 3.0)` | a list | refused by `Tuple[...]` |
| `{0.5: 1.0}` | `{"0.5": 1.0}` | **accepted**: containers are checked on their outer type, so the string keys pass silently |
| a NumPy array | a nested list, if it serialises at all | refused, or wrong |

The first three were verified on 1.27.0; the NumPy row follows from JSON having no array type. The requester's baseline is full of whole-number floats
(550 km, 6378 km, 10 W, 26 GHz), so no smoke run of their real model could pass the MCP. The
same values work from Python.

## Two answers, because there are two problems

**1. Whole numbers are repaired at the boundary, and the repair is reported.** In `run_pipeline`
and `compare_runs`, the two places where a tool call supplies values, an `int` is passed as a
`float` when every step reading it declares a type that accepts a float and refuses an int.
`inputs_used.coerced` names each one. Precisely:

- **Never a `bool`.** `True` is an `int` in Python and a mistake in a float input.
- **Every consumer must declare the type.** A step that declares nothing might use the value as an
  index, so one such step leaves the value alone.
- **Exact or refused.** Above 2^53 a float cannot hold every integer. Such a value is refused,
  with the reason, rather than rounded.
- **The boundary only.** Python callers keep the strict rule, and cookbook pitfall 5 still holds
  there. Values recovered from the file's own `run()` call are Python literals and are not touched.

This is not the kind of default 003 forbids. The number is the same number, in the
representation the step declares, and the response says it happened.

**2. Everything else is built by the project, inside the run.**
`run_pipeline(path, inputs_from="package.module:function", inputs_args=[...], inputs_kwargs={...})`
calls the project's own function **inside the run process**: in the project's environment (007)
and under the run's wall clock. Tuples, arrays and float-keyed dicts go straight into the pipeline
and never become JSON. Only the spec and its arguments cross the boundary. `compare_runs` accepts
the same arguments and builds the inputs on each side.

- **Precedence:** what the call supplies, then what the project builds, then the file's own
  `run()` literals. `inputs_used` reports each group: `supplied`, `built_by_project`,
  `found_in_source`.
- **Where the module is found:** on the loader's search path for the entry file (its package's
  parent, or its own directory), so a `baseline.py` beside the pipeline is found like the
  pipeline's own imports.
- **What is refused, with the reason:** a malformed spec, a module or attribute that is not there,
  a function that raises, and a function that returns something other than a mapping from names to
  values.
- **Never in analysis.** `analyze`, `validate`, `explain` and `render` do not take `inputs_from`.
  They do not execute project code (invariant 1), and they need only input *names*, which
  `Pipeline(inputs=[...])` declares.

## Alternatives rejected

- **Coerce more at the boundary:** lists into tuples, string keys into floats. Each is a guess
  about what JSON lost, and some guesses are wrong: `"1e3"` as a key is not obviously `1000.0`.
  `inputs_from` removes the need to guess.
- **Loosen the type rule, so `int` satisfies `float` everywhere.** That would change Python
  callers' behaviour to fix a JSON problem. The strict rule catches real bugs, and a project that
  wants it looser can already supply its own `TypeChecker`.
- **Evaluate `inputs_from` in the server and send the result to the run.** The result would
  cross JSON again, and the problem would be back.
- **An expression language in the tool call** (`"inputs": {"bbox": "tuple(...)"}`). That is code
  execution through a string, harder to read than a named function in the project.

## Found while building it

- **`Literal` made `run()` crash for every value.** A `Protocol` not marked `@runtime_checkable`
  did too. The type checker passed both to `isinstance`, which raises `TypeError`. `Literal` is
  now checked on its values: a value must be one of the options, and a producer's `Literal` must
  fit within the consumer's. Anything `isinstance` cannot test is unchecked, never an error
  (invariant 2). Reported as F-018; the `Protocol` case was found by auditing the other special
  forms.
- **An older project cannot take new arguments.** A 1.28 server talking to a project pinned at
  1.26 or 1.27 sends only the arguments a call actually gives. A plain call therefore still works,
  and `inputs_from` is refused with "upgrade SmartMDAO there" rather than crashing the worker.
