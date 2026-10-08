# Codex working rules — SAFAR NP BOT / SAFAR APP

## Canonical context

Read [docs/SAFAR_APP_CODEX_BRIEF_v1.md](docs/SAFAR_APP_CODEX_BRIEF_v1.md) **in full** before any app work. This is a **brownfield production system**, not a greenfield startup.
Current live Telegram/Nova Poshta integration is in `bot.py`, `order_pipeline.py`, `order_journal.py`, `np_client.py`; do not recreate it or move the source of truth.

## Required execution

1. Audit repo, read `README.md` and CI; write a concise ADR for PWA architecture + auth.
2. Implement a visually exceptional Android-first installable PWA with actual functionality, not a generated image or conceptual prototype.
3. Build incremental PRs against `main`, isolate server-facing changes, preserve current Render service and Supabase DB.
4. Any new orders/receipts/media endpoints MUST enforce server-side authenticated Telegram user ID and chat scope and must not expose Telegram file IDs as public links.
5. Existing order pipeline and receipt idempotency are sacred. Do not directly call carrier TTN save outside journaling; never retry uncertain save without proof; never conflate declared value with COD; sender identity is immutable per existing shipment.
6. Never access or publish secret keys, connection strings, phones, customer details or credentials in commits, screenshots, logs, tests, or frontend bundles.
7. Write mock regression, auth, backend and E2E tests; run existing disposable PostgreSQL CI with every backend change.
8. Do not alter production environment variables, secret values or Render Build Command without express approval.
9. The FOP sender is not connected: no claim that phone-only linking works. Work on app independently.
10. When finished with first slice, provide screenshot(s), PR link, test results, what is actually live, and any blocker.

## Visual product target

**SAFAR — Shipping Operations OS**. Premium graphite/black cockpit with deliberate deep burgundy accents, carefully balanced typography, subtle hierarchy and native-feeling mobile navigation; accessible text contrast, 360–412px widths and desktop. Implement real order/photo inbox, TTN/shipment views, safe correction, sender management and accurate monitoring using existing backend.
