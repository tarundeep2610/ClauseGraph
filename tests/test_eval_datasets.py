"""Schema validation tests for registered gold fixtures (Test Suite 2).

Guarantees data integrity, schema compliance, and absence of duplicate targets.
"""

import os
import pytest
from pydantic import ValidationError

from evals.datasets.registry import DATASET_REGISTRY, get_benchmark_dataset
from evals.datasets.schema import GoldIndexFixture, GoldMention, GoldTarget
from evals.datasets.loader import (
    load_gold_index_fixture,
    load_gold_action_items_fixture,
)


class TestDatasetSchemaValidation:
    @pytest.mark.parametrize("dataset_id", ["tender-135p", "appeal-100p", "judgment-17p", "legacy-excerpt-17p"])
    def test_registered_index_fixtures_conform_to_schema(self, dataset_id: str):
        bench = get_benchmark_dataset(dataset_id)
        assert os.path.exists(bench.gold_index_path), f"Missing fixture file: {bench.gold_index_path}"

        fixture = load_gold_index_fixture(bench.gold_index_path)
        assert isinstance(fixture, GoldIndexFixture)
        assert fixture.job_id is not None
        assert len(fixture.targets) > 0

    @pytest.mark.parametrize("dataset_id", ["tender-135p", "appeal-100p"])
    def test_registered_action_item_fixtures_conform_to_schema(self, dataset_id: str):
        bench = get_benchmark_dataset(dataset_id)
        assert bench.gold_action_items_path is not None
        assert os.path.exists(bench.gold_action_items_path)

        fixture = load_gold_action_items_fixture(bench.gold_action_items_path)
        assert fixture.job_id is not None
        assert len(fixture.action_items) > 0

    def test_mention_resolution_allowed_values(self):
        # Valid resolutions
        for res in ("exact", "approximate", "unresolved", "suppressed", "undefined"):
            mention = GoldMention(page=1, occurrence_index=0, key="clause:1", resolution=res)
            assert mention.resolution == res

        # Invalid resolution must raise ValidationError
        with pytest.raises(ValidationError):
            GoldMention(page=1, occurrence_index=0, key="clause:1", resolution="fabricated")  # type: ignore

    @pytest.mark.parametrize("dataset_id", ["tender-135p", "appeal-100p", "judgment-17p"])
    def test_target_uniqueness_invariant(self, dataset_id: str):
        """Verifies no duplicate (page, key, occurrence_index) triples exist in any active benchmark fixture."""
        bench = get_benchmark_dataset(dataset_id)
        fixture = load_gold_index_fixture(bench.gold_index_path)

        seen = set()
        duplicates = []
        for t in fixture.targets:
            ident = (t.page, t.key, t.occurrence_index)
            if ident in seen:
                duplicates.append(ident)
            seen.add(ident)

        assert len(duplicates) == 0, f"Found duplicate targets in {dataset_id}: {duplicates[:5]}"

    def test_registry_lookup_unknown_dataset_raises(self):
        with pytest.raises(KeyError, match="not found in registry"):
            get_benchmark_dataset("non-existent-dataset")
