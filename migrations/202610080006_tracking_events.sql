-- SAFAR / CONTROL: private carrier observations, never carrier mutations.
-- Additive only. DBA applies after reviewing 0005 returns migration and backup.
CREATE TABLE IF NOT EXISTS safar_orders.tracking_snapshots (
    order_key TEXT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    ttn TEXT NOT NULL CHECK(ttn ~ '^[0-9]{14}$'),
    sender_profile TEXT NOT NULL,
    status_code TEXT NOT NULL DEFAULT '',
    status_text TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT '',
    checked_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempted_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tracking_snapshots_scope
    ON safar_orders.tracking_snapshots (chat_id,owner_id,checked_at);
CREATE INDEX IF NOT EXISTS tracking_snapshots_due
    ON safar_orders.tracking_snapshots (attempted_at);
CREATE TABLE IF NOT EXISTS safar_orders.tracking_events (
    id TEXT PRIMARY KEY,
    order_key TEXT NOT NULL,
    chat_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    ttn TEXT NOT NULL CHECK(ttn ~ '^[0-9]{14}$'),
    sender_profile TEXT NOT NULL,
    at DOUBLE PRECISION NOT NULL,
    status_code TEXT NOT NULL,
    status_text TEXT NOT NULL,
    phase TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source='nova_poshta')
);
CREATE INDEX IF NOT EXISTS tracking_events_order_time
    ON safar_orders.tracking_events (order_key,at DESC);
REVOKE ALL ON safar_orders.tracking_snapshots FROM PUBLIC;
REVOKE ALL ON safar_orders.tracking_events FROM PUBLIC;
DO $$
DECLARE api_role TEXT;
BEGIN
    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON safar_orders.tracking_snapshots FROM %I', api_role);
        EXECUTE format('REVOKE ALL ON safar_orders.tracking_events FROM %I', api_role);
    END LOOP;
END
$$;
GRANT SELECT,INSERT,UPDATE ON safar_orders.tracking_snapshots TO safar_bot;
GRANT SELECT,INSERT ON safar_orders.tracking_events TO safar_bot;
ALTER TABLE safar_orders.tracking_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE safar_orders.tracking_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS safar_bot_access ON safar_orders.tracking_snapshots;
CREATE POLICY safar_bot_access ON safar_orders.tracking_snapshots
    TO safar_bot USING(true) WITH CHECK(true);
DROP POLICY IF EXISTS safar_bot_access ON safar_orders.tracking_events;
CREATE POLICY safar_bot_access ON safar_orders.tracking_events
    TO safar_bot USING(true) WITH CHECK(true);
