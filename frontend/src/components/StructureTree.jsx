import { useEffect, useMemo, useRef, useState } from 'react';
import { DndContext, useDraggable, useDroppable, PointerSensor, KeyboardSensor, useSensor, useSensors } from '@dnd-kit/core';
import { getStructureCandidates } from '../utils/referenceStore';
import { buildRangeIndex, findActivePathKeys } from '../utils/scopeRanges';

// Mirrors reference_index.py's _format_label_from_key for a candidate's own
// key (scope_chain entries already carry a backend-formatted label; this is
// only needed for the terminal segment, which the wire format doesn't send
// pre-formatted). Uppercases the number segment to match the backend's own
// convention (e.g. "annexure:a-1" -> "Annexure A-1") — otherwise the same
// logical node could display as "Annexure A-1" when reached via a
// descendant's scope_chain entry but "Annexure a-1" when reached via the
// Annexure's own leaf row, depending on which candidate the tree-builder
// happens to visit first.
//
// Not used for `numheading` — that family is an internal synthetic bucket
// name, not real document vocabulary, so "Numheading 2.0" would be wrong
// even when correctly capitalized. See `numheadingLabel` below.
function formatKeyLabel(key) {
  if (!key) return '';
  const idx = key.indexOf(':');
  if (idx === -1) return key;
  const family = key.slice(0, idx);
  const number = key.slice(idx + 1);
  return `${family.charAt(0).toUpperCase()}${family.slice(1)} ${number.toUpperCase()}`;
}

// A numbered- or letter-heading's own display label: the backend sends the
// real captured text (e.g. "2.0 CONTRACT DOCUMENTS", "A INTRODUCTION") as
// `own_label` on the candidate whenever it detected one, or just the bare
// number (e.g. "2.1") for a numbered clause with no title text at all (no
// bare-letter equivalent exists — every letterheading candidate always
// carries a real title). Falls back to parsing the number/letter out of the
// key only for a pre-fix stored row that predates this field (own_label is
// null/undefined).
function ownLabelOrBareKey(candidate) {
  if (candidate.own_label) return candidate.own_label;
  const idx = candidate.key.indexOf(':');
  return idx === -1 ? candidate.key : candidate.key.slice(idx + 1);
}

// 'subdoc' added so an AI-detected keyword-less/number-less heading (the
// residual case the rule-based detector can't catch at all — see
// reference_index.mint_key_for_heading) renders its own real captured text
// instead of the generic synthetic "Subdoc 3" formatKeyLabel would produce.
// 'manual' likewise: a user-added entry's key is an opaque "manual:<id>"
// token, not real document vocabulary — always render its own heading text.
const OWN_LABEL_FAMILIES = new Set(['numheading', 'letterheading', 'subdoc', 'manual', 'point', 'subpoint']);

function terminalLabel(candidate) {
  const idx = candidate.key.indexOf(':');
  const family = idx === -1 ? candidate.key : candidate.key.slice(0, idx);
  return OWN_LABEL_FAMILIES.has(family) ? ownLabelOrBareKey(candidate) : formatKeyLabel(candidate.key);
}

// Builds a tree from the flat, materialized-path candidate list — grouped by
// each ancestor in scope_chain (now including the sub-document tier, unified
// server-side into the same chain — no separate subdoc grouping step needed
// here), with the candidate's own key as the terminal segment. A container's
// own row (e.g. "Article 2") and its role as an ancestor in descendants'
// scope_chain address the same map entry by construction (same path of
// keys), so they collapse onto one node with no separate reconciliation
// step. A bare top-level Article (no enclosing Annexure) and a top-level
// Annexure naturally end up as sibling root branches — no synthetic "Main
// Document" wrapper needed. Multiple occurrences of the same key at the
// same scope (occurrence_index 0, 1, ...) attach as multiple leaves on that
// one terminal node, never overwriting each other.
function buildTree(candidates) {
  const root = { id: 'root', key: '', label: '', children: new Map(), leaves: [] };
  for (const c of candidates) {
    const chain = c.scope_chain || [];
    const pathKeys = [...chain.map((s) => s.key), c.key];
    const pathLabels = [...chain.map((s) => s.label), terminalLabel(c)];

    let node = root;
    for (let i = 0; i < pathKeys.length; i++) {
      const segKey = pathKeys[i];
      if (!node.children.has(segKey)) {
        // `key` is the bare segment key (e.g. "annexure:a-2"), alongside the
        // existing compound `id` (a full path like "root/annexure:a-2") —
        // used to look a node up in the Part B scope-ranges index, which is
        // keyed by the same bare per-node key compute_scope_ranges emits,
        // and now also as the drag-and-drop move identity (see
        // applyHierarchyOverrides below).
        node.children.set(segKey, { id: `${node.id}/${segKey}`, key: segKey, label: pathLabels[i], children: new Map(), leaves: [] });
      }
      node = node.children.get(segKey);
    }
    node.leaves.push(c);
  }
  return root;
}

