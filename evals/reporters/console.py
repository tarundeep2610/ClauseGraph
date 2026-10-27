"""Terminal formatting utilities for evaluation runs."""

from typing import Any, Dict, List


def format_spread_line(label: str, spread_data: Dict[str, float], is_int: bool = False) -> str:
    """Formats min/mean/max distribution spread into a fixed-width terminal row."""
    lo = spread_data.get("min", 0.0)
    mean = spread_data.get("mean", 0.0)
    hi = spread_data.get("max", 0.0)
    if is_int:
        return f"{label:<26} min={lo:.0f}  mean={mean:.2f}  max={hi:.0f}"
    return f"{label:<26} min={lo:.3f}  mean={mean:.3f}  max={hi:.3f}"


def format_benchmark_summary(benchmark_results: Dict[str, Any]) -> str:
    """Formats the statistical spread of a multi-trial benchmark run into a clean terminal report."""
    spread = benchmark_results.get("spread", {})
    trials = benchmark_results.get("trials_count", 1)

    lines = [
        f"\n{'='*70}",
        f" EVALUATION SUMMARY — {trials} Trial(s)",
        f"{'='*70}",
    ]

    if "target_precision" in spread:
        lines.append("\n[Merged End-to-End Indexing]")
        lines.append(format_spread_line("Merged Precision:", spread["target_precision"]))
        lines.append(format_spread_line("Merged Recall:", spread["target_recall"]))
        lines.append(format_spread_line("Scope-Path Accuracy:", spread["scope_accuracy"]))
        lines.append(format_spread_line("Family Drift Count:", spread["family_drift_count"], is_int=True))

    if "ai_precision" in spread:
        lines.append("\n[AI Model Performance]")
        lines.append(format_spread_line("AI Target Precision:", spread["ai_precision"]))
        lines.append(format_spread_line("AI Target Recall:", spread["ai_recall"]))
        lines.append(format_spread_line("AI Scope Accuracy:", spread["ai_scope_accuracy"]))
        lines.append(format_spread_line("AI Family Drift:", spread["ai_family_drift"], is_int=True))

    if "rule_precision" in spread:
        lines.append("\n[Rule Control Path (Invariance)]")
        lines.append(format_spread_line("Rule Precision:", spread["rule_precision"]))
        lines.append(format_spread_line("Rule Recall:", spread["rule_recall"]))

    if "mention_precision" in spread:
        lines.append("\n[Cross-Reference Mentions]")
        lines.append(format_spread_line("Mention Precision:", spread["mention_precision"]))
        lines.append(format_spread_line("Mention Recall:", spread["mention_recall"]))

    lines.append(f"{'='*70}\n")
    return "\n".join(lines)
