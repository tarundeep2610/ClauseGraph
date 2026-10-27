"""Registry of curated benchmark datasets and gold fixture paths."""

from dataclasses import dataclass
import os
from typing import Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURES_DIR = os.path.join(_REPO_ROOT, "tests", "fixtures")


@dataclass(frozen=True)
class BenchmarkDataset:
    """Metadata specification for a benchmark document."""

    dataset_id: str
    name: str
    domain: str  # document domain category metadata
    source_job_id: str
    gold_index_path: str
    gold_action_items_path: Optional[str] = None
    page_count: int = 1
    default_trials: int = 1
    legacy: bool = False


DATASET_REGISTRY: Dict[str, BenchmarkDataset] = {
    "tender-135p": BenchmarkDataset(
        dataset_id="tender-135p",
        name="MPPKVVCL Commercial Tender (135 pages)",
        domain="commercial_tender",
        source_job_id="89cfa529-2390-464c-9c3e-2446ef68c588",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_89cfa529.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_89cfa529.json"),
        page_count=135,
        default_trials=1,
    ),
    "appeal-100p": BenchmarkDataset(
        dataset_id="appeal-100p",
        name="HVPNL Arbitration Appeal (100 pages)",
        domain="arbitration_appeal",
        source_job_id="bb417471-561c-4029-bb21-3bdcf74e7dcb",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_bb417471.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_bb417471.json"),
        page_count=100,
        default_trials=3,
    ),
    "judgment-17p": BenchmarkDataset(
        dataset_id="judgment-17p",
        name="Commercial Arbitration Judgment (17 pages)",
        domain="legal_judgment",
        source_job_id="108ba949-bb7b-4f29-b582-ce4f4a44631b",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_108ba949.json"),
        page_count=17,
        default_trials=3,
    ),
    "legacy-excerpt-17p": BenchmarkDataset(
        dataset_id="legacy-excerpt-17p",
        name="Legacy Arbitration Judgment Excerpt (17 pages, positional subdoc keys)",
        domain="legal_judgment",
        source_job_id="9c3dc6e6-9e74-4088-b7f0-638022e4ab84",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_9c3dc6e6.json"),
        page_count=17,
        default_trials=1,
        legacy=True,
    ),
    "gem-bid-dfccil-10p": BenchmarkDataset(
        dataset_id="gem-bid-dfccil-10p",
        name="GeM Bid GEM/2026/B/8032683 - Frauscher MSDAC AMC/CMC, DFCCIL (10 pages)",
        domain="gem_bid",
        source_job_id="30d148dd-c1e4-4e38-877b-608b5584ff4e",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_30d148dd.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_30d148dd.json"),
        page_count=10,
        default_trials=1,
    ),
    "gem-bid-midhani-8p": BenchmarkDataset(
        dataset_id="gem-bid-midhani-8p",
        name="GeM Bid GEM/2026/B/8056905 - Up-Armouring of Defender Vehicles, MIDHANI (8 pages)",
        domain="gem_bid",
        source_job_id="179ab46e-b105-4107-ad38-7a0160724948",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_179ab46e.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_179ab46e.json"),
        page_count=8,
        default_trials=1,
    ),
}


def get_benchmark_dataset(dataset_id: str) -> BenchmarkDataset:
    """Retrieve benchmark metadata by dataset identifier."""
    if dataset_id not in DATASET_REGISTRY:
        available = ", ".join(DATASET_REGISTRY.keys())
        raise KeyError(f"Dataset '{dataset_id}' not found in registry. Available: {available}")
    return DATASET_REGISTRY[dataset_id]


def list_benchmark_datasets() -> List[BenchmarkDataset]:
    """List all registered benchmark datasets."""
    return list(DATASET_REGISTRY.values())
