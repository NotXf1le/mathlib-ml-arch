# /review-architecture

Audit an ML architecture with the `mathlib-ml-arch` discipline.

## Inputs

- `architecture_notes`: the architecture, code paths, or design description to audit
- `focus`: optional area to emphasize, such as routing, memory, optimization, or geometry

## Workflow

1. Inventory the important internal variables, modules, and state.
2. Assign entity signatures and typed interfaces where the design mixes roles.
3. Check whether the target workspace has a local `proofs/` project. If not, state that formal verification is unavailable in this workspace.
4. Search for local mathlib evidence before claiming formal support.
5. Separate each claim into formal support, engineering inference, or empirical gap.
6. Emit the artifact bundle required by the skill contract.

## Output Contract

- Proposed architecture
- Formal evidence from mathlib
- Engineering inference built on top of formal facts
- Gaps requiring benchmarks or papers
- Risks

The default artifact location should be `reports/` next to the workspace root or
plugin root. The post-write hook watches that location and validates
`report.md` or `architecture_audit_report*.md` plus `evidence.json` when they
become structurally valid. Sample/demo artifacts belong in
`fixtures/review-architecture/`.

If no theorem or definition can be verified, state:

`No direct formal support found in mathlib.`

If the workspace lacks `proofs/`, state that the local Lean project is missing
instead of attributing the failure to `lake`, `git`, or theorem search.
