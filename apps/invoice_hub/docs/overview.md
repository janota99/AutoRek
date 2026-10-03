# Invoice Lifecycle Hub: overview

A student prototype of an Outlook invoice tracker. It uses fictitious data and connects to no real mailbox.
It's a static HTML/JavaScript site in `site/` with three pages:

| Page | Script(s) | What it does |
|---|---|---|
| `index.html` | `service.js` | Service description, greeting, savings estimator. |
| `feedback.html` | `feedback.js` | Sample reviews plus a feedback form, stored in localStorage. |
| `dashboard.html` | `hub-*.js`, `inbox-ui.js`, `settings-ui.js`, `dashboard.js` | Simulated role-based sign-in, vendor-bill lifecycle, inbox rules, per-person settings. |

## How it runs inside Streamlit

- `component.py` declares `site/` as a Streamlit component. Streamlit then serves the folder
  with correct MIME types, and the site's own links (`dashboard.html`, `#anchors`) work inside the iframe.
- `app.py` (the Invoice Lifecycle Hub page) renders that component: `index.html` (Service) with a
  `workspace-nav` tab bar (Service | My Dashboard) that switches the frame to `dashboard.html`.
  Links to `feedback.html` carry `standalone-only` and are hidden in the suite.
- `apps/feedback.py` (the suite's Reviews & Feedback page) embeds it with
  `invoice_hub(page="feedback.html")`. The component always opens `index.html`; the bridge reads
  `page` from the first render and opens that page instead. It also opens `dashboard.html` once
  when the suite's account strip set sessionStorage `pp.suite.hubPage` (Sign in, My invoice workspace).
  `feedback.html` needs no sign-in and starts its form with the signed-in person's name and role.
- Suite mode is CSS keyed off classes the bridge sets on `<html>`: `in-suite` everywhere (hides the
  site header and the Service greeting; the suite has its own), plus `workspace` on
  `dashboard.html` / `feedback.html` (rules at the end of `styles.css`).
  Opened straight from disk, the site is unchanged: three pages with the full navigation.
- The bridge copies the signed-in person (name, role line, intro) to sessionStorage
  `pp.suite.profile.v1` and posts `pp-suite:session` to the page; the suite account strip
  (`shared/suite_banner.js`) reads it. See `docs/suite-architecture.md`.
- `site/streamlit-bridge.js` is loaded last on every page. It sends `componentReady` and sizes the
  frame to the page's full content height (kept current with a `ResizeObserver`), so the Streamlit
  page scrolls the hub and nothing is hidden in an inner scroll box. Because the frame is that tall,
  the bridge also:
  - pins open modal `<dialog>`s to the visible part of the frame, below Streamlit's top bar, while the page scrolls;
  - makes `#anchor` links scroll the Streamlit page (its scroller is `section.stMain`, not the window);
  - scrolls back to the top of the frame when you move to another hub page.

  These need the parent page to be readable (same origin, as when Streamlit serves `site/`). If it
  isn't, the frame still fits its content but dialogs use the browser's default placement.
  It's a no-op when a page is opened straight from disk.
- **A new page in `site/` needs** `<script src="streamlit-bridge.js"></script>` before `</body>`.

## Rules

- **Script order matters** in `dashboard.html`: `hub-data.js` → `hub-access.js` →
  `hub-workflow.js` → `hub-settings.js` → `hub-mail.js` → `hub-inbox.js` → `inbox-ui.js` →
  `settings-ui.js` → `dashboard.js`. They're classic scripts sharing `window.InvoiceHub`.
- User text is only ever set via `textContent` (see `el()` in `dashboard.js`); keep it that way.
- Sign-in and privacy are **simulated in the browser**. The access list lives in localStorage
  and the session in sessionStorage. Never present this as real access control.
- `hub-mail-graph.js` (Microsoft Graph provider) is **not loaded** by the demo. It's only been
  tested against mocked responses. Check each request in Graph Explorer before relying on it.

## Tests

`node apps/invoice_hub/tests/test_layers.js` loads the `hub-*.js` layers into Node and checks
filename patterns, settings, the mail layer, the mocked Graph provider, and the whole chain.
