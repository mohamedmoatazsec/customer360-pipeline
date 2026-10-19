# Recorded live execution

A real, read-only run against DummyJSON completed at **2026-10-19T13:41:36.060847+00:00**. Source data is synthetic and can change. These are measured snapshot counts, not hardcoded expectations.

| Dataset | Rows |
|---|---:|
| Customers | 208 |
| Products | 194 |
| Carts | 208 |
| Cart source lines | 800 |

**28/28 SQL checks passed**, with zero failed records under those checks. Pipeline duration: 13.438 seconds.

The accompanying offline suite passed **35 tests** on Python 3.12.14; lint and formatting checks passed. GitHub CI separately checks Python 3.11, 3.12 and 3.13.

- [Run manifest](run.json): times, counts, runtime versions, input fingerprint and SQL hashes.
- [Quality report](quality.json): every check and failure count.
- [Category totals](category_summary.csv): aggregate inspection export. Category cart counts overlap when a cart contains multiple categories.
- [CI runs](https://github.com/mohamedmoatazsec/customer360-pipeline/actions/workflows/ci.yml).

## A source issue the live run exposed

An initial contract assumed one product per cart. The full dataset contained repeated product IDs within a cart, which are legitimate separate source lines. The model now preserves each line with a 1-based `line_number` and keys it by `(cart_id, line_number)`. A regression test confirms that repeated products are preserved and customer totals still reconcile. Nothing is silently deduplicated.

The recorded live dataset has one cart per customer. The offline fixture deliberately also covers customers without carts, multiple carts, and a category-value tie. This separates observed source behavior from tested edge cases.

## Reproduce

```sh
python -m customer360 run --output data/live
python -m pytest -q
```

A future live run may have different counts or values. Use the manifest's sanitized-input hash to compare snapshots. This published evidence excludes customer-level exports and the local warehouse.
