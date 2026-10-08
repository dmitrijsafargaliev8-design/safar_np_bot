# SAFAR NP BOT

## FOP as primary sender — deployment without publishing secrets

**Goal:** new Telegram forwards ship under the FOP's own Nova Poshta cabinet,
while the previous sender is available as `/sender default` and all old TTN
receipts stay tied to the original account.

In the **existing Render service** `safar_np_bot` → **Environment**, add
both variables in **one Save & Deploy operation**:

- `NP_FOP_API_KEY`: her already-issued FOP cabinet API key (secret).
- `NP_PRIMARY_SENDER_PROFILE`: exact value `fop` (non-secret).

**Never send the API key through Telegram, ChatGPT, GitHub, screenshots
or README.** Leave `NOVA_POSHTA_API_KEY` and existing `NP_SENDER_*`
unchanged: they are needed to check/delete previously issued TTNs.

With these settings the bot uses `NP_FOP_API_KEY` for new orders, but does
not copy any `NP_SENDER_*` fields from the former account. The FOP account
must return **one unique sender, one unique contact, and one unique sending
address** via read-only `Counterparty` queries; otherwise `/sendercheck`
reports ambiguity and the shipment fails closed. If her account lists
multiple entries, set exact official sender/contact/warehouse/city references
under the `fop` entry in `NP_SENDER_PROFILES_JSON`; `api_key_env` must be
`NP_FOP_API_KEY`. Do not guess references.

After Render deploy, check `/ready`, then run `/sendercheck` and
`/sender` in the private, authorized Telegram chat. These checks **do not
create a TTN**. There are no per-operator explicit sender overrides as of
the 2026-10-08 cutover, so changing `NP_PRIMARY_SENDER_PROFILE` makes
`fop` the default for future orders. If operators later choose
`/sender default`, that choice is sticky per operator/chat until switched.
Old orders and retries preserve original `sender_profile` and cannot
switch accounts merely because the global primary changed.

To roll back the *default for new orders*, restore
`NP_PRIMARY_SENDER_PROFILE=default` without removing the FOP key/profile,
because any existing FOP orders may still require FOP API access to check
their history or deletion.

## Smart corrections and multi-sender operations (candidate)

**Single-field corrections:** Reply to a previously issued order card with
`Телефон: +380...`, `Оценка: 1600`, `Наложка: 0`, `Город: ...`,
`Отделение: 7` or `Вес: 1.5`. You may correct up to two named fields
in one reply. Photos and the original immutable shipment receipt are retained.
For a **created TTN**, the correction is only staged; first delete that TTN in
Nova Poshta, then reply `/retry` to its card. A new TTN is blocked until
the carrier confirms deletion. A full corrected order remains supported.
Incomplete orders can also be supplemented with a missing named field by replying to the error card.

**Sender switching:** `/sender` lists available configured profiles and your
current profile in that chat. `/sender other` selects a profile for **future**
forwarded orders from that exact authorized Telegram user in that chat.
Selection is persisted in the private `safar_orders.sender_preferences` table,
never in local memory. Already forwarded orders keep their original sender
assignment; the old TTN is always queried with its original sender client.
A profile ID unavailable at runtime fails closed (no fallback to primary).

A second person's sender identity is NOT activated until their authorized
Nova Poshta account and exact official sender refs are configured. On Render,
set a server-only `NP_SENDER_PROFILES_JSON`, e.g.:

```json
{
  "other": {
    "label": "Второй отправитель",
    "api_key_env": "NP_SECONDARY_API_KEY",
    "sender_ref": "REPLACE_WITH_OFFICIAL_UUID",
    "contact_ref": "REPLACE_WITH_OFFICIAL_UUID",
    "address_ref": "REPLACE_WITH_OFFICIAL_UUID",
    "city_ref": "REPLACE_WITH_OFFICIAL_UUID",
    "phone": "+380XXXXXXXXX"
  }
}
```

Store the **actual secret API key** only in Render variable
`NP_SECONDARY_API_KEY`, not JSON, code, chat or commit. If the secondary
identity is legitimately associated with the existing NP API key,
`api_key_env` may be omitted; ownership and sender refs still must be
verified in NP. Never invent UUIDs or treat changing a phone alone as proof
of authority to ship from another person's account.

