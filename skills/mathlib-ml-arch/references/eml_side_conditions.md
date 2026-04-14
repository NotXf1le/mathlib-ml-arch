# EML Side-Condition Taxonomy

Use this reference when a formula passes through the EML normalization pipeline.

## Kinds

- `domain`: mathematical preconditions required for the expression to be defined.
- `branch`: assumptions tied to principal-branch semantics for complex functions.
- `totalization`: runtime or framework behavior that can diverge from the mathematical model.

## Required Status Values

- `required`: needed for the complex-valued semantics itself.
- `required_for_real_output`: needed only when the downstream claim expects a real-valued result.
- `assumed`: an explicit branch convention or other semantic assumption.
- `boundary`: a runtime-facing risk that must stay in the unsupported-boundary bucket unless the code guarantees it.

## Typical Examples

- `domain`: `x != 0` before `log(x)` or `a / x`
- `branch`: `principal branch of Complex.log for log(x)`
- `totalization`: `runtime must avoid division by zero in a / x`

## Hard Rules

- Do not drop side conditions just because the normalized formula "looks standard."
- Do not upgrade a claim to `Formal support` unless the root claim is Lean-verified.
- Keep floating-point behavior in the `totalization` or `unsupported_boundary` layer unless the implementation proves the guard.
