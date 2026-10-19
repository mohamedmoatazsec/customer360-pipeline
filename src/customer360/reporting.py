"""Business-intelligence layer: Excel dashboard, chart and Power BI star-schema export.

This reads the gold tables (produced by any engine) with pandas and publishes
analyst-facing outputs:

* ``customer360_dashboard.xlsx`` - an Excel workbook with an overview, a segment
  breakdown and a category breakdown, each with a native Excel chart.
* ``dashboard.png`` - a ranked category chart for quick review or embedding.
* ``powerbi/`` - the bronze star schema as CSVs plus a model description, ready to
  import into Power BI or any BI tool.

Money is stored in integer cents upstream; these presentation outputs divide by 100
into display units, kept separate from the integer-cent system of record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from openpyxl.chart import BarChart, Reference  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

NAVY = "1F3A5F"
ACCENT = "#1F3A5F"
SEGMENT_ORDER = ["none", "single_cart", "multiple_carts"]
SEGMENT_LABELS = {
    "none": "No carts",
    "single_cart": "Single cart",
    "multiple_carts": "Multiple carts",
}
_HEADER_FILL = PatternFill("solid", fgColor=NAVY)
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=14, color=NAVY)
_MONEY_FORMAT = "#,##0.00"
_INT_FORMAT = "#,##0"


def _cents_to_units(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    for column in columns:
        frame[column] = frame[column] / 100.0
    return frame


def segment_breakdown(customer_360: pd.DataFrame) -> pd.DataFrame:
    """Customers, carts, quantity and value per engagement segment, in a fixed order."""
    grouped = (
        customer_360.groupby("engagement_segment")
        .agg(
            customers=("customer_id", "size"),
            carts=("cart_count", "sum"),
            item_quantity=("item_quantity", "sum"),
            cart_value=("cart_value_cents", "sum"),
        )
        .reindex(SEGMENT_ORDER)
        .dropna(how="all")
        .reset_index()
    )
    grouped["segment"] = grouped["engagement_segment"].map(SEGMENT_LABELS)
    grouped["cart_value"] = grouped["cart_value"] / 100.0
    for column in ("customers", "carts", "item_quantity"):
        grouped[column] = grouped[column].fillna(0).astype("int64")
    return grouped[["segment", "customers", "carts", "item_quantity", "cart_value"]]


def overview(customer_360: pd.DataFrame, category_summary: pd.DataFrame) -> dict[str, Any]:
    """Headline KPIs for the overview sheet and the run summary."""
    active = customer_360[customer_360["cart_count"] > 0]
    top_category = None
    if not category_summary.empty:
        top_category = category_summary.sort_values(
            ["cart_value_cents", "category"], ascending=[False, True]
        ).iloc[0]["category"]
    return {
        "customers": int(len(customer_360)),
        "active_customers": int(len(active)),
        "customers_multiple_carts": int(
            (customer_360["engagement_segment"] == "multiple_carts").sum()
        ),
        "total_carts": int(customer_360["cart_count"].sum()),
        "total_item_quantity": int(customer_360["item_quantity"].sum()),
        "total_cart_value": round(float(customer_360["cart_value_cents"].sum()) / 100.0, 2),
        "total_discounted_value": round(
            float(customer_360["discounted_cart_value_cents"].sum()) / 100.0, 2
        ),
        "categories": int(len(category_summary)),
        "top_category_by_value": top_category,
    }


def _style_table(worksheet, title: str, header_row: int, columns: int, rows: int) -> None:
    worksheet["A1"] = title
    worksheet["A1"].font = _TITLE_FONT
    for index in range(1, columns + 1):
        cell = worksheet.cell(row=header_row, column=index)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    for index in range(1, columns + 1):
        letter = get_column_letter(index)
        longest = max(
            [
                len(str(worksheet.cell(row=r, column=index).value or ""))
                for r in range(header_row, header_row + rows + 1)
            ]
            + [12]
        )
        worksheet.column_dimensions[letter].width = longest + 2
    worksheet.freeze_panes = worksheet.cell(row=header_row + 1, column=1)


def _bar_chart(
    worksheet, title: str, label: str, min_col: int, data_col: int, rows: int, anchor: str
):
    chart = BarChart()
    chart.type = "col"
    chart.title = title
    chart.y_axis.title = label
    chart.legend = None
    chart.height = 8
    chart.width = 16
    data = Reference(worksheet, min_col=data_col, min_row=2, max_row=rows + 2)
    categories = Reference(worksheet, min_col=min_col, min_row=3, max_row=rows + 2)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    worksheet.add_chart(chart, anchor)


def build_excel(customer_360: pd.DataFrame, category_summary: pd.DataFrame, path: Path) -> Path:
    """Write the multi-sheet Excel dashboard with native charts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kpis = overview(customer_360, category_summary)
    overview_frame = pd.DataFrame(
        {
            "Metric": [
                "Customers",
                "Active customers (>=1 cart)",
                "Customers with multiple carts",
                "Total carts",
                "Total item quantity",
                "Total cart value",
                "Total discounted value",
                "Distinct categories",
                "Top category by value",
            ],
            "Value": [
                kpis["customers"],
                kpis["active_customers"],
                kpis["customers_multiple_carts"],
                kpis["total_carts"],
                kpis["total_item_quantity"],
                kpis["total_cart_value"],
                kpis["total_discounted_value"],
                kpis["categories"],
                kpis["top_category_by_value"],
            ],
        }
    )
    segments = segment_breakdown(customer_360)
    categories = _cents_to_units(
        category_summary.rename(
            columns={
                "category": "Category",
                "cart_count": "Carts",
                "item_quantity": "Item quantity",
                "cart_value_cents": "Cart value",
                "discounted_cart_value_cents": "Discounted value",
            }
        ),
        ["Cart value", "Discounted value"],
    ).sort_values("Cart value", ascending=False)
    segments_display = segments.rename(
        columns={
            "segment": "Segment",
            "customers": "Customers",
            "carts": "Carts",
            "item_quantity": "Item quantity",
            "cart_value": "Cart value",
        }
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        overview_frame.to_excel(writer, sheet_name="Overview", index=False, startrow=1)
        segments_display.to_excel(writer, sheet_name="By segment", index=False, startrow=1)
        categories.to_excel(writer, sheet_name="By category", index=False, startrow=1)

        overview_ws = writer.sheets["Overview"]
        _style_table(
            overview_ws,
            "Customer360 - overview",
            2,
            len(overview_frame.columns),
            len(overview_frame),
        )
        overview_ws["B8"].number_format = _MONEY_FORMAT
        overview_ws["B9"].number_format = _MONEY_FORMAT

        segment_ws = writer.sheets["By segment"]
        _style_table(
            segment_ws,
            "Customer360 - engagement segments",
            2,
            len(segments_display.columns),
            len(segments_display),
        )
        for row in range(3, len(segments_display) + 3):
            segment_ws.cell(row=row, column=5).number_format = _MONEY_FORMAT
        _bar_chart(
            segment_ws, "Customers by segment", "Customers", 1, 2, len(segments_display), "H2"
        )

        category_ws = writer.sheets["By category"]
        _style_table(
            category_ws, "Customer360 - category value", 2, len(categories.columns), len(categories)
        )
        for row in range(3, len(categories) + 3):
            category_ws.cell(row=row, column=4).number_format = _MONEY_FORMAT
            category_ws.cell(row=row, column=5).number_format = _MONEY_FORMAT
        _bar_chart(category_ws, "Cart value by category", "Cart value", 1, 4, len(categories), "H2")
    return path


def category_value_chart(
    category_summary: pd.DataFrame,
    path: Path,
    title: str = "Cart value by category",
    top_n: int = 15,
) -> Path:
    """Render a ranked horizontal bar chart of category cart value to a PNG."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = category_summary.copy()
    frame["cart_value"] = frame["cart_value_cents"] / 100.0
    frame = frame.sort_values("cart_value", ascending=False).head(top_n).iloc[::-1]

    fig, axis = plt.subplots(figsize=(9, max(3.5, 0.42 * len(frame) + 1.2)))
    bars = axis.barh(frame["category"], frame["cart_value"], color=ACCENT)
    axis.set_title(title, fontsize=14, fontweight="bold", color=ACCENT, loc="left", pad=12)
    axis.set_xlabel("Cart value (units)")
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.tick_params(length=0)
    span = frame["cart_value"].max() or 1.0
    for bar in bars:
        width = bar.get_width()
        axis.text(
            width + span * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{width:,.0f}",
            va="center",
            fontsize=9,
            color="#333333",
        )
    axis.margins(x=0.12)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def build_powerbi_export(bronze: dict[str, list[dict[str, Any]]], outdir: Path) -> Path:
    """Write the bronze star schema as CSVs for Power BI modelling."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tables = {
        "dim_customer": "customers",
        "dim_product": "products",
        "fact_cart": "carts",
        "fact_cart_item": "cart_items",
    }
    for file_name, resource in tables.items():
        pd.DataFrame(bronze.get(resource, [])).to_csv(outdir / f"{file_name}.csv", index=False)
    return outdir


def build_report(
    bronze: dict[str, list[dict[str, Any]]],
    gold: dict[str, pd.DataFrame],
    outdir: Path,
) -> dict[str, Any]:
    """Produce the Excel dashboard, category chart, Power BI export and a summary."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    customer_360 = gold["customer_360"]
    category_summary = gold["category_summary"]
    build_excel(customer_360, category_summary, outdir / "customer360_dashboard.xlsx")
    category_value_chart(category_summary, outdir / "dashboard.png")
    build_powerbi_export(bronze, outdir / "powerbi")
    summary = overview(customer_360, category_summary)
    (outdir / "report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
