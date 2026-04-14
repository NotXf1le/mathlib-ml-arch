# Mathlib Scope

Use this file to decide whether a claim belongs in the formal layer or outside it.

## What Mathlib Can Support

- Algebraic identities and tensor-free linear algebra reasoning.
- Matrix, vector space, linear map, and bilinear form properties.
- Normed space and inner product space facts.
- Measure-theoretic and probability-theoretic definitions and lemmas.
- Local analysis facts such as continuity, Lipschitz conditions, convexity, and boundedness when explicit theorems exist.

## What Mathlib Does Not Prove For You

- Model family selection such as Transformer vs. Mamba.
- Dataset fit, generalization quality, and benchmark outcomes.
- Hardware latency, memory-pressure tradeoffs, or deployment cost.
- Training stability in a concrete stack unless you can point to a theorem covering the exact setup.

## Search Playbook

- Start with domain nouns and structure names: `LinearMap`, `Matrix`, `InnerProductSpace`, `Norm`, `Measure`, `ProbabilityTheory`, `ConditionalExpectation`, `Independent`, `Lipschitz`, `convex`.
- Narrow from definitions to theorem names before citing anything.
- Prefer exact import paths and theorem names over broad paraphrases.
- Record negative evidence when no direct theorem is found.
- Use the plugin-root CLI entrypoint: `python "../../scripts/search_mathlib.py" "<query>"`.
- Prefer `--json` when you want machine-readable theorem candidates with names, import paths, and source locations.

## Local Proof Workflow

- The plugin does not ship a pre-fetched standalone `proofs/` project, but it does ship bootstrap and diagnostics helpers.
- Start with `python "../../scripts/doctor.py"` when you need to know whether the workspace is ready.
- If `proofs/` is missing, run `python "../../scripts/bootstrap_proofs.py"` before treating formal verification as available.
- This repo is currently pinned to Lean `4.29.0` and `mathlib` `v4.29.0`.
- Install Lean 4 through `elan` or the direct Windows `Lean` package so `lake` is available.
- From `proofs/`, run `lake update` only when mathlib sources are missing or you intentionally want to refresh dependencies.
- Run `lake exe cache get` when compiled `.olean` artifacts are missing.
- Keep `proofs/lean-toolchain` and `proofs/lakefile.toml` on matching release lines when you intentionally upgrade.
- `lean_check.py` now treats `lake env lean` as the preferred verification path and direct `lean` with discovered `LEAN_PATH` as an explicit fallback.
- Validate finished bundles with `python "../../scripts/validate_artifact_bundle.py" --bundle-dir "<dir>"`.
- On sandboxed Windows runs, the plugin injects temporary `git safe.directory` entries for `proofs/.lake/packages/*` so `lake` can inspect package repos without global git config changes.
- If `proofs/` is missing and bootstrap is not run, report that the local Lean project is absent and keep all mathlib claims in the unverified or negative-evidence bucket.
