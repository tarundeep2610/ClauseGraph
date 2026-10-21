with open("/app/ai_indexing/prompt.py", "r", encoding="utf-8") as f:
    text = f.read()

target = 'contains a verb like "shall", "must", or "is".'
assert target in text, "target not in text"
addition = '\n   This includes essential tender information tables, schedules of requirements, and specification tables where\n   Column 1 is an item/clause number (e.g. "1.", "2.", "3.") and Column 2 is an item or specification heading\n   (e.g. "Name of works/services to be procured", "Purchaser", "Contract Period", "Earnest Money Deposit").\n   Report every such numbered row under its section heading (e.g. "1. Name of works/services to be procured", "2. Purchaser").'
new_text = text.replace(target, target + addition)
with open("/app/ai_indexing/prompt.py", "w", encoding="utf-8") as f:
    f.write(new_text)
print("Updated /app/ai_indexing/prompt.py successfully!")
