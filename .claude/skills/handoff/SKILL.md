---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff document summarizing the current conversation so a fresh agent can continue the work. Save to `HANDOFF.md` in the project root (next to `CLAUDE.md`), overwriting that file if it exists.

Include a "suggested skills" section listing skills that the agent should invoke.

Do not duplicate content already captured in other artifacts (`CLAUDE.md`, `docs/*.md`, `apps/*/docs/*.md`, specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead — e.g. "see apps/fifo_inventory/docs/period-lifecycle.md" rather than re-explaining the close flow. If the session found a new bug or doc drift, say so and suggest adding it to the affected app's known issues (`apps/fifo_inventory/docs/known-issues.md`, `apps/recon/docs/known-issues.md`, or the Known issues section of `apps/sales_tax/docs/overview.md`; suite-wide issues go in `docs/suite-architecture.md`).

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

Focus for the next session (may be empty): $ARGUMENTS

If a focus was given above, treat it as a description of what the next session will work on and tailor the doc accordingly.