-- 001 base schema. Timezone Africa/Johannesburg. Currency ZAR.
-- Tables match the shared SmartStock schema.

CREATE SCHEMA IF NOT EXISTS public;

-- ---------------------------------------------------------------------------
-- Shops
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shops (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    business_type TEXT,
    timezone      TEXT NOT NULL DEFAULT 'Africa/Johannesburg',
    currency      TEXT NOT NULL DEFAULT 'ZAR' CHECK (currency = 'ZAR'),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Users (owner / staff). consent_at records POPIA consent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id     TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    name        TEXT,
    phone       TEXT,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'staff')),
    consent_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (shop_id, phone)
);

CREATE INDEX IF NOT EXISTS users_shop_id_idx ON users (shop_id);

-- ---------------------------------------------------------------------------
-- Products the shop sells
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id      TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    unit         TEXT NOT NULL DEFAULT 'each',
    usual_price  NUMERIC(12,2) CHECK (usual_price IS NULL OR usual_price >= 0),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (shop_id, name)
);

CREATE INDEX IF NOT EXISTS products_shop_id_idx ON products (shop_id);

-- ---------------------------------------------------------------------------
-- Ingredients / supplies. stock and costs may be NULL until first count/purchase.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingredients (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id          TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    unit             TEXT NOT NULL,
    stock_on_hand    NUMERIC(12,3) CHECK (stock_on_hand IS NULL OR stock_on_hand >= 0),
    reorder_level    NUMERIC(12,3) CHECK (reorder_level IS NULL OR reorder_level >= 0),
    usual_supplier   TEXT,
    latest_unit_cost NUMERIC(12,2) CHECK (latest_unit_cost IS NULL OR latest_unit_cost >= 0),
    pack_size        NUMERIC(12,3) CHECK (pack_size IS NULL OR pack_size > 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (shop_id, name)
);

CREATE INDEX IF NOT EXISTS ingredients_shop_id_idx ON ingredients (shop_id);

-- ---------------------------------------------------------------------------
-- Recipes: how much of each ingredient one unit of a product uses
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recipes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id       TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    product_id    UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    ingredient_id UUID NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
    qty_per_unit  NUMERIC(12,4) NOT NULL CHECK (qty_per_unit > 0),
    UNIQUE (product_id, ingredient_id)
);

CREATE INDEX IF NOT EXISTS recipes_shop_id_idx ON recipes (shop_id);
CREATE INDEX IF NOT EXISTS recipes_product_id_idx ON recipes (product_id);

-- ---------------------------------------------------------------------------
-- Messages (WhatsApp or group-chat source messages)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id     TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    channel     TEXT NOT NULL DEFAULT 'whatsapp',
    body        TEXT,
    media_url   TEXT,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw         JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_shop_id_sent_at_idx ON messages (shop_id, sent_at);

