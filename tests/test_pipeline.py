"""Exercise the published data product and failure behavior, not implementation details."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from customer360.pipeline import PipelineError, run_pipeline


def warehouse_rows(run_dir: Path, query: str) -> list[tuple]:
    with duckdb.connect(str(run_dir / "warehouse.duckdb"), read_only=True) as connection:
        return connection.execute(query).fetchall()


def test_customer_gold_preserves_no_cart_users_and_avoids_join_fanout(
    tmp_path: Path, fixture_path: Path
):
    result = run_pipeline(tmp_path / "data", fixture_path=fixture_path)
    run_dir = tmp_path / "data" / result["run_dir"]
    rows = warehouse_rows(
        run_dir,
        """SELECT customer_id, cart_count, item_quantity, cart_value_cents,
                  discounted_cart_value_cents, top_category, engagement_segment
           FROM gold.customer_360 ORDER BY customer_id""",
    )
    assert rows == [
        (1, 2, 6, 1000, 975, "groceries", "multiple_carts"),
        (2, 1, 4, 300, 285, "stationery", "single_cart"),
        (3, 0, 0, 0, 0, None, "none"),
    ]
    assert warehouse_rows(
        run_dir, "SELECT city, country FROM gold.customer_360 WHERE customer_id = 1"
    ) == [("Cairo", "Egypt")]


def test_category_gold_uses_distinct_carts_and_reconciles_money(tmp_path: Path, fixture_path: Path):
    result = run_pipeline(tmp_path / "data", fixture_path=fixture_path)
    rows = warehouse_rows(
        tmp_path / "data" / result["run_dir"],
        """SELECT category, cart_count, item_quantity, cart_value_cents,
                  discounted_cart_value_cents
           FROM gold.category_summary ORDER BY category""",
    )
    assert rows == [("groceries", 2, 4, 500, 475), ("stationery", 3, 6, 800, 785)]


def test_run_is_reproducible_and_exports_readable_parquet(tmp_path: Path, fixture_path: Path):
    output = tmp_path / "data"
    first = run_pipeline(output, fixture_path=fixture_path)
    second = run_pipeline(output, fixture_path=fixture_path)
    assert first["dataset_hash"] == second["dataset_hash"]
    assert first["row_counts"] == second["row_counts"]
    assert first["run_id"] != second["run_id"]
    assert (output / first["run_dir"]).is_dir()
    run_dir = output / second["run_dir"]
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT count(*), sum(cart_value_cents) FROM read_parquet(?)",
            [str(run_dir / "gold" / "customer_360.parquet")],
        ).fetchone()
    assert rows == (3, 1300)
    assert (run_dir / "gold" / "customer_360.csv").is_file()
    assert (run_dir / "gold" / "category_summary.parquet").is_file()
    quality = json.loads((run_dir / "quality.json").read_text(encoding="utf-8"))
    assert quality["status"] == "passed"
    assert quality["checks"]
    assert all(check["failures"] == 0 for check in quality["checks"])


def test_fingerprint_ignores_collection_order_but_tracks_line_order_and_content(
    tmp_path: Path, fixture_path: Path, sample_data: dict, write_fixture
):
    output = tmp_path / "data"
    first = run_pipeline(output, fixture_path=fixture_path)
    for resource in ("users", "products", "carts"):
        sample_data[resource].reverse()
    reordered = run_pipeline(output, fixture_path=write_fixture(sample_data))
    assert reordered["dataset_hash"] == first["dataset_hash"]
    for cart in sample_data["carts"]:
        cart["products"].reverse()
    changed_lines = run_pipeline(output, fixture_path=write_fixture(sample_data, "lines.json"))
    assert changed_lines["dataset_hash"] != first["dataset_hash"]
    sample_data["users"][0]["address"]["city"] = "Luxor"
    changed = run_pipeline(output, fixture_path=write_fixture(sample_data, "changed.json"))
    assert changed["dataset_hash"] != changed_lines["dataset_hash"]


def test_repeated_product_lines_are_preserved_at_source_line_grain(
    tmp_path: Path, sample_data: dict, write_fixture
):
    cart = sample_data["carts"][0]
    cart["products"].append(cart["products"][0].copy())
    cart.update(total=7.50, discountedTotal=7.00, totalProducts=3, totalQuantity=5)
    result = run_pipeline(tmp_path / "data", fixture_path=write_fixture(sample_data))
    run_dir = tmp_path / "data" / result["run_dir"]
    assert result["row_counts"]["cart_items"] == 6
    assert warehouse_rows(
        run_dir,
        """SELECT line_number, product_id, quantity FROM silver.fact_cart_item
           WHERE cart_id = 11 AND product_id = 101 ORDER BY line_number""",
    ) == [(1, 101, 2), (3, 101, 2)]
    assert warehouse_rows(
        run_dir,
        """SELECT cart_count, item_quantity, cart_value_cents, discounted_cart_value_cents
           FROM gold.customer_360 WHERE customer_id = 1""",
    ) == [(2, 8, 1250, 1200)]


def test_bronze_and_warehouse_exclude_plaintext_contact_and_unselected_fields(
    tmp_path: Path, fixture_path: Path
):
    result = run_pipeline(tmp_path / "data", fixture_path=fixture_path)
    run_dir = tmp_path / "data" / result["run_dir"]
    expected = hashlib.sha256(b"first.customer@example.test").hexdigest()
    assert warehouse_rows(
        run_dir, "SELECT contact_key FROM silver.dim_customer WHERE customer_id = 1"
    ) == [(expected,)]
    forbidden = [
        b"first.customer@example.test",
        b"FIRST.Customer@Example.test",
        b"second.customer@example.test",
        b"DO_NOT_PERSIST_PASSWORD_23859",
        b"DO_NOT_PERSIST_SSN_23859",
        b"DO_NOT_PERSIST_CARD_23859",
        b"DO_NOT_PERSIST_STREET_23859",
    ]
    for path in run_dir.rglob("*"):
        if path.is_file():
            contents = path.read_bytes()
            assert not any(value in contents for value in forbidden), path


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate_customer",
        "orphan_customer",
        "orphan_product",
        "wrong_line_total",
        "wrong_cart_total",
        "wrong_quantity_total",
        "negative_quantity",
        "fractional_quantity",
        "boolean_identifier",
        "nonfinite_price",
        "duplicate_cart",
        "missing_email",
        "discount_above_total",
    ],
)
def test_invalid_snapshot_never_replaces_last_good_publication(
    tmp_path: Path, fixture_path: Path, sample_data: dict, write_fixture, corruption: str
):
    output = tmp_path / "data"
    good = run_pipeline(output, fixture_path=fixture_path)
    pointer_before = (output / "current.json").read_bytes()
    if corruption == "duplicate_customer":
        sample_data["users"].append(sample_data["users"][0].copy())
    elif corruption == "orphan_customer":
        sample_data["carts"][0]["userId"] = 99999
    elif corruption == "orphan_product":
        sample_data["carts"][0]["products"][0]["id"] = 99999
    elif corruption == "wrong_line_total":
        sample_data["carts"][0]["products"][0]["total"] = 99
    elif corruption == "wrong_cart_total":
        sample_data["carts"][0]["total"] = 99
    elif corruption == "wrong_quantity_total":
        sample_data["carts"][0]["totalQuantity"] = 99
    elif corruption == "negative_quantity":
        sample_data["carts"][0]["products"][0]["quantity"] = -1
    elif corruption == "fractional_quantity":
        sample_data["carts"][0]["products"][0]["quantity"] = 1.5
    elif corruption == "boolean_identifier":
        sample_data["users"][0]["id"] = True
    elif corruption == "nonfinite_price":
        sample_data["products"][0]["price"] = float("nan")
    elif corruption == "duplicate_cart":
        sample_data["carts"].append(sample_data["carts"][0].copy())
    elif corruption == "missing_email":
        sample_data["users"][0].pop("email")
    elif corruption == "discount_above_total":
        sample_data["carts"][0]["products"][0]["discountedTotal"] = 100
    with pytest.raises(PipelineError):
        run_pipeline(output, fixture_path=write_fixture(sample_data))
    assert (output / "current.json").read_bytes() == pointer_before
    assert warehouse_rows(
        output / good["run_dir"], "SELECT sum(cart_value_cents) FROM gold.customer_360"
    ) == [(1300,)]
    assert not list((output / "runs").glob("*.staging"))
    failure_files = list((output / "failures").glob("*.json"))
    assert len(failure_files) == 1
    failure = json.loads(failure_files[0].read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert "DO_NOT_PERSIST" not in failure_files[0].read_text(encoding="utf-8")


def test_publication_io_failure_keeps_previous_pointer(
    tmp_path: Path, fixture_path: Path, monkeypatch
):
    output = tmp_path / "data"
    run_pipeline(output, fixture_path=fixture_path)
    previous_pointer = (output / "current.json").read_bytes()

    def fail_replace(*args):
        raise OSError("DO_NOT_PERSIST_INTERNAL_ERROR_23859")

    monkeypatch.setattr("customer360.pipeline.os.replace", fail_replace)
    with pytest.raises(PipelineError):
        run_pipeline(output, fixture_path=fixture_path)
    assert (output / "current.json").read_bytes() == previous_pointer
    assert not list(output.glob(".current.*.tmp"))
    failure = next((output / "failures").glob("*.json")).read_text(encoding="utf-8")
    assert "DO_NOT_PERSIST_INTERNAL_ERROR_23859" not in failure


def test_corrupt_fixture_has_nonzero_cli_exit(tmp_path: Path, sample_data: dict, write_fixture):
    sample_data["carts"][0]["userId"] = 99999
    fixture = write_fixture(sample_data)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "customer360",
            "run",
            "--output",
            str(tmp_path / "data"),
            "--fixture",
            str(fixture),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Pipeline failed:" in result.stderr
    assert not (tmp_path / "data" / "current.json").exists()


def test_cli_publishes_complete_snapshot(tmp_path: Path, fixture_path: Path):
    output = tmp_path / "data"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "customer360",
            "run",
            "--output",
            str(output),
            "--fixture",
            str(fixture_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "current.json").is_file()
    assert len(list((output / "runs").glob("*/warehouse.duckdb"))) == 1
