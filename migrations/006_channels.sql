-- 006 channel identity, message dedupe, and scheduled-send ledger.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS whatsapp_wa_id TEXT,
    ADD COLUMN IF NOT EXISTS instagram_user_id TEXT,
    ADD COLUMN IF NOT EXISTS last_inbound_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS users_whatsapp_wa_id_uniq
    ON users (whatsapp_wa_id) WHERE whatsapp_wa_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS users_instagram_user_id_uniq
    ON users (instagram_user_id) WHERE instagram_user_id IS NOT NULL;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS external_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS messages_channel_direction_external_uniq
    ON messages (channel, direction, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS webhook_events (
    channel     TEXT NOT NULL,
    external_id TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (channel, external_id)
);

CREATE TABLE IF NOT EXISTS scheduled_sends (
    user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slot     TEXT NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, slot)
);
