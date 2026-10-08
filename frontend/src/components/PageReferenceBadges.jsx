import { useEffect, useState } from 'react';
import { resolveMention, getPageScopeKey } from '../utils/referenceStore';
import PageReferenceCandidatesModal from './PageReferenceCandidatesModal';

// Ranks a mention's candidates by proximity to the reader's current
// position: a candidate sharing the reader's own current scope sorts first,
// then the physically nearest page. Exported as its own small, testable unit
// rather than an inline comparator.
export function sortCandidates(list, currentPage, currentScopeKey) {
  return [...list].sort((a, b) => {
    const aExact = currentScopeKey != null && a.scope_key === currentScopeKey ? 2 : 0;
    const bExact = currentScopeKey != null && b.scope_key === currentScopeKey ? 2 : 0;
    const aRoot =
      currentScopeKey != null &&
      a.scope_key != null &&
      a.scope_key.split('/')[0] === currentScopeKey.split('/')[0]
        ? 1
        : 0;
    const bRoot =
      currentScopeKey != null &&
      b.scope_key != null &&
      b.scope_key.split('/')[0] === currentScopeKey.split('/')[0]
        ? 1
        : 0;
    const aTier = aExact || aRoot;
    const bTier = bExact || bRoot;
    if (aTier !== bTier) return bTier - aTier;
    const aDist = Math.abs((a.page || 0) - currentPage);
    const bDist = Math.abs((b.page || 0) - currentPage);
    if (aDist !== bDist) return aDist - bDist;
    return (a.page || 0) - (b.page || 0);
  });
}

// Top-right badge list for the PDF pane: one pill per resolvable mention on
// the current page (e.g. "Annexure A-1", "para 2"). A single-candidate
// mention opens the target directly; a multi-candidate one opens
// PageReferenceCandidatesModal to pick among them (a small anchored dropdown
// was tried first but was too cramped to read comfortably).
export default function PageReferenceBadges({ mentions, referenceStore, scopeRanges, currentPage, onOpenReference }) {
  const [candidatesTarget, setCandidatesTarget] = useState(null);

  // Navigating away shouldn't leave a stale picker open over the new page.
  useEffect(() => {
    setCandidatesTarget(null);
  }, [currentPage]);

  if (!mentions || mentions.length === 0) return null;

  function handleBadgeClick(mention) {
    if (mention.undefined) return;
    const { exact, approximate } = resolveMention(referenceStore, mention.key);
    if (exact.length + approximate.length === 0) return;
    // Only an EXACT single candidate is safe to auto-open — a candidate
    // reached only via the cross-family fallback is a guess, even when it's
    // the only one, and must still be confirmed. This is the direct fix for
    // a confirmed live failure: "clause 9.0 SCC" (payment terms) had exactly
    // one fallback candidate, "clause 9.0 ITB" (site conditions, a different
    // bidding sub-document) — an unlucky single guess that auto-opened with
    // no chance to catch the mistake before navigating to the wrong page.
    if (exact.length === 1 && approximate.length === 0) {
      onOpenReference(exact[0]);
      return;
    }
    const currentScopeKey = referenceStore ? getPageScopeKey(referenceStore, currentPage, scopeRanges) : null;
    setCandidatesTarget({
      mentionRaw: mention.raw,
      exact: sortCandidates(exact, currentPage, currentScopeKey),
      approximate: sortCandidates(approximate, currentPage, currentScopeKey),
    });
  }

  function handleSelect(candidate) {
    onOpenReference(candidate);
    setCandidatesTarget(null);
  }

  return (
    <div className="page-reference-badges" data-testid="page-reference-badges">
      {mentions.map((mention) => (
        <button
          key={mention.key}
          type="button"
          className={`page-reference-badge${
            mention.undefined
              ? ' page-reference-badge--undefined'
              : mention.approximate
                ? ' page-reference-badge--approximate'
                : ''
          }`}
          disabled={Boolean(mention.undefined)}
          onClick={() => handleBadgeClick(mention)}
          data-testid={`page-reference-badge-${mention.key}`}
        >
          {mention.raw}
        </button>
      ))}
      <PageReferenceCandidatesModal target={candidatesTarget} onClose={() => setCandidatesTarget(null)} onSelect={handleSelect} />
    </div>
  );
}
