"""Datasets subpackage for evals.

Provides:
- Pydantic models for gold annotations (schema.py)
- Benchmark registry manifest (registry.py)
- Type-safe dataset loaders (loader.py)
"""

from evals.datasets.schema import (
    GoldTarget,
    GoldMention,
    GoldIndexFixture,
    GoldActionItem,
    GoldActionItemsFixture,
    GoldOCRPage,
    GoldOCRFixture,
)
from evals.datasets.registry import (
    BenchmarkDataset,
    DATASET_REGISTRY,
    get_benchmark_dataset,
)
from evals.datasets.loader import (
    load_gold_index_fixture,
    load_gold_action_items_fixture,
    export_gold_index_dict,
)

__all__ = [
    "GoldTarget",
    "GoldMention",
    "GoldIndexFixture",
    "GoldActionItem",
    "GoldActionItemsFixture",
    "GoldOCRPage",
    "GoldOCRFixture",
    "BenchmarkDataset",
    "DATASET_REGISTRY",
    "get_benchmark_dataset",
    "load_gold_index_fixture",
    "load_gold_action_items_fixture",
    "export_gold_index_dict",
]
