"""Small synthetic cases with totals that can be checked by hand."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "sample.json"


@pytest.fixture
def sample_data(fixture_path: Path) -> dict:
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def write_fixture(tmp_path: Path):
    def write(data: dict, name: str = "input.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return write
