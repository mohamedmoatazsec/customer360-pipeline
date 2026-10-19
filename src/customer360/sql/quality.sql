-- Every query reports offending rows/groups. Publication requires zero failures.
WITH item_rollup AS (
    SELECT cart_id, COUNT(*) AS product_count, SUM(quantity) AS quantity,
           SUM(source_line_total_cents) AS total_cents,
           SUM(source_line_discounted_cents) AS discounted_cents
    FROM silver.fact_cart_item GROUP BY cart_id
)
SELECT 'customer_primary_key' AS check_name, COUNT(*) AS failures
FROM (SELECT customer_id FROM silver.dim_customer GROUP BY 1 HAVING COUNT(*) > 1)
UNION ALL
SELECT 'product_primary_key', COUNT(*) FROM (
    SELECT product_id FROM silver.dim_product GROUP BY 1 HAVING COUNT(*) > 1)
UNION ALL
SELECT 'cart_primary_key', COUNT(*) FROM (
    SELECT cart_id FROM silver.fact_cart GROUP BY 1 HAVING COUNT(*) > 1)
UNION ALL
SELECT 'cart_item_composite_key', COUNT(*) FROM (
    SELECT cart_id, line_number FROM silver.fact_cart_item GROUP BY 1, 2 HAVING COUNT(*) > 1)
UNION ALL
SELECT 'cart_customer_reference', COUNT(*) FROM silver.fact_cart c
LEFT JOIN silver.dim_customer d USING (customer_id) WHERE d.customer_id IS NULL
UNION ALL
SELECT 'item_cart_reference', COUNT(*) FROM silver.fact_cart_item i
LEFT JOIN silver.fact_cart c USING (cart_id) WHERE c.cart_id IS NULL
UNION ALL
SELECT 'item_product_reference', COUNT(*) FROM silver.fact_cart_item i
LEFT JOIN silver.dim_product p USING (product_id) WHERE p.product_id IS NULL
UNION ALL
SELECT 'customer_contact_key_format', COUNT(*) FROM silver.dim_customer
WHERE contact_key IS NULL OR NOT regexp_full_match(contact_key, '[0-9a-f]{64}')
UNION ALL
SELECT 'customer_location_present', COUNT(*) FROM silver.dim_customer
WHERE city IS NULL OR country IS NULL OR trim(city) = '' OR trim(country) = ''
UNION ALL
SELECT 'product_attributes_valid', COUNT(*) FROM silver.dim_product
WHERE trim(product_name) = '' OR trim(category) = '' OR list_price_cents < 0
UNION ALL
SELECT 'item_quantity_positive', COUNT(*) FROM silver.fact_cart_item WHERE quantity <= 0
UNION ALL
SELECT 'item_amounts_valid', COUNT(*) FROM silver.fact_cart_item
WHERE unit_price_cents < 0 OR source_line_total_cents < 0
   OR source_line_discounted_cents < 0 OR source_line_discounted_cents > source_line_total_cents
   OR discount_basis_points NOT BETWEEN 0 AND 10000
UNION ALL
SELECT 'line_price_times_quantity', COUNT(*) FROM silver.fact_cart_item
WHERE unit_price_cents * quantity <> source_line_total_cents
UNION ALL
SELECT 'line_discount_reconciles', COUNT(*) FROM silver.fact_cart_item
WHERE ABS(source_line_discounted_cents - ROUND(
    source_line_total_cents::DECIMAL(24, 4) * (10000 - discount_basis_points) / 10000
)) > 1
UNION ALL
SELECT 'cart_product_count_reconciles', COUNT(*) FROM silver.fact_cart c
LEFT JOIN item_rollup i USING (cart_id)
WHERE i.cart_id IS NULL OR c.source_product_count <> i.product_count
UNION ALL
SELECT 'cart_quantity_reconciles', COUNT(*) FROM silver.fact_cart c
LEFT JOIN item_rollup i USING (cart_id)
WHERE i.cart_id IS NULL OR c.source_quantity <> i.quantity
UNION ALL
SELECT 'cart_total_reconciles', COUNT(*) FROM silver.fact_cart c
LEFT JOIN item_rollup i USING (cart_id)
WHERE i.cart_id IS NULL OR ABS(c.source_total_cents - i.total_cents) > 1
UNION ALL
SELECT 'cart_discounted_total_reconciles', COUNT(*) FROM silver.fact_cart c
LEFT JOIN item_rollup i USING (cart_id)
WHERE i.cart_id IS NULL OR ABS(c.source_discounted_cents - i.discounted_cents) > 1
UNION ALL
SELECT 'customer_grain_preserved', ABS(
    (SELECT COUNT(*) FROM gold.customer_360) - (SELECT COUNT(*) FROM silver.dim_customer))
UNION ALL
SELECT 'customer_gold_primary_key', COUNT(*) FROM (
    SELECT customer_id FROM gold.customer_360 GROUP BY 1 HAVING COUNT(*) > 1)
UNION ALL
SELECT 'customer_gold_carts_reconcile', ABS(
    (SELECT COALESCE(SUM(cart_count), 0) FROM gold.customer_360)
    - (SELECT COUNT(*) FROM silver.fact_cart))
UNION ALL
SELECT 'customer_gold_value_reconciles', ABS(
    (SELECT COALESCE(SUM(cart_value_cents), 0) FROM gold.customer_360)
    - (SELECT COALESCE(SUM(source_total_cents), 0) FROM silver.fact_cart))
UNION ALL
SELECT 'customer_gold_discount_reconciles', ABS(
    (SELECT COALESCE(SUM(discounted_cart_value_cents), 0) FROM gold.customer_360)
    - (SELECT COALESCE(SUM(source_discounted_cents), 0) FROM silver.fact_cart))
UNION ALL
SELECT 'customer_gold_quantity_reconciles', ABS(
    (SELECT COALESCE(SUM(item_quantity), 0) FROM gold.customer_360)
    - (SELECT COALESCE(SUM(quantity), 0) FROM silver.fact_cart_item))
UNION ALL
SELECT 'customer_segment_consistent', COUNT(*) FROM gold.customer_360
WHERE engagement_segment <> CASE WHEN cart_count = 0 THEN 'none'
    WHEN cart_count = 1 THEN 'single_cart' ELSE 'multiple_carts' END
   OR (cart_count = 0 AND top_category IS NOT NULL)
   OR (cart_count > 0 AND top_category IS NULL)
UNION ALL
SELECT 'category_gold_value_reconciles', ABS(
    (SELECT COALESCE(SUM(cart_value_cents), 0) FROM gold.category_summary)
    - (SELECT COALESCE(SUM(source_line_total_cents), 0) FROM silver.fact_cart_item))
UNION ALL
SELECT 'category_gold_discount_reconciles', ABS(
    (SELECT COALESCE(SUM(discounted_cart_value_cents), 0) FROM gold.category_summary)
    - (SELECT COALESCE(SUM(source_line_discounted_cents), 0) FROM silver.fact_cart_item))
UNION ALL
SELECT 'category_gold_quantity_reconciles', ABS(
    (SELECT COALESCE(SUM(item_quantity), 0) FROM gold.category_summary)
    - (SELECT COALESCE(SUM(quantity), 0) FROM silver.fact_cart_item));
