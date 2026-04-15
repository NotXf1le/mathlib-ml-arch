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
- `python scripts/bootstrap_toolchain.py`
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
- Generate a shared scratch Lean file, `report.md`, `evidence.json`, and Mermaid figures

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
2. If `doctor.py` reports that `lake` / `lean` are missing, run `python scripts/bootstrap_toolchain.py` first. That command populates the plugin-local toolchain cache under `$CODEX_HOME/cache/mathlib-ml-arch/toolchains` and falls back to a temp cache when the shared CODEX_HOME root is not writable.
3. Run `python scripts/bootstrap_proofs.py` and let it create or reuse the shared user-scoped proofs workspace under `$CODEX_HOME/cache/mathlib-ml-arch/shared_workspace`.
5. Search candidate theorems with `python scripts/search_mathlib.py "<query>"`.
6. Verify `proofs/ProofScratch.lean` with `python scripts/lean_check.py`.
7. Search and verification use the shared proofs workspace only. Repo-local `proofs/` directories are ignored as legacy state.
8. Write `report.md` and `evidence.json`.
9. Validate the bundle with `python scripts/validate_artifact_bundle.py --bundle-dir <dir>`.

`bootstrap_proofs.py` now performs explicit writability checks before creating a
shared workspace. The JSON payload and human output record the effective `HOME`
/ `ELAN_HOME`, warnings, and postconditions so partial bootstrap progress is
easier to diagnose. Repo-local `proofs/` directories are detected and surfaced
as ignored legacy state instead of being used for verification.
When `search_mathlib.py`, `lean_check.py`, or `eml_verify.py` encounter a
missing or partial shared workspace, they now attempt shared bootstrap
automatically before giving up, and surface bootstrap/network diagnostics in
their JSON output.

`bootstrap_toolchain.py` prefers a plugin-local toolchain cache before the host
profile. It first reuses any cached `lake` / `lean`, then tries to copy the
active host toolchain into the plugin cache, and only falls back to `elan
toolchain install` when the cache still does not contain usable binaries. When
the shared `$CODEX_HOME/cache/...` root is not writable, both toolchain and
shared proofs cache locations fall back to a temp cache automatically.

Use `python scripts/bootstrap_proofs.py --timeout-seconds 600` in slow or
sandboxed environments. If `lake update` or `lake exe cache get` report a
late-stage cleanup failure but mathlib sources and compiled libraries are
already present, bootstrap now surfaces that as a warning instead of an opaque
hard failure.

For formula-specific workflows:

1. Run `python scripts/eml_normalize.py --formula "<expr>"`.
2. Inspect `artifacts/formula.json`, `artifacts/eml.json`, `figures/eml_tree.mmd`, and `figures/boundary_graph.mmd`.
3. Run `python scripts/eml_verify.py --formula "<expr>"` when you want a shared, workspace-namespaced scratch proof attempt plus Lean diagnostics.
4. Use `python scripts/boundary_classify.py --formula "<expr>"` when you only need typed assumptions without a proof attempt.

When `lake env lean` is unavailable but compiled package libraries exist,
`lean_check.py` falls back to direct `lean` with discovered `LEAN_PATH` and
records that verification method explicitly.

If the shared user-scoped proofs workspace does not exist, the correct output
is that formal verification is unavailable until bootstrap is run. Do not blame
theorem search for that case.

## Hooks

Hooks are experimental and are not part of the first-release success path.

- This plugin does not auto-register hooks in `plugin.json`.
- `hooks.json` is kept only as an optional acceleration example.
- Its matcher is set to `Bash`, matching current PostToolUse runtime behavior.
- Windows users should assume the explicit validation command is the supported path.

Run `python scripts/doctor.py` before bootstrap if you need to confirm whether
the shared cache is writable or which `HOME` / `ELAN_HOME` the toolchain will
use.
