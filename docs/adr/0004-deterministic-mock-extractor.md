# ADR 0004: Deterministic mock extractor first; AI only proposes

Status: accepted (Checkpoint 1)

**Context.** No paid services without approval, and the workflow must be testable and
repeatable.

**Decision.** Extraction is behind an `Extractor` port with a strict output schema and a
guard. Checkpoint 1 ships only a rule-based mock. Whatever the extractor returns is a
proposal: customer matching, prices, rule application and approvals are decided by fixed
code. Customer text is never treated as instructions.

**Consequences.** The mock misses wording it was not written for, which shows up as
clarification questions rather than wrong quotes. A real model can be added in Checkpoint 2
and compared against this baseline on the same evaluation set.
