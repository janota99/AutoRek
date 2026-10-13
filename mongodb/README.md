# MongoDB collections

Three collections that describe the data the website already uses. The site is **not connected to
MongoDB yet**; this folder establishes the structure so connecting it later is a straight mapping.

Database: `janota_fin`. Each collection has a `$jsonSchema` validator in `schemas/`, so the server rejects a
document with a wrong type, an out-of-range value, a missing field, or an extra field.

| Collection | Sample file | Describes | Comes from |
|---|---|---|---|
| `reviews` | `reviews.json` (5 documents) | Reviews on the Reviews & Feedback page | `apps/invoice_hub/site/feedback.js` and `feedback.html` |
| `plans` | `plans.json` (3) | The three purchase tiers on the Plans & Pricing page | `TIERS` in `apps/sales_page.py` |
| `tools` | `tools.json` (4) | The four accounting utilities, one per Dashboard card | `APPS` in `shared/layout.py` |

## Connect to Atlas (ready to connect)

The site is not wired to MongoDB, but the connection layer is in place and testable:

1. In Atlas: a cluster, a database user with only `readWrite` on `janota_fin`, and your IP in the IP Access List.
2. Add a `[mongodb]` block to `.streamlit/secrets.toml` (copy it from `secrets.toml.example`), or set
   `AUTOREK_MONGODB_URI`. Never commit it.
3. Load the sample documents into Atlas with `seed.mongodb.js` (below), using the `mongodb+srv://` string.
4. Run the check:

```bash
py -m mongodb.check_connection
```

It pings the cluster, confirms the three collections and their counts, validates every stored document against
`schemas/`, and warns if the connecting user is over-privileged. It only reads. `connection.py` holds the
settings loader (`load_config`, `connect`) and plain-language failure reasons (`describe_error`);
`test_connection.py` covers them without a server.

## Load it

```bash
mongosh "mongodb://localhost:27017" --file mongodb/seed.mongodb.js
```

Run from the repository root. It **drops and recreates** the three collections in `janota_fin`, creates them
with their validators, inserts the sample documents, and runs a few queries (number, Boolean and null filters).
Or load one file at a time: `mongoimport --db janota_fin --collection reviews --jsonArray --file mongodb/reviews.json`
(into a collection that does not exist yet, this creates it without the validator).

Check the samples without a server: `py mongodb/check_samples.py`, or `py -m pytest mongodb`.

## Field types, and how the JavaScript handles each

### `reviews`

| Field | MongoDB type | In JavaScript (`feedback.js`) |
|---|---|---|
| `_id` | objectId | not used yet; the server generates it |
| `name` | string, 2-80 chars | `values.name`, checked against `LIMITS.name` |
| `role` | string or **null** | `values.role`. The field is optional: blank in the form is `null` here |
| `rating` | number, whole, 1-5 | `Number(fields.rating.value)`, checked `>= 1 && <= 5` |
| `recommend` | **bool** | the form stores `'yes'` / `'no'`; the database stores `true` / `false` |
| `text` | string, 10-1000 chars | `values.text`, checked against `LIMITS.feedback` |
| `isSample` | bool | true for the two built-in reviews, false for submitted ones |
| `submittedAt` | date or **null** | `new Date().toISOString()`. `null` for the sample reviews, which have no date |

When the site is connected, two conversions are needed: `recommend === 'yes'` to a Boolean, and
`new Date(isoString)` for `submittedAt`. Everything else is already the right type.

### `plans`

| Field | MongoDB type | Notes |
|---|---|---|
| `_id` | string | the tier id: `starter`, `professional`, `enterprise` |
| `name`, `positioning`, `audience`, `ctaLabel` | string | shown on the card |
| `monthlyPriceUsd` | number or **null** | whole dollars; `null` is a custom quote (Enterprise) |
| `annualPriceUsd` | number or **null** | twelve months for the price of ten (the page's `_ANNUAL_MONTHS_BILLED`) |
| `inherits` | string or **null** | "Everything in Starter"; `null` for the lowest tier |
| `features` | array of string | the bullet list |
| `featured` | bool | the highlighted "Most popular" tier |
| `requiresQuote` | bool | true when the price is `null` |
| `active` | bool | false would hide the tier |
| `toolIds` | array of string | `tools._id` values this tier adds |

### `tools`

| Field | MongoDB type | Notes |
|---|---|---|
| `_id` | string | the page's URL segment |
| `title`, `summary`, `actionLabel` | string | the card text |
| `route` | string | `/` plus the `_id` |
| `badge` | string or **null** | "Prototype", or `null` when there is none |
| `isPrototype` | bool | true when there is a badge |
| `minimumPlanId` | string, one of the plan ids | the lowest plan that includes the tool |
| `inputs`, `outputs` | array of string | printed on the Dashboard card ("Takes in" / "Produces") |
| `sortOrder` | number | card order, from 1 |
| `active` | bool | |

`tools.minimumPlanId` and `plans.toolIds` describe the same relationship from both sides; the tests check they agree.

## Hover interactions that use this data

- **Dashboard:** each tool card prints that tool's `inputs` and `outputs` (no hover needed, so touch screens see them too).
- **Plans & Pricing:** hovering a plan card does the same with its price (monthly, yearly, and the saving), the tools it
  adds, and what it includes. The plans travel as JSON in the same shape as `plans.json`.

## Keeping the samples honest

`test_samples.py` compares `plans.json` against `TIERS` and `tools.json` against `APPS` field by field, so changing a
price, a feature or a card sentence in the code without updating the sample documents fails the test.
