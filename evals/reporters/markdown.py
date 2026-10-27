"""Markdown report generation for evaluation runs."""

from typing import Any, Dict


def generate_markdown_report(
    benchmark_results: Dict[str, Any],
    dataset_name: str,
    profile_name: str,
    gates_passed: bool,
) -> str:
    """Generates a GitHub-flavored Markdown benchmark table for PR descriptions and documentation."""
    spread = benchmark_results.get("spread", {})
    status_icon = "✅ PASS" if gates_passed else "❌ FAIL"

    md = [
        f"## Evaluation Benchmark Report: {dataset_name}",
        f"**Profile:** `{profile_name}` | **Status:** {status_icon} | **Trials:** {benchmark_results.get('trials_count', 1)}\n",
        "| Evaluation Metric | Min | Mean | Max |",
        "| :--- | :---: | :---: | :---: |",
    ]

    metric_labels = [
        ("target_precision", "Merged Precision"),
        ("target_recall", "Merged Recall"),
        ("scope_accuracy", "Scope Accuracy"),
        ("family_drift_count", "Family Drift"),
        ("ai_precision", "AI Precision"),
        ("ai_recall", "AI Recall"),
        ("rule_recall", "Rule Control Recall"),
        ("mention_precision", "Mention Precision"),
        ("mention_recall", "Mention Recall"),
    ]

    for key, label in metric_labels:
        if key in spread:
            data = spread[key]
            md.append(f"| **{label}** | {data['min']:.3f} | **{data['mean']:.3f}** | {data['max']:.3f} |")

    return "\n".join(md) + "\n"
