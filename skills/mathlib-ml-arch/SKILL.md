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
4. Search local evidence before making formal claims:

```bash
python "<resolved-skill-dir>/scripts/search_mathlib.py" "<query>"
```

5. Confirm that the target workspace has a local Lean project at `proofs/` before treating formal verification as available. The plugin does not bundle one for you.
6. Validate candidate theorems in `proofs/ProofScratch.lean` before citing them as formal support:

```bash
python "<resolved-skill-dir>/scripts/lean_check.py"
```

7. Write the response in this order:
   - Proposed architecture
   - Formal evidence from mathlib
   - Engineering inference built on top of formal facts
   - Gaps requiring benchmarks or papers
   - Risks

8. For nontrivial requests, emit the artifact bundle defined in `references/architecture_contract.md`:
   - `report.md`
   - `evidence.json`

   Keep the bundle consistent with the written response.

## Hard Rules

- Never call a heuristic "proved".
- If no supporting theorem is found or verified, write `No direct formal support found in mathlib.`
- If a theorem only proves a local property, state the exact boundary.
- If Lean tooling is missing or the scratch file was not checked, treat the result as unverified.
- If the workspace has no `proofs/` directory, say that formal verification was unavailable because the local Lean project is missing. Do not attribute that case to `lake`, `git`, or theorem failure.
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
- `scripts/search_mathlib.py`: search the current repo's `proofs/` project and any downloaded mathlib checkout for candidate definitions and theorems.
- `scripts/lean_check.py`: run `lake env lean` on the current repo's `proofs/ProofScratch.lean` when Lean and mathlib are installed locally.
