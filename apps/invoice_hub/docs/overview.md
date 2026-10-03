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
- `app.py` (the page) just renders that component.
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
