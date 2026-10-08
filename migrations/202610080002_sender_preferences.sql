-- Persist operator sender choice outside shipments (no API secrets, no personal data).
-- Run with project administrator before deploying code that reads this table.
CREATE TABLE IF NOT EXISTS safar_orders.sender_preferences (
    chat_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    profile_id TEXT NOT NULL,
    updated DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(chat_id, owner_id)
);
REVOKE ALL ON safar_orders.sender_preferences FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON safar_orders.sender_preferences TO safar_bot;
ALTER TABLE safar_orders.sender_preferences ENABLE ROW LEVEL SECURITY;
CREATE POLICY safar_bot_access ON safar_orders.sender_preferences
    TO safar_bot USING (true) WITH CHECK (true);
