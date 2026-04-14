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
- Use `python "<resolved-skill-dir>/scripts/search_mathlib.py" "<query>"` for local search.

## Local Proof Workflow

- The plugin does not ship a standalone `proofs/` project. Formal checking only works when the target workspace already contains one.
- This repo is currently pinned to Lean `4.29.0` and `mathlib` `v4.29.0`.
- Install Lean 4 through `elan` or the direct Windows `Lean` package so `lake` is available.
- From `proofs/`, run `lake update`.
- Optionally run `lake exe cache get` after dependencies resolve to fetch prebuilt artifacts.
- Keep `proofs/lean-toolchain` and `proofs/lakefile.toml` on matching release lines when you intentionally upgrade.
- If `proofs/` is missing, report that the local Lean project is absent and keep all mathlib claims in the unverified or negative-evidence bucket.
