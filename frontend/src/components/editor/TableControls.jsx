// Shown in EditorToolbar only while the caret is inside a table — a compact
// set of structural edits (TipTap's table extension ships these commands
// directly; this just wires them to buttons), plus one-click table insertion
// when the caret is NOT in a table yet.
export default function TableControls({ editor }) {
  if (!editor) return null;
  const inTable = editor.isActive('table');

  if (!inTable) {
    return (
      <button
        type="button"
        className="editor-toolbar-btn"
        title="Insert table"
        data-testid="editor-insert-table-btn"
        onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
      >
        <i className="fa-solid fa-table" />
      </button>
    );
  }

  return (
    <span className="editor-table-controls" data-testid="editor-table-controls">
      <button
        type="button"
        className="editor-toolbar-btn"
        title="Add row below"
        data-testid="editor-table-add-row"
        onClick={() => editor.chain().focus().addRowAfter().run()}
      >
        <i className="fa-solid fa-arrow-down-to-line" /> Row
      </button>
      <button
        type="button"
        className="editor-toolbar-btn"
        title="Add column after"
        data-testid="editor-table-add-col"
        onClick={() => editor.chain().focus().addColumnAfter().run()}
      >
        <i className="fa-solid fa-arrow-right-to-line" /> Col
      </button>
      <button
        type="button"
        className="editor-toolbar-btn"
        title="Delete current row"
        data-testid="editor-table-delete-row"
        onClick={() => editor.chain().focus().deleteRow().run()}
      >
        <i className="fa-solid fa-trash" /> Row
      </button>
      <button
        type="button"
        className="editor-toolbar-btn"
        title="Delete current column"
        data-testid="editor-table-delete-col"
        onClick={() => editor.chain().focus().deleteColumn().run()}
      >
        <i className="fa-solid fa-trash" /> Col
      </button>
      <button
        type="button"
        className="editor-toolbar-btn"
        title="Delete table"
        data-testid="editor-table-delete"
        onClick={() => editor.chain().focus().deleteTable().run()}
      >
        <i className="fa-solid fa-table-cells" /> <i className="fa-solid fa-xmark" />
      </button>
    </span>
  );
}
