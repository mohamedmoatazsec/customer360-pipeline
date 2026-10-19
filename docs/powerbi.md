# Power BI model

`customer360 report` writes a `powerbi/` folder of CSVs that load directly into Power BI
(or any BI tool) as a small star schema. The same tables back the Excel dashboard and the
category chart, so every surface tells one consistent story.

## Tables

| CSV | Role | Grain | Key |
|---|---|---|---|
| `dim_customer.csv` | Dimension | One customer | `customer_id` |
| `dim_product.csv` | Dimension | One product | `product_id` |
| `fact_cart.csv` | Fact (header) | One cart | `cart_id` |
| `fact_cart_item.csv` | Fact (line) | One line in one cart | `cart_id`, `line_number` |

## Relationships

Build these as single-direction, one-to-many relationships (the `1` side is the dimension):

- `dim_customer[customer_id]` 1 → * `fact_cart[customer_id]`
- `dim_product[product_id]` 1 → * `fact_cart_item[product_id]`
- `fact_cart[cart_id]` 1 → * `fact_cart_item[cart_id]`

`fact_cart` holds the source cart header (totals and quantities); `fact_cart_item` holds the
individual lines. Keeping both avoids fan-out: cart value is measured from the header, while
category preference and category totals are measured from the lines.

## Measures

Money is stored in integer cents, so every value measure divides by 100 for display.

```DAX
Cart value = DIVIDE ( SUM ( fact_cart[source_total_cents] ), 100 )

Discounted value = DIVIDE ( SUM ( fact_cart[source_discounted_cents] ), 100 )

Discount % =
VAR Gross = SUM ( fact_cart[source_total_cents] )
RETURN DIVIDE ( Gross - SUM ( fact_cart[source_discounted_cents] ), Gross )

Carts = DISTINCTCOUNT ( fact_cart[cart_id] )

Active customers =
CALCULATE ( DISTINCTCOUNT ( fact_cart[customer_id] ) )

Line value = DIVIDE ( SUM ( fact_cart_item[source_line_total_cents] ), 100 )

Items sold = SUM ( fact_cart_item[quantity] )
```

Suggested visuals: `Cart value` by `dim_product[category]` (bar), `Carts` by customer
engagement (built from a `cart_count` grouping), and a card row for `Active customers`,
`Carts` and `Cart value`. The committed [`dashboard.png`](evidence/dashboard.png) is the
same category-value view rendered from a live snapshot.

## Notes

- `dim_customer[contact_key]` is a SHA-256 pseudonym, not a name or email. Do not treat it as
  a marketing identifier.
- These exports are derived from synthetic DummyJSON data. Cart activity is not revenue, and
  amounts carry no currency.
