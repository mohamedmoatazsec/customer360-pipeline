"""The reporting layer turns gold tables into an Excel dashboard, chart and BI export."""

import json
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from customer360 import pandas_transform, reporting
from customer360.contracts import sanitize


@pytest.fixture
def bronze_and_gold() -> tuple[dict, dict]:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "sample.json").read_text(encoding="utf-8"),
        parse_float=Decimal,
    )
    bronze = sanitize(payload)
    return bronze, pandas_transform.transform(bronze)


def test_build_report_writes_every_artifact(tmp_path: Path, bronze_and_gold: tuple[dict, dict]):
    bronze, gold = bronze_and_gold
    summary = reporting.build_report(bronze, gold, tmp_path)
    assert summary["customers"] == 3
    assert summary["active_customers"] == 2
    assert summary["total_item_quantity"] == 10
    assert (tmp_path / "customer360_dashboard.xlsx").is_file()
    assert (tmp_path / "dashboard.png").is_file()
    for name in ("dim_customer", "dim_product", "fact_cart", "fact_cart_item"):
        assert (tmp_path / "powerbi" / f"{name}.csv").is_file()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["categories"] == 2


def test_excel_dashboard_has_named_sheets_and_two_charts(
    tmp_path: Path, bronze_and_gold: tuple[dict, dict]
):
    _, gold = bronze_and_gold
    path = reporting.build_excel(
        gold["customer_360"], gold["category_summary"], tmp_path / "dashboard.xlsx"
    )
    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames == ["Overview", "By segment", "By category"]
    assert sum(len(sheet._charts) for sheet in workbook.worksheets) == 2


def test_segment_breakdown_is_ordered_and_labelled(bronze_and_gold: tuple[dict, dict]):
    _, gold = bronze_and_gold
    segments = reporting.segment_breakdown(gold["customer_360"])
    assert list(segments["segment"]) == ["No carts", "Single cart", "Multiple carts"]
    multiple = segments.loc[segments["segment"] == "Multiple carts", "customers"].iloc[0]
    assert int(multiple) == 1


def test_powerbi_export_excludes_plaintext_contact(
    tmp_path: Path, bronze_and_gold: tuple[dict, dict]
):
    bronze, _ = bronze_and_gold
    outdir = reporting.build_powerbi_export(bronze, tmp_path / "powerbi")
    customer_csv = (outdir / "dim_customer.csv").read_text(encoding="utf-8")
    assert "@example.test" not in customer_csv
    assert "DO_NOT_PERSIST" not in customer_csv