**Deployment order:** Apply `migrations/202610080002_sender_preferences.sql`
with the Supabase project administrator before deploying the new version.
Verify permissions and RLS; keep `STATE_REQUIRE_PERSISTENT=1`.
Then deploy the existing Render service (never create a replacement).
No existing TTNs, chat allowlists or sender refs are changed by this migration.

## Operations v2.0 candidate (safe rollout)

Commands added in the `feat/safar-v2-access-and-operations-20261008` branch:

- `/whoami`: shows only the caller's Telegram user ID and the current chat ID.
  Use once in a private chat and once in each authorized order group.
- `/queue`: lists up to 10 outstanding/failed orders for this human sender
  in the current chat.
- `/stats`: owner-scoped lifetime state counts from the journal (not revenue).
- `/status`: displays whether the journal is Postgres or development SQLite.

Security rollout on the existing Render service:

1. Start the bot and send `/whoami` in private chat and in the order group.
2. Set `ALLOWED_CHAT_IDS` to comma-separated allowed private/group chat IDs.
3. Set `ALLOWED_USER_IDS` to comma-separated authorized human Telegram IDs.
4. Set `STRICT_ACCESS_POLICY=1`. This **fails closed** if either list is empty.
   The group alone is never authority to create a shipment in strict mode.
5. Verify the restricted policy on `/health` (only booleans are exposed), test
   `/whoami`, `/queue`, `/stats`, one existing order, and an unauthorized actor.
   Do NOT create a live test shipment; mock APIs or an already-created order suffice.

The legacy one-time direct shipment URL is retired in this branch and cannot create\na live TTN even when its old environment switch remains set.\n\nThe strict flag is intentionally opt-in so the PR can be reviewed without
interrupting the currently operating service. **Before enabling strict mode,
orders are not protected by user allowlisting unless `ALLOWED_USER_IDS` is
already configured.** Leaving both ID lists empty leaves legacy open access.
Never merge/deploy believing the strict policy is active before setting the
Render variables.

**Data persistence:** Set `STATE_DATABASE_URL` to the restricted Postgres
connection and `STATE_REQUIRE_PERSISTENT=1`; verify `/status` reports
`PostgreSQL (постоянное)`. Never set the required flag before a valid Postgres
database and migration are ready; otherwise new webhook requests get 503.
Render Free local SQLite is not durable, even when the runtime looks healthy.

Existing Flask / Gunicorn service for forwarded Nova Poshta orders.

- Forward a photo with its order caption, or an album with a caption on any image.
- The bot collects an album for 3 seconds and creates one shipment.
- Completed orders appear as a Telegram photo-first card with a 2×2 keyboard:
  **Статус доставки**, **Исправить заказ**, **Копировать ТТН**, **История заказа**.
  The copy button uses Telegram's native clipboard action (Bot API 7.11+).
  The lead image carries the card and the remaining album photos are delivered
  separately because Telegram cannot attach inline keyboards to a media group.
  All sent message IDs remain linked to the same owner-scoped order.
