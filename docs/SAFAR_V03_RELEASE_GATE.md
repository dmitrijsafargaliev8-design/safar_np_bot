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
The code ships **no external schedule**. Render's sleeping/free web services cannot be promised to execute background polling 24/7.
- By default `POST /api/internal/safar/sync` is **404** (not configured).
- On a validated supported always-reachable service, provision a **new dedicated random 40+ character server-side secret** named `SAFAR_SYNC_SECRET` and opt in with `SAFAR_SYNC_ENABLED=1`.
- An authorized scheduler may call the HTTPS POST endpoint with the secret in the **`X-Safar-Sync-Token` HTTP header**, never a query parameter or URL. Keep scheduler logs from recording the header.
- Every request attempts **at most four** original-sender carrier status reads; each TTN has a durable 15-minute minimum recheck delay, including failed reads. Check throughput/backlog against actual shipment counts.
- Read-only carrier failures preserve last verified status and set a separate error marker; tracking status change events are written only after a real NP observation.
- Limit network exposure, audit scheduler access and rotate the secret if exposed. Disable instantly with `SAFAR_SYNC_ENABLED=0`. An internal route is not by itself an always-running worker.
- Do not set any scheduler until the monitoring system has passed staging + load tests and exact account-specific rate limits are validated.

## Shipment intake/printing opt-ins
- `SAFAR_APP_AUTO_CREATE=1` enables PWA text intake to the existing worker and **may result in real NP TTNs**. Default OFF; requires tested authorized sender profile, NP sandbox/approved controlled pilot and explicit owner consent.
- `SAFAR_NP_PDF_PRINT=1` enables fetching official existing PDF from the original sender account. Default OFF; validate exact NP endpoint and document permissions on a controlled sender.
- Screenshot OCR and permanent secure image storage are NOT finished; do not claim photo-to-TTN works in app before they're implemented.
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
- Secure private file storage and production OCR for app image intake.
- Automated reverse-TTN discovery and approved official return initiation.
- Verified settlement ledger for COD/costs and more complete product analytics.
- External reliable cron/scheduler provisioning and production load/rate testing.
- Final 360/390/412/1440 visual/polish and real-device release QA.

**Status:** PR #24 is a DRAFT; passing mocked CI does NOT constitute production readiness or a completed NP live pilot.
