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
    action_items_gates: Optional[Dict[str, float]] = None
    index_gates: Optional[Dict[str, float]] = None


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
        action_items_gates={
            "min_precision": 0.750,
            "min_recall": 0.950,
            "min_grounding_rate": 0.950,
        },
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
        action_items_gates={
            "min_precision": 0.700,
            "min_recall": 0.950,
            "min_grounding_rate": 0.950,
        },
    ),
    "dmrc-nit-5p": BenchmarkDataset(
        dataset_id="dmrc-nit-5p",
        name="DMRC Open E-Tender NIT Chapter-1 (5 pages)",
        domain="metro_nit",
        source_job_id="de8764d2-4249-49bd-a271-e8ec2c02c869",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_de8764d2.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_de8764d2.json"),
        page_count=5,
        default_trials=1,
        index_gates={
            "min_merged_precision": 0.350,
            "min_merged_recall": 0.800,
        },
        action_items_gates={
            "min_precision": 0.850,
            "min_recall": 0.950,
            "min_grounding_rate": 0.950,
        },
    ),
    "dmrc-oew-16p": BenchmarkDataset(
        dataset_id="dmrc-oew-16p",
        name="DMRC Contract OEW-1620 Tender Documents & NIT (16 pages)",
        domain="metro_works_contract",
        source_job_id="0a42f322-858a-45c3-90af-30438476884c",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_0a42f322.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_0a42f322.json"),
        page_count=16,
        default_trials=1,
        index_gates={
            "min_merged_precision": 0.100,
            "min_merged_recall": 0.750,
            "min_mention_precision": 0.100,
            "min_mention_recall": 0.900,
        },
        action_items_gates={
            "min_precision": 0.500,  # 24 gold milestone items sampled across 16-page works contract
            "min_recall": 0.950,
            "min_grounding_rate": 0.950,
        },
    ),
    "dmrc-jcdd-27p": BenchmarkDataset(
        dataset_id="dmrc-jcdd-27p",
        name="DMRC Consultancy JCDD-02 Jaipur Metro Phase-II DDC (27 pages)",
        domain="metro_consultancy_tender",
        source_job_id="4755bd5c-e11e-4be0-baa9-26d299f1e37c",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_4755bd5c.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_4755bd5c.json"),
        page_count=27,
        default_trials=1,
        index_gates={
            "min_merged_precision": 0.100,
            "min_merged_recall": 0.750,
            "min_mention_precision": 0.050,
            "min_mention_recall": 0.900,
        },
        action_items_gates={
            "min_precision": 0.500,
            "min_recall": 0.750,
            "min_grounding_rate": 0.900,
        },
    ),
    "iocl-mathura-59p": BenchmarkDataset(
        dataset_id="iocl-mathura-59p",
        name="IOCL Mathura Refinery Shutdown Tender (59 pages)",
        domain="oil_refinery_tender",
        source_job_id="a9da15c3-eb89-44f8-aabc-d8b49b28b543",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_a9da15c3.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_a9da15c3.json"),
        page_count=59,
        default_trials=1,
        index_gates={
            "min_merged_precision": 0.050,  # 35 targets annotated up to page 31 across 59-page document
            "min_merged_recall": 0.900,
            "min_mention_precision": 0.050,
            "min_mention_recall": 0.900,
        },
        action_items_gates={
            "min_precision": 0.050,  # 8 gold milestone items sampled across 59-page refinery contract
            "min_recall": 0.850,
            "min_grounding_rate": 0.850,
        },
    ),
    "gail-jsw-61p": BenchmarkDataset(
        dataset_id="gail-jsw-61p",
        name="Delhi HC GAIL v JSW Steel Commercial Arbitration Appeal Judgment (61 pages)",
        domain="legal_judgment",
        source_job_id="3fb2b1cf-dc4d-4d5f-bd06-7c680dfa051c",
        gold_index_path=os.path.join(_FIXTURES_DIR, "gold_index_3fb2b1cf.json"),
        gold_action_items_path=os.path.join(_FIXTURES_DIR, "gold_action_items_3fb2b1cf.json"),
        page_count=61,
        default_trials=1,
        index_gates={
            "min_merged_precision": 0.050,  # 12 macro divisions annotated across 61-page judgment
            "min_merged_recall": 0.800,
            "min_mention_precision": 0.050,
            "min_mention_recall": 0.900,
        },
        action_items_gates={
            "min_precision": 0.050,  # 3 operative judicial order items sampled across 61-page appellate judgment
            "min_recall": 0.300,
            "min_grounding_rate": 0.850,
        },
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
