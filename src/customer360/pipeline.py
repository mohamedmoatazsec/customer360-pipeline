"""Full-snapshot orchestration with a last-good publication pointer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import duckdb

from .contracts import sanitize
from .errors import ContractError, PipelineError, QualityError, SourceError
from .source import extract_live

TABLES = {
    "customers": (
        "bronze_customers",
        "customer_id BIGINT, contact_key VARCHAR, city VARCHAR, country VARCHAR",
    ),
    "products": (
        "bronze_products",
        "product_id BIGINT, product_name VARCHAR, category VARCHAR, list_price_cents BIGINT",
    ),
    "carts": (
        "bronze_carts",
        "cart_id BIGINT, customer_id BIGINT, source_total_cents BIGINT, "
        "source_discounted_cents BIGINT, source_product_count BIGINT, source_quantity BIGINT",
    ),
    "cart_items": (
        "bronze_cart_items",
        "cart_id BIGINT, line_number BIGINT, product_id BIGINT, "
        "unit_price_cents BIGINT, quantity BIGINT, "
        "source_line_total_cents BIGINT, source_line_discounted_cents BIGINT, "
        "discount_basis_points BIGINT",
    ),
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _read_fixture(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle, parse_float=Decimal)
    except (OSError, ValueError):
        raise SourceError("Fixture cannot be read as valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ContractError("Fixture must contain a JSON object")
    return payload


def _sql_literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def _provenance() -> dict[str, Any]:
    try:
        package_version = version("customer360-pipeline")
    except PackageNotFoundError:
        package_version = "1.0.0"
    sql_dir = Path(__file__).parent / "sql"
    return {
        "runtime": {
            "package_version": package_version,
            "python_version": platform.python_version(),
            "duckdb_version": duckdb.__version__,
        },
        "sql_hashes": {
            name: hashlib.sha256((sql_dir / name).read_bytes()).hexdigest()
            for name in ("transform.sql", "quality.sql")
        },
    }


def _build_warehouse(stage: Path, bronze: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    sql_dir = Path(__file__).parent / "sql"
    database = stage / "warehouse.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("BEGIN TRANSACTION")
        for resource, (table, schema) in TABLES.items():
            connection.execute(f"CREATE TABLE {table} ({schema})")
            rows = bronze[resource]
            column_names = [column.split()[0] for column in schema.split(", ")]
            values = [[row[column] for column in column_names] for row in rows]
            placeholders = ",".join("?" for _ in column_names)
            connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
        connection.execute((sql_dir / "transform.sql").read_text(encoding="utf-8"))
        quality_rows = connection.execute(
            (sql_dir / "quality.sql").read_text(encoding="utf-8")
        ).fetchall()
        if not quality_rows:
            raise QualityError("Quality gate returned no checks")
        checks = []
        names = set()
        for row in quality_rows:
            if (
                len(row) != 2
                or not isinstance(row[0], str)
                or not row[0]
                or row[0] in names
                or not isinstance(row[1], int)
                or isinstance(row[1], bool)
                or row[1] < 0
            ):
                raise QualityError("Quality gate returned an invalid check result")
            names.add(row[0])
            checks.append({"check_name": row[0], "failures": row[1]})
        checks.sort(key=lambda check: check["check_name"])
        failed = [check for check in checks if check["failures"]]
        _write_json(
            stage / "quality.json",
            {
                "status": "failed" if failed else "passed",
                "check_count": len(checks),
                "checks": checks,
            },
        )
        if failed:
            connection.execute("ROLLBACK")
            error = QualityError(f"Quality gate failed: {len(failed)} of {len(checks)} checks")
            error.checks = checks
            raise error
        connection.execute("COMMIT")
        gold_dir = stage / "gold"
        gold_dir.mkdir()
        for table, order in (("customer_360", "customer_id"), ("category_summary", "category")):
            query = f"SELECT * FROM gold.{table} ORDER BY {order}"
            parquet = _sql_literal(gold_dir / f"{table}.parquet")
            csv = _sql_literal(gold_dir / f"{table}.csv")
            connection.execute(f"COPY ({query}) TO {parquet} (FORMAT PARQUET, COMPRESSION ZSTD)")
            connection.execute(f"COPY ({query}) TO {csv} (FORMAT CSV, HEADER TRUE)")
    return checks


def run_pipeline(
    output_dir: Path,
    fixture_path: Path | None = None,
    base_url: str = "https://dummyjson.com",
    page_size: int = 50,
) -> dict[str, Any]:
    """Build and publish one complete, quality-gated snapshot.

    Artifacts are staged on the same filesystem as the destination. Only a
    successful run replaces current.json with os.replace. Concurrent successful
    writers are last-writer-wins; use one writer per output directory.
    """
    output_dir = Path(output_dir).resolve()
    fixture_path = Path(fixture_path) if fixture_path is not None else None
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    started_at = _timestamp()
    start_clock = time.monotonic()
    mode = "fixture" if fixture_path is not None else "live"
    stage = output_dir / "runs" / f".{run_id}.staging"
    final = output_dir / "runs" / run_id
    pointer_temp = output_dir / f".current.{run_id}.tmp"
    try:
        stage.mkdir(parents=True)
        payload = (
            _read_fixture(fixture_path)
            if fixture_path is not None
            else extract_live(base_url, page_size)
        )
        bronze = sanitize(payload)
        del payload
        canonical = json.dumps(bronze, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        dataset_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        row_counts = {resource: len(rows) for resource, rows in bronze.items()}
        for resource, rows in bronze.items():
            _write_json(stage / "bronze" / f"{resource}.json", rows)
        checks = _build_warehouse(stage, bronze)
        summary = {
            "run_id": run_id,
            "dataset_hash": dataset_hash,
            "mode": mode,
            "started_at": started_at,
            "completed_at": _timestamp(),
            "duration_seconds": round(time.monotonic() - start_clock, 3),
            "source": "fixture" if mode == "fixture" else base_url.rstrip("/"),
            "row_counts": row_counts,
            "check_count": len(checks),
            "checks": checks,
            "run_dir": f"runs/{run_id}",
            "status": "passed",
            **_provenance(),
        }
        _write_json(stage / "run.json", summary)
        stage.rename(final)
        _write_json(pointer_temp, summary)
        os.replace(pointer_temp, output_dir / "current.json")
        return summary
    except Exception as exc:
        # Never serialize source values, exception repr/tracebacks or credentials.
        safe_error = (
            str(exc) if isinstance(exc, PipelineError) else "Unexpected internal pipeline failure"
        )
        failure = {
            "run_id": run_id,
            "mode": mode,
            "started_at": started_at,
            "failed_at": _timestamp(),
            "status": "failed",
            "error_type": type(exc).__name__,
            "message": safe_error,
        }
        if isinstance(exc, QualityError) and hasattr(exc, "checks"):
            failure["checks"] = exc.checks
        try:
            _write_json(output_dir / "failures" / f"{run_id}.json", failure)
        finally:
            if stage.exists():
                if stage.resolve().parent != (output_dir / "runs").resolve():
                    raise PipelineError(
                        "Staging cleanup target is outside the run directory"
                    ) from exc
                shutil.rmtree(stage)
            pointer_temp.unlink(missing_ok=True)
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError(safe_error) from exc
