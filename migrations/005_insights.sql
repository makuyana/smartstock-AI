-- 005 peak hour, cash snapshots, margin and monthly P&L views.

ALTER TABLE sales_entries ADD COLUMN IF NOT EXISTS sale_hour smallint CHECK (sale_hour IS NULL OR sale_hour BETWEEN 0 AND 23);
CREATE TABLE IF NOT EXISTS cash_snapshots (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id       text NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    snapshot_date date NOT NULL,
    cash_on_hand  numeric(12,2) CHECK (cash_on_hand IS NULL OR cash_on_hand >= 0),
    bank_balance  numeric(12,2),
    savings       numeric(12,2),
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (shop_id, snapshot_date)
);
-- Ingredient cost of one unit of each product (NULL when any ingredient cost is missing)
CREATE OR REPLACE VIEW product_unit_cost AS
SELECT p.shop_id, p.id AS product_id, p.name, p.usual_price,
       CASE WHEN count(r.id) = 0 OR bool_or(COALESCE(i.avg_unit_cost, i.latest_unit_cost) IS NULL) THEN NULL
            ELSE sum(r.qty_per_unit * COALESCE(i.avg_unit_cost, i.latest_unit_cost)) END AS unit_cogs,
       count(r.id) AS recipe_lines
FROM products p
LEFT JOIN recipes r ON r.product_id = p.id
LEFT JOIN ingredients i ON i.id = r.ingredient_id
GROUP BY p.shop_id, p.id, p.name, p.usual_price;
-- Margin per product
CREATE OR REPLACE VIEW product_margins AS
SELECT shop_id, product_id, name, usual_price, unit_cogs,
       usual_price - unit_cogs AS unit_margin,
       CASE WHEN usual_price > 0 AND unit_cogs IS NOT NULL THEN round((usual_price - unit_cogs) / usual_price * 100, 1) END AS margin_pct
FROM product_unit_cost;
-- Monthly P&L; NULL parts mean data is missing
CREATE OR REPLACE VIEW monthly_pnl AS
WITH s AS (
  SELECT se.shop_id, date_trunc('month', se.sale_date)::date AS month,
         sum(se.quantity * COALESCE(se.unit_price, puc.usual_price)) AS revenue,
         bool_or(COALESCE(se.unit_price, puc.usual_price) IS NULL) AS revenue_incomplete,
         sum(se.quantity * puc.unit_cogs) AS cogs,
         bool_or(puc.unit_cogs IS NULL) AS cogs_incomplete
  FROM sales_entries se JOIN product_unit_cost puc ON puc.product_id = se.product_id
  GROUP BY 1, 2),
w AS (SELECT shop_id, date_trunc('month', waste_date)::date AS month, sum(estimated_cost) AS waste_cost,
             bool_or(estimated_cost IS NULL) AS waste_incomplete FROM waste GROUP BY 1, 2),
e AS (SELECT shop_id, date_trunc('month', expense_date)::date AS month, sum(amount) AS opex,
             sum(amount) FILTER (WHERE category IN ('utilities_electricity','utilities_gas','equipment_fridge','equipment_appliances')) AS utility_appliance_costs
      FROM expenses GROUP BY 1, 2)
SELECT COALESCE(s.shop_id, w.shop_id, e.shop_id) AS shop_id,
       COALESCE(s.month, w.month, e.month) AS month,
       s.revenue, s.cogs, COALESCE(w.waste_cost, 0) AS waste_cost, COALESCE(e.opex, 0) AS opex,
       COALESCE(e.utility_appliance_costs, 0) AS utility_appliance_costs,
       s.revenue - s.cogs - COALESCE(w.waste_cost, 0) - COALESCE(e.opex, 0) AS net_profit,
       COALESCE(s.revenue_incomplete, false) AS revenue_incomplete,
       COALESCE(s.cogs_incomplete, false) AS cogs_incomplete,
       COALESCE(w.waste_incomplete, false) AS waste_incomplete
FROM s FULL JOIN w ON w.shop_id = s.shop_id AND w.month = s.month
       FULL JOIN e ON e.shop_id = COALESCE(s.shop_id, w.shop_id) AND e.month = COALESCE(s.month, w.month);
