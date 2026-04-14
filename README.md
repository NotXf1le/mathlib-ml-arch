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

The `skills/mathlib-ml-arch/scripts/` directory is implementation detail for the
skill bundle. README, command docs, and the skill now all point back to root
`scripts/` as the canonical launch surface.

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

## Manual Workflow

When you want a live review instead of the shipped demo:

1. Run `python scripts/doctor.py`.
2. If `proofs/` is missing, run `python scripts/bootstrap_proofs.py`.
3. Search candidate theorems with `python scripts/search_mathlib.py "<query>"`.
4. Verify `proofs/ProofScratch.lean` with `python scripts/lean_check.py`.
5. Write `report.md` and `evidence.json`.
6. Validate the bundle with `python scripts/validate_artifact_bundle.py --bundle-dir <dir>`.

When `lake env lean` is unavailable but compiled package libraries exist,
`lean_check.py` falls back to direct `lean` with discovered `LEAN_PATH` and
records that verification method explicitly.

If `proofs/` is missing, the correct output is that local formal verification is
unavailable in this workspace. Do not blame theorem search for that case.

## Hooks

Hooks are experimental and are not part of the first-release success path.

- This plugin does not auto-register hooks in `plugin.json`.
- `hooks.json` is kept only as an optional acceleration example.
- Its matcher is set to `Bash`, matching current PostToolUse runtime behavior.
- Windows users should assume the explicit validation command is the supported path.
