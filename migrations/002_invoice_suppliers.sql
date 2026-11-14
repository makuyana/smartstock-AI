-- 002 suppliers, delivery fields, supplier price history.

-- Migration 002: supplier and delivery intelligence for the Invoice Bot
CREATE TABLE IF NOT EXISTS suppliers (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id     text NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    name        text NOT NULL,
    branch      text,
    location    text,
    phone       text,
    notes       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (shop_id, name, branch)
);
ALTER TABLE invoices
    ADD COLUMN IF NOT EXISTS supplier_id uuid REFERENCES suppliers(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS supplier_branch text,
    ADD COLUMN IF NOT EXISTS delivery_fee numeric(12,2) CHECK (delivery_fee IS NULL OR delivery_fee >= 0),
    ADD COLUMN IF NOT EXISTS expected_delivery_date date,
    ADD COLUMN IF NOT EXISTS delivered_date date,
    ADD COLUMN IF NOT EXISTS delivery_status text CHECK (delivery_status IS NULL OR delivery_status IN ('on_time','late','short_delivered','not_delivered','collected')),
    ADD COLUMN IF NOT EXISTS flags text[] NOT NULL DEFAULT '{}';
ALTER TABLE invoice_lines
    ADD COLUMN IF NOT EXISTS pack_size text,
    ADD COLUMN IF NOT EXISTS vat_amount numeric(12,2) CHECK (vat_amount IS NULL OR vat_amount >= 0),
    ADD COLUMN IF NOT EXISTS quantity_received numeric(12,3) CHECK (quantity_received IS NULL OR quantity_received >= 0),
    ADD COLUMN IF NOT EXISTS flags text[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS invoices_supplier_idx ON invoices (shop_id, supplier_id, invoice_date);
CREATE INDEX IF NOT EXISTS invoice_lines_ingredient_idx ON invoice_lines (shop_id, ingredient_id);
-- Supplier price history: unit price per ingredient per supplier over time
CREATE OR REPLACE VIEW supplier_price_history AS
SELECT il.shop_id, s.name AS supplier, i.supplier_branch, il.ingredient_id, il.description,
       i.invoice_date, il.unit, il.pack_size, il.unit_price
FROM invoice_lines il
JOIN invoices i ON i.id = il.invoice_id
LEFT JOIN suppliers s ON s.id = i.supplier_id
WHERE il.confirmed AND i.confirmed;
