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
const OWN_LABEL_FAMILIES = new Set(['numheading', 'letterheading', 'subdoc', 'manual']);

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

// Applies drag-and-drop hierarchy overrides on top of the just-built tree —
// per the plan's Part C2, parent/order overrides never rewrite scope_chain
// server-side; they're plain data (GET .../structure-hierarchy-overrides:
// {parents: {item_key: new_parent_key}, order: {scope_key: [item_key,...]}})
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
// Move identity is the node's bare key (matching what compute_scope_ranges
// and the rest of this tree already key nodes by) — NOT disambiguated by
// page/occurrence_index. Accepted v1 limitation: if the exact same bare key
// appears more than once in the document (most often a repeated leaf
// heading like "Clause 4.1" under two different Annexures), a move/drop
// targeting that key applies to whichever occurrence this function's index
// pass visits first, not necessarily the one the reader clicked. Container-
// tier keys (Annexure/Article/Section/...) essentially never collide in
// practice, so this mainly matters for leaf-level reparenting of a
// genuinely repeated key — a rare case, not silently corrupting anything.
function applyHierarchyOverrides(root, parentOverrides) {
  if (!parentOverrides || Object.keys(parentOverrides).length === 0) return root;

  const nodeByKey = new Map();
  (function index(node) {
    if (node !== root) nodeByKey.set(node.key, node);
    for (const child of node.children.values()) index(child);
  })(root);

  for (const [itemKey, newParentKey] of Object.entries(parentOverrides)) {
    const node = nodeByKey.get(itemKey);
    if (!node) continue; // orphaned (moved item no longer exists) — inert
    const newParent = newParentKey ? nodeByKey.get(newParentKey) : root;
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
    newParent.children.set(itemKey, node);
  }

  return root;
}

