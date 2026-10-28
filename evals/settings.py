"""Settings, profiles, and threshold definitions for the evaluation suite.

Named 'settings.py' rather than 'config.py' to avoid shadowing the root application
`config.py` in ocr-services.
"""

import os
from typing import Dict


# Universal reference indexing quality gates across all document domains
INDEX_GATES: Dict[str, float] = {
    "min_merged_precision": 0.850,
    "min_merged_recall": 0.950,
    "min_rule_recall": 1.000,  # Rule control path must never regress
    "min_mention_precision": 0.900,
    "min_mention_recall": 0.900,
    "max_family_drift": 15,
}

# Actionable items extraction quality gates
ACTION_ITEMS_GATES: Dict[str, float] = {
    "min_precision": 0.850,
    "min_recall": 0.950,
    "min_grounding_rate": 0.900,
}

# OCR & transcription quality gates
OCR_GATES: Dict[str, float] = {
    "max_cer_clean": 0.020,
    "max_cer_degraded": 0.080,
    "max_wer": 0.050,
}

# Artifact storage
EVAL_ARTIFACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "eval_reports"
)