- Button callbacks use the existing signed webhook with \`callback_query\` updates.
  Only the original forwarding user in the same chat can inspect shipment
  tracking/history or request correction help. A correction to an issued card
  stays tied to its original receipt, so changing fields cannot bypass the
  active-TTN duplicate guard.

- Its reply includes the photos, shipment number, recipient, destination and amounts.
- Declared value is required. Cash on delivery is enabled only by an explicit COD field.
- Reply to an error with a complete corrected order; the original photos stay attached.
- Forward the same order again after deleting its TTN in Nova Poshta: the bot
  checks the exact number and creates a replacement only after confirmed deletion.
  There is no limit on confirmed delete/recreate cycles. Photos and amounts remain attached.
- `/retry` as a reply retries a known failed order or checks whether a completed
  TTN has been deleted. Active TTNs are reused; uncertain shipment saves stay blocked.
  Unknown status or a failed tracking request never unlocks another live shipment.
  Deleted numbers remain in receipt history, and all album aliases share the replacement.
- `/orders` lists the last 10 orders for the current chat and sender.

Telegram's slash menu is published and verified at startup (default, Russian
and Ukrainian interface languages). `/start` and `/menu` open the command list;
`/help` explains forwarding photos, corrections and amounts; `/example` supplies
an editable order; `/status` checks the services. `/track NUMBER` reads a TTN
status without creating or editing a shipment. `/track` alone selects the most
recent completed order, or its referenced card when sent as a reply. Saved
order lookup is restricted to the current chat and sender.

Runtime: `pip install -r requirements.txt` then
`gunicorn bot:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`.
Webhook accepts signed `message` and `edited_message` updates. Incoming messages
are committed before acknowledgement, and shipment results before Telegram replies.

Required configuration: `TELEGRAM_BOT_TOKEN`, `NOVA_POSHTA_API_KEY`,
`WEBHOOK_SECRET`, sender `NP_SENDER_*` configuration. Existing legacy environment
variable aliases remain supported. Set `ALLOWED_CHAT_IDS` to restrict access.

Production order storage uses a separate Postgres database. Apply
[migrations/202610080001_private_order_journal.sql](migrations/202610080001_private_order_journal.sql)
once as the project administrator, set a randomly generated password for the
restricted `safar_bot` role, and save its connection string in the existing
Render service as `STATE_DATABASE_URL`. Set `STATE_REQUIRE_PERSISTENT=1` so a
missing database setting cannot silently enable local storage.

For Supabase, copy the actual **session pooler** host from the project's Connect
dialog and use port **5432**, user `safar_bot.PROJECT_REF`, and
`sslmode=verify-full`. For Supabase hosts, the runtime loads the official
[Supabase Root 2021 CA](https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt)
from `certs/supabase-prod-ca-2021.crt` (valid until April 2031). Other providers
use the Requests CA bundle. An explicit `sslrootcert` remains supported. The
transaction pooler on port 6543 is deliberately rejected because it cannot
retain the worker's session lock. Only the lock holder recovers or processes
the queue, including while old and new deployments overlap. Short journal
transactions also have a database lock so concurrent webhook instances do not
overwrite each other's photos or acknowledge the same update twice.

Tables live in the private `safar_orders` schema, with RLS and access limited
to the bot's server role. No schema access is granted to anonymous or ordinary
authenticated API users. Do not put database passwords or customer data in
GitHub, public environment variables, or browser code.

Incoming updates, album contents, corrections and completed shipment receipts
are committed before acknowledgement or Telegram replies. If the configured
database is unavailable, the webhook returns 503 for Telegram retry; it never
falls back to SQLite. A shipment save with an uncertain result stays blocked
until it is checked in Nova Poshta. A host restart may repeat a notification
whose delivery was not confirmed, but never deliberately repeats that shipment
save.

Without `STATE_DATABASE_URL`, `STATE_DB_PATH` defaults to
`.state/orders.sqlite3` for development. Render Free loses local SQLite data
on restart, redeploy and idle shutdown. This code change does not provision or
bill for a database. Images themselves remain in Telegram; the journal retains
Telegram file identifiers, captions and shipment results.

Supabase Free projects may pause after a week of inactivity and do not include
automatic daily backups. Use a separate encrypted off-site database backup for
production, or choose a plan that includes managed backups. Provisioning a
durable journal is separate from configuring and verifying backups.

Run `python -m unittest discover -s tests -v` for parser, album, photo, webhook,
retry and restart scenarios. Tests use mock APIs and never create live shipments. CI runs the same photo,
album, retry and restart tests against SQLite and a disposable Postgres 17
instance, plus overlapping deployment, transaction rollback and private-role
access scenarios. Local Postgres tests run only if `SAFAR_TEST_DATABASE_URL`
points to a dedicated disposable test database; never use a production URL.
`/health` reports the release and worker; `/ready` checks Telegram, Nova Poshta
and sender configuration. The former `/ops/create-one-time-ttn` endpoint is permanently retired (HTTP 410),\nbecause in-memory idempotency could create duplicate shipment numbers after a restart.\nLegacy `ENABLE_ONE_TIME_TTN_OPS` no longer re-enables it; use the durable Telegram journal.
