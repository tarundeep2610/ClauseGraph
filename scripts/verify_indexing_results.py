import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job
import ai_reference_index

async def main(source_job_id: str):
    print(f"Creating duplicate of {source_job_id}...")
    dup_id = duplicate_job(source_job_id)
    try:
        print(f"Running reindex_job_with_ai on {dup_id}...")
        res = await ai_reference_index.reindex_job_with_ai(dup_id)
        print(f"Outcome: {res}")

        targets_by_page = database.get_reference_targets_since(dup_id, since_version=-1)
        print("\n=== PERSISTED TARGETS ===")
        total_targets = 0
        all_targets = []
        for p in sorted(targets_by_page.keys(), key=lambda x: int(x)):
            print(f"\n--- PAGE {p} ---")
            for k, cands in targets_by_page[p].items():
                for c in cands:
                    total_targets += 1
                    all_targets.append((int(p), k, c.get("scope_key", ""), c.get("own_label") or c.get("scope_label") or ""))
                    print(f"  [{c.get('key')}] scope='{c.get('scope_key')}' label='{c.get('own_label') or c.get('scope_label')}'")

        print(f"\nTotal targets indexed: {total_targets}")

        # Verification checks
        print("\n=== VERIFICATION CRITERIA ===")
        judgment = [t for t in all_targets if "JUDGMENT" in t[3]]
        facts = [t for t in all_targets if "FACTS" in t[3]]
        challenge = [t for t in all_targets if "Challenge to the Arbitral" in t[3]]
        respondent = [t for t in all_targets if "Respondent's case" in t[3]]
        conclusion = [t for t in all_targets if "Conclusion" in t[3]]

        # Print all targets in detail
        print("\n=== ALL TARGETS ===")
        for p, k, sk, lbl in all_targets:
            if k.startswith("subdoc:") or k.startswith("point:"):
                print(f"P{p:02d} | {k:12s} | scope={sk:25s} | label={lbl[:60]}")

        print(f"\nDuplicate job kept for inspection: {dup_id}")
    except Exception as e:
        print(f"Error during verification: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    job_id = sys.argv[1] if len(sys.argv) > 1 else "9c3dc6e6-9e74-4088-b7f0-638022e4ab84"
    asyncio.run(main(job_id))
