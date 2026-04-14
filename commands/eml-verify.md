# /eml-verify

Generate `ProofScratch.lean` for one EML normalization attempt and run `lean_check.py`.

## Canonical Entry Point

`python scripts/eml_verify.py --formula "<expr>"`

## Outputs

- `proofs/ProofScratch.lean`
- `session_log.json`
- `evidence.json`
- `report.md`
- `artifacts/formula.json`
- `artifacts/eml.json`

## Hard Rules

- Only mark a root claim as `Formal support` when the root formula is in the exact shipped subset and `lean_check.py` succeeds.
- Keep unsupported arithmetic or branch assumptions in `unsupported_boundary` instead of guessing a witness.
- Record the exact verification method returned by `lean_check.py`.
