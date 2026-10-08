# SAFAR / CONTROL v0.3 — Production Gate & Recovery
Last updated: 2026-10-08. This document describes **implementation**, not a completed rollout.

## What is code-complete in draft PR #24
- Secure PWA text order intake through the existing order parser/carrier worker, with per-request idempotency and a 120-second same-source accidental replay guard. Off by default.
- Separate private return cases with immutable sender/source TTN, auditable verified Easy Return association and operator-confirmed physical warehouse receipt. Physical receipt and carrier arrival are distinct; financial status remains unreviewed.
- Bounded, read-only scheduler entry point, private last verified carrier snapshots and append-only status change events. A carrier code *never* creates a return case, another TTN, an invoice, a refund or a warehouse receipt.
- Secure optional existing-waybill PDF proxy, original sender only. Off by default.
- Source code and browser design pass for SAFAR / CONTROL; no claim of full visual system completion.

## Database rollout (mandatory DBA review)
1. Back up the existing PostgreSQL `safar_orders` schema and verify restore procedures on a disposable database. Do not delete the existing Render service or reset credentials.
2. Apply **`migrations/202610080005_return_cases.sql`** then **`migrations/202610080006_tracking_events.sql`** via a privileged migration account, never the `safar_bot` runtime role. Both are additive.
3. Verify ownership, grants and RLS for `return_cases`, `return_events`, `tracking_snapshots`, `tracking_events`; the public/`anon`/`authenticated` roles must have zero access to private tables. Only `safar_bot` should read/write its necessary data.
4. Run all automated Python/SQLite/Postgres/JS/Chromium tests with a **disposable test DB**. Never use real shipment API keys in CI.
5. Confirm existing jobs/receipts/sender profiles unchanged before and after migration.

## Optional scheduler — NOT active automatically
The branch includes an **opt-in GitHub Actions schedule**, but it runs on the default branch only after merge and explicit secrets/variable setup. It is not activated by the PR itself. Render's sleeping/free web services cannot be promised to execute background polling 24/7.
- By default `POST /api/internal/safar/sync` is **404** (not configured).
- On a validated supported always-reachable service, provision a **new dedicated random 40+ character server-side secret** named `SAFAR_SYNC_SECRET` and opt in with `SAFAR_SYNC_ENABLED=1`.
- An authorized scheduler may call the HTTPS POST endpoint with the secret in the **`X-Safar-Sync-Token` HTTP header**, never a query parameter or URL. Keep scheduler logs from recording the header.
- Every request attempts **at most four** original-sender carrier status reads; each TTN has a durable 15-minute minimum recheck delay, including failed reads. Check throughput/backlog against actual shipment counts.
- Read-only carrier failures preserve last verified status and set a separate error marker; tracking status change events are written only after a real NP observation.
- Limit network exposure, audit scheduler access and rotate the secret if exposed. Disable instantly with `SAFAR_SYNC_ENABLED=0`. An internal route is not by itself an always-running worker.
- GitHub Actions opt-in: `.github/workflows/safar-carrier-monitor.yml` (runs at minute 12/42 if `vars.SAFAR_SCHEDULER_ENABLED=1`, `secrets.SAFAR_SYNC_URL` contains the exact HTTPS `/api/internal/safar/sync` endpoint, and `secrets.SAFAR_SYNC_SECRET` matches the backend). GitHub schedule execution can be delayed; do not claim guaranteed timing.
- Do not set any scheduler until the monitoring system has passed staging + load tests and exact account-specific rate limits are validated.

## Shipment intake/printing opt-ins
- `SAFAR_APP_AUTO_CREATE=1` enables PWA text intake to the existing worker and **may result in real NP TTNs**. Default OFF; requires tested authorized sender profile, NP sandbox/approved controlled pilot and explicit owner consent.
- `SAFAR_NP_PDF_PRINT=1` enables fetching official existing PDF from the original sender account. Default OFF; validate exact NP endpoint and document permissions on a controlled sender.
- `SAFAR_APP_MEDIA_INTAKE=1` **also requires** `SAFAR_APP_AUTO_CREATE=1`: PWA accepts one <=2 MiB JPG/PNG per intake and stores it through the existing Telegram bot by sending to **the signed-in operator's own private bot chat**. The operator must have started the bot privately. The returned Telegram file ID is journalled privately, and the image appears on the order detail. This uses Telegram media retention—not an independent object-store durability SLA. A user-facing notice explains the privacy boundary.
- The text+photo path can process the typed/pasted caption through the existing verified parser, but a screenshot-only request must fail closed unless local OCR is enabled and installed. `SAFAR_OCR_ENABLED=1` requires installed Tesseract CLI plus `ukr+rus+eng` traineddata, sufficient CPU/memory and a controlled image QA pilot. Never imply that a paid Gemini subscription automatically gives API access. OCR extraction is not confirmation of phone/city/COD; NP field validation remains mandatory.
- The private-media Telegram bridge can fail if the user never started the bot; this is a supported exception case, never a silent new TTN.
- Future independent encrypted object storage and multi-image app albums still need design and rollout.
- Automatic creation of carrier return requests is NOT implemented; do not auto-create reverse TTNs or silently cancel COD.

## Rollout sequence
1. CI green for **exact final SHA** of PR #24, code review, privacy/security review.
2. Replay tests: concurrent sends, NP timeout uncertain state, duplicated text/reused request ID, same customer with a different order, historical sender change, official deletion/reissue.
3. Privileged schema migration and backup on staging, then a controlled production migration only after authorization.
4. Deploy existing Render service by normal, recoverable deployment path, with new feature gates still OFF.
5. Smoke test existing bot/webhook/Telegram auth, photo retrieval and live tracking in **read-only mode**. Watch error logs without exposing PII/credentials.
6. Separately enable optional features in controlled authorized stages. Verify each action against the actual Nova Poshta cabinet.
7. Production acceptance: zero lost historical records, zero duplicate TTNs, real carrier evidence freshness, traceable warehouse operator events, no automatic payouts, no secret exposure.
8. Rollback: disable new feature flags, roll back code to pre-v0.3 commit. Retain additive tables/event records, do **not** drop them to roll back.

## Known unfinished scope
- Independent secure object storage, production OCR engine installation/QA, and multi-image app album upload (single photo is implemented through private Telegram chat).
- Automated reverse-TTN discovery and approved official return initiation.
- Official bank/provider-verified COD settlement and more complete product analytics. Return expenses can now be **operator-reported** with exact integer kopecks, evidence reference, append-only event and request-id replay protection; this is not independent bank validation.
- External reliable cron/scheduler provisioning and production load/rate testing.
- Final 360/390/412/1440 visual/polish and real-device release QA.

**Status:** PR #24 is a DRAFT; passing mocked CI does NOT constitute production readiness or a completed NP live pilot.

## Return expenses (no bank-verified claims)
- `POST /api/safar/returns/<id>/expense` requires a signed operator session, chat/owner authorization, CSRF and explicitly acknowledged UAH expense with reason, receipt reference and idempotent `request_id`.
- Expenses are append-only in existing private `return_events`, stored as integer kopecks. No new migration beyond 0005; totals in return cards/list/detail are derived only from recorded evidence.
- The `finance_state` remains `unreviewed`: manually entered receipts are NOT bank-confirmed COD transfers, refunds or accounting settlement. Don't use expenses to mark a paid COD.
