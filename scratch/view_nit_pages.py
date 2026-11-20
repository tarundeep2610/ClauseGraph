import sys, os
sys.path.insert(0, os.path.abspath("."))
import database

pages = database.get_pages('de8764d2-4249-49bd-a271-e8ec2c02c869')
for p in pages:
    print(f"==================== PAGE {p['page']} ({len(p.get('text', ''))} chars) ====================")
    lines = [l.strip() for l in p.get('text', '').splitlines() if l.strip()]
    for line in lines:
        if len(line) < 120:
            print("  ", line)