// A node's own primary occurrence — its first leaf by (page,
// occurrence_index) order. Sorts a COPY rather than relying on `node.leaves`
// already being sorted, so this is correct whether called before or after
// `finalize` (which does its own leaf sort) — applyHierarchyOverrides runs
// BEFORE finalize, so node.leaves is still in raw insertion order there.
function primaryLeafOf(node) {
  if (!node.leaves.length) return null;
  return [...node.leaves].sort(
    (a, b) => (a.page || 0) - (b.page || 0) || (a.occurrence_index || 0) - (b.occurrence_index || 0)
  )[0];
}

// A node's move/reparent identity: NOT the bare key alone — the same bare
// key can repeat in one document (a repeated heading like "point:5" under
// two different ancestors), and a bare-key identity silently misapplies a
// move to whichever occurrence happens to be indexed last. '' (no leaves at
// all — a pure grouping node synthesized only from descendants' scope_chain,
// never itself detected) is the one case with no real page/occurrence to
// disambiguate with; it degrades to a bare-key identity, same as before —
// accepted, since such a node has no stored row for an override to target
// anyway (see containerItemDescriptor's own null case).
function nodeMoveId(node) {
  if (!node || node.key === '') return ''; // root
  const leaf = primaryLeafOf(node);
  return `${node.key}\u0000${leaf ? leaf.page : ''}\u0000${leaf ? (leaf.occurrence_index ?? 0) : ''}`;
}

// A bare leaf candidate's own move identity (used for an individual
// occurrence row under an already-multi-occurrence container — see
// LeafRow) — same (key, page, occurrence_index) scheme as nodeMoveId, just
// built directly from the candidate instead of a tree node's primary leaf.
function leafMoveId(leaf) {
  return `${leaf.key}\u0000${leaf.page}\u0000${leaf.occurrence_index ?? 0}`;
}

function parseMoveId(id) {
  if (!id) return { key: '', page: -1, occurrenceIndex: -1 };
  const parts = String(id).split('\u0000');
  const key = parts[0];
  const page = parts[1] === undefined || parts[1] === '' ? -1 : Number(parts[1]);
  const occurrenceIndex = parts[2] === undefined || parts[2] === '' ? -1 : Number(parts[2]);
  return { key, page, occurrenceIndex };
}

// Applies drag-and-drop hierarchy overrides on top of the just-built tree —
// per the plan's Part C2, parent/order overrides never rewrite scope_chain
// server-side; they're plain data (GET .../structure-hierarchy-overrides:
// {parents: [{item_key, item_page, item_occurrence_index, new_parent_key,
// new_parent_page, new_parent_occurrence_index}, ...], order: [...]})
// applied here, client-side, so a moved node's whole subtree (children
// included) follows it automatically — no per-descendant patching needed.
//
// Every node buildTree creates — whether it ends up looking like a "leaf"
// or a "container" in the UI — already carries its own `children` Map (see
// buildTree above: a node is created for every segment in a candidate's
// path, terminal segment included), so any node's bare key is always a
// valid reparent target with no separate "promote a leaf into a container"
// step needed.
//
// Move identity is (key, page, occurrence_index) via nodeMoveId — matching
// the server's own disambiguated override identity (main.py's
// move_structure_item / structure_overrides.set_parent) — so a move/drop
// targeting one occurrence of a repeated key never touches the other.
function applyHierarchyOverrides(root, parentOverrides) {
  if (!parentOverrides || parentOverrides.length === 0) return root;

  const nodeByMoveId = new Map();
  const nodesByBareKey = new Map(); // wildcard fallback — bare key -> [nodes]
  (function index(node) {
    if (node !== root) {
      nodeByMoveId.set(nodeMoveId(node), node);
      const list = nodesByBareKey.get(node.key);
      if (list) list.push(node); else nodesByBareKey.set(node.key, [node]);
    }
    for (const child of node.children.values()) index(child);
  })(root);

  // Exact (key, page, occurrence_index) match first; a wildcard row
  // (page/occurrence_index === -1, the pre-migration legacy shape) only
  // applies when exactly one node in the whole tree carries that bare key —
  // an ambiguous wildcard (multiple occurrences, none specified) is inert
  // rather than guessing which one the override meant.
  function resolveNode(key, page, occurrenceIndex) {
    if (page !== -1 && page !== undefined && page !== null) {
      const exact = nodeByMoveId.get(`${key}\u0000${page}\u0000${occurrenceIndex ?? 0}`);
      if (exact) return exact;
    }
    const candidates = nodesByBareKey.get(key);
    return candidates && candidates.length === 1 ? candidates[0] : null;
  }

  for (const row of parentOverrides) {
    const node = resolveNode(row.item_key, row.item_page, row.item_occurrence_index);
    if (!node) continue; // orphaned (moved item no longer exists), or ambiguous — inert
    const newParent = row.new_parent_key
      ? resolveNode(row.new_parent_key, row.new_parent_page, row.new_parent_occurrence_index)
      : root;
    if (!newParent || newParent === node) continue; // orphaned target, or a no-op
    let detached = false;
    (function detach(candidate) {
      if (detached) return;
      for (const [k, child] of candidate.children) {
        if (child === node) { candidate.children.delete(k); detached = true; return; }
        detach(child);
        if (detached) return;
      }
    })(root);
    newParent.children.set(node.key, node);
  }

  return root;
}

