# Contracts and design decisions

## Source and normalization

All three endpoints are public, read-only DummyJSON APIs. They describe synthetic users, products and shopping baskets. The extractor requests only selected fields, but still applies an independent allowlist before writing anything. Full address objects and nested product metadata can arrive in memory; only the documented output columns survive.

| Source | Required fields | Normalized destination |
|---|---|---|
| `users` | `id`, `email`, `address.city`, `address.country` | `customers`: `customer_id`, `contact_key`, `city`, `country` |
| `products` | `id`, `title`, `category`, `price` | `products`: `product_id`, `product_name`, `category`, `list_price_cents` |
| `carts` | `id`, `userId`, `total`, `discountedTotal`, `totalProducts`, `totalQuantity`, nonempty `products` | Cart headers and cart/source-line items |
| Each cart product | `id`, `price`, `quantity`, `total`, `discountedTotal`, `discountPercentage` | Integer cents, quantity and discount basis points |

Identifiers and quantities must be positive integers; booleans are rejected. Text must be nonempty and at most 1,000 characters. Numeric values must be finite, nonnegative and fit signed BIGINT after scaling. Discounts must be between 0 and 100 percent. Duplicate customer, product or cart identifiers fail the run. Repeated products within a cart are preserved as separate numbered source lines; the code never silently deduplicates them.

JSON decimals are parsed with `Decimal`. Amounts are multiplied by 100 and rounded half up to cents; percentage discounts are scaled to basis points. SQL expects exact unit-price × quantity agreement. Source-reported discounted amounts and cart sums allow a one-cent rounding difference. This is a deliberate data contract, not a generic rule for every currency or accounting system. DummyJSON provides no currency field; no currency conversion or currency symbol is inferred.

Email uses trimmed, lowercase text and SHA-256. It is not used to merge customers. `customer_id` remains the source key; a changed email changes the contact pseudonym. Plaintext email and disallowed fields are not retained, including in failure messages. The fixture uses deliberately fictional `example.test` addresses.

## Why these layers

- **Bronze:** the minimum safe, typed representation needed to explain the result. Sanitization happens before persistence, so this is not a raw forensic archive.
- **Silver:** four tables keep customer, product, cart and cart/source-line grains explicit. Cart-time line prices are kept independently from current product list prices.
- **Gold:** two consumer tables provide customer activity and category totals. Customers without carts remain in the customer table with zero metrics and a null top category.

Activity segments are `none`, `single_cart` and `multiple_carts`, based solely on cart count in the current snapshot. They are not marketing propensity scores. The live dataset may contain only one of these cases; the deterministic fixture tests all three.

## Quality gate

The Python contract checks types, bounds, required fields, duplicate identifiers and pagination completeness. The separate [SQL gate](../src/customer360/sql/quality.sql) evaluates 28 named checks for:

- Dimension/fact key uniqueness and referential integrity.
- Present contact/location attributes, positive quantities and valid amounts.
- Line calculation and discount agreement.
- Cart header product counts, quantities and amounts against their lines.
- Customer and category grains, segment logic and aggregate reconciliation.

Every SQL check returns a nonnegative failure count. No check is skipped, and any nonzero value stops publication. Bad records are rejected as a snapshot, not silently repaired or discarded. Failure reports preserve check names and counts, not original records.

## Publication and repeatability

The pipeline stages files on the destination filesystem, builds the database in a transaction, evaluates all SQL checks, and exports the gold files. It then renames the completed run folder and atomically replaces `current.json` with `os.replace`. Readers must use that pointer. Failure before publication leaves it unchanged. A crash between the directory rename and pointer replacement can leave an unreferenced completed folder; readers still see the last published run.

One writer per output directory is assumed. There is no distributed lock, catalog transaction or object-store atomicity claim. Filesystem atomic rename does not guarantee durability through power failure. Retention is manual; a long-running deployment would need lifecycle rules and a writer lock.

Re-running the same input produces the same sorted logical rows and `dataset_hash`, without appending duplicate business records. Run IDs and ingestion timestamps change. The input fingerprint excludes timing; the manifest also records SQL hashes and runtime versions. A Parquet or DuckDB file is not claimed to be byte-identical across different engine versions.

The API has neither a consistent snapshot token nor a reliable cross-endpoint watermark. Total changes, duplicate keys and broken relationships are detected; a same-size source mutation can still slip through. Full snapshots are a practical choice for this small, synthetic source, not a substitute for CDC.

## Moving this design to Databricks

This section is a design mapping, **not implemented experience**. Python extraction could write to restricted cloud storage; Spark transformations could populate Delta tables, with explicit merge keys and DLT expectations replacing the local gate. A Workflows job would sequence extraction, transformation and publishing. Contact identifiers and consent would require governed access and key management. Incremental ingestion would first need a source change contract or immutable event stream. Adding Kafka without such a source would only add infrastructure.
