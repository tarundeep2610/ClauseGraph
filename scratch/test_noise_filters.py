import json
import re

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))['targets']
diff = json.load(open('data/diff_full_trial1.json'))
fps = diff['false_positives']

_GENERIC_LEGAL_NOISE_RE = re.compile(
    r"^\s*(?:"
    r"Versus|V/s|Vs\.?|"
    r"Advocate|Advocates|Counsel(?:\s+for\s+.*)?|Sr\.?\s*Advocate|ATOVOCATG|"
    r".*(?:\.{2,}|[-–—]{2,}|\()\s*(?:Appellants?|Respondents?|Petitioners?|Defendants?|Plaintiffs?)\s*[\.:\)]?|"
    r"(?:Dated\s*[:\.]?\s*)?\d{1,2}[\./\-]\d{1,2}[\./\-]\d{2,4}|"
    r"Note\s*:\s*.*|Total\s+\d+.*|U·f\s+\d+.*|"
    r"IN\s+THE\s+(?:HIGH\s*COURT|SUPREME\s*COURT|DISTRICT\s*COURT|COURT\s*OF)\b.*|"
    r"AT\s+[A-Z\s]+|JUDICIAL\s+DEPARTMENT|CIVIL\s+APPELLATE\s+SIDE|"
    r"(?:F\.?A\.?O\.?|Civil\s+Misc\.?|C\.?M\.?|W\.?P\.?|Arbitration\s+Case|Commercial\s+Appeal)(?:[\s\(\.\-].*|$)|"
    r".*Appeal\s+No\.?\s*\d+.*|"
    r"Stereo\s+H\.?C\.?J\.?D\.?.*|.*Govt\.?\s*Press.*|^[A-Za-z]\d{1,4}$|"
    r"INDIA\s+COURT\s+FEE|COURT\s+FEE|Rs\.\s*\d+|\bRUPEES\b|Price\s*:\s*\d+.*|.*Paise\b|\([A-Z]{2,}\s+[A-Z\s]{2,}\)|"
    r"Endst\.?\s*No\.?[\s\.\:\/\-].*|"
    r"Copy\s+(?:of\s+the\s+above\s+)?(?:is\s+)?forwarded\s+to\b.*|"
    r"Copy\s+to\s*:?|"
    r"(?:\(?\d+[\.\)]?\s+)?(?:The\s+)?(?:Sr\.?\s*PS|Superin[tl][a-z]+ing\s*Engineer|Executive\s*Engineer|Chief\s*Engineer|Chief\s*Auditor|Director/|FA\s*&\s*CAO|Dy\.?\s*Secy|Supdt\.?\s*(?:\([^\)]*\))?|Accounts\s*Officer|Personal\s*Assistant|Under\s*Secretary)\b.*|"
    r"(?:AND|OR|WITH)|"
    r"To\s*,?|Dear\s+(?:Sirs?|Madam)\s*,?|"
    r"For\s+[A-Z0-9\s,\.]+\b(?:LTD|LIMITED|BANK|CORP|INC|NA|N\.A\.)\b.*|"
    r"[A-Z]{1,3}\s*[-–—]?\s*\d+[a-zA-Z]?|"
    r"[A-Z]{2}|\d+[a-z]|"
    r"\(?(?:[a-zA-Z]|\d{1,2}|[ivxlcdmIVXLCDM]{1,4})\)"
    r")\s*$",
    re.IGNORECASE
)

_LATEX_NOISE_RE = re.compile(r"^\s*(?:\\\(.*\\\)|\\\$|\\\$.*\\\$)\s*$")

def is_filtered(text):
    s = (text or "").strip()
    return bool(_GENERIC_LEGAL_NOISE_RE.match(s) or _LATEX_NOISE_RE.match(s))

filtered_gold = [g for g in gold if is_filtered(g.get('label') or '')]
print(f"ADVERSARIAL NEGATIVE TEST: Gold targets mistakenly filtered: {len(filtered_gold)}")
if filtered_gold:
    for fg in filtered_gold:
        print(f"  P{fg['page']} {fg['key']}: {fg.get('label')}")

filtered_fps = [fp for fp in fps if is_filtered(fp.get('label') or '')]
print(f"False Positives eliminated: {len(filtered_fps)} of {len(fps)} ({(len(filtered_fps)/len(fps))*100:.1f}%)")

from collections import Counter
elim_by_page = Counter([fp['page'] for fp in filtered_fps])
print("\nEliminated FPs by page:")
for p, c in elim_by_page.most_common(10):
    print(f"  Page {p}: {c} FPs eliminated")
