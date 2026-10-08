# SAFAR APP v0.2 — architecture and reviewed rollout

## Architecture decision

Continue the v0.1 same-origin Flask Blueprint and dependency-free JavaScript PWA
on the existing Render service. The Telegram webhook, carrier client, private
PostgreSQL journal and receipts remain the source of truth. No Node build step
is required by the Python production service; Node and Playwright are development
and CI tools only. The existing branch's paginated reads are extended rather than
replaced.

Authentication uses signed Telegram initData or short-lived bot-assisted device
pairing. Opaque session and device secrets are stored as hashes in private journal
tables; a server-side session can expire or be revoked. Browser access always
requires explicit human and chat allowlists even while legacy webhook policy is
being rolled out. Each query rechecks the owner and approved chat. Details and
photos use the same scope. Mutation requests require same-origin JSON and the
session's CSRF token. See [auth threat model](SAFAR_APP_AUTH.md).

Order filters and pagination run before SQL LIMIT in the journal, and dashboard
counts cover the authorized history. Activity is labelled as journal activity,
not income or carrier delivery. A created job only means a TTN was issued.
Read-only tracking uses the original sender account and exact document number;
its short server cache never influences duplicate or deletion guards.

App corrections are separate validated drafts with a revision conflict check.
They do not schedule a worker, change a receipt, change existing sender identity,
or call a carrier save. Telegram `/retry` can adopt a current draft through the
existing journal state machine. For issued shipments, independently confirmed
carrier deletion remains mandatory before replacement. Uncertain saves remain
locked. Incoming Telegram changes can make an app draft stale and require review.

Sender selection updates only the operator/chat preference for future forwards.
Configured profiles are not presented as independently verified account ownership.
The FOP remains pending when its own server credentials are unavailable.

The PWA service worker caches only an explicit public shell asset list. API data,
media, initData, cookies and private HTML responses are excluded. Logout clears
in-memory records and revokes the server session. The demo is labelled synthetic,
has no API mutation path and writes no orders into the journal.

## Deployment gate (not performed by this PR)

This work does not merge the branch, deploy Render, change secrets, run production
migrations or create real shipments. The live release remains the existing v0.1
until the owner approves a reviewed rollout.

1. Back up the private production journal using the existing approved process.
2. Review the additive `202610080003_app_auth.sql` migration and apply it with
   the administrator in the private schema; ensure the earlier sender preference
   migration is present. Apply `202610080004_app_read_indexes.sql` for scoped
   paging and chat lookups; these indexes change no order values. The runtime
   role does not perform schema changes.
3. Verify RLS, `safar_bot` privileges and lack of `anon`/`authenticated` schema
   access. Verify explicit user and chat allowlists include approved private login
   chats plus the order groups each operator needs. Do not relax allowlists.
4. Deploy the reviewed commit to the **existing** Render service with its existing
   Python build/start commands and database. No additional hosting is needed.
5. Verify `/health`, `/ready`, signed webhook handling, `/orders`, `/sender` and
   authorized `/app` using existing records. Verify denied actors, group owner
   isolation, logout, expired pairing, photo recovery and accurate totals. Do not
   create a live test TTN.

Missing auth tables fail app sign-in closed; they do not reset jobs or change the
Telegram webhook. Production schema and credentials stay untouched during local
development and CI. Automated tests use mock carrier clients and disposable DBs.

## Rollback

Restore the previously approved v0.1 commit on the same Render service. Keep the
additive app tables and all existing journal/receipt tables; do not delete or
rebuild shipment history. The former short-lived browser cookies are incompatible
with v0.2 and require sign-in again. Keep sender credentials needed by historical
shipments. Rollback does not require changing the Python build or DB URL.

## Verification commands

```sh
SAFAR_DISABLE_BACKGROUND=1 python -m unittest discover -s tests -v
node --check safar_web/app.js
npm ci
npm run test:js
npm run test:ui
```

The full Postgres suite additionally requires `SAFAR_TEST_DATABASE_URL` targeting
a dedicated disposable database with all migrations and the restricted runtime
role, as provisioned by CI. Never point that variable to production.

## Verified candidate, 2026-10-08

- Full Python suite: **292 passed**, including SQLite and disposable PostgreSQL
  17 with the restricted runtime role; no skipped database checks.
- PWA JavaScript checks: **4 passed**. Full Chromium browser suite: **16 passed**.
- Desktop and mobile core pages pass automated WCAG AA checks. Navigation and
  layout are checked at **360, 390, 412 and 1440 px**; touch targets are at least
  44 px, and critical views have no horizontal overflow.
- Browser regressions exercise signed login, one-use pairing, logout/relogin,
  delayed tracking after logout, scoped pagination/search, safe correction review,
  full private albums, photo recovery and offline shell/reconnection.
- All carrier writes are mocked or rejected. Browser requests are restricted to
  the local synthetic fixture; no production database or customer records are used.

Screenshots below show **fictitious local test records and generated package
images**, not production data. Browser CI also uploads its screenshots, report
and failure traces as `safar-app-browser-evidence`.

| View | Evidence |
| --- | --- |
| Mobile overview | [390 px](screenshots/safar-v02-overview-390.png) |
| Mobile photo inbox | [390 px](screenshots/safar-v02-orders-390.png) |
| Mobile order album | [390 px](screenshots/safar-v02-detail-390.png) |
| Desktop operations dashboard | [1440 px](screenshots/safar-v02-overview-1440.png) |

The reviewed candidate is not live until a separate authorized rollout. Real
production record reconciliation and Android device installation remain rollout
checks; simulated browser QA does not substitute for those checks. FOP connection
still requires independently verified server credentials.
