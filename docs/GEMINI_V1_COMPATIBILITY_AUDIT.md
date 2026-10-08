# Gemini SAFAR v1 proposal — compatibility and safety audit
Date: 2026-10-08
Target: SAFAR / CONTROL existing Flask/PostgreSQL/PWA codebase, draft PR #24
Decision: **DO NOT APPLY THE PROPOSED CODE VERBATIM.**
This file records a compatibility review. It does NOT change any production service.

## Actual source of truth
- Runtime DB: `order_journal.py` with existing `safar_orders.jobs`, `messages`, `receipts`, `updates`, `sender_preferences`, signed sessions and return tables. Jobs retain JSON text in `body`.
- Migrations: additive, independently reviewed `migrations/*.sql`. PostgreSQL RLS and a restricted `safar_bot` role. Do NOT run `flask db upgrade`: this repository does not implement a Flask-Migrate/Alembic model suite.
- Order parser: existing `bot.parse_order` plus `order_pipeline.OrderPipeline`; do not replace with a small regex subset.
- Application: `safar_app.py`, `safar_web/app.js`, `safar_web/style.css`. Current HTML already uses accessible `viewport-fit=cover` and allows user scaling.
- Return cases: `safar_returns.py` and `migrations/202610080005_return_cases.sql`. Outbound and reverse TTN identities differ; warehouse/finance states require independent human or verified financial evidence.
- Shipping integrations: `np_client.py`, `safar_operations.py` and immutable original sender profile. Status polling must be read-only and must never invoke a real carrier return/cancellation implicitly.
- PR #24 is DRAFT with feature gates OFF. Existing production Render deployment remains untouched.

## Assessment of proposed modules

### 1. `alembic/versions/20261009_safar_v1_integration.py`
**REJECT:** It refers to unverified `orders` and `shipments` tables and SQLAlchemy ORM not present in this repo. `sa.Column(..., unique=True)` is an API misuse for `op.add_column` as a schema migration uniqueness guarantee. The `ondelete='CASCADE'` proposal could erase return history when an order is removed, which is unacceptable. Unsafe drop-based downgrade can destroy real records. No demonstrated RLS or ownership partition.
**ADAPT:** Extend the existing append-only private Postgres schema with a reviewed additive SQL migration only as needed; never drop or rewrite existing jobs/receipts.

### 2. `app/services/intelligence/order_parser.py`
**REJECT as a replacement:** It does not extract full recipient name, declared value, COD, weight, street delivery, recipient matching, area ambiguity, sender. It cannot satisfy required TTN validation. Hashing `phone + full_name` forever would merge independent orders from returning customers (and can block legitimate replacements).
**ADAPT:** Keep the verified deterministic parser; add a separately permissioned OCR provider interface only after durable private media storage and PII policy/cost have been reviewed. Track original source/field provenance, treat OCR confidence as advisory, never infer missing payment data. Use client-request idempotency PLUS a narrow accidental-replay guard for identical source content (implemented in PR #24, 120 s). It must not permanently block legitimate repeat customers or independently confirmed reissues.

### 3. `app/api/logistics_sync.py`
**REJECT as shipped:** Nonexistent SQLAlchemy imports, nonexistent batch API wrapper, unbounded full-table loads, string token equality, no secret presence check or safe handling of disabled configuration, no owner/sender preservation, verified carrier status mapping or cancellation guard. It mutates returned business status based on unconfirmed codes (103/108) and inserts a return case as a side effect of polling, treating a carrier observation as initiation.
**ADAPT:** If scheduled sync is needed, isolate a bounded read-only service with a private authenticated trigger and strong constant-time token checking; use verified sender-specific APIs and never mutate NP as part of status scanning. Persist independently verified tracking snapshots/event origins, throttle and dedupe notifications, implement locked transactional cursor/batch lease and backoff. Do not claim 24/7 monitoring on a sleeping free web service without actually testing the selected scheduler.

### 4. `app/static/css/safar-theme.css`
**ADAPT selectively:** Obsidian/carbon/titanium/volt tokens already appear in PR #24. Port accessibility, real component states, mobile spacing, typography and animation changes to `safar_web/style.css`; do not replace existing CSS wholesale or introduce imaginary Jinja dashboard/templates. All displayed KPI counters must come from authenticated journal data, not a static sample `42`. Avoid `maximum-scale=1,user-scalable=0`: it blocks pinch zoom.

## Verified carrier constraints
- Official NP Easy Return page documents `LightReturnNumber` in `TrackingDocument/getStatusDocuments` as the **original** waybill number of an Easy Return, so linking requires inspecting the **incoming** TTN and matching the exact original.
- Ordinary Return and Easy Return are distinct. Cancellation/deletion of a TTN is not physical return and does not justify restocking or financial settlement.
- Official NP return policy says ordering a Return may cancel COD. Treat carrier return initiation as a separately confirmed high-impact operation, feature gated and unavailable until exact official API support is verified.
- Hardcoded codes 103/108 may not constitute a correct general classification for every return type. They are not authorization to create a return. Read-only tracking remains safe.
- References: https://novaposhta.ua/additional-services/easy-return/ and https://novaposhta.ua/additional-services/return/ and https://novaposhta.ua/for-business/cooperation/integration/

## Quality gates before any rollout
1. All independent branch backend/JS/Chromium checks green for exact latest SHA; no CI job writes to actual NP API.
2. Prove same-source accidental double submit cannot issue duplicate labels, but the same customer can order again legitimately. Simulate two different request IDs, two true orders, crash/restart and deleted-TTN reissue.
3. Verify receipt locks/uncertain outcome after NP timeout, and historical sender profile after a preference change.
4. Apply additive RLS migrations only after explicit privileged database review and a backup. Runtime cannot provision them itself.
5. Build and test private photo storage + OCR adapter, reverse-waybill carrier proof and real sender-account readiness separately.
6. Run a controlled live pilot with an explicitly authorized sender and approved cargo data **only after** owner approval; never fabricate production verifications.
7. Keep `SAFAR_APP_AUTO_CREATE` and `SAFAR_NP_PDF_PRINT` disabled by default.

## Status
**Review and safe replay-guard adaptation delivered in PR #24 only; Gemini-proposed ORM migrations, automatic carrier return orders and cron endpoint were not implemented.** No production deployment, no real TTNs created.
