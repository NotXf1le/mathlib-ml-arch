# Mathlib ML Architect Plugin

Stops Codex from laundering engineering guesses into fake mathematical support.

This plugin forces one separation every serious ML architecture review needs:

- what Lean/mathlib actually supports
- what is only an engineering inference
- what still depends on benchmarks, papers, data, or systems behavior

## Happy Path

After installing the plugin, run one explicit demo command:

```bash
python scripts/review_architecture.py --demo --output-dir reports/mathlib_ml_arch_demo
```

That command gives you one end-to-end artifact bundle:

- `report.md`
- `evidence.json`

It also validates the bundle directly and surfaces one verified theorem plus one
unsupported boundary from the shipped sample architecture review.

## Canonical CLI Surface

All public runnable entrypoints live in root `scripts/`:

- `python scripts/review_architecture.py --demo --output-dir <dir>`
- `python scripts/validate_artifact_bundle.py --bundle-dir <dir>`
- `python scripts/doctor.py`
- `python scripts/bootstrap_proofs.py`
- `python scripts/search_mathlib.py "<query>"`
- `python scripts/lean_check.py`
- `python scripts/eml_normalize.py --formula "<expr>"`
- `python scripts/eml_verify.py --formula "<expr>"`
- `python scripts/boundary_classify.py --formula "<expr>"`

The `skills/mathlib-ml-arch/scripts/` directory is implementation detail for the
skill bundle. README, command docs, and the skill now all point back to root
`scripts/` as the canonical launch surface.

## EML Formula Pipeline

The plugin now ships a conservative scalar formula pipeline that sits beside the
existing bootstrap/search/check flow:

- Parse one explicit formula into CalcLang v1
- Desugar supported shorthands such as `sigmoid` and `tanh`
- Normalize the formula deterministically
- Extract typed side conditions (`domain`, `branch`, `totalization`)
- Attempt pure-EML compilation for the currently shipped exact subset `{1, var, exp}`
- Generate `ProofScratch.lean`, `report.md`, `evidence.json`, and Mermaid figures

The current EML witness library is intentionally honest: parsing and boundary
classification cover scalar arithmetic, division, logarithm, and square root,
but exact pure-EML proofs are only shipped for the subset `{1, var, exp}`.
Unsupported nodes stay explicit in artifacts instead of being silently guessed.

## Review Outcome

For nontrivial reviews, the plugin expects a compact artifact bundle alongside
the written response:

- `report.md`
- `evidence.json`
- `session_log.json` when setup or verification diagnostics matter

Validate bundles explicitly:

```bash
python scripts/validate_artifact_bundle.py --bundle-dir <dir>
```

The report order and evidence fields are defined in
`skills/mathlib-ml-arch/references/architecture_contract.md`.

`evidence.json` records now include:

- `verified_in_lean`
- `verification_method`
- `side_conditions`

## Manual Workflow

When you want a live review instead of the shipped demo:

1. Run `python scripts/doctor.py`.
2. If this repo needs its own Lean project, run `python scripts/bootstrap_proofs.py --scope local`.
3. Otherwise run `python scripts/bootstrap_proofs.py` and let it create or reuse the shared user-scoped proofs workspace under `$CODEX_HOME/cache/mathlib-ml-arch/shared_workspace`.
4. Search candidate theorems with `python scripts/search_mathlib.py "<query>"`.
5. Verify `proofs/ProofScratch.lean` with `python scripts/lean_check.py`.
6. Both search and verification prefer a repo-local `proofs/` project when one exists and otherwise fall back to the shared workspace automatically.
7. Write `report.md` and `evidence.json`.
8. Validate the bundle with `python scripts/validate_artifact_bundle.py --bundle-dir <dir>`.

For formula-specific workflows:

1. Run `python scripts/eml_normalize.py --formula "<expr>"`.
2. Inspect `artifacts/formula.json`, `artifacts/eml.json`, `figures/eml_tree.mmd`, and `figures/boundary_graph.mmd`.
3. Run `python scripts/eml_verify.py --formula "<expr>"` when you want a `ProofScratch.lean` attempt plus Lean diagnostics.
4. Use `python scripts/boundary_classify.py --formula "<expr>"` when you only need typed assumptions without a proof attempt.

When `lake env lean` is unavailable but compiled package libraries exist,
`lean_check.py` falls back to direct `lean` with discovered `LEAN_PATH` and
records that verification method explicitly.

If neither a repo-local `proofs/` project nor the shared user-scoped proofs
workspace exists, the correct output is that formal verification is unavailable
until bootstrap is run. Do not blame theorem search for that case.

## Hooks

Hooks are experimental and are not part of the first-release success path.

- This plugin does not auto-register hooks in `plugin.json`.
- `hooks.json` is kept only as an optional acceleration example.
- Its matcher is set to `Bash`, matching current PostToolUse runtime behavior.
- Windows users should assume the explicit validation command is the supported path.
