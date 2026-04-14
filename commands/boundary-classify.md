# /boundary-classify

Extract typed domain, branch, and totalization assumptions for one explicit scalar formula.

## Canonical Entry Point

`python scripts/boundary_classify.py --formula "<expr>"`

## Outputs

- `figures/boundary_graph.mmd`
- `evidence.json`
- `report.md`

## Notes

- This mode does not claim Lean proof by itself.
- Use it when you want bundle-grade assumption tracking before theorem search or proof generation.
