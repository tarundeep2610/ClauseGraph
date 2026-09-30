import asyncio
import sys
import reference_index

job_id = sys.argv[1] if len(sys.argv) > 1 else '108ba949-bb7b-4f29-b582-ce4f4a44631b'
print(f"Triggering reindex_job on {job_id} with force_mode='ai'...")

async def main():
    res = await reference_index.reindex_job(job_id, force_mode='ai')
    print("Reindex finished successfully:", res)

asyncio.run(main())
