import sys
sys.path.insert(0, '.')
import structure_overrides

targets = structure_overrides.resolve_structure_for_job('bb417471-561c-4029-bb21-3bdcf74e7dcb')

print(f"Total targets in DB: {len(targets)}")
for t in targets:
    if t.get('page') in (40, 41, 46):
        print(f"p{t.get('page')} {t.get('key')} {repr(t.get('label'))[:40]} scope={t.get('scope_key')}")
