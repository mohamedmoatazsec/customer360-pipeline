"""The SQL, pandas and Spark engines must agree on one gold contract.

``test_pipeline.py`` pins the SQL engine's expected result for the fixture. The same
values are reused here, so all three engines are checked against one shared truth
rather than against each other's possible bugs. The Spark engine runs only where
PySpark is installed (the dedicated ``Spark engine`` workflow); it is skipped
otherwise so the offline suite needs no JVM.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from customer360 import pandas_transform
from customer360.contracts import sanitize

EXPECTED_CUSTOMER = [
    (1, 2, 6, 1000, 975, "groceries", "multiple_carts"),
    (2, 1, 4, 300, 285, "stationery", "single_cart"),
    (3, 0, 0, 0, 0, None, "none"),
]
EXPECTED_CATEGORY = [
    ("groceries", 2, 4, 500, 475),
    ("stationery", 3, 6, 800, 785),
]
CUSTOMER_METRIC_COLUMNS = [
    "customer_id",
    "cart_count",
    "item_quantity",
    "cart_value_cents",
    "discounted_cart_value_cents",
    "top_category",
    "engagement_segment",
]


@pytest.fixture
def bronze() -> dict:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "sample.json").read_text(encoding="utf-8"),
        parse_float=Decimal,
    )
    return sanitize(payload)


def test_pandas_engine_matches_sql_expected(bronze: dict):
    gold = pandas_transform.transform(bronze)
    customer = pandas_transform.to_rows(gold["customer_360"][CUSTOMER_METRIC_COLUMNS])
    category = pandas_transform.to_rows(gold["category_summary"])
    assert customer == EXPECTED_CUSTOMER
    assert category == EXPECTED_CATEGORY


def test_pandas_engine_preserves_customer_grain_and_pseudonymous_contacts(bronze: dict):
    customer_360 = pandas_transform.transform(bronze)["customer_360"]
    assert list(customer_360["customer_id"]) == [1, 2, 3]
    assert all(len(str(key)) == 64 for key in customer_360["contact_key"])
    first = customer_360[customer_360["customer_id"] == 1].iloc[0]
    assert (first["city"], first["country"]) == ("Cairo", "Egypt")


def test_spark_engine_matches_sql_and_pandas(bronze: dict):
    pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    from customer360 import spark_transform

    spark = (
        SparkSession.builder.master("local[1]")
        .appName("customer360-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    try:
        gold = spark_transform.transform(spark, bronze)
        customer = spark_transform.to_rows(gold["customer_360"], CUSTOMER_METRIC_COLUMNS)
        category = spark_transform.to_rows(gold["category_summary"])
    finally:
        spark.stop()
    assert customer == EXPECTED_CUSTOMER
    assert category == EXPECTED_CATEGORY
