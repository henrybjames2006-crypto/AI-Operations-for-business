# ADR 0006: Optional AI reader with a budget and rule-based fallback

Status: accepted (version 0.2.0)

**Context.** The rule-based reader handles the wording it was written for and misses much
else (16 of 30 held-out cases fully correct). A language model may read varied wording
better, but it costs money per request, can fail or time out, and reads untrusted text.

**Decision.** Add a Claude reader behind the existing `Extractor` interface, off by default.
It returns a fixed JSON schema and passes through the same guard as the rules. A wrapper
enforces an app budget, retries temporary failures once, and falls back to the rule-based
reader on any failure, recording why. The default model is Claude Haiku 4.5, the cheapest
listed, as agreed in the 0.2.0 plan; the model is a setting. Nothing about pricing,
customers or approval moved to the model.

**Consequences.** A request is always read, even with no network or key. Costs are visible
per workflow. The budget is estimated locally and is not a billing cap. Choosing between
the readers is a measured decision (`python -m opsapp eval compare`), not an assumption.
