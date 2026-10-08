# SAFAR APP — CODEX EXECUTION BRIEF v1.0

> Status: implementation task / new UI surface, NOT a rebuild of the shipping system.
> Repository: `dmitrijsafargaliev8-design/safar_np_bot` · base: `main`.
> Product: **SAFAR — Shipping Operations OS**.
> Target: an excellent installable Android-first **PWA**, desktop-responsive operations console, tied to the existing working Telegram + Nova Poshta stack.
> Language: default Ukrainian with Russian toggle; date/currency locale uk-UA / Europe/Kyiv.
> Budget: bootstrap/zero-cost-first; use existing services wherever practical.

## MISSION / PRODUCT OUTCOME

Build a polished **real working application**, not a static Figma-style landing page.
The owner forwards product photos and order text from their existing Telegram group; the current webhook parses and journals the order, then creates a Nova Poshta TTN. The app must show the exact same durable jobs/receipts, their product photos, shipment state, address, status, and history. It must allow safe corrections and staff monitoring without navigating Telegram for every issue.

Design direction: **industrial premium / dark logistics cockpit** (graphite/near-black, restrained burgundy highlight, high-contrast typography, precise tables, subtle glass/metal surface detail). Distinct from an ordinary SaaS admin panel. No bright noisy dashboards, no generic template look. Strong hierarchy, high perceived quality, intentional motion, excellent empty/error/loading states. Design specifically for Android phones first with thumb-reachable navigation, then responsive tablets/desktop. WCAG AA where practical. Preserve speed and battery.

## EXISTING SYSTEM IS CANONICAL — READ BEFORE CODING

- `bot.py`: existing Flask/Gunicorn service, Telegram commands/webhook, strict `ALLOWED_USER_IDS` + `ALLOWED_CHAT_IDS`, `STRICT_ACCESS_POLICY=1`.
- `order_pipeline.py`, `order_journal.py`: durable jobs, messages, photo IDs, worker lock, receipts, idempotent anti-duplicate state machine, retries, sender snapshots.
- `order_card.py`, `order_edits.py`: shipping card, field corrections.
- `np_client.py`: Nova Poshta carrier integration.
- `sender_profiles.py`, `sender_access.py`: authorized sender profiles and read-only discoverability.
- `migrations/`: private `safar_orders` PostgreSQL schema and RLS; `safar_bot` restricted DB role.
- `tests/` + `.github/workflows/`: baseline regressions including disposable PostgreSQL.
- Production: existing **Render** Python web service `safar_np_bot`, currently hosting Telegram webhook, with separate connected **Supabase** PostgreSQL `safar_orders`; **do not destroy, replace, recreate, or disconnect** either.
- Existing production endpoint `/ops/create-one-time-ttn` is intentionally RETIRED (HTTP 410) because it bypassed durable idempotency. **Never resurrect it.**
- Existing Telegram bot must remain functional during and after the work. Never rotate or expose credentials, secret headers, API tokens, or phone numbers.

### Business invariants — NON-NEGOTIABLE

1. **TTN idempotency:** UI must not create a second live TTN by itself or call `np_client.create_ttn` directly outside the journal. An already-created TTN is immutable in the app: stage corrections and only recreate through current journal pipeline AFTER carrier confirms old TTN deletion. Uncertain API response stays locked; a timeout is NOT proof of failure.
2. **Declared value != COD:** assessed/declared value is required; COD is ZERO unless explicitly provided. Never infer cash on delivery from `cost`.
3. Photos/albums and forwarded source messages must remain associated with one order; no loss on corrections/retries/refresh/restarts.
4. Sender selection: preserves historical `sender_profile` with each receipt/job; switching only affects NEW orders. FOP account is NOT authenticated yet. No pretending that knowing a phone number or listing a sender via API grants access to her own separate FOP cabinet.
5. Roles and privacy: operators see only their authorized data; no public `/api/orders` or `/api/admin`; never use unauthenticated Telegram user_id query params as authorization.
6. Existing Telegram bot and `/health`, `/ready`, `/webhook` semantics remain backward compatible; render auto-deploy must not break working production.
7. No real carrier shipments in automated tests or initial visual-demo UI. Read-only tracking can be mocked in test environments.

## IMPLEMENTATION STRATEGY

Prefer a **same-origin Flask-hosted PWA** (to avoid new hosting bills, CORS complexity and an untrusted separate control plane). A standalone `app/` React + TypeScript + Vite frontend is recommended if the existing Render build environment can build assets robustly; provide a safe build/deploy path. Alternatively produce a genuinely premium responsive Flask-served static bundle without installing a second full backend. Explain the trade-off in a short ADR, then implement.

**Do not assume Render supports a new Node build step until checked.** Do not change Render settings or secrets without explicit instructions/permission. A separate `web/` build can be prepared in PR with tests even if production deployment requires a later build change. Avoid large dependency weight and lock dependencies.

Authentication: use **Telegram Web App signed initData verification** (validate HMAC, auth_date freshness, allowlisted Telegram ID); because an installable standalone PWA also needs login, provide a **short-lived, one-use bot-assisted device-pairing flow** or another independently verified mechanism. Pairing must be bound to the allowlisted human user and an approved private chat; persist hashed token/session states in private DB, with expiry, rotation and logout. HttpOnly Secure SameSite cookies, CSRF protection on mutations, rate limits, security headers. No bearer token committed to frontend build or localStorage; no open admin access; no insecure fallback when Telegram context is absent.

