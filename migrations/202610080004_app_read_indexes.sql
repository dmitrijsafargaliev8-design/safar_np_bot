-- Additive read indexes; job/receipt values and identities remain unchanged.
CREATE INDEX IF NOT EXISTS jobs_scope_activity
    ON safar_orders.jobs (chat_id, owner_id, updated DESC, key DESC);
CREATE INDEX IF NOT EXISTS jobs_owner_chats
    ON safar_orders.jobs (owner_id, chat_id);
