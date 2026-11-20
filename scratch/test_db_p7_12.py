import sys
sys.path.insert(0, '.')
import structure_overrides

targets = structure_overrides.resolve_structure_for_job('bb417471-561c-4029-bb21-3bdcf74e7dcb')

for t in targets:
    if 7 <= t.get('page') <= 12:
        print(f"p{t.get('page')} {t.get('key')} {repr(t.get('label'))[:30]} scope={t.get('scope_key')}")