// Post-order: turns each node's children Map into an array, sorted by a
// manual sortOverrides order when the reader has drag-and-drop-reordered
// this node's children, falling back to first-occurrence page (ascending)
// for any sibling not yet manually positioned — appended after the
// manually-ordered ones, in their own stable page order.
function finalize(node, sortOverrides) {
  const children = Array.from(node.children.values()).map((c) => finalize(c, sortOverrides));
  const leaves = [...node.leaves].sort(
    (a, b) => (a.page || 0) - (b.page || 0) || (a.occurrence_index || 0) - (b.occurrence_index || 0)
  );
  const minPage = Math.min(
    Infinity,
    ...leaves.map((l) => l.page ?? Infinity),
    ...children.map((c) => c.minPage)
  );
  const order = sortOverrides?.[node.key]; // node.key === '' for root, matching the backend's "" = root convention
  if (order && order.length) {
    const rank = new Map(order.map((k, i) => [k, i]));
    children.sort((a, b) => {
      const ra = rank.has(a.key) ? rank.get(a.key) : Infinity;
      const rb = rank.has(b.key) ? rank.get(b.key) : Infinity;
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
  chained_citation: 'chained citation to another clause/section',
  other_doc_marker: 'mentions another document (GCC/SCC/Vol...)',
  range_shaped_continuation: 'range-shaped ("... TO N") continuation, likely a running header',
  same_page_repeat: 'repeats an already-seen sub-document trigger on this page',
  non_heading_layout_type: "the OCR model's own layout tag says this is body text, not a heading",
  layout_header: "the OCR model's own layout tag marks this as a header",
  layout_title: "the OCR model's own layout tag marks this as a title",
  ai_unlocated: "the AI-reported heading text couldn't be matched to this page's real content",
  ai_unresolved_parent_ref: "the AI's stated parent heading couldn't be resolved; nested by its reported level instead",
  ai_fuzzy_parent_ref: "the AI's stated parent heading was matched approximately, not exactly",
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
  const spans = ranges.map((r) => (r.start_page === r.end_page ? `${r.start_page}` : `${r.start_page}–${r.end_page}`));
  if (ranges.length === 1 && ranges[0].start_page === ranges[0].end_page) return '';
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

function TreeNode({ node, depth, onSelect, onSelectContainer, rangeIndex, activePathIds, currentNodeId, onEditItem, onDeleteItem }) {
  const [expanded, setExpanded] = useState(depth === 0);
  const isCurrent = node.id === currentNodeId;
  const headerRef = useRef(null);
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: node.key });

  // Forces this node open when it becomes part of the active-page's
  // ancestor chain — but only in reaction to a genuinely new activePathIds
  // (a fresh activePage/scopeRanges computation), never on every render, so
  // a reader who manually collapses a node keeps it collapsed until the
  // active page actually changes again.
  useEffect(() => {
    if (activePathIds?.has(node.id)) setExpanded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePathIds]);

  // Scrolls the current node into view whenever it newly becomes current —
  // not on every render, and not for any other node.
  useEffect(() => {
    if (isCurrent) headerRef.current?.scrollIntoView({ block: 'nearest' });
  }, [isCurrent]);

  if (node.children.length === 0 && node.leaves.length === 0) return null;

  // Only container-tier nodes (Annexure/Article/Section — anything Part B's
  // compute_scope_ranges ever tracked) have a page span worth fetching as
  // its own chunk; a bare leaf-holding node (e.g. "Clause 4.1") never got
  // pushed onto nested_stack, so it has no range entry and gets no button.
  const ranges = rangeIndex?.get(node.key);
  const itemDescriptor = containerItemDescriptor(node, rangeIndex);

  return (
    <li
      className={`structure-tree-node${isCurrent ? ' structure-tree-node--current' : ''}`}
      data-testid={`structure-node-${node.id}`}
    >
      <div
        ref={(el) => { headerRef.current = el; setDropRef(el); }}
        className={`structure-tree-node-header${isOver ? ' structure-tree-node-header--drop-over' : ''}`}
        style={{ paddingLeft: depth * 16 }}
        onClick={() => setExpanded((e) => !e)}
      >
        <DragHandle id={node.key} testId={`structure-drag-handle-${node.id}`} />
        <i className={`fa-solid ${expanded ? 'fa-chevron-down' : 'fa-chevron-right'}`} />
        <span>
          {node.label}
          <span className="structure-tree-node-range">{formatRangeSuffix(node.key, rangeIndex)}</span>
        </span>
        <span className="structure-tree-node-actions">
          {ranges && ranges.length > 0 && onSelectContainer && (
            <button
              className="structure-tree-node-jump"
              onClick={(e) => {
                e.stopPropagation();
                // Disjoint ranges (a container that closed and reopened later)
                // pick the earliest occurrence — a reasonable default for "take
                // me there" with no further disambiguation UI.
                const r = ranges[0];
                onSelectContainer({ startPage: r.start_page, endPage: r.end_page, label: node.label });
              }}
              title={`View just this section (pages ${ranges[0].start_page}–${ranges[0].end_page})`}
              data-testid={`structure-jump-${node.id}`}
            >
              <i className="fa-solid fa-up-right-from-square" />
            </button>
          )}
          {itemDescriptor && onEditItem && (
            <button
              className="structure-tree-node-edit"
              onClick={(e) => { e.stopPropagation(); onEditItem(itemDescriptor); }}
              title="Edit heading / pages"
              data-testid={`structure-edit-${node.id}`}
            >
              <i className="fa-solid fa-pen" />
            </button>
          )}
          {itemDescriptor && onDeleteItem && (
            <button
              className="structure-tree-node-delete"
              onClick={(e) => { e.stopPropagation(); onDeleteItem(itemDescriptor); }}
              title="Delete this entry"
              data-testid={`structure-delete-${node.id}`}
            >
              <i className="fa-solid fa-trash" />
            </button>
          )}
        </span>
      </div>
      {expanded && (
        <ul className="structure-tree-children">
          {node.leaves.map((leaf) => {
            const lowConfidence = leaf.heading_confidence === 'low';
            const leafDescriptor = leafItemDescriptor(leaf);
            return (
              <LeafRow
                key={leaf.anchor_id}
                leaf={leaf}
                depth={depth + 1}
                lowConfidence={lowConfidence}
                onSelect={onSelect}
                onEditItem={onEditItem}
                onDeleteItem={onDeleteItem}
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
              rangeIndex={rangeIndex}
              activePathIds={activePathIds}
              currentNodeId={currentNodeId}
              onEditItem={onEditItem}
              onDeleteItem={onDeleteItem}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function LeafRow({ leaf, depth, lowConfidence, onSelect, onEditItem, onDeleteItem, leafDescriptor }) {
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: leaf.key });
  return (
    <li
      ref={setDropRef}
      className={`structure-tree-leaf${lowConfidence ? ' structure-tree-leaf--low-confidence' : ''}${isOver ? ' structure-tree-leaf--drop-over' : ''}`}
      style={{ paddingLeft: depth * 16 }}
      onClick={() => onSelect(leaf)}
      data-testid={`structure-leaf-${leaf.anchor_id}`}
      title={lowConfidence ? reasonsTooltip(leaf.heading_confidence_reasons) : undefined}
    >
      <DragHandle id={leaf.key} testId={`structure-drag-handle-leaf-${leaf.anchor_id}`} />
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
  referenceStore, onSelect, onSelectContainer, scopeRanges, activePage,
  onEditItem, onDeleteItem, onMoveItem, parentOverrides, sortOverrides,
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );

  const tree = useMemo(() => {
    if (!referenceStore) return null;
    const raw = buildTree(getStructureCandidates(referenceStore));
    const reparented = applyHierarchyOverrides(raw, parentOverrides);
    return finalize(reparented, sortOverrides);
    // referenceStore is a stable ref mutated in place (see Dashboard.jsx) —
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

  // Finds a node's CURRENT children key list (already reparented/sorted) —
  // used to build the full `orderedSiblings` payload a move sends, so the
  // server always receives a complete replacement list, never a diff.
  function findNode(node, key) {
    if (node.key === key) return node;
    for (const child of node.children) {
      const found = findNode(child, key);
      if (found) return found;
    }
    return null;
  }

  function handleDragEnd(event) {
    const { active, over } = event;
    if (!tree || !onMoveItem || !over) return;
    const itemKey = String(active.id);
    const newParentKey = String(over.id); // '' = root drop zone
    if (itemKey === newParentKey) return;

    // Client-side cycle guard, mirroring the server's own authoritative
    // check: don't allow dropping a node onto itself or a node already
    // nested under it.
    const draggedNode = findNode(tree, itemKey);
    if (draggedNode && findNode(draggedNode, newParentKey)) return;

    const targetNode = newParentKey ? findNode(tree, newParentKey) : tree;
    if (!targetNode) return;
    const currentChildKeys = targetNode.children.map((c) => c.key);
    if (currentChildKeys.includes(itemKey)) return; // already a child here — no-op
    onMoveItem({ itemKey, newParentKey, orderedSiblings: [...currentChildKeys, itemKey] });
  }

  if (!tree || tree.children.length === 0) {
    return (
      <div className="structure-tree-empty" data-testid="structure-tree-empty">
        No document structure detected yet.
      </div>
    );
  }

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      <ul className="structure-tree" data-testid="structure-tree">
        {onMoveItem && <RootDropZone />}
        {tree.children.map((child) => (
          <TreeNode
            key={child.id}
            node={child}
            depth={0}
            onSelect={onSelect}
            onSelectContainer={onSelectContainer}
            rangeIndex={rangeIndex}
            activePathIds={activePathIds}
            currentNodeId={currentNodeId}
            onEditItem={onEditItem}
            onDeleteItem={onDeleteItem}
          />
        ))}
      </ul>
    </DndContext>
  );
}
