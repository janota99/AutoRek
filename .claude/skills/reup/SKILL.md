---
name: reup
description: Re-sync CLAUDE.md, README.md, and every docs/*.md and apps/*/docs/*.md file with the current codebase. Removes stale remarks, adds what's missing, and tidies the markdown. Edits docs only, never code.
argument-hint: "Optional scope, e.g. 'recon' or 'CLAUDE.md only'. Leave empty for everything."
disable-model-invocation: true
---

Bring the project's markdown docs back in line with the code as it is **now**. The code is the
source of truth. A doc claim that the code doesn't back up gets fixed or removed. Something
important in the code that no doc mentions gets added in the right place.

Scope (may be empty, meaning all docs): $ARGUMENTS

## Ground rules

- **Edit markdown only.** Never change code, tests, config, or data to make a doc "true". If the
  code looks wrong, that's a known issue to record, not something to fix here.
- **Verify every claim before keeping, changing, or deleting it.** Open the file, grep the
  symbol, run the command. Don't trust memory or the doc's own wording.
- **Never read or quote real data** in `apps/recon/data/`, `apps/sales_tax/data/`, FIFO's
  `fifo_snapshots/`, or `app_settings.json`. Describe formats from code, not from those files.
- **The audience is an accountant/inventory controller.** Use plain language and short
  sentences. Keep the accounting rules (penny comparisons, strict FIFO, "never silently change
  the books") exactly as strong as they are now. Don't soften them while tidying.
- **Don't commit.** Leave the changes in the working tree for the user to review.

## Which files

| File | Role | Keep it… |
|---|---|---|
| `CLAUDE.md` | Agent entry point: app table, run/test commands, always-apply rules, "Read before working on…" routing table, project skills | Short. Rules and pointers only; detail belongs in the linked docs. |
| `README.md` | Human-facing setup and usage | Friendly for a non-developer. No internal module detail. |
| `docs/suite-architecture.md` | Navigation, shared template, cross-app rules, testing/verification | Suite-wide only. App-specific detail goes to that app's docs. |
| `apps/<pkg>/docs/*.md` | Per-app module maps, rules, known issues | Specific to that app. |
| `.claude/skills/*/SKILL.md` | Skills | Only check that the paths they mention still exist and that CLAUDE.md's "Project skills" list matches the folders. |

Leave `HANDOFF.md` alone. It's a snapshot of one session. Tell the user if it looks finished or
out of date.

Find every doc with `git ls-files '*.md'` and look for untracked ones with `git status`. A new
`apps/<pkg>/` folder with no `docs/` folder, or a doc that nothing links to, is a gap to report.

## Steps

### 1. See what changed

- `git log --oneline -20` and `git diff --stat HEAD~10..HEAD -- . ':!*.md'` (fewer commits if
  the history is shorter) show which code areas moved recently. Check those docs hardest.
- Read each doc in scope fully before editing it.

### 2. Check claims against the code

For each doc, check every concrete claim. These go stale most often:

- **File and module paths.** Every path in backticks or a link must exist. Use Glob.
- **Function, class, and constant names.** Grep for each one. Renamed or deleted symbols are the
  commonest drift.
- **Version constants.** Compare what the docs say with the code:
  `grep -rn "VERSION *=" apps --include=*.py`, which covers `ENGINE_VERSION`,
  `SNAPSHOT_SCHEMA_VERSION`, `APP_VERSION`, `MATCHING_RULE_VERSION`, and
  `DUPLICATE_RULE_VERSION`.
- **Counts.** Get the Recon test count from `py -m pytest --collect-only -q` (last line) and
  update every place it appears (CLAUDE.md, suite-architecture.md, README, Recon docs).
  Count apps, pages, and sheets from the code too.
- **Commands.** Run the documented test commands (`py -m pytest -W error::FutureWarning`,
  `node apps/invoice_hub/tests/test_layers.js`) and confirm the stated output. If a test fails,
  don't edit around it. Report it.
- **Folder names and working directory.** Check that the docs name the real repo folder.
- **Registry and navigation.** Compare the `APPS` list in `shared/layout.py` with CLAUDE.md's
  app table, README's tool table, and the routing table: titles, URL paths, and code folders.
- **Dependency pins.** Compare with `requirements.txt`.
- **Behavior descriptions.** Read the code path a doc describes (matching order, column
  positions, control tolerances, sheet layout). If the doc and the code disagree, don't decide
  which one is right. Update the doc only when the code is clearly the intended behavior, for
  example when a recent commit changed it on purpose. Otherwise record the mismatch as a known
  issue and ask the user.

### 3. Known-issues lists

These are `apps/fifo_inventory/docs/known-issues.md`, `apps/recon/docs/known-issues.md`, the
Known issues section of `apps/sales_tax/docs/overview.md`, and suite-wide issues in
`docs/suite-architecture.md`.

- **Remove an issue only after you've confirmed in the code that it's fixed.** Mention the fix
  in your summary. An issue doesn't go just because it's old.
- Add any new bugs or doc-vs-code mismatches you found, in the same format as the existing
  entries.
- Rewrite entries whose file or function references have moved, so they point at the current
  location.

### 4. Edit

- Fix stale facts in place. Delete remarks that no longer apply: removed features, finished
  TODOs, "temporary" notes whose reason is gone, references to deleted files.
- Add new material where the routing table says it belongs. If it fits no existing doc, add a
  doc and a routing-table row in CLAUDE.md rather than growing CLAUDE.md itself.
- Don't repeat information. If two docs explain the same thing, keep it in the more specific doc
  and link to it from the other.
- Keep each file's existing structure and voice unless it's actually confusing. This is an
  update, not a rewrite.

### 5. Tidy

- One `#` title per file. Heading levels in order, without skipping.
- Tables have consistent columns. Code, paths, and symbols go in backticks. Links are relative
  and resolve.
- Remove trailing whitespace, doubled blank lines, broken list nesting, and leftover
  "TODO"/"TBD" lines.
- Check every relative link afterwards. A short script that extracts `](path)` targets from
  each changed file and tests whether they exist is enough.

### 6. Report

End with a short summary for the user, in plain language:

- **Updated:** each file changed, with one line on what changed and why (e.g. "Recon test count
  309 → 324").
- **Removed:** stale remarks deleted, and the evidence they were stale.
- **Added:** new sections, docs, or known issues.
- **Needs your decision:** doc-vs-code disagreements you didn't resolve, failing tests, and
  anything that could touch accounting results.

Then show `git diff --stat` so they can review before committing.
