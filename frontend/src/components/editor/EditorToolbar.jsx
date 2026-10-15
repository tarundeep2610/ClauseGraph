import TableControls from './TableControls';

function ToolbarButton({ editor, title, testId, active, disabled, onClick, children }) {
  return (
    <button
      type="button"
      className={`editor-toolbar-btn${active ? ' active' : ''}`}
      title={title}
      data-testid={testId}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault() /* keep editor selection/focus while clicking */}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

const HEADING_LEVELS = [
  { value: 'p', label: 'Paragraph' },
  { value: '1', label: 'Heading 1' },
  { value: '2', label: 'Heading 2' },
  { value: '3', label: 'Heading 3' },
];

export default function EditorToolbar({ editor, sourceMode, onToggleSource }) {
  if (!editor) return null;

  const currentHeading = [1, 2, 3].find((lvl) => editor.isActive('heading', { level: lvl }));
  const headingValue = sourceMode ? 'p' : String(currentHeading || 'p');

  function applyHeading(value) {
    if (value === 'p') editor.chain().focus().setParagraph().run();
    else editor.chain().focus().toggleHeading({ level: Number(value) }).run();
  }

  return (
    <div className="editor-toolbar" data-testid="editor-toolbar">
      <select
        className="editor-toolbar-select"
        data-testid="editor-heading-select"
        value={headingValue}
        disabled={sourceMode}
        onChange={(e) => applyHeading(e.target.value)}
      >
        {HEADING_LEVELS.map((h) => (
          <option key={h.value} value={h.value}>{h.label}</option>
        ))}
      </select>

      <span className="editor-toolbar-divider" />

      <ToolbarButton editor={editor} title="Bold (Ctrl+B)" testId="editor-bold-btn" active={editor.isActive('bold')} disabled={sourceMode}
        onClick={() => editor.chain().focus().toggleBold().run()}>
        <i className="fa-solid fa-bold" />
      </ToolbarButton>
      <ToolbarButton editor={editor} title="Italic (Ctrl+I)" testId="editor-italic-btn" active={editor.isActive('italic')} disabled={sourceMode}
        onClick={() => editor.chain().focus().toggleItalic().run()}>
        <i className="fa-solid fa-italic" />
      </ToolbarButton>
      <ToolbarButton editor={editor} title="Underline (Ctrl+U)" testId="editor-underline-btn" active={editor.isActive('underline')} disabled={sourceMode}
        onClick={() => editor.chain().focus().toggleUnderline().run()}>
        <i className="fa-solid fa-underline" />
      </ToolbarButton>

      <span className="editor-toolbar-divider" />

      <ToolbarButton editor={editor} title="Align left" testId="editor-align-left-btn" active={editor.isActive({ textAlign: 'left' })} disabled={sourceMode}
        onClick={() => editor.chain().focus().setTextAlign('left').run()}>
        <i className="fa-solid fa-align-left" />
      </ToolbarButton>
      <ToolbarButton editor={editor} title="Align center" testId="editor-align-center-btn" active={editor.isActive({ textAlign: 'center' })} disabled={sourceMode}
        onClick={() => editor.chain().focus().setTextAlign('center').run()}>
        <i className="fa-solid fa-align-center" />
      </ToolbarButton>
      <ToolbarButton editor={editor} title="Align right" testId="editor-align-right-btn" active={editor.isActive({ textAlign: 'right' })} disabled={sourceMode}
        onClick={() => editor.chain().focus().setTextAlign('right').run()}>
        <i className="fa-solid fa-align-right" />
      </ToolbarButton>

      <span className="editor-toolbar-divider" />

      <ToolbarButton editor={editor} title="Bullet list" testId="editor-bullet-list-btn" active={editor.isActive('bulletList')} disabled={sourceMode}
        onClick={() => editor.chain().focus().toggleBulletList().run()}>
        <i className="fa-solid fa-list-ul" />
      </ToolbarButton>
      <ToolbarButton editor={editor} title="Numbered list" testId="editor-ordered-list-btn" active={editor.isActive('orderedList')} disabled={sourceMode}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        <i className="fa-solid fa-list-ol" />
      </ToolbarButton>

      <span className="editor-toolbar-divider" />

      {!sourceMode && <TableControls editor={editor} />}

      <span className="editor-toolbar-divider" />

      <ToolbarButton editor={editor} title="Undo (Ctrl+Z)" testId="editor-undo-btn" disabled={sourceMode}
        onClick={() => editor.chain().focus().undo().run()}>
        <i className="fa-solid fa-rotate-left" />
      </ToolbarButton>
      <ToolbarButton editor={editor} title="Redo (Ctrl+Y)" testId="editor-redo-btn" disabled={sourceMode}
        onClick={() => editor.chain().focus().redo().run()}>
        <i className="fa-solid fa-rotate-right" />
      </ToolbarButton>

      <span className="editor-toolbar-spacer" />

      <button
        type="button"
        className={`editor-toolbar-btn editor-source-toggle${sourceMode ? ' active' : ''}`}
        title="Toggle raw Markdown source view"
        data-testid="editor-source-toggle-btn"
        onClick={onToggleSource}
      >
        {'</> Source'}
      </button>
    </div>
  );
}
