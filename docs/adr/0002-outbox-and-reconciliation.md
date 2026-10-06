# ADR 0002: Transactional outbox with idempotency keys and reconciliation

Status: accepted (Checkpoint 1)

**Context.** Sending a quote twice, or not knowing whether it was sent, is the main
execution risk. Calls can fail, time out, or lose their response.

**Decision.** Approval writes outbox rows in the same transaction. A dispatcher leases rows,
records every attempt, and passes a stable idempotency key per action. When an outcome is
uncertain it looks the key up before any resend; if it still cannot tell, the workflow
escalates to a person instead of guessing.

**Consequences.** At-least-once delivery becomes effectively once for adapters that support
key lookup. Adapters that cannot look up by key will escalate more often.
