import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
import database
import structure_overrides
from evals.datasets.registry import DATASET_REGISTRY

for ds_id in ['dmrc-nit-5p', 'dmrc-oew-16p', 'dmrc-jcdd-27p', 'iocl-mathura-59p', 'gail-jsw-61p']:
    ds = DATASET_REGISTRY[ds_id]
    jid = ds.source_job_id
    pages = database.get_pages(jid)
    
    with open(ds.gold_index_path, 'r', encoding='utf-8') as f:
        g_idx = json.load(f)
    g_targets = g_idx.get('targets', [])
    g_mentions = g_idx.get('mentions', [])
    
    with open(ds.gold_action_items_path, 'r', encoding='utf-8') as f:
        g_act = json.load(f)
    g_items = g_act.get('action_items', [])
    
    # Check actual action items in DB
    act_items = database.get_all_action_items_for_job(jid)
    # Check actual structure targets
    act_targets = structure_overrides.resolve_structure_for_job(jid)
    
    print(f"==================================================")
    print(f"Dataset: {ds_id} (pages: {len(pages)}, jid: {jid[:8]})")
    print(f"  Gold Targets: {len(g_targets)} | Actual Targets in DB: {len(act_targets)}")
    print(f"  Gold Mentions: {len(g_mentions)}")
    print(f"  Gold Action Items: {len(g_items)} | Actual Action Items in DB: {len(act_items)}")
    
    # Distribution across pages
    g_tgt_pages = set(t['page'] for t in g_targets)
    act_tgt_pages = set(t['page'] for t in act_targets)
    print(f"  Gold Target Pages: min={min(g_tgt_pages) if g_tgt_pages else None}, max={max(g_tgt_pages) if g_tgt_pages else None}, count={len(g_tgt_pages)}")
    print(f"  Act Target Pages:  min={min(act_tgt_pages) if act_tgt_pages else None}, max={max(act_tgt_pages) if act_tgt_pages else None}, count={len(act_tgt_pages)}")
    
    g_act_pages = set(a['page'] for a in g_items)
    act_act_pages = set(a['page'] for a in act_items)
    print(f"  Gold Action Pages: min={min(g_act_pages) if g_act_pages else None}, max={max(g_act_pages) if g_act_pages else None}, count={len(g_act_pages)}")
    print(f"  Act Action Pages:  min={min(act_act_pages) if act_act_pages else None}, max={max(act_act_pages) if act_act_pages else None}, count={len(act_act_pages)}")
