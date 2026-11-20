import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export const SearchPluginKey = new PluginKey('searchHighlight');

export function getSearchDecorations(doc, query, activeIndex = 0) {
  if (!query || !query.trim()) {
    return { decorations: DecorationSet.empty, matchCount: 0 };
  }

  const q = query.trim().toLowerCase();
  const decorations = [];
  let matchCount = 0;

  doc.descendants((node, pos) => {
    if (node.isText && node.text) {
      const text = node.text.toLowerCase();
      let index = 0;
      while ((index = text.indexOf(q, index)) !== -1) {
        const from = pos + index;
        const to = from + q.length;
        const isActive = matchCount === activeIndex;

        decorations.push(
          Decoration.inline(from, to, {
            class: isActive ? 'search-match search-match-active' : 'search-match',
            'data-search-index': String(matchCount),
          })
        );
        matchCount++;
        index += q.length;
      }
    }
  });

  return {
    decorations: DecorationSet.create(doc, decorations),
    matchCount,
  };
}

export const SearchHighlightExtension = Extension.create({
  name: 'searchHighlight',

  addOptions() {
    return {
      searchQuery: '',
      activeMatchIndex: 0,
    };
  },

  addProseMirrorPlugins() {
    const extension = this;

    return [
      new Plugin({
        key: SearchPluginKey,
        state: {
          init(_, state) {
            const { decorations, matchCount } = getSearchDecorations(
              state.doc,
              extension.options.searchQuery,
              extension.options.activeMatchIndex
            );
            return {
              query: extension.options.searchQuery || '',
              activeIndex: extension.options.activeMatchIndex || 0,
              decorations,
              matchCount,
            };
          },
          apply(tr, prev, oldState, newState) {
            const meta = tr.getMeta(SearchPluginKey);
            let query = prev.query;
            let activeIndex = prev.activeIndex;

            if (meta !== undefined) {
              if (meta.query !== undefined) query = meta.query;
              if (meta.activeIndex !== undefined) activeIndex = meta.activeIndex;
            }

            if (meta !== undefined || tr.docChanged || query !== prev.query || activeIndex !== prev.activeIndex) {
              const { decorations, matchCount } = getSearchDecorations(newState.doc, query, activeIndex);
              return { query, activeIndex, decorations, matchCount };
            }
            return prev;
          },
        },
        props: {
          decorations(state) {
            return this.getState(state)?.decorations || DecorationSet.empty;
          },
        },
      }),
    ];
  },
});
