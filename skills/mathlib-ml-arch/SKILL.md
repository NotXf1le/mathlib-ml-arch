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

6. If `doctor.py` reports that `lake` or `lean` are unavailable, bootstrap the plugin-local toolchain cache before continuing:

```bash
python "../../scripts/bootstrap_toolchain.py"
```

7. If the shared `proofs/` project is missing and formal checks should be possible, bootstrap before searching:

```bash
python "../../scripts/bootstrap_proofs.py"
```

That command creates or reuses the shared user-scoped workspace under `$CODEX_HOME/cache/mathlib-ml-arch/shared_workspace`.
Repo-local `proofs/` directories are treated as legacy state and ignored by the formal-workspace resolver.
If the shared workspace exists but is missing mathlib sources or compiled libraries, rerun `bootstrap_proofs.py` rather than concluding that formal verification is impossible.

If bootstrap is not possible in the current environment, say that formal verification is unavailable here.

8. Search shared evidence before making formal claims:

```bash
python "../../scripts/search_mathlib.py" "<query>"
```

Use `--json` when you want machine-readable theorem candidates with names, import paths, and locations.

9. Validate candidate theorems in the shared `proofs/` workspace before citing them as formal support:

```bash
python "../../scripts/lean_check.py"
```

The result counts as verified only when `lean_check.py` succeeds. If it falls back from `lake env lean` to direct `lean` with `LEAN_PATH`, record that verification method explicitly.

10. Write the response in this order:
   - Proposed architecture
   - Formal evidence from mathlib
   - Engineering inference built on top of formal facts
   - Gaps requiring benchmarks or papers
   - Risks

11. For nontrivial requests, emit the artifact bundle defined in `references/architecture_contract.md`:
   - `report.md`
   - `evidence.json`
   - `session_log.json` when bootstrap or verification diagnostics matter

   Keep the bundle consistent with the written response.
12. Validate the artifact bundle explicitly:

```bash
python "../../scripts/validate_artifact_bundle.py" --bundle-dir "<dir>"
```

## Hard Rules

- Never call a heuristic "proved".
- If theorem search or witness coverage really comes up empty, write `No direct formal support found in mathlib.`
- If a theorem only proves a local property, state the exact boundary.
- If Lean tooling is missing or `lean_check.py` does not succeed, treat the result as unverified.
- If Lean verification was unavailable or not run, say that formal support from Lean/mathlib was not obtained in this environment. Do not collapse that state into negative theorem evidence.
- If the shared proofs workspace does not exist yet, say that formal verification was unavailable because bootstrap was not run or the shared workspace is unusable. Do not attribute that case to theorem failure.
- If `doctor.py` reports a partial shared workspace, do not stop there. Run an action script or `bootstrap_proofs.py` so the plugin can attempt automatic repair before declaring the formal layer unavailable.
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
- `../../scripts/doctor.py`: inspect the shared Lean/mathlib environment and emit agent-friendly diagnostics, while flagging ignored repo-local `proofs/` directories when they exist.
- `../../scripts/bootstrap_toolchain.py`: populate a plugin-local Lean/Lake toolchain cache under `CODEX_HOME` so later plugin runs do not depend on the host profile.
- `../../scripts/bootstrap_proofs.py`: create or reuse the shared `proofs/` project, fetch cache when needed, and run a smoke verification pass.
- `../../scripts/search_mathlib.py`: search the shared `proofs/` project and any downloaded mathlib checkout for ranked theorem candidates.
- `../../scripts/lean_check.py`: verify a Lean target in the shared `proofs/` project via `lake env lean`, with direct `lean` fallback when the official path is unavailable and command timeouts recorded explicitly.
- `../../scripts/eml_normalize.py`: parse one explicit scalar formula into CalcLang, emit EML artifacts, and write typed side-condition bundles.
- `../../scripts/eml_verify.py`: generate a workspace-namespaced scratch file in shared `proofs/` for the exact shipped EML subset and update evidence metadata with Lean verification status.
- `../../scripts/boundary_classify.py`: extract domain, branch, and totalization assumptions into bundle artifacts without claiming Lean proof.
- `../../scripts/validate_artifact_bundle.py`: validate `report.md` and `evidence.json` directly, without hooks.
