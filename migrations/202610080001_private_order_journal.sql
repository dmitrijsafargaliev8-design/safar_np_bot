-- Private order data: deliberately outside the exposed public API schema.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'safar_bot') THEN
        CREATE ROLE safar_bot LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS safar_orders;
REVOKE ALL ON SCHEMA safar_orders FROM PUBLIC;

CREATE TABLE IF NOT EXISTS safar_orders.updates (
    id BIGINT PRIMARY KEY, at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS safar_orders.jobs (
    key TEXT PRIMARY KEY, chat_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
    state TEXT NOT NULL, due DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_due ON safar_orders.jobs (state, due);
CREATE TABLE IF NOT EXISTS safar_orders.messages (
    chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, key TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS safar_orders.receipts (
    identity TEXT PRIMARY KEY, state TEXT NOT NULL, result TEXT,
    updated DOUBLE PRECISION NOT NULL
);

REVOKE ALL ON ALL TABLES IN SCHEMA safar_orders FROM PUBLIC;
DO $$
DECLARE api_role TEXT;
BEGIN
    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON SCHEMA safar_orders FROM %I', api_role);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA safar_orders FROM %I', api_role);
    END LOOP;
END
$$;
GRANT USAGE ON SCHEMA safar_orders TO safar_bot;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA safar_orders TO safar_bot;

ALTER TABLE safar_orders.updates ENABLE ROW LEVEL SECURITY;
ALTER TABLE safar_orders.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE safar_orders.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE safar_orders.receipts ENABLE ROW LEVEL SECURITY;

CREATE POLICY safar_bot_access ON safar_orders.updates TO safar_bot USING (true) WITH CHECK (true);
CREATE POLICY safar_bot_access ON safar_orders.jobs TO safar_bot USING (true) WITH CHECK (true);
CREATE POLICY safar_bot_access ON safar_orders.messages TO safar_bot USING (true) WITH CHECK (true);
CREATE POLICY safar_bot_access ON safar_orders.receipts TO safar_bot USING (true) WITH CHECK (true);

-- Set a randomly generated password separately; keep it only in server secrets.
-- Supabase anon/authenticated roles have no schema access and no policies.
