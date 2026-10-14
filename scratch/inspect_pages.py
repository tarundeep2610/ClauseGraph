import sqlite3
import json

# We can query from container or check if we can inspect recorded raw_text
rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
chunks = rec['chunks']

# Let's find chunk for page 76 (76_77_78)
chunk_76 = chunks.get('76_77_78')
if chunk_76:
    print("=== Raw Model Output for Pages 76-78 ===")
    print(chunk_76.get('raw_text'))

chunk_91 = chunks.get('91_92_93')
if chunk_91:
    print("=== Raw Model Output for Pages 91-93 ===")
    print(chunk_91.get('raw_text')[:2000])

chunk_61 = chunks.get('61_62_63')
if chunk_61:
    print("=== Raw Model Output for Pages 61-63 ===")
    print(chunk_61.get('raw_text')[:1500])
