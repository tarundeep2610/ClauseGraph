"""JSON artifact persistence for historical tracking and CI dashboards."""

from datetime import datetime, timezone
import json
import os
import subprocess
from typing import Any, Dict, Optional

from evals.settings import EVAL_ARTIFACTS_DIR


def _get_git_commit_hash() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return res.stdout.strip() if res.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def save_json_artifact(
    run_id: str,
    pipeline: str,
    dataset_id: str,
    results: Dict[str, Any],
    gates_passed: bool,
    out_dir: Optional[str] = None,
) -> str:
    """Saves complete evaluation run results with timestamp and commit metadata to a JSON artifact."""
    target_dir = out_dir or EVAL_ARTIFACTS_DIR
    os.makedirs(target_dir, exist_ok=True)

    filename = f"eval_{pipeline}_{dataset_id}_{int(datetime.now().timestamp())}.json"
    filepath = os.path.join(target_dir, filename)

    payload = {
        "run_id": run_id,
        "pipeline": pipeline,
        "dataset_id": dataset_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": _get_git_commit_hash(),
        "gates_passed": gates_passed,
        "results": results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return filepath
