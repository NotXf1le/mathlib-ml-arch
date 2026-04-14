# Sample Architecture Notes

Review a retrieval controller that mixes two semantically different residual streams:

- a projection branch that should stay inside a typed subspace
- a residual merge that is shape-compatible but may still be semantically unsafe

Desired outcome:

- keep one locally verified invariant
- mark one optimizer or product-level claim as unsupported
