-- Sufra Café & Bakery (سُفرة): schema for the Arabic Restaurant Ops Agent.
-- Safe to re-run: data/seed.py runs it before every load, and docker compose runs it on the
-- first start. Every derived table is filled by data/build.py; the rules are in data/README.md.

CREATE EXTENSION IF NOT EXISTS vector;

DROP VIEW IF EXISTS item_cost_history, inventory_costed, waste_costed, shift_costs;
DROP FUNCTION IF EXISTS unit_cost_at;
DROP TABLE IF EXISTS pending_actions, kb_chunks, reviews, shifts, staff, waste_events,
    inventory_daily, sales, recipes, ingredient_costs, ingredients, suppliers, item_prices,
    source_product_map, menu_items, weather_daily, calendar, branches CASCADE;

-- ─── Branches and calendar ─────────────────────────────────────────────

CREATE TABLE branches (
    branch_id        text PRIMARY KEY,                 -- B1..B4
    name_en          text NOT NULL,
    name_ar          text NOT NULL,
    city             text NOT NULL,
    country          char(2) NOT NULL CHECK (country IN ('SA', 'EG')),
    area             text NOT NULL,                    -- area-manager scope, e.g. 'riyadh'
    currency         char(3) NOT NULL CHECK (currency IN ('SAR', 'EGP')),
    source_store_id  int,                              -- store_id in the Maven file; NULL for derived B4
    opened_on        date NOT NULL
);

CREATE TABLE calendar (
    cal_date      date NOT NULL,
    country       char(2) NOT NULL CHECK (country IN ('SA', 'EG')),
    is_weekend    boolean NOT NULL,
    is_ramadan    boolean NOT NULL DEFAULT false,
    holiday_name  text,
    PRIMARY KEY (cal_date, country)
);

CREATE TABLE weather_daily (
    city        text NOT NULL,
    obs_date    date NOT NULL,
    temp_max_c  numeric(4,1) NOT NULL,
    PRIMARY KEY (city, obs_date)
);

-- ─── Menu ──────────────────────────────────────────────────────────────

CREATE TABLE menu_items (
    item_id    text PRIMARY KEY,                       -- e.g. 'latte'
    name_en    text NOT NULL,
    name_ar    text NOT NULL,
    category   text NOT NULL,
    is_active  boolean NOT NULL DEFAULT true
);

-- Keeps every sale traceable to the source file's product.
CREATE TABLE source_product_map (
    source_product_id  int PRIMARY KEY,                -- product_id in the Maven file
    source_product     text NOT NULL,                  -- product_detail in the Maven file
    item_id            text NOT NULL REFERENCES menu_items
);

-- Menu price list. The current price is the row with the latest valid_from <= the date.
CREATE TABLE item_prices (
    item_id     text NOT NULL REFERENCES menu_items,
    currency    char(3) NOT NULL CHECK (currency IN ('SAR', 'EGP')),
    price       numeric(10,2) NOT NULL CHECK (price > 0),
    valid_from  date NOT NULL,
    PRIMARY KEY (item_id, currency, valid_from)
);

-- ─── Ingredients, suppliers and recipes ────────────────────────────────

CREATE TABLE suppliers (
    supplier_id     text PRIMARY KEY,
    name            text NOT NULL,
    lead_time_days  int NOT NULL CHECK (lead_time_days >= 0)
);

-- Bought-in baked goods (e.g. croissants) are ingredients counted in units,
-- so waste is always recorded at ingredient level.
CREATE TABLE ingredients (
    ingredient_id    text PRIMARY KEY,                 -- e.g. 'milk'
    name_en          text NOT NULL,
    name_ar          text NOT NULL,
    unit             text NOT NULL CHECK (unit IN ('g', 'ml', 'unit')),
    usda_fdc_id      int,                              -- USDA FoodData Central id
    shelf_life_days  int NOT NULL CHECK (shelf_life_days > 0),
    supplier_id      text NOT NULL REFERENCES suppliers,
    order_cycle_days int NOT NULL CHECK (order_cycle_days > 0),  -- days one order covers
    safety_days      int NOT NULL CHECK (safety_days >= 0)       -- 3 for perishables, 4 otherwise
);

-- Cost per g / ml / unit. A new row records a supplier price change.
CREATE TABLE ingredient_costs (
    ingredient_id  text NOT NULL REFERENCES ingredients,
    currency       char(3) NOT NULL CHECK (currency IN ('SAR', 'EGP')),
    unit_cost      numeric(12,4) NOT NULL CHECK (unit_cost > 0),
    valid_from     date NOT NULL,
    PRIMARY KEY (ingredient_id, currency, valid_from)
);

