"""Type-safe loaders and validators for evaluation fixtures."""

import json
import os
from typing import Any, Dict

from evals.datasets.schema import GoldActionItemsFixture, GoldIndexFixture


def load_gold_index_fixture(fixture_path: str) -> GoldIndexFixture:
    """Loads and validates a structure index gold fixture JSON file."""
    if not os.path.exists(fixture_path):
        raise FileNotFoundError(f"Gold index fixture not found: {fixture_path}")

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return GoldIndexFixture.model_validate(data)


def load_gold_action_items_fixture(fixture_path: str) -> GoldActionItemsFixture:
    """Loads and validates an action items gold fixture JSON file."""
    if not os.path.exists(fixture_path):
        raise FileNotFoundError(f"Gold action items fixture not found: {fixture_path}")

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return GoldActionItemsFixture.model_validate(data)


def export_gold_index_dict(fixture: GoldIndexFixture) -> Dict[str, Any]:
    """Serializes a GoldIndexFixture to a clean dictionary matching existing fixture schema."""
    return fixture.model_dump(exclude_none=False)
