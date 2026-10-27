"""Base classes and shared database isolation helpers for evaluation runners."""

from abc import ABC, abstractmethod
import os
import sqlite3
import sys
from typing import Any, Dict

import database

# index_runner.py / action_items_runner.py both do a lazy
# `from compare_ai_indexing_qwen_vs_gemini import duplicate_job` inside their
# trial-running functions — that module lives in scripts/, which was only
# ever on sys.path because the OLD standalone scripts/score_*.py CLIs added
# it themselves at their own top. Now that evals.cli is the primary entry
# point and never does that insert, the import silently depended on
# whichever script happened to run first — confirmed to raise
# ModuleNotFoundError when evals.cli invokes a real trial directly. Both
# runner modules import this base module, so adding it here once covers
# both call sites instead of duplicating the sys.path insert in each.
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def cleanup_duplicate_job(job_id: str) -> bool:
    """Completely purges a temporary or duplicated evaluation job from the database,

    cascading across all targets, mentions, action items, overrides, pages,
    and removes the physical duplicated PDF file from disk.
    """
    job = database.get_job(job_id, include_pages=False)
    if job and job.get("file_path") and os.path.exists(job["file_path"]):
        try:
            os.remove(job["file_path"])
        except Exception as e:
            print(f"Warning: Failed to remove file {job['file_path']}: {e}")

    conn = database.get_db_connection()
    cursor = conn.cursor()
    try:
        # Purge reference targets and mentions
        cursor.execute("DELETE FROM ocr_reference_targets WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM ocr_reference_mentions WHERE job_id = ?", (job_id,))

        # Purge action items
        cursor.execute("DELETE FROM ocr_action_items WHERE job_id = ?", (job_id,))

        # Purge structure override tables if they exist
        for tbl in (
            "ocr_structure_overrides",
            "ocr_structure_override_parents",
            "ocr_structure_override_deletes",
            "ocr_structure_override_manual",
        ):
            try:
                cursor.execute(f"DELETE FROM {tbl} WHERE job_id = ?", (job_id,))
            except sqlite3.OperationalError:
                pass  # Table might not exist in test sqlite schema

        # Purge page attempts, pages, and job record
        cursor.execute("DELETE FROM ocr_page_attempts WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM ocr_pages WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM ocr_jobs WHERE job_id = ?", (job_id,))

        conn.commit()
    finally:
        conn.close()

    return True


class BaseEvalRunner(ABC):
    """Abstract base runner interface for all evaluation pipelines."""

    @abstractmethod
    async def run(self, dataset_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Execute the evaluation pipeline across the specified benchmark dataset."""
        pass
