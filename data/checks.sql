-- Checks that confirm each planted fact and the main integrity rules after `make seed`.
-- Every statement returns rows of (check_name, passed, detail); tests/test_planted_facts.py
-- runs them all and fails on any passed = false.

-- Every source line is loaded once, in the branch mapped from its store.
SELECT 'source_rows_per_branch' AS check_name,
       bool_and(n = expected) AS passed,
       string_agg(branch_id || '=' || n, ', ' ORDER BY branch_id) AS detail
FROM (SELECT branch_id, count(*) AS n,
             CASE branch_id WHEN 'B1' THEN 50735 WHEN 'B2' THEN 47782 WHEN 'B3' THEN 50599 END AS expected
      FROM sales WHERE branch_id <> 'B4' GROUP BY branch_id) s;

-- B4 Al Malqa opened on 1 April 2026: no sales before, sales from that day.
SELECT 'b4_opened_1_april',
       min(sold_on) = DATE '2026-04-01',
       'first sale ' || min(sold_on) || ', ' || count(*) || ' lines'
FROM sales WHERE branch_id = 'B4';

-- B2 Tahlia's waste spike: May is the worst branch-month by far, driven by prep_error on
-- milk and croissant dough between 10 and 23 May.
WITH monthly AS (
    SELECT branch_id, date_trunc('month', stock_date)::date AS month,
           sum(wasted_cost) / nullif(sum(used_cost + wasted_cost), 0) AS share
    FROM inventory_costed GROUP BY 1, 2
), spike AS (
    SELECT count(DISTINCT wasted_on) AS days, sum(cost) AS cost
    FROM waste_costed
    WHERE branch_id = 'B2' AND reason = 'prep_error' AND ingredient_id IN ('milk', 'croissant_dough')
      AND wasted_on BETWEEN '2026-05-10' AND '2026-05-23'
)
SELECT 'b2_may_waste_spike',
       (SELECT share FROM monthly WHERE branch_id = 'B2' AND month = '2026-05-01')
           > 2 * (SELECT max(share) FROM monthly WHERE NOT (branch_id = 'B2' AND month = '2026-05-01'))
       AND (SELECT days FROM spike) = 14,
       'B2 May share ' || round(100 * (SELECT share FROM monthly WHERE branch_id = 'B2' AND month = '2026-05-01'), 2)
           || '%, next highest branch-month '
           || round(100 * (SELECT max(share) FROM monthly WHERE NOT (branch_id = 'B2' AND month = '2026-05-01')), 2)
           || '%, prep_error days in window ' || (SELECT days FROM spike);

-- Exactly one menu item sells below recipe cost on 30 June (in both currencies): pistachio
-- ma'amoul, after the 1 April pistachio price increase. It was profitable before.
WITH priced AS (
    SELECT p.item_id, p.currency, p.price, h.unit_cost, h.valid_from
    FROM item_prices p JOIN item_cost_history h USING (item_id, currency)
)
SELECT 'one_negative_margin_item',
       array_agg(DISTINCT item_id) = ARRAY['pistachio_maamoul']
       AND count(DISTINCT currency) = 2
       AND (SELECT bool_and(price > unit_cost) FROM priced
             WHERE item_id = 'pistachio_maamoul' AND valid_from < '2026-04-01'),
       'negative after April: ' || string_agg(item_id || ' ' || currency || ' ' || round(price - unit_cost, 2), ', ')
FROM priced
WHERE valid_from <= '2026-06-30' AND price < unit_cost
  AND valid_from = (SELECT max(valid_from) FROM item_cost_history x
                     WHERE x.item_id = priced.item_id AND x.currency = priced.currency
                       AND x.valid_from <= '2026-06-30');

-- B1 Olaya kept its normal schedule in Ramadan and covered evenings with overtime: labour
-- above 30% of sales. B2 and B3 adjusted and stay at or below 30%.
WITH r AS (
    SELECT b.branch_id,
           (SELECT sum(cost) FROM shift_costs s WHERE s.branch_id = b.branch_id
               AND s.shift_date BETWEEN '2026-02-18' AND '2026-03-19')
         / (SELECT sum(line_total) FROM sales s WHERE s.branch_id = b.branch_id
               AND s.sold_on BETWEEN '2026-02-18' AND '2026-03-19') AS ratio
    FROM branches b WHERE b.branch_id IN ('B1', 'B2', 'B3')
)
SELECT 'b1_ramadan_labour_over_30pct',
       bool_and(CASE WHEN branch_id = 'B1' THEN ratio > 0.30 ELSE ratio <= 0.30 END),
       string_agg(branch_id || '=' || round(100 * ratio, 1) || '%', ', ' ORDER BY branch_id)
FROM r;

-- Outside Ramadan, every branch-month stays at or below 30% labour.
WITH m AS (
    SELECT s.branch_id, date_trunc('month', s.shift_date)::date AS month, sum(s.cost) AS cost
    FROM shift_costs s
    WHERE s.shift_date NOT BETWEEN '2026-02-18' AND '2026-03-19'
    GROUP BY 1, 2
), v AS (
    SELECT m.branch_id, m.month, m.cost / sum(x.line_total) AS ratio
    FROM m JOIN sales x ON x.branch_id = m.branch_id
                       AND date_trunc('month', x.sold_on) = m.month
                       AND x.sold_on NOT BETWEEN '2026-02-18' AND '2026-03-19'
    GROUP BY 1, 2, m.cost
)
SELECT 'labour_at_most_30pct_outside_ramadan', bool_and(ratio <= 0.30),
       'highest ' || round(100 * max(ratio), 1) || '%'
FROM v;

-- Low-stock trap: on 30 June, espresso beans are the only item at B1 below 2 days of cover,
-- and they first dropped below 2 days in the last week of June.
WITH cover AS (
    SELECT ingredient_id, stock_date,
           closing_qty / nullif(avg(used_qty) OVER (PARTITION BY ingredient_id ORDER BY stock_date
                                                ROWS BETWEEN 13 PRECEDING AND CURRENT ROW), 0) AS days
    FROM inventory_daily WHERE branch_id = 'B1'
)
SELECT 'b1_low_stock_trap',
       array_agg(ingredient_id) FILTER (WHERE stock_date = '2026-06-30' AND days < 2) = ARRAY['espresso_beans']
       AND min(stock_date) FILTER (WHERE ingredient_id = 'espresso_beans' AND days < 2
                                     AND stock_date >= '2026-06-01') >= '2026-06-24',
       'below 2 days on 30 June: '
           || array_to_string(array_agg(ingredient_id || ' ' || round(days, 2))
                                  FILTER (WHERE stock_date = '2026-06-30' AND days < 2), ', ')
FROM cover;
