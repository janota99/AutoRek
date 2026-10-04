# Contributing

Thanks for your interest in AutoRek. This is an accounting tool, so the bar is
**accuracy and auditability**: a change must never alter a number, a match, or a workbook cell
without being called out.

## Getting started

```bash
pip install -r requirements.txt
streamlit run app.py          # run from the repository root
python -m pytest              # Recon + MongoDB sample checks
node apps/invoice_hub/tests/test_layers.js
```

Read [CLAUDE.md](CLAUDE.md) and [docs/suite-architecture.md](docs/suite-architecture.md) first.
Each app's `docs/known-issues.md` lists problems that are already known.

## Ground rules

- **Never change accounting results silently.** If a cleanup would change a result, open an
  issue instead and keep the existing behavior.
- **Never commit real company data.** Use the synthetic files in `sample_data/`.
- Money is compared to the penny with `Decimal` or integer cents, never float equality.
- Add or update tests for any change to Recon matching, and bump the version constants
  (`MATCHING_RULE_VERSION`, `ENGINE_VERSION`, `SNAPSHOT_SCHEMA_VERSION`) where the docs say to.
- Keep pull requests small and describe what you verified.

## Reporting a problem

Use the issue templates. Please do not attach real financial data; describe the shape of it or
reproduce the problem with the sample files.
