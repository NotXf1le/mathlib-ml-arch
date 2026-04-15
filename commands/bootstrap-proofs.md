# /bootstrap-proofs

Prepare the current workspace for mathlib-backed verification.

## Workflow

1. Use the root CLI entrypoint: `python scripts/doctor.py`.
2. If `doctor.py` reports that `lake` / `lean` are unavailable, run `python scripts/bootstrap_toolchain.py` first so the plugin can use a cache-local toolchain.
3. Run `python scripts/bootstrap_proofs.py` and let it create or reuse the shared proofs workspace under `$CODEX_HOME/cache/mathlib-ml-arch/shared_workspace`.
5. Fetch mathlib sources and cache only when they are actually missing.
6. Create or preserve `proofs/ProofScratch.lean`.
7. Run `python scripts/lean_check.py` and record the verification method.
8. Use `--timeout-seconds <n>` in slow or sandboxed environments.
9. Read warnings in the bootstrap payload before retrying: late cleanup failures are non-fatal when required postconditions already hold.

## Expected Outputs

- a ready shared `proofs/` project
- `ProofScratch.lean`
- machine-readable bootstrap diagnostics when `--json` is used
- actionable fallback guidance when the shared CODEX_HOME cache is not writable
