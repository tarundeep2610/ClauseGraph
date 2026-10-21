with open("/app/ai_indexing/prompt.py", "r", encoding="utf-8") as f:
    text = f.read()

rule = '''- NUMBERED CLAUSES AND SUB-CLAUSES OF CONTRACTS, TENDERS, AND SPECIFICATIONS:
  In contracts, tenders, specifications, and conditions of contract (e.g. GCC, SCC), clauses and
  sub-clauses are sequentially numbered (e.g. "1.1.", "1.9.1.", "1.11.1.", "1.15.2.", "1.18.4.", "1.19.1.", "24.2.1.", or "1.", "2.", "3.").
  Report EVERY such numbered clause and sub-clause as a heading under its parent clause or section
  (e.g. report "1.11.1. It is a condition of this Bid that the information", "1.18.4. Expiry or termination of a parallel contract", "1.19.1. Only Purchaser-approved AI models/services", "1.19.2. AI access shall respect").
  Even though a contract clause contains full sentences with verbs like "shall", "must", or "is",
  it is a numbered contract clause that must be indexed. Do NOT strip or exclude it under the noun-phrase test.
'''

target = 'copy it VERBATIM without adding any number.\n"""'
assert target in text, "target not found"
new_text = text.replace(target, 'copy it VERBATIM without adding any number.\n' + rule + '"""')
with open("/app/ai_indexing/prompt.py", "w", encoding="utf-8") as f:
    f.write(new_text)
print("Updated /app/ai_indexing/prompt.py successfully!")
