# SAFAR NP BOT

Existing Flask / Gunicorn service for forwarded Nova Poshta orders.

- Forward a photo with its order caption, or an album with a caption on any image.
- The bot collects an album for 3 seconds and creates one shipment.
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
and sender configuration. The temporary one-time shipment endpoint is disabled
unless `ENABLE_ONE_TIME_TTN_OPS=1` is explicitly configured.