-- ---------------------------------------------------------------------------
-- Sales (confirmed only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_entries (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    sale_date          DATE NOT NULL,
    product_id         UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity           NUMERIC(12,3) NOT NULL CHECK (quantity > 0),
    unit_price         NUMERIC(12,2) CHECK (unit_price IS NULL OR unit_price >= 0),
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sales_entries_shop_id_sale_date_idx
    ON sales_entries (shop_id, sale_date);
CREATE INDEX IF NOT EXISTS sales_entries_product_id_idx ON sales_entries (product_id);

-- ---------------------------------------------------------------------------
-- Invoices (header). Confirmed flag: never update stock from unconfirmed rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoices (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    supplier           TEXT,
    invoice_number     TEXT,
    invoice_date       DATE,
    vat_number         TEXT,
    subtotal           NUMERIC(12,2) CHECK (subtotal IS NULL OR subtotal >= 0),
    vat                NUMERIC(12,2) CHECK (vat IS NULL OR vat >= 0),
    discount           NUMERIC(12,2) CHECK (discount IS NULL OR discount >= 0),
    total              NUMERIC(12,2) CHECK (total IS NULL OR total >= 0),
    payment_method     TEXT,
    file_url           TEXT,
    confirmed          BOOLEAN NOT NULL DEFAULT FALSE,
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS invoices_shop_id_invoice_date_idx
    ON invoices (shop_id, invoice_date);

-- ---------------------------------------------------------------------------
-- Invoice lines
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice_lines (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id     UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    shop_id        TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    line_no        INTEGER CHECK (line_no IS NULL OR line_no > 0),
    description    TEXT,
    ingredient_id  UUID REFERENCES ingredients(id) ON DELETE SET NULL,
    quantity       NUMERIC(12,3) CHECK (quantity IS NULL OR quantity > 0),
    unit           TEXT,
    unit_price     NUMERIC(12,2) CHECK (unit_price IS NULL OR unit_price >= 0),
    line_total     NUMERIC(12,2) CHECK (line_total IS NULL OR line_total >= 0),
    confidence     TEXT CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low')),
    confirmed      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS invoice_lines_invoice_id_idx ON invoice_lines (invoice_id);
CREATE INDEX IF NOT EXISTS invoice_lines_shop_id_idx ON invoice_lines (shop_id);

-- ---------------------------------------------------------------------------
-- Purchases (confirmed only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchases (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    purchase_date      DATE NOT NULL,
    ingredient_id      UUID NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
    quantity           NUMERIC(12,3) NOT NULL CHECK (quantity > 0),
    unit               TEXT NOT NULL,
    unit_cost          NUMERIC(12,2) CHECK (unit_cost IS NULL OR unit_cost >= 0),
    total_cost         NUMERIC(12,2) CHECK (total_cost IS NULL OR total_cost >= 0),
    supplier           TEXT,
    invoice_id         UUID REFERENCES invoices(id) ON DELETE SET NULL,
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS purchases_shop_id_purchase_date_idx
    ON purchases (shop_id, purchase_date);

-- ---------------------------------------------------------------------------
-- Expenses (confirmed only). Amounts in ZAR.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expenses (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    expense_date       DATE NOT NULL,
    amount             NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    category           TEXT NOT NULL CHECK (category IN ('utilities_electricity','utilities_gas','equipment_fridge','equipment_appliances','general_ops','rent','other')),
    note               TEXT,
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS expenses_shop_id_expense_date_idx
    ON expenses (shop_id, expense_date);

-- ---------------------------------------------------------------------------
-- Waste (confirmed only). At least one of ingredient_id or product_id required.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS waste (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    waste_date         DATE NOT NULL,
    ingredient_id      UUID REFERENCES ingredients(id) ON DELETE RESTRICT,
    product_id         UUID REFERENCES products(id) ON DELETE RESTRICT,
    quantity           NUMERIC(12,3) NOT NULL CHECK (quantity > 0),
    unit               TEXT NOT NULL,
    reason             TEXT CHECK (reason IS NULL OR reason IN ('expired', 'burnt', 'spoiled', 'unsold', 'other')),
    estimated_cost     NUMERIC(12,2) CHECK (estimated_cost IS NULL OR estimated_cost >= 0),
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ingredient_id IS NOT NULL OR product_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS waste_shop_id_waste_date_idx ON waste (shop_id, waste_date);

-- ---------------------------------------------------------------------------
-- Stock counts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_counts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    count_date         DATE NOT NULL,
    ingredient_id      UUID NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
    counted_qty        NUMERIC(12,3) NOT NULL CHECK (counted_qty >= 0),
    expected_qty       NUMERIC(12,3) CHECK (expected_qty IS NULL OR expected_qty >= 0),
    variance           NUMERIC(12,3),
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stock_counts_shop_id_count_date_idx
    ON stock_counts (shop_id, count_date);

-- ---------------------------------------------------------------------------
-- Forecasts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forecasts (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id        TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    target_date    DATE NOT NULL,
    product_id     UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    predicted_qty  NUMERIC(12,3) NOT NULL CHECK (predicted_qty >= 0),
    range_low      NUMERIC(12,3) CHECK (range_low IS NULL OR range_low >= 0),
    range_high     NUMERIC(12,3) CHECK (range_high IS NULL OR range_high >= 0),
    method         TEXT,
    confidence     TEXT CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low')),
    reason         TEXT,
    actual_qty     NUMERIC(12,3) CHECK (actual_qty IS NULL OR actual_qty >= 0),
    error          NUMERIC(12,3),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (range_low IS NULL OR range_high IS NULL OR range_low <= range_high),
    UNIQUE (shop_id, target_date, product_id, method)
);

CREATE INDEX IF NOT EXISTS forecasts_shop_id_target_date_idx
    ON forecasts (shop_id, target_date);

-- ---------------------------------------------------------------------------
-- Pending confirmations (drafts). Do not treat these as saved business records.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_confirmations (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id            TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    kind               TEXT NOT NULL CHECK (kind IN ('sale', 'purchase', 'expense', 'waste', 'invoice', 'stock_count', 'other')),
    payload            JSONB NOT NULL,
    read_back          TEXT,
    source_message_id  UUID REFERENCES messages(id) ON DELETE SET NULL,
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'edited', 'cancelled')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pending_confirmations_shop_id_status_idx
    ON pending_confirmations (shop_id, status, created_at);
