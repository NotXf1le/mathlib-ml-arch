# Architecture Contract

Use this output contract whenever the skill is active.

## Required Output Order

1. Proposed architecture
2. Formal evidence from mathlib
3. Engineering inference built on top of formal facts
4. Gaps requiring benchmarks or papers
5. Risks

## Claim Labels

- `Formal support`: A theorem or definition was located and the exact subclaim was verified.
- `Partial formal support`: A theorem supports a local property but not the whole design decision.
- `No direct formal support found in mathlib`: No verified theorem or definition supports the claim.
- `Empirical gap`: The claim depends on benchmarks, hardware, datasets, training dynamics, or product constraints.

## Evidence Record

For each cited theorem or definition, record:

- name
- import path
- faithful paraphrase of the statement
- supported subclaim
- unsupported boundary

## Artifact Bundle

For nontrivial requests, produce the following files alongside the response:

- `report.md`
- `evidence.json`
- `session_log.json` when setup, verification, or fallback diagnostics matter

The `evidence.json` file should be machine-readable and include, at minimum, these fields for each claim or theorem record:

- `name`
- `import_path`
- `plain_language_meaning`
- `supported_subclaim`
- `unsupported_boundary`
- `claim_label`
- `verified_in_lean`
- `verification_method`

The textual response should still follow the required output order, and the artifact bundle should not contradict it.

Validate the finished bundle explicitly with:

`python scripts/validate_artifact_bundle.py --bundle-dir <dir>`

## Do Not Collapse Categories

- Do not present benchmark expectations as formal results.
- Do not present local invariants as proof of end-to-end model quality.
- Do not treat an unchecked `ProofScratch.lean` edit as verified evidence.
- Do not treat absence of a counterexample as proof.

## Example Split

- `Orthogonal projection is idempotent`: potentially formal.
- `This projection layer improves retrieval quality`: empirical.
- `This invariant may reduce numerical drift in a long pipeline`: engineering inference.
