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

def set_parent(job_id: str, item_key: str, new_parent_key: str, updated_at: Optional[str] = None) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = updated_at or _now()
    cursor.execute("""
        INSERT INTO ocr_structure_parent_overrides (job_id, item_key, new_parent_key, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id, item_key) DO UPDATE SET
            new_parent_key = excluded.new_parent_key,
            updated_at = excluded.updated_at
    """, (job_id, item_key, new_parent_key, now))
    conn.commit()
    conn.close()


def get_parent_overrides(job_id: str) -> Dict[str, str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_key, new_parent_key FROM ocr_structure_parent_overrides WHERE job_id = ?",
        (job_id,),
    )
    result = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return result


def resolve_effective_parent(job_id: str, item_key: str, natural_parent_key: str) -> str:
    """The override's parent for item_key if one exists, else whatever
    natural_parent_key the caller supplied — this function doesn't derive a
    natural parent from scope_chain itself, the caller already knows it (or,
    for the move endpoint's cycle walk, deliberately passes "" — see its own
    comment for why that's correct there)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT new_parent_key FROM ocr_structure_parent_overrides WHERE job_id = ? AND item_key = ?",
        (job_id, item_key),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else natural_parent_key


# ─── Sibling order overrides (ocr_structure_sort_overrides) ───────────────

def set_sibling_order(job_id: str, scope_key: str, ordered_item_keys: List[str]) -> None:
    """Delete-then-reinsert every row for (job_id, scope_key) — mirrors
    database.upsert_reference_targets_for_page's own delete-then-reinsert
    idiom for a "this list is the complete replacement" write."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute(
        "DELETE FROM ocr_structure_sort_overrides WHERE job_id = ? AND scope_key = ?",
        (job_id, scope_key),
    )
    for idx, item_key in enumerate(ordered_item_keys):
        cursor.execute("""
            INSERT INTO ocr_structure_sort_overrides (job_id, scope_key, item_key, sort_index, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (job_id, scope_key, item_key, idx, now))
    conn.commit()
    conn.close()


def get_sort_overrides(job_id: str) -> Dict[str, List[str]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT scope_key, item_key FROM ocr_structure_sort_overrides
        WHERE job_id = ? ORDER BY scope_key, sort_index
    """, (job_id,))
    result: Dict[str, List[str]] = {}
    for scope_key, item_key in cursor.fetchall():
        result.setdefault(scope_key, []).append(item_key)
    conn.close()
    return result


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
