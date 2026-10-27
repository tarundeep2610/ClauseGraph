"""Runners subpackage for evals.

Provides orchestrated pipeline evaluation:
- Index runner (structure & cross-reference indexing)
- Action items runner
- OCR transcription quality runner
- Confidence calibration runner
"""

from evals.runners.base import (
    BaseEvalRunner,
    cleanup_duplicate_job,
)
from evals.runners.index_runner import (
    IndexEvalRunner,
    IndexTrialConfig,
    evaluate_job_structure,
    run_index_benchmark,
    run_index_trial,
)
from evals.runners.action_items_runner import (
    ActionItemsEvalRunner,
    evaluate_job_action_items,
    run_action_items_benchmark,
    run_action_items_trial,
)
from evals.runners.ocr_runner import (
    OCREvalRunner,
    evaluate_ocr_fixture,
    evaluate_ocr_page,
)
from evals.runners.confidence_runner import (
    ConfidenceEvalRunner,
    evaluate_confidence_calibration,
)

__all__ = [
    "BaseEvalRunner",
    "cleanup_duplicate_job",
    "IndexEvalRunner",
    "IndexTrialConfig",
    "evaluate_job_structure",
    "run_index_benchmark",
    "run_index_trial",
    "ActionItemsEvalRunner",
    "evaluate_job_action_items",
    "run_action_items_benchmark",
    "run_action_items_trial",
    "OCREvalRunner",
    "evaluate_ocr_fixture",
    "evaluate_ocr_page",
    "ConfidenceEvalRunner",
    "evaluate_confidence_calibration",
]
