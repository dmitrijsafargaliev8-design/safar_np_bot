-- SAFAR returns are business cases, NEVER aliases for cancelled TTN receipts.
-- Additive schema, apply with DB administrator after a reviewed backup.
CREATE TABLE IF NOT EXISTS safar_orders.return_cases (
    id TEXT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL CHECK(owner_id > 0),
    order_key TEXT NOT NULL,
    outbound_ttn TEXT NOT NULL CHECK(outbound_ttn ~ '^[0-9]{14}$'),
    sender_profile TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN
        ('refused_by_recipient','unclaimed','easy_return_after_delivery','customer_exchange','other')),
    warehouse_state TEXT NOT NULL DEFAULT 'not_received',
    finance_state TEXT NOT NULL DEFAULT 'unreviewed',
    reverse_ttn TEXT,
    created DOUBLE PRECISION NOT NULL,
    updated DOUBLE PRECISION NOT NULL,
    UNIQUE(chat_id,owner_id,order_key)
);
CREATE INDEX IF NOT EXISTS return_cases_owner_updated
    ON safar_orders.return_cases(chat_id,owner_id,updated DESC);
CREATE TABLE IF NOT EXISTS safar_orders.return_events (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES safar_orders.return_cases(id),
    actor_id BIGINT NOT NULL,
    at DOUBLE PRECISION NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS return_events_case_time
    ON safar_orders.return_events(case_id,at,id);

REVOKE ALL ON safar_orders.return_cases FROM PUBLIC;
REVOKE ALL ON safar_orders.return_events FROM PUBLIC;
DO $$
DECLARE api_role TEXT;
BEGIN
    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON safar_orders.return_cases FROM %I', api_role);
        EXECUTE format('REVOKE ALL ON safar_orders.return_events FROM %I', api_role);
    END LOOP;
END
$$;
GRANT SELECT,INSERT,UPDATE,DELETE ON safar_orders.return_cases TO safar_bot;
GRANT SELECT,INSERT ON safar_orders.return_events TO safar_bot;
ALTER TABLE safar_orders.return_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE safar_orders.return_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS safar_bot_access ON safar_orders.return_cases;
CREATE POLICY safar_bot_access ON safar_orders.return_cases
    TO safar_bot USING(true) WITH CHECK(true);
DROP POLICY IF EXISTS safar_bot_access ON safar_orders.return_events;
CREATE POLICY safar_bot_access ON safar_orders.return_events
    TO safar_bot USING(true) WITH CHECK(true);
