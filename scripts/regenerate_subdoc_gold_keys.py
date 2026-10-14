"""One-off: rewrite a gold-index fixture's `subdoc:N` positional keys to the
new content-addressed scheme (reference_index._slugify_subdoc_label).

Why this exists: reference_index.py's `subdoc` fallback family used to mint
keys from a positional counter (boundary_ordinal) that drifts whenever one
indexing run detects a different number of generic boundaries than another —
the dominant confirmed cause of false positive/negative scoring against the
gold fixtures. The fix makes `subdoc` content-addressed like every other
family, which means the two gold fixtures (frozen with the old positional
keys when they were hand-built) need their own keys regenerated to match, or
every live run would now score as 100% wrong on this family by construction,
not because anything is actually broken.

This is a deterministic rewrite of already-verified labels, not a new live
AI run: each `subdoc`-family target's own stored `label` is exactly what
reference_index.mint_key_for_heading/build_targets_for_page slugs live (see
their own comments on why the DISAMBIGUATED label, not raw heading text, is
what gets slugged — a repeated identical heading like "Affidavit"/"Affidavit
(2)" must stay distinguishable, and the "(2)" suffix survives slugification
as a trailing digit for exactly that reason). No human re-verification of
content is needed; only a spot-check that the resulting slugs look sane and
that same-page collisions (rare) still disambiguate correctly via
occurrence_index, same as before.

Usage:
    py scripts/regenerate_subdoc_gold_keys.py tests/fixtures/gold_index_bb417471.json
    py scripts/regenerate_subdoc_gold_keys.py tests/fixtures/gold_index_108ba949.json
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reference_index import _slugify_subdoc_label


def _remap_scope_key(scope_key, key_map):
    """scope_key is a "/"-joined ancestor chain (e.g. "subdoc:5/subdoc:6"),
    never a single key here — must remap per-segment, not via a substring
    replace, which would also corrupt "subdoc:10" while targeting "subdoc:1"."""
    if not scope_key:
        return scope_key
    return "/".join(key_map.get(seg, seg) for seg in scope_key.split("/"))


def regenerate(fixture_path: Path) -> None:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    targets = data.get("targets", [])

    # Naive per-target slug first, in the fixture's own (document) order.
    subdoc_targets = [t for t in targets if t["key"].startswith("subdoc:")]
    naive_keys = [_slugify_subdoc_label(t["label"]) for t in subdoc_targets]

    # A second collision pass catches what the naive slug alone can't:
    # confirmed on the real fixture, "RESPECTFULLY SHOWETH" and "Respectfully
    # Showeth" are two genuinely different sub-documents whose labels differ
    # only in case — the ORIGINAL hand-built fixture's disambiguation never
    # caught this (it compared exact strings, and both looked like unrelated
    # "first occurrences"), which was harmless under the old positional
    # scheme but collides under this one. Rather than re-deriving exactly
    # what a live run's (now case-insensitive, see _disambiguate_label)
    # disambiguation would produce, just number same-slug repeats in file
    # order here directly — same "-2"/"-3" convention _slugify_subdoc_label
    # itself already uses, and provably identical to what a fresh live run
    # produces for this exact case (same base slug text, same sequential
    # count). Deliberately does NOT touch the stored `label` field: the
    # scorer's content-recall dimension already normalizes case/whitespace
    # independently (_norm_label), so the two labels already matched as
    # equivalent content before this fix — only the KEY needed it.
    seen_counts: Dict[str, int] = {}
    final_keys = []
    for nk in naive_keys:
        n = seen_counts.get(nk, 0)
        seen_counts[nk] = n + 1
        final_keys.append(nk if n == 0 else f"{nk}-{n + 1}")

    key_map = {
        t["key"]: new_key
        for t, new_key in zip(subdoc_targets, final_keys)
        if t["key"] != new_key
    }

    if not key_map:
        print(f"{fixture_path}: no subdoc keys needed remapping (already current, or none present)")
        return

    for t in targets:
        if t["key"] in key_map:
            t["key"] = key_map[t["key"]]
        t["scope_key"] = _remap_scope_key(t.get("scope_key"), key_map)

    for m in data.get("mentions", []):
        if m.get("key") in key_map:
            m["key"] = key_map[m["key"]]

    fixture_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"{fixture_path}: remapped {len(key_map)} subdoc key(s)")
    for old, new in sorted(key_map.items()):
        print(f"  {old} -> {new}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="Path to a gold_index_*.json fixture")
    args = parser.parse_args()
    regenerate(args.fixture)
