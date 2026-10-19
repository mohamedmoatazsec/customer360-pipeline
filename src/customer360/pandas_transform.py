"""Pandas reference engine for the gold transform.

This is a second, portable implementation of the exact transform in
``sql/transform.sql``. DuckDB SQL remains the default production engine; this
module expresses the same contract with the pandas API so the logic can be read,
reused and cross-checked independently of any one engine. ``tests/test_engines.py``
asserts that the SQL, pandas and Spark engines produce identical gold rows from
the same bronze snapshot.

Grain rules that this engine reproduces exactly:

* Customer cart metrics aggregate the **cart header** before joining to customers,
  so a cart's value is never multiplied by its number of lines.
* The preferred category aggregates **line** values per customer; ties are broken
  by category name ascending (``value DESC, category ASC``).
* Customers without carts are retained with zero metrics and a null top category.
* Category totals count **distinct carts** per category.

All money stays in integer cents; no floating-point money is produced.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

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

_BRONZE_INT_COLUMNS = {
    "customers": ["customer_id"],
    "products": ["product_id", "list_price_cents"],
    "carts": [
        "cart_id",
        "customer_id",
        "source_total_cents",
        "source_discounted_cents",
        "source_product_count",
        "source_quantity",
    ],
    "cart_items": [
        "cart_id",
        "line_number",
        "product_id",
        "unit_price_cents",
        "quantity",
        "source_line_total_cents",
        "source_line_discounted_cents",
        "discount_basis_points",
    ],
}

_BRONZE_COLUMNS = {
    "customers": ["customer_id", "contact_key", "city", "country"],
    "products": ["product_id", "product_name", "category", "list_price_cents"],
    "carts": _BRONZE_INT_COLUMNS["carts"],
    "cart_items": _BRONZE_INT_COLUMNS["cart_items"],
}


def _frame(bronze: dict[str, list[dict[str, Any]]], resource: str) -> pd.DataFrame:
    """Build a typed DataFrame for one bronze resource, correct even when empty."""
    columns = _BRONZE_COLUMNS[resource]
    frame = pd.DataFrame(bronze.get(resource, []), columns=columns)
    for column in _BRONZE_INT_COLUMNS[resource]:
        frame[column] = frame[column].astype("int64")
    return frame


def transform(bronze: dict[str, list[dict[str, Any]]]) -> dict[str, pd.DataFrame]:
    """Return the gold ``customer_360`` and ``category_summary`` tables.

    ``bronze`` is the sanitized snapshot produced by :func:`customer360.contracts.sanitize`.
    """
    customers = _frame(bronze, "customers")
    products = _frame(bronze, "products")
    carts = _frame(bronze, "carts")
    items = _frame(bronze, "cart_items")

    cart_metrics = carts.groupby("customer_id", as_index=False).agg(
        cart_count=("cart_id", "size"),
        item_quantity=("source_quantity", "sum"),
        cart_value_cents=("source_total_cents", "sum"),
        discounted_cart_value_cents=("source_discounted_cents", "sum"),
    )

    item_categories = items.merge(
        carts[["cart_id", "customer_id"]], on="cart_id", how="inner"
    ).merge(products[["product_id", "category"]], on="product_id", how="inner")

    category_value = (
        item_categories.groupby(["customer_id", "category"], as_index=False)[
            "source_line_total_cents"
        ]
        .sum()
        .sort_values(
            ["customer_id", "source_line_total_cents", "category"],
            ascending=[True, False, True],
        )
    )
    top_category = (
        category_value.groupby("customer_id", as_index=False)
        .head(1)[["customer_id", "category"]]
        .rename(columns={"category": "top_category"})
    )

    customer_360 = customers.merge(cart_metrics, on="customer_id", how="left").merge(
        top_category, on="customer_id", how="left"
    )
    for column in (
        "cart_count",
        "item_quantity",
        "cart_value_cents",
        "discounted_cart_value_cents",
    ):
        customer_360[column] = customer_360[column].fillna(0).astype("int64")
    customer_360["engagement_segment"] = np.select(
        [customer_360["cart_count"] == 0, customer_360["cart_count"] == 1],
        ["none", "single_cart"],
        default="multiple_carts",
    )
    customer_360["top_category"] = (
        customer_360["top_category"]
        .astype(object)
        .where(customer_360["top_category"].notna(), None)
    )
    customer_360 = customer_360.sort_values("customer_id").reset_index(drop=True)
    customer_360 = customer_360[CUSTOMER_360_COLUMNS]

    category_items = items.merge(products[["product_id", "category"]], on="product_id", how="inner")
    category_summary = category_items.groupby("category", as_index=False).agg(
        cart_count=("cart_id", "nunique"),
        item_quantity=("quantity", "sum"),
        cart_value_cents=("source_line_total_cents", "sum"),
        discounted_cart_value_cents=("source_line_discounted_cents", "sum"),
    )
    for column in (
        "cart_count",
        "item_quantity",
        "cart_value_cents",
        "discounted_cart_value_cents",
    ):
        category_summary[column] = category_summary[column].astype("int64")
    category_summary = category_summary.sort_values("category").reset_index(drop=True)
    category_summary = category_summary[CATEGORY_SUMMARY_COLUMNS]

    return {"customer_360": customer_360, "category_summary": category_summary}


def to_rows(frame: pd.DataFrame) -> list[tuple]:
    """Convert a frame to a list of tuples of native Python types for comparison."""
    rows: list[tuple] = []
    for record in frame.itertuples(index=False, name=None):
        cleaned = []
        for value in record:
            if value is None or value is pd.NA:
                cleaned.append(None)
            elif isinstance(value, float) and math.isnan(value):
                cleaned.append(None)
            elif hasattr(value, "item"):
                cleaned.append(value.item())
            else:
                cleaned.append(value)
        rows.append(tuple(cleaned))
    return rows
