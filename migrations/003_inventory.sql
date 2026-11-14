-- 003 inventory costing, expiry, stock ledger.

ALTER TABLE ingredients
    ADD COLUMN IF NOT EXISTS avg_unit_cost numeric(12,4) CHECK (avg_unit_cost IS NULL OR avg_unit_cost >= 0),
    ADD COLUMN IF NOT EXISTS safety_buffer numeric(12,3) CHECK (safety_buffer IS NULL OR safety_buffer >= 0),
    ADD COLUMN IF NOT EXISTS is_perishable boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS shelf_life_days integer CHECK (shelf_life_days IS NULL OR shelf_life_days > 0),
    ADD COLUMN IF NOT EXISTS preferred_supplier_id uuid REFERENCES suppliers(id) ON DELETE SET NULL;
ALTER TABLE purchases
    ADD COLUMN IF NOT EXISTS supplier_id uuid REFERENCES suppliers(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS expiry_date date,
    ADD COLUMN IF NOT EXISTS qty_remaining numeric(12,3) CHECK (qty_remaining IS NULL OR qty_remaining >= 0);
-- Every stock change, for an audit trail and variance checks
CREATE TABLE IF NOT EXISTS stock_movements (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id        text NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    ingredient_id  uuid NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
    movement_date  date NOT NULL,
    movement_type  text NOT NULL CHECK (movement_type IN ('purchase','sale_deduction','waste','count_adjustment','manual')),
    qty_change     numeric(12,3) NOT NULL,
    unit_cost      numeric(12,4),
    ref_table      text,
    ref_id         uuid,
    stock_after    numeric(12,3),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stock_movements_shop_idx ON stock_movements (shop_id, ingredient_id, movement_date);
CREATE UNIQUE INDEX IF NOT EXISTS stock_movements_ref_uniq ON stock_movements (ref_table, ref_id, ingredient_id) WHERE ref_id IS NOT NULL;
