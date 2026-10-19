-- Full snapshots: natural keys are unique within this synthetic source.
CREATE SCHEMA silver;
CREATE SCHEMA gold;

CREATE TABLE silver.dim_customer AS
SELECT customer_id, contact_key, city, country
FROM bronze_customers ORDER BY customer_id;

CREATE TABLE silver.dim_product AS
SELECT product_id, product_name, category, list_price_cents
FROM bronze_products ORDER BY product_id;

CREATE TABLE silver.fact_cart AS
SELECT * FROM bronze_carts ORDER BY cart_id;

CREATE TABLE silver.fact_cart_item AS
SELECT * FROM bronze_cart_items ORDER BY cart_id, product_id;

-- Aggregate each grain separately before joining to avoid multiplying cart totals.
CREATE TABLE gold.customer_360 AS
WITH cart_metrics AS (
    SELECT customer_id, COUNT(*)::BIGINT AS cart_count,
           SUM(source_quantity)::BIGINT AS item_quantity,
           SUM(source_total_cents)::BIGINT AS cart_value_cents,
           SUM(source_discounted_cents)::BIGINT AS discounted_cart_value_cents
    FROM silver.fact_cart GROUP BY customer_id
), ranked_categories AS (
    SELECT c.customer_id, p.category,
           ROW_NUMBER() OVER (
               PARTITION BY c.customer_id
               ORDER BY SUM(i.source_line_total_cents) DESC, p.category ASC
           ) AS preference_rank
    FROM silver.fact_cart c
    JOIN silver.fact_cart_item i USING (cart_id)
    JOIN silver.dim_product p USING (product_id)
    GROUP BY c.customer_id, p.category
)
SELECT d.customer_id, d.contact_key, d.city, d.country,
       COALESCE(m.cart_count, 0)::BIGINT AS cart_count,
       COALESCE(m.item_quantity, 0)::BIGINT AS item_quantity,
       COALESCE(m.cart_value_cents, 0)::BIGINT AS cart_value_cents,
       COALESCE(m.discounted_cart_value_cents, 0)::BIGINT AS discounted_cart_value_cents,
       r.category AS top_category,
       CASE WHEN m.cart_count IS NULL THEN 'none'
            WHEN m.cart_count = 1 THEN 'single_cart'
            ELSE 'multiple_carts' END AS engagement_segment
FROM silver.dim_customer d
LEFT JOIN cart_metrics m USING (customer_id)
LEFT JOIN ranked_categories r ON d.customer_id = r.customer_id AND r.preference_rank = 1
ORDER BY d.customer_id;

CREATE TABLE gold.category_summary AS
SELECT p.category, COUNT(DISTINCT i.cart_id)::BIGINT AS cart_count,
       SUM(i.quantity)::BIGINT AS item_quantity,
       SUM(i.source_line_total_cents)::BIGINT AS cart_value_cents,
       SUM(i.source_line_discounted_cents)::BIGINT AS discounted_cart_value_cents
FROM silver.fact_cart_item i
JOIN silver.dim_product p USING (product_id)
GROUP BY p.category ORDER BY p.category;
