-- Additive SAFAR PWA authentication; never alters order/receipt identity.
-- Apply with the project administrator before enabling the reviewed app.
-- Runtime must fail closed until these tables exist; it cannot provision them.
CREATE TABLE IF NOT EXISTS safar_orders.app_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    csrf_hash TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    expires DOUBLE PRECISION NOT NULL CHECK (expires > created),
    revoked DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS app_sessions_expiry ON safar_orders.app_sessions(expires);
CREATE TABLE IF NOT EXISTS safar_orders.app_auth_replays (
    replay_hash TEXT PRIMARY KEY,
    expires DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS safar_orders.app_pairings (
    pairing_hash TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    user_id BIGINT,
    approved_chat_id BIGINT,
    created DOUBLE PRECISION NOT NULL,
    expires DOUBLE PRECISION NOT NULL CHECK (expires > created),
    approved DOUBLE PRECISION,
    consumed DOUBLE PRECISION,
    CHECK ((approved IS NULL AND user_id IS NULL AND approved_chat_id IS NULL)
           OR (approved IS NOT NULL AND user_id > 0 AND approved_chat_id = user_id))
);
CREATE INDEX IF NOT EXISTS app_pairings_expiry ON safar_orders.app_pairings(expires);

DO $$
DECLARE table_name TEXT;
DECLARE api_role TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['app_sessions', 'app_auth_replays', 'app_pairings']
    LOOP
        EXECUTE format('REVOKE ALL ON safar_orders.%I FROM PUBLIC', table_name);
        FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
        LOOP
            EXECUTE format('REVOKE ALL ON safar_orders.%I FROM %I', table_name, api_role);
        END LOOP;
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON safar_orders.%I TO safar_bot', table_name);
        EXECUTE format('ALTER TABLE safar_orders.%I ENABLE ROW LEVEL SECURITY', table_name);
        -- Reapplying this additive migration leaves the same restricted policy.
        EXECUTE format('DROP POLICY IF EXISTS safar_bot_access ON safar_orders.%I', table_name);
        EXECUTE format('CREATE POLICY safar_bot_access ON safar_orders.%I TO safar_bot USING (true) WITH CHECK (true)', table_name);
    END LOOP;
END
$$;