Use server-scoped database access to reuse jobs, photos, receipts and identity. Read queries must be backed by `owner_id` and `chat_id` constraints from the verified session and actual authorized chats. For group staff, explicit role design and permission checks before enabling new visibility. If schema changes are needed, additive migration + RLS + tests; never mutate existing `jobs`/receipt identity semantics without proof.

## UX / FEATURE SCOPE

### 1. Home / Live Cockpit
Premium dashboard with live actionable data: pending, creating, invalid, uncertain, created, delivered/unknown as grounded; alerts; last activity; small sparkline or chart ONLY with real computed values. Skeleton loading, offline indicator, toast errors, last sync. Do NOT label shipments delivered based merely on job `state='created'`; delivery must come from carrier tracking.

### 2. Orders Inbox
Photo-first, responsive rich cards and desktop table; filter status/date/sender/search/recipient; visual discrepancy flags. Each entry displays receiver, city, branch, declared value, COD separately, photos, source and last update. No secret/raw JSON leakage. Tap opens detailed record with full photo gallery and linked Telegram source when safe.

### 3. Order Details / Safe Action Drawer
Original order text, parsed fields, attachment thumbnails, receipt immutable history, shipment number and carrier link, progress timeline. For invalid or collecting jobs, editable validated fields. For created jobs, correction staging only; UI must clearly say **existing TTN not changed** and show current vs proposed values. Safe action for retry/recheck routed to the existing pipeline only; explicit acknowledgement for potentially creating a TTN. NEVER auto-retry uncertain saves.

### 4. Shipments + Tracking
Search TTN, copy, carrier status timeline, cache freshness, handling of NP latency. Distinguish 'TTN issued' from 'picked up / on the way / delivered'. Never silently infer statuses. Track via the same sender context as original record.

### 5. Senders
Display available authorized sender profiles, which account is primary, profile readiness (configured/unconfigured), active user preference and safely selected future sender. **FOP connection pending** until verified credentials; no connect-by-phone fiction. Sensitive refs/keys stay server-only.

### 6. Activity / Operations / Reports
Owner-scoped queue, pending errors, retries, journal-safe audit view. Accurate counts by state/date, time-to-process for completed jobs; not invented revenue. CSV export only if access-bound and properly escaped. Basic per-operator operations metrics before adding broader staff admin.

### 7. Settings / Help / Onboarding
Theme, language uk/ru, notifications permission where actually supported, install-PWA instructions for Android, session/logout, connection diagnostics, privacy-safe links to Telegram and existing Render status where appropriate.

## DELIVERABLES / EXECUTION PHASES — IMPLEMENT, DON'T JUST DISCUSS

**Phase A — Audit + architecture (one short report + working skeleton):**
Inspect all current files, document invariants, select frontend integration and login design, produce initial polished shell with mock-only read views and empty/error states; do not ask founder to repeat repo credentials.

**Phase B — Secure API + persistence:**
Implement verified auth and strictly scoped read APIs, server-side photo representation (Telegram file IDs are not public URLs; either short-lived authorized media endpoint using bot token on the server or safe cache), pagination/filtering/search, short-lived device pairing, tests. No new generic public endpoints.

**Phase C — Functional operations:**
Connect real journal order list, details, photo viewer, tracking, safe corrections and sender selection with state transitions and audit. Ensure CSRF, session lifecycle, idempotency semantics and transactional race safety.

**Phase D — Release quality:**
Offline-aware installable manifest and service worker with **no caching sensitive responses**. Android browser install + TWA readiness only after PWA stable. Responsive QA at 360/390/412 phone widths and desktop. Accessibility and performance. E2E browser screenshots and critical-route tests. Update README and deploy instructions; PR with passing existing CI + new tests. Preserve current Render service and DB; no production cargo or carrier writes in tests.

## ACCEPTANCE GATES

- Android portrait phone shows a striking, polished app with five primary sections **Overview / Orders / Shipments / Senders / Settings**, touch-responsive, no horizontal overflow; desktop works as dashboard.
- Authorization bypass tests fail closed: unsigned Telegram initData, expired initData, forged user, unallowlisted actor, different chat/owner, mismatched CSRF.
- Screenshot-backed functional demo uses *seeded fictitious data* separate from prod with real UI state transitions; no demo product data written into production.
- Once authenticated, real production read views reconcile with private job count and photos; statuses correctly reflect DB and NP.
- Repeated create/retry never creates a second active TTN; active shipment edits do not rewrite carrier data; removed TTN requires positive deletion confirmation.
- No credential or recipient PII appears in public assets, logs, analytics or frontend error stack.
- All existing Python/PostgreSQL tests remain passing; new frontend unit/E2E tests green.
- **Never stop after mockups.** First PR should contain actual functional UI+API scaffold and tests, not only a spec, image, or lorem ipsum.

## CODING / HANDOFF RULES

- Work in a new feature branch from `main`, open PR(s); don't merge until green tests and human approval of any production-impacting changes.
- Do not overwrite already existing bot code without preserving behavior; no rewrites just for aesthetics.
- Commit incremental vertical slices. Show UI screenshots for founder review and list clear 'works now' vs 'requires connection' items.
- If stuck on a required secret or deployment permission, implement safe feature flags, mock-only local demo, and continue on non-blocked tasks.
- Scope deliberately: prioritize exceptional visual polish **and** trustworthy operations on Android, not every possible feature simultaneously.
