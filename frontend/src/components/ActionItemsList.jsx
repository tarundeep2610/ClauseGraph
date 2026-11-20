import { useMemo, useState } from 'react';

// Flat list (no tree/drag-drop — action items are independent page-anchored
// leaves, not a hierarchy) with type-filter pills and a sort-mode toggle.
// Deliberately much simpler than StructureTree.jsx for the same reason
// actionItemStore.js is simpler than referenceStore.js.

const TYPE_LABEL = { deadline: 'Deadline', obligation: 'Obligation', condition: 'Condition', other: 'Other' };
const TYPE_ICON = {
  deadline: 'fa-calendar-days',
  obligation: 'fa-clipboard-check',
  condition: 'fa-code-branch',
  other: 'fa-circle-question',
};
const FILTER_ORDER = ['deadline', 'obligation', 'condition', 'other'];

function ActionItemRow({ item, active, onSelect }) {
  const [expanded, setExpanded] = useState(false);
  const typeKey = TYPE_LABEL[item.item_type] ? item.item_type : 'other';

  let timingLabel = null;
  if (item.date_text) {
    timingLabel = item.date_text;
  } else if (item.anchor_event) {
    timingLabel = `relative to: ${item.anchor_event}`;
  } else if (item.cadence) {
    timingLabel = `recurring: ${item.cadence}`;
  }

  return (
    <li
      className={`action-item-row${active ? ' active' : ''}`}
      data-testid={`action-item-row-${item.page}-${item.occurrence_index}`}
    >
      <button
        type="button"
        className="action-item-row-main"
        onClick={() => onSelect({ page: item.page, anchor_id: item.anchor_id })}
        title={`Jump to page ${item.page}`}
      >
        <span className={`action-item-type-icon action-item-type-${typeKey}`}>
          <i className={`fa-solid ${TYPE_ICON[typeKey]}`} />
        </span>
        <span className="action-item-row-body">
          <span className="action-item-text">{item.item_text}</span>
          <span className="action-item-meta">
            {timingLabel && <span className="action-item-pill action-item-pill-date">{timingLabel}</span>}
            {item.responsible_party && (
              <span className="action-item-pill action-item-pill-party">{item.responsible_party}</span>
            )}
            <span className="action-item-pill action-item-pill-page">Page {item.page}</span>
          </span>
        </span>
      </button>
      {item.evidence_quote && (
        <div className="action-item-evidence">
          <button
            type="button"
            className="action-item-evidence-toggle"
            onClick={() => setExpanded((v) => !v)}
          >
            <i className={`fa-solid ${expanded ? 'fa-chevron-down' : 'fa-chevron-right'}`} /> Evidence
          </button>
          {expanded && <blockquote className="action-item-evidence-quote">{item.evidence_quote}</blockquote>}
        </div>
      )}
    </li>
  );
}

export default function ActionItemsList({ items = [], sortMode = 'page', onSortModeChange, activePage, onSelect, typeCounts }) {
  const [filterType, setFilterType] = useState('all');

  const availableFilters = useMemo(
    () => FILTER_ORDER.filter((t) => (typeCounts?.[t] || 0) > 0),
    [typeCounts],
  );

  const filtered = useMemo(
    () => (filterType === 'all' ? items : items.filter((it) => it.item_type === filterType)),
    [items, filterType],
  );

  if (!items.length) return null;

  return (
    <div className="action-items-list">
      <div className="action-items-toolbar">
        <div className="action-items-filter-pills">
          <button
            type="button"
            className={`action-items-pill${filterType === 'all' ? ' active' : ''}`}
            onClick={() => setFilterType('all')}
          >
            All ({typeCounts?.all ?? items.length})
          </button>
          {availableFilters.map((t) => (
            <button
              key={t}
              type="button"
              className={`action-items-pill${filterType === t ? ' active' : ''}`}
              onClick={() => setFilterType(t)}
            >
              {TYPE_LABEL[t]} ({typeCounts[t]})
            </button>
          ))}
        </div>
        <div className="action-items-sort-toggle">
          <button
            type="button"
            className={`btn btn-sm btn-secondary${sortMode === 'page' ? ' active' : ''}`}
            onClick={() => onSortModeChange && onSortModeChange('page')}
            title="Sort by document order"
          >
            Page order
          </button>
          <button
            type="button"
            className={`btn btn-sm btn-secondary${sortMode === 'date' ? ' active' : ''}`}
            onClick={() => onSortModeChange && onSortModeChange('date')}
            title="Sort dated items chronologically"
          >
            Chronological
          </button>
        </div>
      </div>
      <ul className="action-items-rows">
        {filtered.map((item) => (
          <ActionItemRow
            key={`${item.page}-${item.occurrence_index}`}
            item={item}
            active={item.page === activePage}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </div>
  );
}
