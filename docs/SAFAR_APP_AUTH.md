# SAFAR app authentication

The Flask app continues to use the existing private order journal. Authentication
does not modify jobs, shipment receipts, sender identities, the webhook policy,
or any Nova Poshta API operation.

Before a reviewed deployment, the database administrator must apply
`migrations/202610080003_app_auth.sql` to the existing private schema. This is
an additive migration; the runtime role cannot create tables. App sign-in
returns a safe HTTP 503 until the journal and its auth tables are available.
The production migration has not been applied by this change.

## Verified Telegram entry

`POST /api/safar/session` accepts Telegram Web App `initData`. The backend
validates the documented bot-token HMAC over all fields except `hash` (including
the optional third-party `signature` field), rejects duplicate fields and
malformed data, limits timestamps to ten minutes old and thirty seconds ahead,
and requires a positive integer Telegram user ID. An app-specific access
callback requires both explicit human and chat allowlists even when the legacy
bot policy is permissive. The approved private chat must match the human ID.

Each verified canonical signature is consumed once in `app_auth_replays`.
Reordering or URL-encoding the same launch cannot establish another session.
Reloads first call `GET /api/safar/session` to reuse a valid session. After an
expired session or logout, reopen the Mini App for new launch data or approve
a new device pairing. The verifier follows [Telegram's official validation
specification](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

## Standalone device pairing

1. The browser posts to `/api/safar/pairing/start`. The backend creates a
   five-minute request with a random 256-bit `device_token` and a separate
   twelve-character approval code. The device token remains in page memory.
2. The operator sends `/app CODE` to the existing bot in their approved private
   chat. Only the signed Telegram webhook can call `approve_pairing`; the human
   and private chat must match the explicit app allowlists. Approval is final
   and cannot be reassigned to another actor. A transfered code must never be
   approved on behalf of another device; the bot instructs the operator to
   approve only the code visible on their own screen.
3. The original browser posts `device_token` to `/api/safar/pairing/complete`.
   Pending requests return `status: pending`. An approved request is consumed
   once and issues the session cookie. The code alone cannot complete pairing.
   Expiry, actor removal, repeat approval and repeat completion fail closed.

Both code and device token are persisted only as domain-separated keyed hashes.
Approval and consumption use the journal's transaction lock, including across
overlapping deployments. A failed session write rolls back replay consumption
or pairing consumption, allowing a safe retry without a partially issued
session. Pairing attempts and auth requests are rate limited; the approval
helper also limits attempts by the verified actor.

## Session lifecycle and request protection

Session cookies contain random opaque 256-bit values, expire after thirty
minutes, and use `Secure`, `HttpOnly`, `SameSite=Lax` and `/api/safar` scope.
Only a keyed cookie hash, verified user ID, CSRF hash, creation/expiry and
revocation timestamps are persisted in `app_sessions`. Existing v0.1 signed
identity cookies are rejected as sessions; a new verified login upgrades them.

Every read verifies the persisted row, expiry and current allowlists. API
queries additionally constrain the actual chat and owner; a supplied chat or
order ID never grants access. Successful login or pairing rotates any previous
opaque cookie in the same transaction. `POST /api/safar/logout` requires the
current session, exact same-origin browser `Origin` and `X-CSRF-Token`; it
revokes the DB row before clearing the cookie. Replaying a copied old cookie
fails on another process and after a restart. There is no cookie-only logout
and no signed identity fallback.

All unsafe app requests reject missing, foreign, `null` or insecure internet
origins, as well as cross-site Fetch Metadata. Session mutations also require
the session-bound CSRF header. HTTP is allowed only for local development.
Auth and private responses use `Cache-Control: private, no-store`; the service
worker must never cache session, order, photo or mutation responses. Credentials
and pairing device tokens must not enter browser persistent storage, URL
queries, public bundles, analytics, logs or screenshots.

The credential-derived domain-separated hashing key stays server-only. Rotating
the bot token or webhook secret invalidates existing app sessions and pending
pairings; this change does not rotate either credential. Expired replay and
pairing rows are pruned on new authentication attempts; expired session rows
are pruned after a further day. Session activity does not extend the fixed
expiry. Rate limiting is a bounded per-process throttle, supplemented by
one-use database checks and high-entropy secrets; it is not a distributed
network abuse service.

## Verification

`tests/test_safar_auth.py` runs the same auth regressions against SQLite and,
when `SAFAR_TEST_DATABASE_URL` is set, the disposable PostgreSQL CI database.
It covers unsigned and tampered data, duplicate/invalid fields, stale/future
timestamps, forged actor types, canonical replays, fixed expiry, allowlist
removal, cross-process logout, session rotation, hash-only storage, private
device approval, original-device possession, concurrent one-use completion,
transaction rollback, missing configuration/migration, Origin and CSRF bypass
attempts, and bounded rate limits. PostgreSQL tests also check that original
order tables and all auth tables retain RLS and anonymous API roles have no
auth table privileges. All authentication tests use fictitious IDs and local
databases; they do not contact Telegram, Nova Poshta or production.