CREATE FUNCTION unit_cost_at(p_ingredient text, p_currency char(3), p_on date)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT unit_cost FROM ingredient_costs
     WHERE ingredient_id = p_ingredient AND currency = p_currency AND valid_from <= p_on
     ORDER BY valid_from DESC LIMIT 1
$$;

CREATE TABLE recipes (                                 -- bill of materials per menu item
    item_id        text NOT NULL REFERENCES menu_items,
    ingredient_id  text NOT NULL REFERENCES ingredients,
    qty_per_item   numeric(10,3) NOT NULL CHECK (qty_per_item > 0),
    PRIMARY KEY (item_id, ingredient_id)
);

-- Recipe cost per item and currency, one row per period between ingredient price changes.
CREATE VIEW item_cost_history AS
WITH change_dates AS (
    SELECT DISTINCT r.item_id, c.currency, c.valid_from
    FROM recipes r JOIN ingredient_costs c USING (ingredient_id)
)
SELECT d.item_id, d.currency, d.valid_from,
       lead(d.valid_from) OVER (PARTITION BY d.item_id, d.currency ORDER BY d.valid_from) AS valid_to,
       (SELECT sum(r.qty_per_item * c.unit_cost)
          FROM recipes r
          CROSS JOIN LATERAL (
              SELECT unit_cost FROM ingredient_costs c
               WHERE c.ingredient_id = r.ingredient_id AND c.currency = d.currency
                 AND c.valid_from <= d.valid_from
               ORDER BY c.valid_from DESC LIMIT 1) c
         WHERE r.item_id = d.item_id) AS unit_cost
FROM change_dates d;

-- ─── Sales ─────────────────────────────────────────────────────────────

-- One row per sale line. In the source file every transaction_id is a single line,
-- so orders = rows.
CREATE TABLE sales (
    sale_id            bigint PRIMARY KEY,             -- transaction_id from the source; new ids for B4
    branch_id          text NOT NULL REFERENCES branches,
    sold_at            timestamp NOT NULL,             -- branch local time
    sold_on            date GENERATED ALWAYS AS (sold_at::date) STORED,
    item_id            text NOT NULL REFERENCES menu_items,
    source_product_id  int NOT NULL REFERENCES source_product_map,
    qty                int NOT NULL CHECK (qty > 0),
    unit_price         numeric(10,2) NOT NULL CHECK (unit_price > 0),
    currency           char(3) NOT NULL CHECK (currency IN ('SAR', 'EGP')),
    line_total         numeric(12,2) GENERATED ALWAYS AS (qty * unit_price) STORED
);
CREATE INDEX sales_branch_day ON sales (branch_id, sold_on);
CREATE INDEX sales_item_day   ON sales (item_id, sold_on);

-- ─── Inventory and waste ───────────────────────────────────────────────

CREATE TABLE inventory_daily (
    branch_id      text NOT NULL REFERENCES branches,
    ingredient_id  text NOT NULL REFERENCES ingredients,
    stock_date     date NOT NULL,
    opening_qty    numeric(14,3) NOT NULL,
    delivered_qty  numeric(14,3) NOT NULL DEFAULT 0,
    used_qty       numeric(14,3) NOT NULL DEFAULT 0,
    wasted_qty     numeric(14,3) NOT NULL DEFAULT 0,
    closing_qty    numeric(14,3) NOT NULL,
    PRIMARY KEY (branch_id, ingredient_id, stock_date),
    CHECK (closing_qty = opening_qty + delivered_qty - used_qty - wasted_qty),
    CHECK (closing_qty >= 0)
);

CREATE TABLE waste_events (
    waste_id       bigserial PRIMARY KEY,
    branch_id      text NOT NULL REFERENCES branches,
    ingredient_id  text NOT NULL REFERENCES ingredients,
    wasted_on      date NOT NULL,
    qty            numeric(14,3) NOT NULL CHECK (qty > 0),
    reason         text NOT NULL CHECK (reason IN ('expired', 'prep_error', 'damaged', 'quality'))
);
CREATE INDEX waste_branch_day ON waste_events (branch_id, wasted_on);

-- Costed views shared by the MCP tools and data/checks.sql, so both compute the same numbers.
-- Waste share = waste cost ÷ cost of stock used (used + wasted), as in the waste SOP.
CREATE VIEW inventory_costed AS
SELECT i.*, b.currency, c.unit_cost,
       i.used_qty * c.unit_cost      AS used_cost,
       i.wasted_qty * c.unit_cost    AS wasted_cost,
       i.delivered_qty * c.unit_cost AS delivered_cost
