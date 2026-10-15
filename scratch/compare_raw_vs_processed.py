"""Post-hoc diagnostic: for a given recording file, print each chunk's RAW
AI-reported heading count/text alongside what ends up in the final
persisted/scored output (act_count from score_chunks, already printed live
during the run) - so a big gap between "what the model actually said" and
"what we scored" points at our own pipeline, not the model.

Usage: python3 compare_raw_vs_processed.py <recording_file.json>
Prints, per chunk (sorted by page range): page range, raw AI heading
count, each raw heading's text (truncated), so it can be read side-by-side
with the sweep log's "[chunk N/TOTAL] pages X-Y | ... gold=G act=A matched=M"
lines for the same page range.
"""
import json
import sys


def main(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    chunks = data.get("chunks", {})

    def sort_key(k):
        return min(int(x) for x in k.split("_"))

    for key in sorted(chunks.keys(), key=sort_key):
        c = chunks[key]
        page_nums = c["page_nums"]
        try:
            parsed = json.loads(c["raw_text"])
            headings = parsed.get("headings", [])
        except (json.JSONDecodeError, TypeError):
            headings = None
        print(f"=== pages {page_nums[0]}-{page_nums[-1]} (duration_ms={c.get('duration_ms')}) ===")
        if headings is None:
            print("  <raw_text not valid JSON - model output malformed>")
            continue
        print(f"  RAW AI heading count: {len(headings)}")
        for h in headings:
            print(f"    page={h.get('start_page')} | {h.get('heading_text', '')[:90]!r}")


if __name__ == "__main__":
    main(sys.argv[1])
