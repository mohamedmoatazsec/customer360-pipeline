"""Explicit source allowlists, strict types, deterministic money conversion."""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .errors import ContractError

MAX_BIGINT = 9_223_372_036_854_775_807


def _integer(value: Any, field: str, *, positive: bool = True) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_BIGINT:
        raise ContractError(
            f"{field}: expected a {'positive' if positive else 'nonnegative'} integer"
        )
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1_000:
        raise ContractError(f"{field}: expected nonempty text of at most 1000 characters")
    return value.strip()


def _scaled(value: Any, field: str, *, maximum: Decimal | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ContractError(f"{field}: expected a finite nonnegative number")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (maximum is not None and number > maximum):
            raise ContractError(f"{field}: number is outside its allowed range")
        scaled = (number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if scaled > MAX_BIGINT:
            raise ContractError(f"{field}: number exceeds BIGINT range")
        return int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise ContractError(f"{field}: number cannot be represented in cents") from None


def _objects(payload: dict[str, Any], resource: str) -> list[dict[str, Any]]:
    rows = payload.get(resource)
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ContractError(f"{resource}: expected a nonempty list of objects")
    return rows


def _unique(rows: list[dict[str, Any]], key: str, resource: str) -> None:
    values = [row[key] for row in rows]
    if len(set(values)) != len(values):
        raise ContractError(f"{resource}: duplicate identifiers are not allowed")


def sanitize(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return only allowlisted fields; never copy an entire source record.

    Email addresses are normalized and hashed in memory. An unkeyed SHA-256
    digest is a pseudonym, not anonymization or a production identity strategy.
    """
    if not isinstance(payload, dict):
        raise ContractError("Snapshot must be a JSON object")
    customers = []
    for row in _objects(payload, "users"):
        email = _text(row.get("email"), "users.email").lower()
        if email.count("@") != 1 or any(char.isspace() for char in email):
            raise ContractError("users.email: expected a structurally valid email address")
        local, domain = email.split("@")
        if not local or not domain or "." not in domain:
            raise ContractError("users.email: expected a structurally valid email address")
        address = row.get("address")
        if not isinstance(address, dict):
            raise ContractError("users.address: expected an object")
        customers.append(
            {
                "customer_id": _integer(row.get("id"), "users.id"),
                "contact_key": hashlib.sha256(email.encode("utf-8")).hexdigest(),
                "city": _text(address.get("city"), "users.address.city"),
                "country": _text(address.get("country"), "users.address.country"),
            }
        )
    products = []
    for row in _objects(payload, "products"):
        products.append(
            {
                "product_id": _integer(row.get("id"), "products.id"),
                "product_name": _text(row.get("title"), "products.title"),
                "category": _text(row.get("category"), "products.category"),
                "list_price_cents": _scaled(row.get("price"), "products.price"),
            }
        )
    carts = []
    items = []
    for row in _objects(payload, "carts"):
        cart_id = _integer(row.get("id"), "carts.id")
        cart_products = row.get("products")
        if (
            not isinstance(cart_products, list)
            or not cart_products
            or any(not isinstance(item, dict) for item in cart_products)
        ):
            raise ContractError("carts.products: expected a nonempty list of objects")
        carts.append(
            {
                "cart_id": cart_id,
                "customer_id": _integer(row.get("userId"), "carts.userId"),
                "source_total_cents": _scaled(row.get("total"), "carts.total"),
                "source_discounted_cents": _scaled(
                    row.get("discountedTotal"), "carts.discountedTotal"
                ),
                "source_product_count": _integer(row.get("totalProducts"), "carts.totalProducts"),
                "source_quantity": _integer(row.get("totalQuantity"), "carts.totalQuantity"),
            }
        )
        for line_number, item in enumerate(cart_products, start=1):
            product_id = _integer(item.get("id"), "carts.products.id")
            items.append(
                {
                    "cart_id": cart_id,
                    "line_number": line_number,
                    "product_id": product_id,
                    "unit_price_cents": _scaled(item.get("price"), "carts.products.price"),
                    "quantity": _integer(item.get("quantity"), "carts.products.quantity"),
                    "source_line_total_cents": _scaled(item.get("total"), "carts.products.total"),
                    "source_line_discounted_cents": _scaled(
                        item.get("discountedTotal"), "carts.products.discountedTotal"
                    ),
                    "discount_basis_points": _scaled(
                        item.get("discountPercentage"),
                        "carts.products.discountPercentage",
                        maximum=Decimal("100"),
                    ),
                }
            )
    _unique(customers, "customer_id", "users")
    _unique(products, "product_id", "products")
    _unique(carts, "cart_id", "carts")
    return {
        "customers": sorted(customers, key=lambda row: row["customer_id"]),
        "products": sorted(products, key=lambda row: row["product_id"]),
        "carts": sorted(carts, key=lambda row: row["cart_id"]),
        "cart_items": sorted(items, key=lambda row: (row["cart_id"], row["line_number"])),
    }
