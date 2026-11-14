-- 007 demo shops: fuel expenses, daily takings, usage, and price observations.

ALTER TABLE shops
    ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS demo_as_of DATE;

DO $$
DECLARE
    cname text;
BEGIN
    SELECT conname INTO cname
    FROM pg_constraint
    WHERE conrelid = 'expenses'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%category%';
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE expenses DROP CONSTRAINT %I', cname);
    END IF;
END $$;

ALTER TABLE expenses ADD CONSTRAINT expenses_category_check CHECK (
    category IN (
        'utilities_electricity',
        'utilities_gas',
        'equipment_fridge',
        'equipment_appliances',
        'general_ops',
        'rent',
        'fuel',
        'other'
    )
);

CREATE TABLE IF NOT EXISTS daily_revenue (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id       TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    revenue_date  DATE NOT NULL,
    amount        NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    UNIQUE (shop_id, revenue_date)
);

CREATE INDEX IF NOT EXISTS daily_revenue_shop_idx ON daily_revenue (shop_id, revenue_date);

CREATE TABLE IF NOT EXISTS usage_entries (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id        TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    usage_date     DATE NOT NULL,
    ingredient_id  UUID NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
    quantity       NUMERIC(12,3) NOT NULL CHECK (quantity > 0),
    unit           TEXT NOT NULL,
    unit_cost      NUMERIC(12,2) CHECK (unit_cost IS NULL OR unit_cost >= 0),
    UNIQUE (shop_id, usage_date, ingredient_id)
);

CREATE INDEX IF NOT EXISTS usage_entries_shop_idx ON usage_entries (shop_id, usage_date);

CREATE TABLE IF NOT EXISTS price_observations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id      TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    observed_on  DATE NOT NULL,
    item_name    TEXT NOT NULL,
    unit         TEXT NOT NULL,
    unit_price   NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE (shop_id, observed_on, item_name)
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    shop_id      TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    metric_date  DATE NOT NULL,
    metric       TEXT NOT NULL,
    quantity     NUMERIC(14,3) NOT NULL,
    unit         TEXT NOT NULL,
    PRIMARY KEY (shop_id, metric_date, metric)
);
