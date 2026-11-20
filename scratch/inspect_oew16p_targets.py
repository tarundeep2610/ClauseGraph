import sys, os
sys.path.insert(0, os.path.abspath("."))
import json
import database
from evals.datasets.loader import load_gold_index_fixture
from evals.runners.index_runner import load_actual_targets, score_target_slice

job_id = "0a42f322-858a-45c3-90af-30438476884c"
gold = load_gold_index_fixture("tests/fixtures/gold_index_0a42f322.json")
gold_targets = [t.model_dump() for t in gold.targets]
actual_targets = load_actual_targets(job_id)

print(f"OEW-16P: Gold targets={len(gold_targets)}, Actual targets={len(actual_targets)}")
scores = score_target_slice(gold_targets, actual_targets)
print(f"Matched={scores['matched_count']}, Precision={scores['precision']:.3f}, Recall={scores['recall']:.3f}")

print("\nSample Actual targets (first 30):")
for i, a in enumerate(actual_targets[:30]):
    print(f"Page {a.get('page')}: key='{a.get('key')}', label='{a.get('label')[:60]}', scope='{a.get('scope_key')}'")
