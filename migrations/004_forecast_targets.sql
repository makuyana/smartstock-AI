-- 004 forecast notes and sales targets.

ALTER TABLE forecasts
    ADD COLUMN IF NOT EXISTS expected_revenue numeric(12,2) CHECK (expected_revenue IS NULL OR expected_revenue >= 0),
    ADD COLUMN IF NOT EXISTS is_early_estimate boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS day_type text CHECK (day_type IS NULL OR day_type IN ('busy','normal','quiet')),
    ADD COLUMN IF NOT EXISTS adjustments text[] NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS sales_targets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id         text NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    period_type     text NOT NULL CHECK (period_type IN ('day','week','month')),
    period_start    date NOT NULL,
    period_end      date NOT NULL,
    product_id      uuid REFERENCES products(id) ON DELETE CASCADE,  -- NULL = whole shop
    target_units    numeric(12,3) CHECK (target_units IS NULL OR target_units >= 0),
    target_revenue  numeric(12,2) CHECK (target_revenue IS NULL OR target_revenue >= 0),
    actual_units    numeric(12,3),
    actual_revenue  numeric(12,2),
    unit_variance   numeric(12,3),
    revenue_variance numeric(12,2),
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','reached','missed')),
    basis           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    evaluated_at    timestamptz,
    CHECK (period_end >= period_start)
);
CREATE UNIQUE INDEX IF NOT EXISTS sales_targets_uniq ON sales_targets (shop_id, period_type, period_start, COALESCE(product_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX IF NOT EXISTS sales_targets_shop_idx ON sales_targets (shop_id, period_start);
