# ADR 0003: Approval is bound to a hash of the exact quote and actions

Status: accepted (Checkpoint 1)

**Context.** An approval must mean "these exact numbers, sent to this recipient, with these
proposed times", not "this workflow".

**Decision.** Approval stores the SHA-256 of the quote version id, its content hash and each
action payload hash. Approving with a different hash is refused; any edit voids approvals.
The preparer or submitter can never approve.

**Consequences.** Every change, even a recipient fix, needs a fresh approval. That is the
intended trade-off.
