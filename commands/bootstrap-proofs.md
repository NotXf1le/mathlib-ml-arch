# /bootstrap-proofs

Prepare the current workspace for mathlib-backed verification.

## Workflow

1. Use the root CLI entrypoint: `python scripts/doctor.py`.
2. If `proofs/` is missing, create it with `python scripts/bootstrap_proofs.py`.
3. Fetch mathlib sources and cache only when they are actually missing.
4. Create or preserve `proofs/ProofScratch.lean`.
5. Run `python scripts/lean_check.py` and record the verification method.

## Expected Outputs

- a ready `proofs/` project
- `ProofScratch.lean`
- machine-readable bootstrap diagnostics when `--json` is used