// Pre-groups the raw sortOverrides row list (per the new disambiguated
// wire shape — see get_structure_hierarchy_overrides) by the SCOPE
// (parent) they apply to, once, rather than re-scanning the whole list on
// every recursive finalize() call. Exact (scope_key, scope_page,
// scope_occurrence_index) match is tried first; a wildcard scope row
// (scope_page === -1, the pre-migration legacy shape) is kept as a
// bare-scope_key fallback — lower severity than the parent-override
// ambiguity (worst case here is a child rendering in default page order
// instead of its custom one, never data loss), so no uniqueness check.
function groupSortOverrides(sortOverrides) {
  const byExactScope = new Map();
  const byWildcardScope = new Map();
  for (const row of sortOverrides || []) {
    const exactId = `${row.scope_key}\u0000${row.scope_page}\u0000${row.scope_occurrence_index}`;
    if (!byExactScope.has(exactId)) byExactScope.set(exactId, []);
    byExactScope.get(exactId).push(row);
    if (row.scope_page === -1) {
      if (!byWildcardScope.has(row.scope_key)) byWildcardScope.set(row.scope_key, []);
      byWildcardScope.get(row.scope_key).push(row);
    }
  }
  return { byExactScope, byWildcardScope };
}

// Post-order: turns each node's children Map into an array, sorted by a
// manual sortOverrides order when the reader has drag-and-drop-reordered
// this node's children, falling back to first-occurrence page (ascending)
// for any sibling not yet manually positioned — appended after the
// manually-ordered ones, in their own stable page order. `sortIndex` is the
// pre-grouped result of groupSortOverrides, computed once by the caller.
function finalize(node, sortIndex) {
  const children = Array.from(node.children.values()).map((c) => finalize(c, sortIndex));
  const leaves = [...node.leaves].sort(
    (a, b) => (a.page || 0) - (b.page || 0) || (a.occurrence_index || 0) - (b.occurrence_index || 0)
  );
  const minPage = Math.min(
    Infinity,
    ...leaves.map((l) => l.page ?? Infinity),
    ...children.map((c) => c.minPage)
  );
  // node.key === '' for root, matching the backend's "" = root convention.
  const rows = sortIndex?.byExactScope.get(nodeMoveId(node)) || sortIndex?.byWildcardScope.get(node.key);
  if (rows && rows.length) {
    const rankByMoveId = new Map(rows.map((r, i) => [`${r.item_key}\u0000${r.item_page}\u0000${r.item_occurrence_index}`, i]));
    const rankByBareKey = new Map(rows.map((r, i) => [r.item_key, i]));
    children.sort((a, b) => {
      const ra = rankByMoveId.has(nodeMoveId(a)) ? rankByMoveId.get(nodeMoveId(a))
        : (rankByBareKey.has(a.key) ? rankByBareKey.get(a.key) : Infinity);
      const rb = rankByMoveId.has(nodeMoveId(b)) ? rankByMoveId.get(nodeMoveId(b))
        : (rankByBareKey.has(b.key) ? rankByBareKey.get(b.key) : Infinity);
      return ra !== rb ? ra - rb : a.minPage - b.minPage;
    });
  } else {
    children.sort((a, b) => a.minPage - b.minPage);
  }
  return { ...node, children, leaves, minPage };
}

// Human-readable phrasing for each heading_confidence_reasons signal name —
// shown in a leaf's tooltip so a reviewer sees *why* it's flagged, not just
// *that* it is. Falls back to the raw reason string for anything unlisted
// (e.g. a future signal added server-side before this map is updated).
const REASON_LABELS = {
  chained_citation: 'chained reference to another clause/section',
  other_doc_marker: 'mentions another document (GCC/SCC/Vol...)',
  range_shaped_continuation: 'range-shaped ("... TO N") continuation, likely a running header',
  same_page_repeat: 'repeats an already-seen sub-document trigger on this page',
  non_heading_layout_type: "the OCR model's own layout tag says this is body text, not a heading",
  layout_header: "the OCR model's own layout tag marks this as a header",
  layout_title: "the OCR model's own layout tag marks this as a title",
  ai_unlocated: "the AI-reported heading text couldn't be matched to this page's real content",
  ai_unresolved_parent_ref: "the AI's stated parent heading couldn't be resolved; nested by its reported level instead",
  ai_fuzzy_parent_ref: "the AI's stated parent heading was matched approximately, not exactly",
  ai_page_corrected: 'the AI reported the wrong page for this heading; matched on a neighboring page instead',
  ai_fuzzy_grounded: "the AI-reported heading text only matched approximately, not exactly, to this page's real content",
  rule_prior_unreviewed: 'unreviewed prior heading kept from rule detection',
  rule_prior_rejection_unverified: "AI rejected this prior but evidence quote couldn't be verified on page",
  rule_prior_key_drift: 'AI re-minted a different target key than the rule path assigned',
};

function reasonsTooltip(reasons) {
  const phrases = (reasons || []).map((r) => REASON_LABELS[r] || r);
  return `Possible reference, not a confirmed heading — ${phrases.join('; ')}`;
}

