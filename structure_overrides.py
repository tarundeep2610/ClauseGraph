"""Persisted override layer for the Structure tree — user adds, edits,
deletes, and drag-and-drop reparent/reorder made directly on the
auto-detected cross-reference outline.

Everything here lives in tables reindex_job / upsert_reference_targets_for_page
/ delete_reference_targets_for_page never read or write (see database.py's
init_db(): ocr_structure_overrides, ocr_structure_manual_items,
ocr_structure_parent_overrides, ocr_structure_sort_overrides) — so a
"Rebuild Cross-References" click can wipe and regenerate the whole detected
ocr_reference_targets table without erasing a single user edit. That
survival is by construction (separate tables the reindex path never
touches), not because reindex_job special-cases anything.

This module is a pure read-time MERGE layer on top of that: apply_to_*
functions combine these override tables with whatever the detector already
produced, every time a client asks for it. Nothing here is ever written
back into ocr_reference_targets.
"""
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import database
from database import get_db_connection


def _now() -> str:
    return datetime.utcnow().isoformat()


# ─── Edit / delete overrides on a DETECTED row (ocr_structure_overrides) ───

def upsert_edit(
    job_id: str, key: str, page: int, occurrence_index: int,
    heading: Optional[str] = None, start_page: Optional[int] = None,
    end_page: Optional[int] = None, updated_by: Optional[str] = None,
) -> None:
    """Upserts an action='edit' row. Only the columns actually passed
    (non-None) change — COALESCE(excluded.col, existing.col) preserves
    whatever was already stored for a column this call didn't mention, the
    same "accept a subset of fields" idiom database.upsert_page uses for
    ocr_pages. Explicitly re-asserts action='edit' on conflict so an edit
    issued against a (key, page, occurrence_index) that was previously
    tombstoned un-tombstones it — DELETE is the only path meant to leave a
    row in the 'delete' state."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute("""
        INSERT INTO ocr_structure_overrides (
            job_id, key, page, occurrence_index, action,
            heading_override, start_page_override, end_page_override,
            updated_at, updated_by
        ) VALUES (?, ?, ?, ?, 'edit', ?, ?, ?, ?, ?)
        ON CONFLICT(job_id, key, page, occurrence_index) DO UPDATE SET
            action = 'edit',
            heading_override = COALESCE(excluded.heading_override, ocr_structure_overrides.heading_override),
            start_page_override = COALESCE(excluded.start_page_override, ocr_structure_overrides.start_page_override),
            end_page_override = COALESCE(excluded.end_page_override, ocr_structure_overrides.end_page_override),
            updated_at = excluded.updated_at,
            updated_by = COALESCE(excluded.updated_by, ocr_structure_overrides.updated_by)
    """, (job_id, key, page, occurrence_index, heading, start_page, end_page, now, updated_by))
    conn.commit()
    conn.close()


def tombstone(job_id: str, key: str, page: int, occurrence_index: int, updated_by: Optional[str] = None) -> None:
    """Upserts an action='delete' row. Terminal by contract: once this is
    set, apply_to_references_delta drops the candidate outright regardless
    of whatever heading_override/start_page_override/end_page_override
    columns still hold from an earlier edit — those are left as-is (moot,
    not cleared) rather than wiped, since nothing reads them once action is
    'delete'."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute("""
        INSERT INTO ocr_structure_overrides (
            job_id, key, page, occurrence_index, action, updated_at, updated_by
        ) VALUES (?, ?, ?, ?, 'delete', ?, ?)
        ON CONFLICT(job_id, key, page, occurrence_index) DO UPDATE SET
            action = 'delete',
            updated_at = excluded.updated_at,
            updated_by = COALESCE(excluded.updated_by, ocr_structure_overrides.updated_by)
    """, (job_id, key, page, occurrence_index, now, updated_by))
    conn.commit()
    conn.close()


def get_edit_overrides(job_id: str) -> Dict[Tuple[str, int, int], Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT key, page, occurrence_index, action, heading_override,
               start_page_override, end_page_override, updated_at, updated_by
        FROM ocr_structure_overrides WHERE job_id = ?
    """, (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {(r["key"], r["page"], r["occurrence_index"]): r for r in rows}


# ─── Manual (user-created) items (ocr_structure_manual_items) ─────────────

def create_manual_item(
    job_id: str, heading: str, start_page: int, end_page: int, created_by: Optional[str] = None,
) -> Dict[str, Any]:
    now = _now()
    last_err: Optional[Exception] = None
    for _attempt in range(5):  # astronomically unlikely PK collision; retry the mint rather than fail
        manual_key = "manual:" + uuid.uuid4().hex[:10]
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ocr_structure_manual_items (
                    manual_key, job_id, heading, start_page, end_page,
                    created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (manual_key, job_id, heading, start_page, end_page, now, now, created_by))
            conn.commit()
        except sqlite3.IntegrityError as e:
            last_err = e
            conn.close()
            continue
        conn.close()
        return {
            "manual_key": manual_key, "job_id": job_id, "heading": heading,
            "start_page": start_page, "end_page": end_page,
            "created_at": now, "updated_at": now, "created_by": created_by,
        }
    raise RuntimeError(f"Failed to mint a unique manual_key after 5 attempts: {last_err}")


def update_manual_item(
    job_id: str, manual_key: str, heading: Optional[str] = None,
    start_page: Optional[int] = None, end_page: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute("""
        UPDATE ocr_structure_manual_items
        SET heading = COALESCE(?, heading),
            start_page = COALESCE(?, start_page),
            end_page = COALESCE(?, end_page),
            updated_at = ?
        WHERE manual_key = ? AND job_id = ?
    """, (heading, start_page, end_page, now, manual_key, job_id))
    changed = cursor.rowcount > 0
    if not changed:
        conn.commit()
        conn.close()
        return None
    cursor.execute("SELECT * FROM ocr_structure_manual_items WHERE manual_key = ?", (manual_key,))
    row = cursor.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def delete_manual_item(job_id: str, manual_key: str) -> bool:
    """Hard delete — the row IS the item, no tombstone concept applies
    (unlike a detected row, there's nothing underneath it that could
    reappear on the next reindex)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_structure_manual_items WHERE manual_key = ? AND job_id = ?", (manual_key, job_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_manual_item(job_id: str, manual_key: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM ocr_structure_manual_items WHERE manual_key = ? AND job_id = ?",
        (manual_key, job_id),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_manual_items(job_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ocr_structure_manual_items WHERE job_id = ? ORDER BY start_page", (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ─── Parent (reparent) overrides (ocr_structure_parent_overrides) ─────────
#
# A node's identity for move/reparent purposes is (key, page, occurrence_index)
# — NOT the bare key alone. The same bare key can appear more than once in one
# document (a repeated heading like "point:5" under two different ancestors),
# and a bare-key identity silently misapplies a move to whichever occurrence
# happens to be visited last when the override is resolved. -1 is a non-NULL
# "any occurrence of this key" wildcard sentinel (never a real page number),
# used for pre-migration legacy rows and never written by a NEW move (the
# client always resolves and sends a real page/occurrence_index — see
# main.py's move_structure_item). Every lookup here tries an EXACT
# (key, page, occurrence_index) match first, falling back to the (-1, -1)
# wildcard row for that key — never the reverse order, so a later real,
# disambiguated override always takes precedence over an old wildcard one
# for the specific occurrence it targets.

_WILDCARD = -1


def set_parent(
    job_id: str, item_key: str, item_page: int, item_occurrence_index: int,
    new_parent_key: str, new_parent_page: int = _WILDCARD, new_parent_occurrence_index: int = _WILDCARD,
    updated_at: Optional[str] = None,
) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = updated_at or _now()
    cursor.execute("""
        INSERT INTO ocr_structure_parent_overrides (
            job_id, item_key, item_page, item_occurrence_index,
            new_parent_key, new_parent_page, new_parent_occurrence_index, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id, item_key, item_page, item_occurrence_index) DO UPDATE SET
            new_parent_key = excluded.new_parent_key,
            new_parent_page = excluded.new_parent_page,
            new_parent_occurrence_index = excluded.new_parent_occurrence_index,
            updated_at = excluded.updated_at
    """, (job_id, item_key, item_page, item_occurrence_index, new_parent_key, new_parent_page, new_parent_occurrence_index, now))
    conn.commit()
    conn.close()


def get_parent_overrides(job_id: str) -> List[Dict[str, Any]]:
    """Every parent-override row, raw — the frontend builds its own identity
    index from these (it already computes a node's (key, page,
    occurrence_index) while building the tree, so there's no need to encode
    a matching compound-string scheme in both Python and JS)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT item_key, item_page, item_occurrence_index,
               new_parent_key, new_parent_page, new_parent_occurrence_index
        FROM ocr_structure_parent_overrides WHERE job_id = ?
    """, (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def resolve_effective_parent(
    job_id: str, item_key: str, item_page: int, item_occurrence_index: int, natural_parent_key: str,
) -> Tuple[str, int, int]:
    """The override's parent for this exact (item_key, item_page,
    item_occurrence_index) if one exists; else the wildcard override for
    item_key alone, if one exists; else (natural_parent_key, -1, -1) — this
    function doesn't derive a natural parent from scope_chain itself, the
    caller already knows it (or, for the move endpoint's cycle walk,
    deliberately passes "" — see its own comment for why that's correct
    there). Returns (new_parent_key, new_parent_page, new_parent_occurrence_index)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT new_parent_key, new_parent_page, new_parent_occurrence_index
        FROM ocr_structure_parent_overrides
        WHERE job_id = ? AND item_key = ? AND item_page = ? AND item_occurrence_index = ?
    """, (job_id, item_key, item_page, item_occurrence_index))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("""
            SELECT new_parent_key, new_parent_page, new_parent_occurrence_index
            FROM ocr_structure_parent_overrides
            WHERE job_id = ? AND item_key = ? AND item_page = ? AND item_occurrence_index = ?
        """, (job_id, item_key, _WILDCARD, _WILDCARD))
        row = cursor.fetchone()
    conn.close()
    if row is None:
        return natural_parent_key, _WILDCARD, _WILDCARD
    return row[0], row[1], row[2]


# ─── Sibling order overrides (ocr_structure_sort_overrides) ───────────────

def set_sibling_order(
    job_id: str, scope_key: str, scope_page: int, scope_occurrence_index: int,
    ordered_items: List[Tuple[str, int, int]],
) -> None:
    """Delete-then-reinsert every row for this exact (scope_key, scope_page,
    scope_occurrence_index) — mirrors database.upsert_reference_targets_for_page's
    own delete-then-reinsert idiom for a "this list is the complete
    replacement" write. `ordered_items` is a list of
    (item_key, item_page, item_occurrence_index) tuples, in the new order."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute(
        "DELETE FROM ocr_structure_sort_overrides WHERE job_id = ? AND scope_key = ? AND scope_page = ? AND scope_occurrence_index = ?",
        (job_id, scope_key, scope_page, scope_occurrence_index),
    )
    for idx, (item_key, item_page, item_occurrence_index) in enumerate(ordered_items):
        cursor.execute("""
            INSERT INTO ocr_structure_sort_overrides (
                job_id, scope_key, scope_page, scope_occurrence_index,
                item_key, item_page, item_occurrence_index, sort_index, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, scope_key, scope_page, scope_occurrence_index, item_key, item_page, item_occurrence_index, idx, now))
    conn.commit()
    conn.close()


def get_sort_overrides(job_id: str) -> List[Dict[str, Any]]:
    """Every sort-override row, raw — see get_parent_overrides for why this
    is a flat list rather than a pre-grouped map."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT scope_key, scope_page, scope_occurrence_index,
               item_key, item_page, item_occurrence_index, sort_index
        FROM ocr_structure_sort_overrides
        WHERE job_id = ? ORDER BY scope_key, scope_page, scope_occurrence_index, sort_index
    """, (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ─── Rename-propagation support ────────────────────────────────────────────

def pages_referencing_key(job_id: str, key: str) -> List[int]:
    """Every page whose currently-stored candidates have `key` somewhere in
    their scope_chain (i.e. every page nested under it) — used only to know
    which pages a container rename needs to touch so they show up in the
    next /references delta with a rewritten ancestor label. Not
    performance-sensitive (called once per heading-changing edit, not on a
    hot read path) — correctness matters more than speed, so this is a plain
    full scan of the job's rows rather than an indexed lookup."""
    pages: set = set()
    for row in database.get_all_reference_targets_for_job(job_id):
        raw_chain = row.get("scope_chain")
        try:
            chain = json.loads(raw_chain) if raw_chain else []
        except Exception:
            chain = []
        if any(entry.get("key") == key for entry in chain):
            pages.add(row["page"])
    return sorted(pages)


# ─── Merge layer: overrides + detector output, at read time ───────────────

def apply_to_references_delta(
    job_id: str, changed_pages: Dict[int, Dict[str, List[Dict[str, Any]]]],
) -> Dict[int, Dict[str, List[Dict[str, Any]]]]:
    """Merges edit/delete overrides and manual items into a /references
    delta response (the {page_num: {key: [candidate, ...]}} shape
    GET /api/jobs/{job_id}/references already builds). Mutates and returns
    `changed_pages` — callers should use the return value rather than assume
    in-place mutation is guaranteed, but in the current implementation it is
    the same dict.

    Order: (1) edits/deletes on candidates already present, including
    relocating a moved candidate into its new page's bucket, (2) rename
    propagation into every remaining candidate's scope_chain, (3) manual
    item injection. Rename propagation runs after relocation so a
    just-moved candidate's own scope_chain is eligible for the same rename
    pass as everything else already in the delta.
    """
    edits = get_edit_overrides(job_id)

    # Relocations are collected rather than applied in-place while iterating
    # `changed_pages` — a start_page_override can target a page/key bucket
    # that doesn't exist yet in this delta at all (see spec: "create that
    # page/key bucket if it doesn't exist"), which would be unsafe to insert
    # into the very dict `.items()` is currently walking.
    moves: List[Tuple[str, Dict[str, Any], int]] = []  # (key, candidate, new_page)
    for page_num, by_key in list(changed_pages.items()):
        for key, candidates in list(by_key.items()):
            kept = []
            for cand in candidates:
                ov = edits.get((key, page_num, cand.get("occurrence_index")))
                if ov is None:
                    kept.append(cand)
                    continue
                if ov["action"] == "delete":
                    continue  # dropped — the list is allowed to end up empty
                # action == 'edit'
                if ov.get("heading_override"):
                    cand["own_label"] = ov["heading_override"]
                new_start = ov.get("start_page_override")
                if new_start is not None and new_start != page_num:
                    moves.append((key, cand, new_start))
                    continue  # relocated below, not kept in this page's bucket
                kept.append(cand)
            by_key[key] = kept

    for key, cand, new_page in moves:
        page_row = database.get_page(job_id, new_page)
        cand["snippet"] = (page_row.get("text") or "")[:300] if page_row else ""
        changed_pages.setdefault(new_page, {}).setdefault(key, []).append(cand)

    # Rename propagation: every candidate anywhere in this delta whose
    # scope_chain names an overridden-heading container gets that entry's
    # label rewritten. Only reaches pages already present in `changed_pages`
    # by the time this runs — the endpoint layer is responsible for having
    # touched (and thus included via get_reference_targets_since) every page
    # pages_referencing_key() names, or a rename would have nothing to
    # rewrite for pages other than the container's own.
    renamed = {
        ov["key"]: ov["heading_override"]
        for ov in edits.values()
        if ov["action"] == "edit" and ov.get("heading_override")
    }
    if renamed:
        for by_key in changed_pages.values():
            for candidates in by_key.values():
                for cand in candidates:
                    for entry in (cand.get("scope_chain") or []):
                        if entry.get("key") in renamed:
                            entry["label"] = renamed[entry["key"]]

    # Manual items: synthesized fresh every call (never persisted into
    # ocr_reference_targets), injected only onto a page already present in
    # this delta — mirrors "don't proactively fetch pages outside the
    # current response" from the rename-propagation note above.
    for item in list_manual_items(job_id):
        start_page = item["start_page"]
        if start_page not in changed_pages:
            continue
        page_row = database.get_page(job_id, start_page)
        snippet = (page_row.get("text") or "")[:300] if page_row else ""
        manual_key = item["manual_key"]
        changed_pages[start_page][manual_key] = [{
            "key": manual_key,
            "occurrence_index": 0,
            "anchor_id": f"structure-manual-{manual_key}",
            "snippet": snippet,
            "truncated": False,
            "continues_on_page": None,
            "scope_key": "",
            "scope_label": "",
            "scope_provisional": False,
            "collapsed_key": manual_key,
            "fingerprint": "",
            "scope_chain": [],
            "subdoc_label": None,
            "subdoc_index": None,
            "own_label": item["heading"],
            "heading_confidence": "high",
            "heading_confidence_reasons": [],
            "detection_source": "manual",
            "detection_model": None,
            "start_line_hint": None,
        }]

    return changed_pages


def apply_to_scope_ranges(job_id: str, ranges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merges edit overrides and manual items into a /scope-ranges response
    (the list reference_index.compute_scope_ranges returns). Deliberately
    kept cheap and always-run rather than memoized — reference_index.py's
    own _scope_ranges_cache covers the expensive detector-side computation;
    every override mutation already bumps reference_index_version (which
    invalidates that cache too), so this function only ever runs over an
    already-fresh `ranges` list."""
    edits = get_edit_overrides(job_id)

    heading_by_key: Dict[str, str] = {}
    range_edits_by_key: Dict[str, Dict[str, Optional[int]]] = {}
    for ov in edits.values():
        if ov["action"] != "edit":
            continue
        key = ov["key"]
        if ov.get("heading_override"):
            heading_by_key[key] = ov["heading_override"]
        if ov.get("start_page_override") is not None or ov.get("end_page_override") is not None:
            range_edits_by_key[key] = {
                "start_page_override": ov.get("start_page_override"),
                "end_page_override": ov.get("end_page_override"),
            }

    if heading_by_key:
        for r in ranges:
            if r["key"] in heading_by_key:
                r["label"] = heading_by_key[r["key"]]

    if range_edits_by_key:
        matches_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for r in ranges:
            if r["key"] in range_edits_by_key:
                matches_by_key.setdefault(r["key"], []).append(r)
        for key, override in range_edits_by_key.items():
            matches = matches_by_key.get(key, [])
            # Exactly one match is the only case we ever act on. Zero means
            # nothing to apply to (a stale override whose row no longer
            # exists post-reindex — see the module docstring's "orphaned
            # override" guarantee). More than one means the container closes
            # and reopens elsewhere in the document — the endpoint layer
            # already rejects a NEW page-range edit against a
            # multi-occurrence container at write time (409), so this is
            # purely a "don't corrupt data if the document changed shape
            # after an earlier, valid edit" guard, never a normal path.
            if len(matches) != 1:
                continue
            r = matches[0]
            if override["start_page_override"] is not None:
                r["start_page"] = override["start_page_override"]
            if override["end_page_override"] is not None:
                r["end_page"] = override["end_page_override"]

    for item in list_manual_items(job_id):
        ranges.append({
            "family": "manual",
            "key": item["manual_key"],
            "label": item["heading"],
            "depth": 0,
            "scope_key": "",
            "start_page": item["start_page"],
            "end_page": item["end_page"],
            # No `synthetic` flag — that means "no real container was
            # detected here, this is a gap-fill" (see
            # reference_index.compute_scope_ranges), a different concept
            # from "a real user-added container," which this is.
        })

    return ranges


# ─── Server-side hierarchy resolution (parent/sort overrides applied) ──────
#
# Everything above this point (apply_to_references_delta / apply_to_scope_ranges)
# applies edit/delete/manual-item overrides, but NOT parent/sort overrides —
# those were, until now, merged ONLY in the browser
# (frontend/src/components/StructureTree.jsx's applyHierarchyOverrides /
# finalize), which means nothing server-side (a gold-fixture export, a
# scoring script, a future AI-review pass) could ever see a user's
# drag-and-drop corrections. The functions below port that merge to Python,
# closely mirroring StructureTree.jsx's own logic so the two stay in sync —
# see that file for the parallel implementation and its own comments on the
# same design points (move identity, wildcard fallback, etc.).
#
# Deliberately NOT ported here: sibling ORDER (sort overrides). Reordering
# only affects display order, never a node's scope_key/ancestor chain, and
# the first server-side consumer (the gold-fixture exporter) only needs an
# accurate scope_key per target — add order resolution here too if a future
# caller actually needs display order server-side.

def _tree_node(key: str) -> Dict[str, Any]:
    return {"key": key, "children": {}, "leaves": []}


def _build_tree_from_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mirrors StructureTree.jsx's buildTree — groups the flat,
    materialized-path candidate list into a tree by each ancestor in
    scope_chain (ancestors only, self excluded) plus the candidate's own
    key as the terminal segment."""
    root = _tree_node("")
    for c in candidates:
        chain = c.get("scope_chain") or []
        path_keys = [s["key"] for s in chain] + [c["key"]]
        node = root
        for seg_key in path_keys:
            if seg_key not in node["children"]:
                node["children"][seg_key] = _tree_node(seg_key)
            node = node["children"][seg_key]
        node["leaves"].append(c)
    return root


def _primary_leaf(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A node's own primary occurrence — its first leaf by (page,
    occurrence_index) order. Mirrors StructureTree.jsx's primaryLeafOf."""
    if not node["leaves"]:
        return None
    return sorted(
        node["leaves"], key=lambda l: (l.get("page") or 0, l.get("occurrence_index") or 0),
    )[0]


def _node_move_id(node: Dict[str, Any]) -> str:
    """A node's move/reparent identity — (key, page, occurrence_index), not
    the bare key alone. Mirrors StructureTree.jsx's nodeMoveId exactly
    (same '\\0'-joined encoding) — see that function's own comment for why
    bare-key identity is the bug this whole mechanism exists to fix."""
    if node["key"] == "":
        return ""  # root
    leaf = _primary_leaf(node)
    if leaf is None:
        return f"{node['key']}\x00\x00"
    return f"{node['key']}\x00{leaf.get('page')}\x00{leaf.get('occurrence_index') or 0}"


def _apply_parent_overrides_to_tree(root: Dict[str, Any], parent_overrides: List[Dict[str, Any]]) -> None:
    """Mutates `root` in place — mirrors StructureTree.jsx's
    applyHierarchyOverrides, including its exact-match-first-then-
    unambiguous-wildcard-fallback resolution order."""
    if not parent_overrides:
        return

    node_by_move_id: Dict[str, Dict[str, Any]] = {}
    nodes_by_bare_key: Dict[str, List[Dict[str, Any]]] = {}

    def index(node: Dict[str, Any]) -> None:
        if node is not root:
            node_by_move_id[_node_move_id(node)] = node
            nodes_by_bare_key.setdefault(node["key"], []).append(node)
        for child in node["children"].values():
            index(child)

    index(root)

    def resolve(key: str, page: Optional[int], occurrence_index: Optional[int]) -> Optional[Dict[str, Any]]:
        if key == "":
            return root
        if page is not None and page != -1:
            exact = node_by_move_id.get(f"{key}\x00{page}\x00{occurrence_index if occurrence_index is not None else 0}")
            if exact is not None:
                return exact
        candidates = nodes_by_bare_key.get(key)
        return candidates[0] if candidates and len(candidates) == 1 else None

    for row in parent_overrides:
        node = resolve(row["item_key"], row.get("item_page"), row.get("item_occurrence_index"))
        if node is None:
            continue  # orphaned (moved item no longer exists), or ambiguous — inert
        new_parent_key = row["new_parent_key"]
        new_parent = resolve(new_parent_key, row.get("new_parent_page"), row.get("new_parent_occurrence_index")) if new_parent_key else root
        if new_parent is None or new_parent is node:
            continue  # orphaned target, or a no-op

        def detach(candidate: Dict[str, Any]) -> bool:
            for k, child in list(candidate["children"].items()):
                if child is node:
                    del candidate["children"][k]
                    return True
                if detach(child):
                    return True
            return False

        detach(root)
        new_parent["children"][node["key"]] = node


def _annotate_resolved_scope_key(node: Dict[str, Any], ancestor_keys: List[str]) -> None:
    """Walks the (now-reparented) tree, stamping each leaf row with
    `resolved_scope_key` — the '/'-joined ancestor key chain of the
    CORRECTED tree. The detector's own `scope_key` column is never
    rewritten by a move (see StructureTree.jsx's own note on this), so it
    reflects only the pre-correction parentage; `resolved_scope_key` is
    what a caller that cares about the user's actual corrections should
    compare against.

    `ancestor_keys` is this node's own ancestor chain — excludes this
    node's own key by construction (scope_key is always ancestors-only,
    self excluded). This node's OWN key only joins the chain passed down to
    ITS children, below — never gets mixed into what's stamped on its own
    leaves."""
    for leaf in node["leaves"]:
        leaf["resolved_scope_key"] = "/".join(ancestor_keys)
    next_ancestors = ancestor_keys if node["key"] == "" else ancestor_keys + [node["key"]]
    for child in node["children"].values():
        _annotate_resolved_scope_key(child, next_ancestors)


def resolve_structure_for_job(job_id: str) -> List[Dict[str, Any]]:
    """The fully-corrected structure for a job: detector output merged with
    edit/delete/manual-item overrides (apply_to_references_delta) AND
    parent overrides (previously applied only in the browser). Returns a
    flat list of target rows (each carrying `page`, plus every column
    apply_to_references_delta already produces), with `resolved_scope_key`
    added — the corrected ancestor chain, which may differ from the row's
    own stored `scope_key` wherever a parent override applies."""
    delta = database.get_reference_targets_since(job_id, since_version=-1)
    merged = apply_to_references_delta(job_id, delta)

    candidates: List[Dict[str, Any]] = []
    for page, by_key in merged.items():
        for key, rows in by_key.items():
            for row in rows:
                row = dict(row)
                row.setdefault("page", page)
                row.setdefault("key", key)
                candidates.append(row)

    root = _build_tree_from_candidates(candidates)
    _apply_parent_overrides_to_tree(root, get_parent_overrides(job_id))
    _annotate_resolved_scope_key(root, [])

    return candidates
