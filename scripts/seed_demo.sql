-- Demo shop for the local webhook test. Safe to run more than once.
INSERT INTO shops (id, name, business_type)
VALUES ('kota-shop-001', 'Demo Kota Shop', 'kota')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (shop_id, name, phone, role, consent_at, whatsapp_wa_id, instagram_user_id)
VALUES (
    'kota-shop-001',
    'Demo Owner',
    '27820001111',
    'owner',
    now(),
    '27820001111',
    'ig-demo-owner'
)
ON CONFLICT (shop_id, phone) DO UPDATE
SET consent_at = COALESCE(users.consent_at, now()),
    whatsapp_wa_id = EXCLUDED.whatsapp_wa_id,
    instagram_user_id = EXCLUDED.instagram_user_id,
    name = EXCLUDED.name;

INSERT INTO products (shop_id, name, unit)
VALUES
    ('kota-shop-001', 'chicken kota', 'each'),
    ('kota-shop-001', 'russian kota', 'each')
ON CONFLICT (shop_id, name) DO NOTHING;

INSERT INTO ingredients (shop_id, name, unit)
VALUES
    ('kota-shop-001', 'bread quarter loaf', 'each'),
    ('kota-shop-001', 'chips', 'kg'),
    ('kota-shop-001', 'polony', 'kg'),
    ('kota-shop-001', 'russian sausage', 'kg'),
    ('kota-shop-001', 'sauce', 'litre'),
    ('kota-shop-001', 'wrapper', 'each')
ON CONFLICT (shop_id, name) DO NOTHING;