// A node's own page span, shown only when it's actually informative: a
// range spanning just its own single page adds nothing a reader doesn't
// already know from browsing to it, so it's shown only when the range
// covers more than one page, or the node has more than one disjoint range
// (both cases from Part B's compute_scope_ranges).
function formatRangeSuffix(nodeKey, rangeIndex) {
  const ranges = rangeIndex?.get(nodeKey);
  if (!ranges || ranges.length === 0) return '';
  const seen = new Set();
  const spans = [];
  for (const r of ranges) {
    const span = r.start_page === r.end_page ? `${r.start_page}` : `${r.start_page}–${r.end_page}`;
    if (!seen.has(span)) {
      seen.add(span);
      spans.push(span);
    }
  }
  if (spans.length === 1 && ranges[0].start_page === ranges[0].end_page) return '';
  return ` (pages ${spans.join(', ')})`;
}

// Container-tier edit/delete descriptor — built from the node's own
// occurrence (node.leaves[0], its earliest self-detection), since that's
// the actual addressable row an edit/delete PATCH/DELETE attaches to. A
// node with no leaves of its own (a pure grouping artifact assembled only
// from descendants' scope_chain) has no such row — edit/delete stay hidden
// for it, though it remains a valid drag-and-drop move target.
function containerItemDescriptor(node, rangeIndex) {
  if (!node.leaves.length) return null;
  const leaf = node.leaves[0];
  const ranges = rangeIndex?.get(node.key);
  const range = ranges && ranges[0];
  return {
    itemKey: node.key, page: leaf.page, occurrenceIndex: leaf.occurrence_index ?? 0,
    isManual: node.key.startsWith('manual:'),
    heading: node.label, startPage: range ? range.start_page : leaf.page, endPage: range ? range.end_page : leaf.page,
  };
}

function leafItemDescriptor(leaf) {
  return {
    itemKey: leaf.key, page: leaf.page, occurrenceIndex: leaf.occurrence_index ?? 0,
    isManual: leaf.key.startsWith('manual:'),
    heading: ownLabelOrBareKey(leaf), startPage: leaf.page, endPage: leaf.page,
  };
}

// Drag handle — a small grip icon, its own drag SOURCE (dnd-kit
// useDraggable), deliberately separate from the row's own click target
// (expand/collapse for a container header, onSelect for a leaf) so picking
// up a row to drag it never also fires that row's normal click action.
function DragHandle({ id, testId }) {
  const { attributes, listeners, setNodeRef } = useDraggable({ id });
  return (
    <span
      ref={setNodeRef}
      className="structure-tree-drag-handle"
      title="Drag to move under a different heading"
      data-testid={testId}
      {...listeners}
      {...attributes}
      onClick={(e) => e.stopPropagation()}
    >
      <i className="fa-solid fa-grip-vertical" />
    </span>
  );
}