FROM inventory_daily i
JOIN branches b USING (branch_id)
CROSS JOIN LATERAL (SELECT unit_cost_at(i.ingredient_id, b.currency, i.stock_date) AS unit_cost) c;

CREATE VIEW waste_costed AS
SELECT w.*, b.currency, w.qty * unit_cost_at(w.ingredient_id, b.currency, w.wasted_on) AS cost
FROM waste_events w JOIN branches b USING (branch_id);

-- ─── Staff ─────────────────────────────────────────────────────────────

CREATE TABLE staff (
    staff_id     text PRIMARY KEY,
    branch_id    text NOT NULL REFERENCES branches,
    full_name    text NOT NULL,
    job_role     text NOT NULL CHECK (job_role IN ('barista', 'baker', 'cashier', 'supervisor')),
    hourly_rate  numeric(8,2) NOT NULL CHECK (hourly_rate > 0),
    currency     char(3) NOT NULL CHECK (currency IN ('SAR', 'EGP'))
);

-- Timestamps, not times: Ramadan night shifts cross midnight.
CREATE TABLE shifts (
    shift_id    bigserial PRIMARY KEY,
    staff_id    text NOT NULL REFERENCES staff,
    branch_id   text NOT NULL REFERENCES branches,
    shift_date  date NOT NULL,
    starts_at   timestamp NOT NULL,
    ends_at     timestamp NOT NULL,
    overtime    boolean NOT NULL DEFAULT false,              -- paid at 1.5× the hourly rate
    CHECK (ends_at > starts_at)
);
CREATE INDEX shifts_branch_day ON shifts (branch_id, shift_date);

CREATE VIEW shift_costs AS
SELECT sh.*, st.full_name, st.job_role, st.currency,
       extract(epoch FROM sh.ends_at - sh.starts_at) / 3600 AS hours,
       extract(epoch FROM sh.ends_at - sh.starts_at) / 3600 * st.hourly_rate
           * CASE WHEN sh.overtime THEN 1.5 ELSE 1 END AS cost
FROM shifts sh JOIN staff st USING (staff_id);

-- ─── Reviews (loaded at runtime from Hugging Face, never committed) ────

CREATE TABLE reviews (
    review_id    bigserial PRIMARY KEY,
    branch_id    text NOT NULL REFERENCES branches,
    reviewed_on  date NOT NULL,
    body_ar      text NOT NULL,
    sentiment    text NOT NULL CHECK (sentiment IN ('positive', 'negative')),
    source_row   int NOT NULL                          -- row index in hadyelsahar/ar_res_reviews
);
CREATE INDEX reviews_branch_day ON reviews (branch_id, reviewed_on);

-- ─── Knowledge base (SOPs) ─────────────────────────────────────────────

-- Retrieval filters on allowed_roles before ranking. The table holds a few hundred
-- chunks, so exact search is fast and no ANN index is needed (an HNSW index would
-- filter after ranking and could drop permitted chunks).
CREATE TABLE kb_chunks (
    chunk_id       bigserial PRIMARY KEY,
    doc_id         text NOT NULL,                      -- file name under data/sop/
    title          text NOT NULL,
    language       char(2) NOT NULL CHECK (language IN ('ar', 'en')),
    allowed_roles  text[] NOT NULL,
    source         text NOT NULL,                      -- 'company' or the FDA Food Code section
    content        text NOT NULL,
    embedding      vector(1024) NOT NULL               -- BGE-M3 / multilingual-e5-large size
);

-- ─── Approvals ─────────────────────────────────────────────────────────

-- Write tools only insert here. Nothing changes until an approver decides.
CREATE TABLE pending_actions (
    action_id       bigserial PRIMARY KEY,
    action_type     text NOT NULL CHECK (action_type IN ('reorder', 'price_change')),
    branch_id       text REFERENCES branches,          -- NULL for chain-wide price changes
    payload         jsonb NOT NULL,                    -- the tool arguments as requested
    amount          numeric(12,2),                     -- reorder value, checked against approval limits
    currency        char(3) CHECK (currency IN ('SAR', 'EGP')),
    requested_by    text NOT NULL,
    requested_role  text NOT NULL
                    CHECK (requested_role IN ('owner', 'area_manager', 'branch_manager', 'staff')),
    approver_role   text NOT NULL CHECK (approver_role IN ('owner', 'area_manager')),
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_by      text,
    decided_at      timestamptz,
    decision_note   text,
    CHECK ((status = 'pending') = (decided_at IS NULL)),
    CHECK ((amount IS NULL) = (currency IS NULL))
);
CREATE INDEX pending_actions_open ON pending_actions (status, approver_role);
