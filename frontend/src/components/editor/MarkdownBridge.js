import StarterKit from '@tiptap/starter-kit';
import TextAlign from '@tiptap/extension-text-align';
import { TableKit } from '@tiptap/extension-table';
import Placeholder from '@tiptap/extension-placeholder';
import { Markdown } from 'tiptap-markdown';
import DOMPurify from 'dompurify';

// Centralizes the TipTap <-> Markdown wiring so WysiwygEditor.jsx doesn't need
// to know TipTap extension internals, and so the exact same extension set is
// used everywhere an editor instance is created (avoids two call sites
// drifting out of sync on e.g. table or heading-level support).
//
// Ingestion: a plain Markdown string passed as `content` (or via
// `editor.commands.setContent(markdownString)`) is parsed into the TipTap
// document automatically by the Markdown extension below — see
// node_modules/tiptap-markdown's `setContent`/`insertContentAt` command
// overrides, which run every string through its own markdown parser rather
// than treating it as HTML.
//
// Persistence: `getMarkdown(editor)` serializes the live TipTap document back
// to a clean Markdown string (GFM tables included, via TableKit's markdown
// spec) for autosave.
export function buildEditorExtensions({ placeholder } = {}) {
  return [
    StarterKit.configure({
      // The Markdown extension below handles our own undo/redo-friendly
      // serialization; StarterKit's defaults for everything else (headings
      // H1-H3 and beyond, lists, blockquote, code block, history, and —
      // as of TipTap v3 — underline) are fine.
    }),
    TextAlign.configure({ types: ['heading', 'paragraph'] }),
    TableKit.configure({
      table: { resizable: true },
    }),
    Placeholder.configure({
      placeholder: placeholder || 'Start typing…',
    }),
    Markdown.configure({
      // The OCR backend's layout formatter (layout_format.py's
      // _render_column_run) legitimately embeds raw HTML <table> blocks in
      // formatted_text for side-by-side column layouts — html:false blocked
      // ALL raw HTML indiscriminately, which also broke those legitimate
      // tables (rendered as literal escaped tag text instead of a table).
      // html:true lets TipTap's parser turn them into real nodes via the
      // registered Table/TableKit extensions above. sanitizeEmbeddedHtml()
      // below is what keeps this safe against untrusted OCR'd content —
      // never pass raw `value`/markdown straight to the editor without it.
      html: true,
      tightLists: true,
      linkify: false,
      transformPastedText: true,
      transformCopiedText: true,
    }),
  ];
}

// The only raw-HTML construct the OCR backend ever generates (confirmed by
// grepping layout_format.py for every <table>/<tr>/<td> producer) is a
// column-run's <table>...</table> block — OCR'd text inside those cells is
// NOT pre-escaped server-side (see layout_format._format_block_text), so
// sanitizing here is the only thing standing between untrusted OCR'd content
// and the editor's DOM once html:true is enabled. Matches the old read-only
// renderer's model (frontend/src/utils/markdown.js: DOMPurify.sanitize AFTER
// allowing HTML through) but scoped to just the <table> blocks rather than
// the whole markdown string, so normal markdown syntax that happens to
// contain `<`/`>` (e.g. a GFM autolink `<http://...>`) is never at risk of
// being reparsed/mangled by DOMPurify.
const RAW_HTML_TABLE_RE = /<table\b[\s\S]*?<\/table>/gi;

export function sanitizeEmbeddedHtml(markdown) {
  if (!markdown) return markdown;
  return markdown.replace(RAW_HTML_TABLE_RE, (block) => DOMPurify.sanitize(block));
}

export function getMarkdown(editor) {
  if (!editor) return '';
  return editor.storage.markdown.getMarkdown();
}

// Markdown round-trips (parse -> serialize) can introduce harmless
// whitespace differences (e.g. a trailing newline) that would otherwise
// trip a naive equality check into firing a spurious autosave on load.
// Both sides of every comparison in WysiwygEditor.jsx go through this.
export function normalizeForComparison(markdown) {
  return (markdown || '').replace(/\s+$/, '').replace(/\r\n/g, '\n');
}
