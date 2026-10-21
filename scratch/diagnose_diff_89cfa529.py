import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import sqlite3
import database
import structure_overrides

JOB_ID = "55b38865-ad68-4a4c-88b5-f93fb309b9ea"
FIXTURE_PATH = "tests/fixtures/gold_index_89cfa529.json"

with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
    gold = json.load(f)

gold_targets = gold.get("targets", [])
structure = structure_overrides.resolve_structure_for_job(JOB_ID)
act_targets = [{
    "page": r.get("page"),
    "key": r.get("key"),
    "label": r.get("own_label") or "",
    "detection_source": r.get("detection_source") or "rule",
} for r in structure]

print(f"Total gold targets: {len(gold_targets)}, Total actual targets: {len(act_targets)}")

# Compare pages 13-19 (Chunk 2)
gold_p13_19 = [t for t in gold_targets if 13 <= t.get("page", 0) <= 19]
act_p13_19 = [t for t in act_targets if 13 <= t.get("page", 0) <= 19]

print(f"\n--- Chunk 2 (Pages 13-19) ---")
print(f"Gold targets count: {len(gold_p13_19)}")
print(f"Actual targets count: {len(act_p13_19)}")

gold_keys_p13_19 = {t["key"]: t for t in gold_p13_19}
act_keys_p13_19 = {t["key"]: t for t in act_p13_19}

matched = set(gold_keys_p13_19.keys()) & set(act_keys_p13_19.keys())
print(f"Key-matched count: {len(matched)}")

print("\nMatched keys sample:")
for k in sorted(list(matched))[:10]:
    print(f"  MATCH: {k} -> gold: {gold_keys_p13_19[k].get('label')} | act: {act_keys_p13_19[k].get('label')}")

gold_only = set(gold_keys_p13_19.keys()) - set(act_keys_p13_19.keys())
print(f"\nGold only count: {len(gold_only)}")
print("Sample gold only keys (first 15):")
for k in sorted(list(gold_only))[:15]:
    gt = gold_keys_p13_19[k]
    print(f"  GOLD ONLY: {k} (page {gt.get('page')}) label='{gt.get('label')}'")

act_only = set(act_keys_p13_19.keys()) - set(gold_keys_p13_19.keys())
print(f"\nAct only count: {len(act_only)}")
print("Sample act only keys (first 15):")
for k in sorted(list(act_only))[:15]:
    at = act_keys_p13_19[k]
    print(f"  ACT ONLY: {k} (page {at.get('page')}) label='{at.get('label')}'")
