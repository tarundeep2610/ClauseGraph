import json
import re

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))['targets']
diff = json.load(open('data/diff_replay_v3.json'))
fps = diff['false_positives']

PATTERNS = [
    # 1. Single word prepositions / conjunctions
    re.compile(r"^\s*(?:FOR|AND|OR|WITH|IN|ON|OF|AT|BY|TO)\s*$", re.IGNORECASE),
    
    # 2. Signature lines and roles
    re.compile(r"^\s*Authorized\s+Signator(?:y|ies)\b.*", re.IGNORECASE),
    re.compile(r"^\s*For\s+[A-Z0-9\s,\.\/&\-]+\b(?:LTD|LIMITED|BANK|CORP|INC|NA|N\.A\.|CHENNAI)\b.*", re.IGNORECASE),

    # 3. Math equations / LaTeX formulas
    re.compile(r"\\(?:therefore|Delta|frac|xi|sum|times|le|ge)\b"),
    re.compile(r"^\s*\\\$"),
    re.compile(r"^\s*\\\(.*\\\)\s*$"),

    # 4. Dates and metadata reference numbers
    re.compile(r"^\s*Date\s*[:\s]\s*\d+.*", re.IGNORECASE),
    re.compile(r"^\s*Date\s*:\s*.*", re.IGNORECASE),
    re.compile(r"^\s*Bank\s+Guarantee\s+No\.?\s*\d+.*", re.IGNORECASE),
    re.compile(r"^\s*CONTRACT\s+No\.?\s*[-A-Z0-9\/\(\)\s]+$", re.IGNORECASE),
    
    # 5. Docket headers and case number lines
    re.compile(r"^\s*In\s+(?:F\.?A\.?O\.?|C\.?M\.?|W\.?P\.?|Civil|Arbitration)\b.*", re.IGNORECASE),
    
    # 6. Party address / location lines
    re.compile(r".*,\s*Panchkula\s*-\s*\d{6}\s*$", re.IGNORECASE),

    # 7. Equipment BOQ lines ending in "(As per Specification...)"
    re.compile(r".*\(As\s+per\s+Technical\s+Specifications?\)\s*$", re.IGNORECASE),
    re.compile(r".*\(As\s+per\s+Specifications?\)\s*$", re.IGNORECASE),
]

def is_filtered(text):
    s = (text or "").strip()
    return any(p.search(s) for p in PATTERNS)

# ADVERSARIAL TEST: check gold
filtered_gold = [g for g in gold if is_filtered(g.get('label') or '')]
print(f"ADVERSARIAL NEGATIVE TEST: Gold targets mistakenly filtered: {len(filtered_gold)}")
for fg in filtered_gold:
    print(f"  P{fg['page']} {fg['key']}: {repr(fg.get('label'))}")

# Check FPs eliminated
filtered_fps = [fp for fp in fps if is_filtered(fp.get('label') or '')]
print(f"Additional False Positives eliminated: {len(filtered_fps)} of {len(fps)}")
for fp in filtered_fps:
    print(f"  P{fp['page']} {fp['key']}: {repr(fp.get('label'))}")