function TreeNode({ node, depth, onSelect, onSelectContainer, onOpenReference, onJumpContainer, rangeIndex, activePathIds, currentNodeId, onEditItem, onDeleteItem, onAddChild, collapseSignal, expandSignal, dragExpandIds }) {
  const [expanded, setExpanded] = useState(depth === 0);
  const isCurrent = node.id === currentNodeId;
  const headerRef = useRef(null);
  const moveId = nodeMoveId(node);
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: moveId });

  // Forces this node open when it becomes part of the active-page's
  // ancestor chain, or when a drag has been hovering over it long enough to
  // auto-reveal its children (see StructureTree's handleDragOver) — but
  // only in reaction to a genuinely new activePathIds/dragExpandIds value,
  // never on every render, so a reader who manually collapses a node keeps
  // it collapsed until one of those actually changes again.
  useEffect(() => {
    if (activePathIds?.has(node.id) || dragExpandIds?.has(moveId)) setExpanded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePathIds, dragExpandIds]);

  // "Collapse all" / "Expand all" — each is a counter bumped by its own
  // toolbar button; any change (not just a first truthy value) means "do it
  // now," including depth-0 nodes that default open.
  useEffect(() => {
    if (collapseSignal) setExpanded(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapseSignal]);

  useEffect(() => {
    if (expandSignal) setExpanded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandSignal]);

  // Scrolls the current node into view whenever it newly becomes current —
  // not on every render, and not for any other node.
  useEffect(() => {
    if (isCurrent) headerRef.current?.scrollIntoView({ block: 'nearest' });
  }, [isCurrent]);

  if (node.children.length === 0 && node.leaves.length === 0) return null;

  // Real backend-tracked container tiers (Annexure/Article/Section —
  // anything Part B's compute_scope_ranges ever pushed onto nested_stack)
  // get an explicit multi-page span; anything else falls back to this
  // node's own earliest known page (finalize() bubbles minPage up from
  // every leaf/child, so it's always defined here) — every node gets a
  // working jump/preview target, never just the ranged ones.
  const ranges = rangeIndex?.get(node.key);
  const hasOwnRange = !!ranges?.length;
  const jumpRange = hasOwnRange
    ? { startPage: ranges[0].start_page, endPage: ranges[0].end_page }
    : { startPage: node.minPage, endPage: node.minPage };

  // Exactly one occurrence of its own, no real backend range — this node's
  // own header IS that occurrence (jump/preview/edit/delete all target it
  // directly, with the leaf's own anchor-precise jump), regardless of
  // whether it also has real nested children (those still get their own
  // chevron+expand below) — a redundant "Page N" row under it would add a
  // click for zero new information. A node WITH a real range keeps full
  // container treatment even with just one occurrence (still worth a
  // whole-section preview). A node with MORE than one occurrence keeps each
  // shown separately, as today — there's genuinely more than one distinct
  // page to reach.
  const primaryLeaf = (!hasOwnRange && node.leaves.length === 1) ? node.leaves[0] : null;
  const lowConfidence = primaryLeaf?.heading_confidence === 'low';
  const itemDescriptor = primaryLeaf
    ? { ...leafItemDescriptor(primaryLeaf), heading: node.label }
    : containerItemDescriptor(node, rangeIndex);
  // A non-primaryLeaf node always has *something* to expand (leaves-or-
  // children, per the null-guard above); a primaryLeaf node only has real
  // children left to expand, since its own one occurrence is folded above.
  const expandable = primaryLeaf ? node.children.length > 0 : true;

  function handleRowClick() {
    if (primaryLeaf) {
      if (onSelect) onSelect(primaryLeaf);
    } else if (onJumpContainer) {
      onJumpContainer({ startPage: jumpRange.startPage, endPage: jumpRange.endPage, label: node.label });
    } else {
      setExpanded((e) => !e);
    }
  }

  function handleMagnifyClick(e) {
    e.stopPropagation();
    if (primaryLeaf) {
      if (onOpenReference) onOpenReference(primaryLeaf);
    } else if (onSelectContainer) {
      onSelectContainer({ startPage: jumpRange.startPage, endPage: jumpRange.endPage, label: node.label });
    }
  }

  return (
    <li
      className={`structure-tree-node${isCurrent ? ' structure-tree-node--current' : ''}${lowConfidence ? ' structure-tree-leaf--low-confidence' : ''}`}
      data-testid={primaryLeaf ? `structure-leaf-${primaryLeaf.anchor_id}` : `structure-node-${node.id}`}
      title={lowConfidence ? reasonsTooltip(primaryLeaf.heading_confidence_reasons) : undefined}
      // The row's own click lives here, on the outer <li> — not just the
      // header <div> — so a synthetic click targeting this node directly
      // (its own testid) fires the same way a real click bubbling up from
      // inside the header does. Stopping propagation immediately is what
      // keeps that safe: a nested child row's own click (LeafRow, or a
      // deeper TreeNode — both stop propagation the same way) is swallowed
      // at ITS level and never reaches this <li>, so a click deep in an
      // expanded subtree never also re-fires this ancestor's jump.
      onClick={(e) => { e.stopPropagation(); handleRowClick(); }}
    >
      <div
        ref={(el) => { headerRef.current = el; setDropRef(el); }}
        className={`structure-tree-node-header${isOver ? ' structure-tree-node-header--drop-over' : ''}`}
        style={{ paddingLeft: depth * 12 }}
      >
        <DragHandle id={moveId} testId={`structure-drag-handle-${node.id}`} />
        {expandable ? (
          <i
            className={`fa-solid ${expanded ? 'fa-chevron-down' : 'fa-chevron-right'}`}
            onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
            data-testid={`structure-toggle-${node.id}`}
          />
        ) : (
          <i className="fa-regular fa-file-lines" />
        )}
        <span className="structure-tree-node-label">
          {lowConfidence && <span className="structure-low-confidence-marker">≈ </span>}
          <span className="structure-tree-label-text" title={node.label}>{node.label}</span>
          {primaryLeaf?.detection_source === 'ai' && (
            <span className="structure-ai-badge" title={`Detected by AI${primaryLeaf.detection_model ? ` (${primaryLeaf.detection_model})` : ''}`}> AI</span>
          )}
          {primaryLeaf?.detection_source === 'manual' && (
            <span className="structure-ai-badge structure-manual-badge" title="Added manually"> Manual</span>
          )}
          {primaryLeaf?.scope_provisional && <span className="structure-provisional-hint"> (provisional)</span>}
          {!primaryLeaf && <span className="structure-tree-node-range">{formatRangeSuffix(node.key, rangeIndex)}</span>}
        </span>
        <span className="structure-tree-node-actions">
          {/* Temporarily disabled — see StructureTree.jsx task to re-enable. */}
          {false && (primaryLeaf ? onOpenReference : onSelectContainer) && (
            <button
              className="structure-tree-node-jump"
              onClick={handleMagnifyClick}
              title={primaryLeaf || jumpRange.startPage === jumpRange.endPage
                ? 'Preview this page in a reading modal'
                : `Preview this section in a reading modal (pages ${jumpRange.startPage}–${jumpRange.endPage})`}
              data-testid={primaryLeaf ? `structure-view-leaf-${primaryLeaf.anchor_id}` : `structure-jump-${node.id}`}
            >
              <i className="fa-solid fa-magnifying-glass" />
            </button>
          )}
          {itemDescriptor && onAddChild && (
            <button
              className="structure-tree-node-add"
              onClick={(e) => { e.stopPropagation(); onAddChild(moveId, itemDescriptor); }}
              title="Add a new entry as a child of this one"
              data-testid={primaryLeaf ? `structure-add-child-leaf-${primaryLeaf.anchor_id}` : `structure-add-child-${node.id}`}
            >
              <i className="fa-solid fa-plus" />
            </button>
          )}
          {itemDescriptor && onEditItem && (
            <button
              className="structure-tree-node-edit"
              onClick={(e) => { e.stopPropagation(); onEditItem(itemDescriptor); }}
              title={primaryLeaf ? 'Edit heading / page' : 'Edit heading / pages'}
              data-testid={primaryLeaf ? `structure-edit-leaf-${primaryLeaf.anchor_id}` : `structure-edit-${node.id}`}
            >
              <i className="fa-solid fa-pen" />
            </button>
          )}
          {itemDescriptor && onDeleteItem && (
            <button
              className="structure-tree-node-delete"
              onClick={(e) => { e.stopPropagation(); onDeleteItem(itemDescriptor); }}
              title="Delete this entry"
              data-testid={primaryLeaf ? `structure-delete-leaf-${primaryLeaf.anchor_id}` : `structure-delete-${node.id}`}
            >
              <i className="fa-solid fa-trash" />
            </button>
          )}
        </span>
      </div>
      {expandable && expanded && (
        <ul className="structure-tree-children">
          {!primaryLeaf && node.leaves.map((leaf) => {
            const leafLowConfidence = leaf.heading_confidence === 'low';
            const leafDescriptor = leafItemDescriptor(leaf);
            return (
              <LeafRow
                key={leaf.anchor_id}
                leaf={leaf}
                depth={depth + 1}
                lowConfidence={leafLowConfidence}
                onSelect={onSelect}
                onOpenReference={onOpenReference}
                onEditItem={onEditItem}
                onDeleteItem={onDeleteItem}
                onAddChild={onAddChild}
                leafDescriptor={leafDescriptor}
              />
            );
          })}
          {node.children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
              onSelectContainer={onSelectContainer}
              onOpenReference={onOpenReference}
              onJumpContainer={onJumpContainer}
              rangeIndex={rangeIndex}
              activePathIds={activePathIds}
              currentNodeId={currentNodeId}
              onEditItem={onEditItem}
              onDeleteItem={onDeleteItem}
              onAddChild={onAddChild}
              collapseSignal={collapseSignal}
              expandSignal={expandSignal}
              dragExpandIds={dragExpandIds}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function LeafRow({ leaf, depth, lowConfidence, onSelect, onOpenReference, onEditItem, onDeleteItem, onAddChild, leafDescriptor }) {
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: leafMoveId(leaf) });
  return (
    <li
      ref={setDropRef}
      className={`structure-tree-leaf${lowConfidence ? ' structure-tree-leaf--low-confidence' : ''}${isOver ? ' structure-tree-leaf--drop-over' : ''}`}
      style={{ paddingLeft: depth * 12 }}
      // Stops here so this occurrence's own click never also re-fires an
      // ancestor node's row click (see the matching note on TreeNode's <li>).
      onClick={(e) => { e.stopPropagation(); onSelect(leaf); }}
      data-testid={`structure-leaf-${leaf.anchor_id}`}
      title={lowConfidence ? reasonsTooltip(leaf.heading_confidence_reasons) : undefined}
    >
      <DragHandle id={leafMoveId(leaf)} testId={`structure-drag-handle-leaf-${leaf.anchor_id}`} />
      <i className="fa-regular fa-file-lines" />
      {lowConfidence && <span className="structure-low-confidence-marker">≈ </span>}
      Page {leaf.page}
      {leaf.detection_source === 'ai' && (
        <span className="structure-ai-badge" title={`Detected by AI${leaf.detection_model ? ` (${leaf.detection_model})` : ''}`}> AI</span>
      )}
      {leaf.detection_source === 'manual' && (
        <span className="structure-ai-badge structure-manual-badge" title="Added manually"> Manual</span>
      )}
      {leaf.scope_provisional && <span className="structure-provisional-hint"> (provisional)</span>}
      <span className="structure-tree-node-actions">
        {/* Temporarily disabled — see StructureTree.jsx task to re-enable. */}
        {false && onOpenReference && (
          <button
            className="structure-tree-node-jump"
            onClick={(e) => { e.stopPropagation(); onOpenReference(leaf); }}
            title="Preview this page in a reading modal"
            data-testid={`structure-view-leaf-${leaf.anchor_id}`}
          >
            <i className="fa-solid fa-magnifying-glass" />
          </button>
        )}
        {onAddChild && (
          <button
            className="structure-tree-node-add"
            onClick={(e) => { e.stopPropagation(); onAddChild(leafMoveId(leaf), leafDescriptor); }}
            title="Add a new entry as a child of this one"
            data-testid={`structure-add-child-leaf-${leaf.anchor_id}`}
          >
            <i className="fa-solid fa-plus" />
          </button>
        )}
        {onEditItem && (
          <button
            className="structure-tree-node-edit"
            onClick={(e) => { e.stopPropagation(); onEditItem(leafDescriptor); }}
            title="Edit heading / page"
            data-testid={`structure-edit-leaf-${leaf.anchor_id}`}
          >
            <i className="fa-solid fa-pen" />
          </button>
        )}
        {onDeleteItem && (
          <button
            className="structure-tree-node-delete"
            onClick={(e) => { e.stopPropagation(); onDeleteItem(leafDescriptor); }}
            title="Delete this entry"
            data-testid={`structure-delete-leaf-${leaf.anchor_id}`}
          >
            <i className="fa-solid fa-trash" />
          </button>
        )}
      </span>
    </li>
  );
}

function RootDropZone() {
  const { setNodeRef, isOver } = useDroppable({ id: '' });
  return (
    <li
      ref={setNodeRef}
      className={`structure-tree-root-dropzone${isOver ? ' structure-tree-root-dropzone--over' : ''}`}
      data-testid="structure-tree-root-dropzone"
    >
      <i className="fa-solid fa-arrow-turn-up" /> Drop here to move to the top level
    </li>
  );
}

export default function StructureTree({
  referenceStore, onSelect, onSelectContainer, onOpenReference, onJumpContainer, scopeRanges, activePage,
  onEditItem, onDeleteItem, onMoveItem, onAddItem, parentOverrides, sortOverrides,
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );

  // "Collapse all"/"Expand all" toolbar buttons — see TreeNode's matching useEffects.
  const [collapseSignal, setCollapseSignal] = useState(0);
  const [expandSignal, setExpandSignal] = useState(0);

  // Hover-to-auto-expand during a drag, plus a short toast for a
  // rejected/no-op drop — see handleDragOver/handleDragEnd below.
  const [dragExpandIds, setDragExpandIds] = useState(() => new Set());
  const [dropFeedback, setDropFeedback] = useState(null);
  const dragOverIdRef = useRef(null);
  const dragOverTimerRef = useRef(null);
  const dropFeedbackTimerRef = useRef(null);

  useEffect(() => () => {
    clearTimeout(dragOverTimerRef.current);
    clearTimeout(dropFeedbackTimerRef.current);
  }, []);

  const tree = useMemo(() => {
    if (!referenceStore) return null;
    const raw = buildTree(getStructureCandidates(referenceStore));
    const reparented = applyHierarchyOverrides(raw, parentOverrides);
    return finalize(reparented, groupSortOverrides(sortOverrides));
    // referenceStore is a stable ref mutated in place (see AppShell.jsx) —
    // `.version` is the actual change signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [referenceStore, referenceStore?.version, parentOverrides, sortOverrides]);

  const rangeIndex = useMemo(() => buildRangeIndex(scopeRanges), [scopeRanges]);

  // The full ancestor-chain-plus-self key path for whichever node currently
  // covers activePage (null when scopeRanges hasn't loaded yet, or no range
  // covers this page — e.g. a synthetic-gap page with no real container).
  const activePath = useMemo(
    () => (activePage == null ? null : findActivePathKeys(scopeRanges, activePage)),
    [scopeRanges, activePage]
  );

  // Every node id along that path (root/k1, root/k1/k2, ...), so any depth
  // of TreeNode can cheaply check "am I on the active path" without
  // re-walking the chain itself; currentNodeId is just the last (deepest) one.
  const { activePathIds, currentNodeId } = useMemo(() => {
    if (!activePath) return { activePathIds: null, currentNodeId: null };
    const ids = new Set();
    let id = 'root';
    for (const key of activePath) {
      id = `${id}/${key}`;
      ids.add(id);
    }
    return { activePathIds: ids, currentNodeId: id };
  }, [activePath]);

  // Finds a node's CURRENT children list (already reparented/sorted) — used
  // to build the full `orderedSiblings` payload a move sends, so the server
  // always receives a complete replacement list, never a diff. Searches by
  // move id (key, page, occurrence_index), not bare key — see nodeMoveId.
  function findNode(node, moveId) {
    if (nodeMoveId(node) === moveId) return node;
    for (const child of node.children) {
      const found = findNode(child, moveId);
      if (found) return found;
    }
    return null;
  }

  // Hover-to-auto-expand: while a drag is over a node for ~600ms straight,
  // reveal its children so a nested drop target becomes reachable without
  // pre-expanding it manually (mirrors the file-explorer/IDE convention).
  // Deliberately does not shrink dragExpandIds back down on drop/cancel —
  // once a container's been revealed this way it stays open, same as if
  // the reader had expanded it by hand.
  function clearDragOverTimer() {
    clearTimeout(dragOverTimerRef.current);
    dragOverTimerRef.current = null;
    dragOverIdRef.current = null;
  }

  function handleDragOver(event) {
    const overId = event.over ? String(event.over.id) : null;
    if (overId === dragOverIdRef.current) return;
    clearDragOverTimer();
    dragOverIdRef.current = overId;
    if (!overId) return;
    dragOverTimerRef.current = setTimeout(() => {
      setDragExpandIds((prev) => (prev.has(overId) ? prev : new Set(prev).add(overId)));
    }, 600);
  }

  // A node's current children, in the same {item_key, item_page,
  // item_occurrence_index} shape handleDragEnd already builds for
  // ordered_siblings — reused as-is for "add as child," just appended to
  // (by the caller, once the new item's own identity is known) instead of
  // appending an already-existing dragged item. findNode only searches
  // container nodes (node.children), never individual leaf occurrences
  // (node.leaves) — a LeafRow's own moveId naturally resolves to `null`
  // here, so it falls through to an empty list; the client's tree model
  // doesn't track per-occurrence children separately from the node's own,
  // and inserting under a specific occurrence is still a perfectly valid
  // move target regardless.
  function handleAddChildClick(targetMoveId, targetDescriptor) {
    if (!onAddItem) return;
    const targetNode = findNode(tree, targetMoveId);
    const existingChildren = (targetNode ? targetNode.children : []).map((c) => {
      const p = parseMoveId(nodeMoveId(c));
      return { item_key: p.key, item_page: p.page, item_occurrence_index: p.occurrenceIndex };
    });
    onAddItem({ targetDescriptor, existingChildren });
  }

  function showDropFeedback(message) {
    setDropFeedback(message);
    clearTimeout(dropFeedbackTimerRef.current);
    dropFeedbackTimerRef.current = setTimeout(() => setDropFeedback(null), 2500);
  }

  function handleDragEnd(event) {
    clearDragOverTimer();
    const { active, over } = event;
    if (!tree || !onMoveItem || !over) return;
    const itemMoveId = String(active.id);
    const newParentMoveId = String(over.id); // '' = root drop zone
    if (itemMoveId === newParentMoveId) { showDropFeedback("Can't drop an item onto itself"); return; }

    // Client-side cycle guard, mirroring the server's own authoritative
    // check: don't allow dropping a node onto itself or a node already
    // nested under it.
    const draggedNode = findNode(tree, itemMoveId);
    if (draggedNode && findNode(draggedNode, newParentMoveId)) { showDropFeedback("Can't move a section into itself"); return; }

    const targetNode = newParentMoveId ? findNode(tree, newParentMoveId) : tree;
    if (!targetNode) { showDropFeedback('Drop target no longer exists'); return; }
    const currentChildren = targetNode.children.map((c) => parseMoveId(nodeMoveId(c)));
    if (targetNode.children.some((c) => nodeMoveId(c) === itemMoveId)) { showDropFeedback('Already there'); return; } // already a child here — no-op

    const { key: itemKey, page: itemPage, occurrenceIndex: itemOccurrenceIndex } = parseMoveId(itemMoveId);
    const { key: newParentKey, page: newParentPage, occurrenceIndex: newParentOccurrenceIndex } = parseMoveId(newParentMoveId);
    const orderedSiblings = [
      ...currentChildren.map((c) => ({ item_key: c.key, item_page: c.page, item_occurrence_index: c.occurrenceIndex })),
      { item_key: itemKey, item_page: itemPage, item_occurrence_index: itemOccurrenceIndex },
    ];
    onMoveItem({
      itemKey, itemPage, itemOccurrenceIndex,
      newParentKey, newParentPage, newParentOccurrenceIndex,
      orderedSiblings,
    });
  }

  if (!tree || tree.children.length === 0) {
    return (
      <div className="structure-tree-empty" data-testid="structure-tree-empty">
        No document structure detected yet.
      </div>
    );
  }

  return (
    <div className="structure-tree-wrap">
      <div className="structure-tree-toolbar">
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={() => setExpandSignal((s) => s + 1)}
          data-testid="structure-expand-all"
        >
          <i className="fa-solid fa-angles-down" /> Expand all
        </button>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={() => setCollapseSignal((s) => s + 1)}
          data-testid="structure-collapse-all"
        >
          <i className="fa-solid fa-angles-up" /> Collapse all
        </button>
      </div>
      {dropFeedback && (
        <div className="structure-tree-toast" data-testid="structure-tree-toast">{dropFeedback}</div>
      )}
      <DndContext
        sensors={sensors}
        onDragStart={clearDragOverTimer}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
        onDragCancel={clearDragOverTimer}
      >
        <ul className="structure-tree" data-testid="structure-tree">
          {onMoveItem && <RootDropZone />}
          {tree.children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={0}
              onSelect={onSelect}
              onSelectContainer={onSelectContainer}
              onOpenReference={onOpenReference}
              onJumpContainer={onJumpContainer}
              rangeIndex={rangeIndex}
              activePathIds={activePathIds}
              currentNodeId={currentNodeId}
              onEditItem={onEditItem}
              onDeleteItem={onDeleteItem}
              onAddChild={onAddItem ? handleAddChildClick : undefined}
              collapseSignal={collapseSignal}
              expandSignal={expandSignal}
              dragExpandIds={dragExpandIds}
            />
          ))}
        </ul>
      </DndContext>
    </div>
  );
}
