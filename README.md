# SAFAR NP BOT

Existing Flask / Gunicorn service for forwarded Nova Poshta orders.

- Forward a photo with its order caption, or an album with a caption on any image.
- The bot collects an album for 3 seconds and creates one shipment.
- Its reply includes the photos, shipment number, recipient, destination and amounts.
- Declared value is required. Cash on delivery is enabled only by an explicit COD field.
- Reply to an error with a complete corrected order; the original photos stay attached.
- `/retry` as a reply retries a known failed order. Uncertain shipment saves stay blocked.
- `/orders` lists the last 10 orders for the current chat and sender.

Runtime: `pip install -r requirements.txt` then
`gunicorn bot:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`.
Webhook accepts signed `message` and `edited_message` updates. Incoming messages
are committed before acknowledgement, and shipment results before Telegram replies.

Required configuration: `TELEGRAM_BOT_TOKEN`, `NOVA_POSHTA_API_KEY`,
`WEBHOOK_SECRET`, sender `NP_SENDER_*` configuration. Existing legacy environment
variable aliases remain supported. Set `ALLOWED_CHAT_IDS` to restrict access.

`STATE_DB_PATH` defaults to `.state/orders.sqlite3`. Place it on **persistent
storage** for production. Render Free loses local SQLite data on restart,
redeploy and idle shutdown; that plan therefore does **not** provide durable
queue history or duplicate protection across host replacement. This release
does not provision or bill for new storage. Images themselves remain in Telegram;
the journal retains Telegram file identifiers, captions and shipment results.

Run `python -m unittest discover -s tests -v` for parser, album, photo, webhook,
retry and restart scenarios. Tests use mock APIs and never create live shipments.
`/health` reports the release and worker; `/ready` checks Telegram, Nova Poshta
and sender configuration. The temporary one-time shipment endpoint is disabled
unless `ENABLE_ONE_TIME_TTN_OPS=1` is explicitly configured.
