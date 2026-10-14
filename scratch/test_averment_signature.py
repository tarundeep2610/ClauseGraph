import json
import sys
import re
sys.path.insert(0, '.')

import database
import reference_index
import ai_reference_index

job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))
gold_targets = gold['targets']

_PLEADING_AVERMENT_RE = re.compile(
    r"^\s*(?:\(?\d+[\.\)]|\(?[a-z][\.\)]|\(?[ivxlcdm]+[\.\)])\s+"
    r"(?:That\s+|The\s+(?:petitioner|appellant|respondent|applicant|plaintiff|defendant|deponent)\b)",
    re.IGNORECASE | re.MULTILINE
)

def _has_pleading_averment_signature(page_text: str) -> bool:
    return bool(_PLEADING_AVERMENT_RE.search(page_text or ""))

# Let's test on all 100 pages of bb417471 which pages fire _has_pleading_averment_signature
firing_pages = []
for p in range(1, 101):
    page_data = database.get_page(job_id, p)
    txt = (page_data or {}).get('text', '')
    if _has_pleading_averment_signature(txt):
        firing_pages.append(p)

print(f"Pages matching _has_pleading_averment_signature ({len(firing_pages)}): {firing_pages}")
