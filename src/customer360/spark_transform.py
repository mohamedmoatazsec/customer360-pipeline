"""PySpark reference engine for the gold transform.

A third implementation of the contract in ``sql/transform.sql``, written with the
Spark DataFrame API so the same logic runs on a Databricks-style engine. It mirrors
``pandas_transform.py`` step for step and is cross-checked against the SQL and pandas
engines in ``tests/test_engines.py`` (the dedicated ``Spark engine`` CI workflow
installs PySpark and runs it; the default offline suite skips it when PySpark is
absent).

Importing this module requires PySpark. Nothing in the core pipeline imports it, so
the package still installs and runs with DuckDB alone.

Grain rules reproduced here are identical to the SQL and pandas engines: header-level
cart aggregation (no line fan-out), line-level category preference with a
``value DESC, category ASC`` tie-break, zero-filled customers without carts, and
distinct-cart category counts. All money stays in integer cents.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType, StructField, StructType

# Mirrors the column order in pandas_transform.py; kept local so this engine needs
# only PySpark to import.
CUSTOMER_360_COLUMNS = [
    "customer_id",
    "contact_key",
    "city",
    "country",
    "cart_count",
    "item_quantity",
    "cart_value_cents",
    "discounted_cart_value_cents",
    "top_category",
    "engagement_segment",
]

CATEGORY_SUMMARY_COLUMNS = [
    "category",
    "cart_count",
    "item_quantity",
    "cart_value_cents",
    "discounted_cart_value_cents",
]

_LONG = LongType()
_STR = StringType()
_SCHEMAS = {
    "customers": StructType(
        [
            StructField("customer_id", _LONG, False),
            StructField("contact_key", _STR, False),
            StructField("city", _STR, False),
            StructField("country", _STR, False),
        ]
    ),
    "products": StructType(
        [
            StructField("product_id", _LONG, False),
            StructField("product_name", _STR, False),
            StructField("category", _STR, False),
            StructField("list_price_cents", _LONG, False),
        ]
    ),
    "carts": StructType(
        [
            StructField("cart_id", _LONG, False),
            StructField("customer_id", _LONG, False),
            StructField("source_total_cents", _LONG, False),
            StructField("source_discounted_cents", _LONG, False),
            StructField("source_product_count", _LONG, False),
            StructField("source_quantity", _LONG, False),
        ]
    ),
    "cart_items": StructType(
        [
            StructField("cart_id", _LONG, False),
            StructField("line_number", _LONG, False),
            StructField("product_id", _LONG, False),
            StructField("unit_price_cents", _LONG, False),
            StructField("quantity", _LONG, False),
            StructField("source_line_total_cents", _LONG, False),
            StructField("source_line_discounted_cents", _LONG, False),
            StructField("discount_basis_points", _LONG, False),
        ]
    ),
}


def _frame(spark: SparkSession, bronze: dict, resource: str) -> DataFrame:
    schema = _SCHEMAS[resource]
    names = [field.name for field in schema.fields]
    rows = [tuple(record[name] for name in names) for record in bronze.get(resource, [])]
    return spark.createDataFrame(rows, schema)


def transform(spark: SparkSession, bronze: dict) -> dict[str, DataFrame]:
    """Return the gold ``customer_360`` and ``category_summary`` Spark DataFrames."""
    customers = _frame(spark, bronze, "customers")
    products = _frame(spark, bronze, "products")
    carts = _frame(spark, bronze, "carts")
    items = _frame(spark, bronze, "cart_items")

    cart_metrics = carts.groupBy("customer_id").agg(
        F.count(F.lit(1)).cast(_LONG).alias("cart_count"),
        F.sum("source_quantity").cast(_LONG).alias("item_quantity"),
        F.sum("source_total_cents").cast(_LONG).alias("cart_value_cents"),
        F.sum("source_discounted_cents").cast(_LONG).alias("discounted_cart_value_cents"),
    )

    item_categories = items.join(carts.select("cart_id", "customer_id"), "cart_id", "inner").join(
        products.select("product_id", "category"), "product_id", "inner"
    )
    category_value = item_categories.groupBy("customer_id", "category").agg(
        F.sum("source_line_total_cents").alias("line_value")
    )
    preference = Window.partitionBy("customer_id").orderBy(
        F.col("line_value").desc(), F.col("category").asc()
    )
    top_category = (
        category_value.withColumn("preference_rank", F.row_number().over(preference))
        .filter(F.col("preference_rank") == 1)
        .select("customer_id", F.col("category").alias("top_category"))
    )

    customer_360 = customers.join(cart_metrics, "customer_id", "left").join(
        top_category, "customer_id", "left"
    )
    for column in (
        "cart_count",
        "item_quantity",
        "cart_value_cents",
        "discounted_cart_value_cents",
    ):
        customer_360 = customer_360.withColumn(
            column, F.coalesce(F.col(column), F.lit(0)).cast(_LONG)
        )
    customer_360 = customer_360.withColumn(
        "engagement_segment",
        F.when(F.col("cart_count") == 0, F.lit("none"))
        .when(F.col("cart_count") == 1, F.lit("single_cart"))
        .otherwise(F.lit("multiple_carts")),
    )
    customer_360 = customer_360.select(*CUSTOMER_360_COLUMNS).orderBy("customer_id")

    category_items = items.join(products.select("product_id", "category"), "product_id", "inner")
    category_summary = (
        category_items.groupBy("category")
        .agg(
            F.count_distinct("cart_id").cast(_LONG).alias("cart_count"),
            F.sum("quantity").cast(_LONG).alias("item_quantity"),
            F.sum("source_line_total_cents").cast(_LONG).alias("cart_value_cents"),
            F.sum("source_line_discounted_cents").cast(_LONG).alias("discounted_cart_value_cents"),
        )
        .select(*CATEGORY_SUMMARY_COLUMNS)
        .orderBy("category")
    )

    return {"customer_360": customer_360, "category_summary": category_summary}


def to_rows(frame: DataFrame, columns: list[str] | None = None) -> list[tuple]:
    """Collect an ordered Spark DataFrame to a list of native Python tuples."""
    if columns is not None:
        frame = frame.select(*columns)
    return [tuple(row) for row in frame.collect()]
