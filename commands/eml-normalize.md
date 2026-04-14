# /eml-normalize

Normalize one explicit scalar formula into CalcLang v1 and emit EML-focused artifacts.

## Canonical Entry Point

`python scripts/eml_normalize.py --formula "<expr>"`

## Outputs

- `artifacts/formula.json`
- `artifacts/eml.json`
- `figures/eml_tree.mmd`
- `figures/boundary_graph.mmd`
- `report.md`
- `evidence.json`

## Notes

- v1 is conservative and only supports explicit scalar syntax.
- Parsing and side-condition extraction cover more operators than the exact EML witness library.
- Exact pure-EML proofs are currently limited to the shipped subset `{1, var, exp}`.
