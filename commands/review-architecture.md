# /review-architecture

Audit an ML architecture with the `mathlib-ml-arch` discipline.

## Canonical Entry Point

The public CLI surface for this plugin lives in root `scripts/`.

Happy-path demo:

`python scripts/review_architecture.py --demo --output-dir reports/mathlib_ml_arch_demo`

## Inputs

- `architecture_notes`: the architecture, code paths, or design description to audit
- `focus`: optional area to emphasize, such as routing, memory, optimization, or geometry

## Workflow

1. Inventory the important internal variables, modules, and state.
2. Assign entity signatures and typed interfaces where the design mixes roles.
3. Run `python scripts/doctor.py` when the local Lean setup is uncertain.
4. If the target workspace has no repo-local `proofs/` project and formal checks should be possible, run `python scripts/bootstrap_proofs.py`. That command reuses or creates the shared user-scoped proofs workspace unless the repo explicitly needs `--scope local`.
5. Search for local or shared mathlib evidence before claiming formal support.
6. Verify candidate theorems with `python scripts/lean_check.py`, and record the exact verification method if fallback verification was used.
7. Separate each claim into formal support, engineering inference, or empirical gap.
8. Emit the artifact bundle required by the skill contract.
9. Validate the bundle explicitly with `python scripts/validate_artifact_bundle.py --bundle-dir <dir>`.
10. When the audit depends on one explicit scalar formula, prefer the EML helpers first:
    - `python scripts/eml_normalize.py --formula "<expr>"`
    - `python scripts/eml_verify.py --formula "<expr>"`
    - `python scripts/boundary_classify.py --formula "<expr>"`

## Output Contract

- Proposed architecture
- Formal evidence from mathlib
- Engineering inference built on top of formal facts
- Gaps requiring benchmarks or papers
- Risks

The default artifact location should be `reports/` next to the workspace root or
plugin root. Sample/demo artifacts belong in `fixtures/review-architecture/`.
Add `session_log.json` when bootstrap or verification diagnostics are part of
the audit trail.

If no theorem or definition can be verified, state:

`No direct formal support found in mathlib.`

If neither a repo-local `proofs/` project nor the shared user-scoped proofs
workspace exists, state that no usable Lean project is available instead of
attributing the failure to `lake`, `git`, or theorem search.
