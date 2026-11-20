import sys, os
sys.path.insert(0, os.path.abspath("."))
import json
import database
from evals.datasets.loader import load_gold_index_fixture
from evals.runners.index_runner import load_actual_targets, score_target_slice

job_id = "de8764d2-4249-49bd-a271-e8ec2c02c869"
gold = load_gold_index_fixture("tests/fixtures/gold_index_de8764d2.json")
gold_targets = [t.model_dump() for t in gold.targets]
actual_targets = load_actual_targets(job_id)

scores = score_target_slice(gold_targets, actual_targets)
print(f"DMRC-NIT-5P Targets: Gold={len(gold_targets)}, Actual={len(actual_targets)}, Matched={scores['matched_count']}")
print(f"Precision: {scores['precision']:.3f}, Recall: {scores['recall']:.3f}")

print("\n--- False Negatives (Gold targets not matched by Actual) ---")
matched_ids = scores.get("matched_ids", set())

for i, g in enumerate(gold_targets):
    tid = (g.get("page"), g.get("key"), g.get("occurrence_index") or 0)
    # normalize key
    from evals.metrics.structure import target_identifier
    if target_identifier(g) not in matched_ids:
        print(f"Gold #{i} (p{g['page']}): key='{g['key']}', label='{g['label']}', scope='{g['scope_key']}'")

print("\n--- False Positives (Actual targets not matched by Gold) ---")
for i, a in enumerate(actual_targets):
    from evals.metrics.structure import target_identifier
    if target_identifier(a) not in matched_ids:
        print(f"Act #{i} (p{a.get('page')}): key='{a.get('key')}', label='{a.get('label')}', scope='{a.get('scope_key')}'")
