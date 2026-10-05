# ADR 0005: Server-rendered HTML forms, no JavaScript

Status: accepted (Checkpoint 1)

**Context.** The plan proposed Jinja2 with HTMX. Every screen turned out to be a page plus
forms that post and redirect.

**Decision.** Plain HTML forms with CSRF tokens and post/redirect/get. HTMX dropped.

**Consequences.** Simpler to test and review; no live updates (refresh to see dispatcher
progress).
