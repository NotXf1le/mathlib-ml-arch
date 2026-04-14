---
name: mathlib-ml-arch
description: Ground ML architecture reasoning in Lean mathlib evidence. Use when the task involves ML architecture proposals, invariants, probabilistic semantics, linear algebra correctness, numerical stability, optimizer-side reasoning, or any mathematical claim that should be separated into formal support, engineering inference, and empirical gaps.
---

# Mathlib-Backed ML Architecture

Use this skill to keep formal math, architecture judgment, and empirical claims separate.

## Workflow

1. Classify every requested claim as one of:
   - formally checkable in Lean/mathlib
   - engineering inference built on formal facts
   - empirical or product/system constraint
2. Read `references/architecture_contract.md` first.
3. Read `references/mathlib_scope.md` when you need theorem families, search patterns, or local setup reminders.
4. The canonical public CLI surface for this plugin lives in the plugin-root `scripts/` directory. From this skill directory, that path is `../../scripts/`.
   Formula-specific workflows can also use:

```bash
python "../../scripts/eml_normalize.py" --formula "<expr>"
python "../../scripts/eml_verify.py" --formula "<expr>"
python "../../scripts/boundary_classify.py" --formula "<expr>"
```
5. If the local Lean setup is unclear, inspect it first:

```bash
python "../../scripts/doctor.py"
```

6. If the repo has no usable `proofs/` project and formal checks should be possible, bootstrap before searching:

```bash
python "../../scripts/bootstrap_proofs.py"
```

That command prefers a repo-local `proofs/` project when one already exists and otherwise creates or reuses the shared user-scoped workspace under `$CODEX_HOME/cache/mathlib-ml-arch/shared_workspace`.

If the repo specifically needs its own Lean project, use:

```bash
python "../../scripts/bootstrap_proofs.py" --scope local
```

If bootstrap is not possible in the current environment, say that formal verification is unavailable here.

7. Search local or shared evidence before making formal claims:

```bash
python "../../scripts/search_mathlib.py" "<query>"
```

Use `--json` when you want machine-readable theorem candidates with names, import paths, and locations.

8. Validate candidate theorems in `proofs/ProofScratch.lean` before citing them as formal support:

```bash
python "../../scripts/lean_check.py"
```

The result counts as verified only when `lean_check.py` succeeds. If it falls back from `lake env lean` to direct `lean` with `LEAN_PATH`, record that verification method explicitly.

9. Write the response in this order:
   - Proposed architecture
   - Formal evidence from mathlib
   - Engineering inference built on top of formal facts
   - Gaps requiring benchmarks or papers
   - Risks

10. For nontrivial requests, emit the artifact bundle defined in `references/architecture_contract.md`:
   - `report.md`
   - `evidence.json`
   - `session_log.json` when bootstrap or verification diagnostics matter

   Keep the bundle consistent with the written response.
11. Validate the artifact bundle explicitly:

```bash
python "../../scripts/validate_artifact_bundle.py" --bundle-dir "<dir>"
```

## Hard Rules

- Never call a heuristic "proved".
- If no supporting theorem is found or verified, write `No direct formal support found in mathlib.`
- If a theorem only proves a local property, state the exact boundary.
- If Lean tooling is missing or `lean_check.py` does not succeed, treat the result as unverified.
- If neither a repo-local `proofs/` project nor the shared user-scoped proofs workspace exists, say that formal verification was unavailable because no usable Lean project was present or bootstrap was not run. Do not attribute that case to theorem failure.
- If fallback verification was used, record the exact verification method rather than implying the official `lake env lean` path succeeded.
- For nontrivial requests, treat the artifact bundle as required rather than optional.

## Evidence Record

For each cited theorem or definition, record:

- theorem or definition name
- import path
- faithful plain-language meaning
- exact architectural subclaim it supports
- exact boundary it does not support

## Local Resources

- `references/architecture_contract.md`: required output contract and claim-labeling rules.
- `references/mathlib_scope.md`: boundaries of what mathlib can and cannot support, plus query ideas and local setup notes.
- `references/eml_side_conditions.md`: domain, branch, and totalization taxonomy for EML-normalized formulas.
- `../../scripts/doctor.py`: inspect repo-local and shared Lean/mathlib environments and emit agent-friendly diagnostics.
- `../../scripts/bootstrap_proofs.py`: create or reuse a repo-local or shared `proofs/` project, fetch cache when needed, and run a smoke verification pass.
- `../../scripts/search_mathlib.py`: search the selected repo-local or shared `proofs/` project and any downloaded mathlib checkout for ranked theorem candidates.
- `../../scripts/lean_check.py`: verify `proofs/ProofScratch.lean` via `lake env lean`, with direct `lean` fallback when the official path is unavailable and command timeouts recorded explicitly.
- `../../scripts/eml_normalize.py`: parse one explicit scalar formula into CalcLang, emit EML artifacts, and write typed side-condition bundles.
- `../../scripts/eml_verify.py`: generate `ProofScratch.lean` for the exact shipped EML subset and update evidence metadata with Lean verification status.
- `../../scripts/boundary_classify.py`: extract domain, branch, and totalization assumptions into bundle artifacts without claiming Lean proof.
- `../../scripts/validate_artifact_bundle.py`: validate `report.md` and `evidence.json` directly, without hooks.
