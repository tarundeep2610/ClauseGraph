import { useEffect, useRef, useState } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import EditorToolbar from './EditorToolbar';
import { buildEditorExtensions, getMarkdown, normalizeForComparison, sanitizeEmbeddedHtml } from './MarkdownBridge';
import { SearchPluginKey } from './SearchHighlightExtension';

const AUTOSAVE_DEBOUNCE_MS = 800;

// Word-style rich editor over a plain Markdown string. Ingests/persists
// Markdown directly (see MarkdownBridge.js) so the backend never sees or
// needs to know about TipTap's internal document format — callers pass the
// same `pages_data[i].text` string they always have, and get the same kind
// of string back via `onChange`.
//
// `onNodeFocused(bbox)` is accepted but currently never fires with a real
// bbox: the OCR backend does not yet persist per-block bounding boxes
// (confirmed against database.py's ocr_pages schema), so there is nothing to
// attach `data-bbox` attributes from. The DOM wiring below is left in place,
// inert, so turning this on later is a backend-plus-one-line-of-wiring
// change here rather than a new feature from scratch.
export default function WysiwygEditor({
  value,
  onChange,
  editable = true,
  placeholder,
  onNodeFocused,
  onSaveStatusChange,
  zoomLevel = 1,
  searchQuery = '',
  searchMatchIndex = -1,
}) {
  const [sourceMode, setSourceMode] = useState(false);
  const [sourceText, setSourceText] = useState(value || '');
  const [saveStatus, setSaveStatus] = useState('saved'); // 'saved' | 'pending' | 'saving'
  const saveTimerRef = useRef(null);
  const lastEmittedRef = useRef(normalizeForComparison(value));
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  function updateSaveStatus(status) {
    setSaveStatus(status);
    if (onSaveStatusChange) onSaveStatusChange(status);
  }

  const editor = useEditor({
    extensions: buildEditorExtensions({ placeholder }),
    // html:true (see MarkdownBridge.js) means this is the one thing standing
    // between untrusted OCR'd content and the editor's DOM — never pass
    // `value` straight through.
    content: sanitizeEmbeddedHtml(value) || '',
    editable: editable && !sourceMode,
    immediatelyRender: false,
    // TipTap v3 defaults this to false (a perf-oriented change from v2) —
    // without it, EditorToolbar's editor.isActive(...) checks never see a
    // fresh value after a transaction (e.g. toggling Bold), since nothing
    // re-renders this component to re-evaluate them.
    shouldRerenderOnTransaction: true,
    onUpdate: ({ editor: ed }) => {
      updateSaveStatus('pending');
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        const md = getMarkdown(ed);
        lastEmittedRef.current = normalizeForComparison(md);
        updateSaveStatus('saving');
        Promise.resolve(onChangeRef.current && onChangeRef.current(md)).finally(() => {
          updateSaveStatus('saved');
        });
      }, AUTOSAVE_DEBOUNCE_MS);
    },
  });

  // Re-sync when a different page/document's text arrives from outside
  // (navigation, poll refresh) — but never clobber the user's own in-flight
  // edit: skip if this is just the echo of what we ourselves last emitted.
  useEffect(() => {
    const incoming = normalizeForComparison(value);
    if (incoming === lastEmittedRef.current) return;
    lastEmittedRef.current = incoming;
    setSourceText(value || '');
    if (editor && !editor.isDestroyed) {
      // emitUpdate: false — this is TipTap loading an external value, not a
      // user edit. Without it, TipTap v3's onUpdate still fires for a
      // programmatic setContent, re-arming the autosave debounce and
      // PATCHing this same (possibly stale-from-a-racing-poll) content back
      // to the server as if the user had typed it — harmless when the
      // incoming text is genuinely new, but actively wrong if a slow poll
      // response race ever delivers text older than what the user already
      // saved: emitUpdate:false stops that from re-propagating as a save.
      editor.commands.setContent(sanitizeEmbeddedHtml(value) || '', { emitUpdate: false });
    }
    updateSaveStatus('saved');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  useEffect(() => () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
  }, []);

  // Synchronize external search query and active match index to TipTap's ProseMirror decorations
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    try {
      if (!editor.view) return;
      const pluginState = SearchPluginKey.getState(editor.state);
      const targetQuery = searchQuery || '';
      const targetIndex = searchMatchIndex ?? -1;

      if (pluginState && (pluginState.query !== targetQuery || pluginState.activeIndex !== targetIndex)) {
        const tr = editor.state.tr.setMeta(SearchPluginKey, {
          query: targetQuery,
          activeIndex: targetIndex,
        });
        tr.setMeta('addToHistory', false);
        editor.view.dispatch(tr);

        if (targetQuery && targetIndex >= 0) {
          requestAnimationFrame(() => {
            try {
              if (editor && !editor.isDestroyed && editor.view?.dom) {
                const activeEl = editor.view.dom.querySelector('.search-match-active');
                if (activeEl) {
                  activeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
              }
            } catch (e) {
              // Ignore if unmounted during animation frame
            }
          });
        }
      }
    } catch (err) {
      // Editor view may not be mounted yet
    }
  }, [editor, searchQuery, searchMatchIndex]);

  useEffect(() => {
    if (!editor || !onNodeFocused) return undefined;
    const dom = editor.view.dom;
    function handleMouseOver(e) {
      const target = e.target.closest ? e.target.closest('[data-bbox]') : null;
      if (!target) return;
      try {
        const bbox = JSON.parse(target.getAttribute('data-bbox'));
        onNodeFocused(bbox);
      } catch (err) {
        // malformed/missing data-bbox — never let a hover handler throw
      }
    }
    dom.addEventListener('mouseover', handleMouseOver);
    return () => dom.removeEventListener('mouseover', handleMouseOver);
  }, [editor, onNodeFocused]);

  function handleToggleSource() {
    if (!sourceMode) {
      // Entering source mode: snapshot the editor's current markdown.
      setSourceText(editor ? getMarkdown(editor) : (value || ''));
      setSourceMode(true);
      return;
    }
    // Leaving source mode: push any hand-edited raw Markdown back into the
    // rich editor (and the same debounced-save path as a normal edit).
    if (editor && !editor.isDestroyed) {
      editor.commands.setContent(sanitizeEmbeddedHtml(sourceText));
    }
    setSourceMode(false);
    updateSaveStatus('pending');
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      lastEmittedRef.current = normalizeForComparison(sourceText);
      updateSaveStatus('saving');
      Promise.resolve(onChangeRef.current && onChangeRef.current(sourceText)).finally(() => {
        updateSaveStatus('saved');
      });
    }, AUTOSAVE_DEBOUNCE_MS);
  }

  const statusLabel = saveStatus === 'saved' ? '✓ All Saved'
    : saveStatus === 'saving' ? '● Saving…'
    : '● Unsaved changes';

  return (
    <div className="wysiwyg-editor" data-testid="wysiwyg-editor">
      <div className="editor-header">
        <EditorToolbar editor={editor} sourceMode={sourceMode} onToggleSource={handleToggleSource} />
        <span className={`editor-save-status editor-save-status--${saveStatus}`} data-testid="editor-save-status">
          {statusLabel}
        </span>
      </div>
      {sourceMode ? (
        <textarea
          className="editor-source-view"
          data-testid="editor-source-textarea"
          value={sourceText}
          style={{ fontSize: `${(0.82 * zoomLevel).toFixed(3)}rem` }}
          onChange={(e) => {
            setSourceText(e.target.value);
            setSaveStatus('pending');
          }}
        />
      ) : (
        <EditorContent
          editor={editor}
          className="editor-content"
          data-testid="editor-content"
          style={{ fontSize: `${(0.92 * zoomLevel).toFixed(3)}rem` }}
        />
      )}
    </div>
  );
}
