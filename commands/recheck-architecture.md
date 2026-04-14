# /recheck-architecture

Run the second-pass adversarial verification for a mathlib-backed architecture review.

## Inputs

- `report_path`: path to the first-pass report
- `source_paths`: files, notes, or equations to reopen for the recheck

## Workflow

1. Re-open the source material and the first-pass report.
2. Challenge the claims, especially boundaries, local lemmas, and inference jumps.
3. Correct any overstatement before finalizing.
4. Append a `RECHECK` section with unresolved points and final verdict.
5. Update `evidence.json` if claim labels or boundaries changed during recheck.
6. Re-run `python scripts/validate_artifact_bundle.py --bundle-dir <dir>` after edits.

## Output Contract

- corrections
- unresolved points
- final verdict

The recheck must never collapse empirical claims into formal support.
