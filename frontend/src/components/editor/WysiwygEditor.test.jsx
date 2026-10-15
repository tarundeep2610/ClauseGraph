import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import WysiwygEditor from './WysiwygEditor';

describe('WysiwygEditor — basic rendering', () => {
  it('renders the initial Markdown content as rich text', async () => {
    render(<WysiwygEditor value={'# Hello\n\nSome **bold** text.'} onChange={vi.fn()} />);
    expect(await screen.findByRole('heading', { name: 'Hello' })).toBeInTheDocument();
    expect(screen.getByText('bold')).toBeInTheDocument();
  });

  it('shows "All Saved" initially, before any edit', async () => {
    render(<WysiwygEditor value={'Plain text'} onChange={vi.fn()} />);
    await screen.findByTestId('wysiwyg-editor');
    expect(screen.getByTestId('editor-save-status')).toHaveTextContent('All Saved');
  });
});

describe('WysiwygEditor — Source toggle', () => {
  it('switches to a raw-Markdown textarea showing the current content, and back', async () => {
    render(<WysiwygEditor value={'# Title\n\nBody text.'} onChange={vi.fn()} />);
    await screen.findByTestId('wysiwyg-editor');
    expect(screen.queryByTestId('editor-source-textarea')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('editor-source-toggle-btn'));

    const textarea = screen.getByTestId('editor-source-textarea');
    expect(textarea.value).toContain('# Title');
    expect(textarea.value).toContain('Body text.');

    fireEvent.click(screen.getByTestId('editor-source-toggle-btn'));
    expect(screen.queryByTestId('editor-source-textarea')).not.toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Title' })).toBeInTheDocument();
  });

  it('edits made in source mode are reflected in the rich editor after toggling back', async () => {
    const onChange = vi.fn();
    render(<WysiwygEditor value={'Original text'} onChange={onChange} />);
    await screen.findByTestId('wysiwyg-editor');

    fireEvent.click(screen.getByTestId('editor-source-toggle-btn'));
    fireEvent.change(screen.getByTestId('editor-source-textarea'), { target: { value: 'Edited from source' } });
    fireEvent.click(screen.getByTestId('editor-source-toggle-btn'));

    expect(await screen.findByText('Edited from source')).toBeInTheDocument();
  });
});

describe('WysiwygEditor — toolbar formatting actions', () => {
  it('toggling Bold flips the toolbar button to active (stored-mark toggle at the current cursor)', async () => {
    const onChange = vi.fn();
    render(<WysiwygEditor value={'plain text'} onChange={onChange} />);
    await screen.findByText('plain text');

    // jsdom has no real caret/selection model, so this exercises the
    // toolbar button's command wiring (focus + toggleBold) at ProseMirror's
    // default selection rather than a specific user-made text selection —
    // typed-while-active round-tripping is covered by the source-mode test
    // above (editing raw Markdown directly, then re-rendering as rich text).
    fireEvent.click(screen.getByTestId('editor-bold-btn'));
    expect(screen.getByTestId('editor-bold-btn')).toHaveClass('active');
  });

  it('inserting a table renders a 3x3 grid with a header row', async () => {
    render(<WysiwygEditor value={'text before table'} onChange={vi.fn()} />);
    await screen.findByText('text before table');

    fireEvent.click(screen.getByTestId('editor-insert-table-btn'));

    const table = document.querySelector('.editor-content table');
    expect(table).toBeInTheDocument();
    expect(table.querySelectorAll('tr')).toHaveLength(3);
    expect(table.querySelectorAll('th')).toHaveLength(3);
  });
});

describe('WysiwygEditor — GFM table round-trip', () => {
  it('ingests a Markdown table and serializes an equivalent table back out on autosave', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onChange = vi.fn();
    const md = '| A | B |\n| --- | --- |\n| 1 | 2 |\n';
    render(<WysiwygEditor value={md} onChange={onChange} />);

    const table = document.querySelector('.editor-content table');
    expect(table).toBeInTheDocument();
    expect(table.querySelectorAll('th')).toHaveLength(2);
    expect(table.textContent).toContain('1');
    expect(table.textContent).toContain('2');

    // Force some editor transaction (any dispatched change) so the autosave
    // path serializes the still-unmodified table back to Markdown — this is
    // a round-trip fidelity check, not a check that bolding a cell works.
    fireEvent.click(screen.getByTestId('editor-bold-btn'));

    await act(async () => { await vi.advanceTimersByTimeAsync(900); });
    expect(onChange).toHaveBeenCalled();
    const serialized = onChange.mock.calls.at(-1)[0];
    expect(serialized).toMatch(/\|\s*A\s*\|\s*B\s*\|/);
    expect(serialized).toContain('1');
    expect(serialized).toContain('2');
    vi.useRealTimers();
  });
});

describe('WysiwygEditor — raw HTML tables from the OCR layout formatter', () => {
  // layout_format.py's _render_column_run() legitimately embeds raw
  // <table><tr><td>...</td></tr></table> HTML in formatted_text for
  // side-by-side column layouts (e.g. a document header's left/right
  // blocks) — this must render as a real table, not literal tag text.
  it('renders an embedded raw HTML table as a real table, not escaped tag text', async () => {
    const md = 'Intro text.\n\n<table><tr><td>Left column</td><td>Right column</td></tr></table>\n\nOutro text.';
    render(<WysiwygEditor value={md} onChange={vi.fn()} />);

    const table = document.querySelector('.editor-content table');
    expect(table).toBeInTheDocument();
    expect(table.textContent).toContain('Left column');
    expect(table.textContent).toContain('Right column');
    // The literal tag characters must never show up as visible text.
    expect(document.querySelector('.editor-content').textContent).not.toContain('<table>');
    expect(document.querySelector('.editor-content').textContent).not.toContain('&lt;table&gt;');
  });

  it('sanitizes a dangerous attribute inside an embedded table while keeping the safe content', async () => {
    const md = '<table><tr><td>Safe text<img src="x" onerror="window.__pwned = true"></td></tr></table>';
    render(<WysiwygEditor value={md} onChange={vi.fn()} />);

    const table = document.querySelector('.editor-content table');
    expect(table).toBeInTheDocument();
    expect(table.textContent).toContain('Safe text');
    // DOMPurify must have stripped the dangerous handler — it must not be
    // present anywhere in the rendered DOM for this to be a real fix.
    expect(document.querySelector('.editor-content').innerHTML).not.toContain('onerror');
    expect(window.__pwned).toBeUndefined();
  });

  it('does not touch normal markdown text that merely contains angle brackets (e.g. a GFM autolink)', async () => {
    const md = 'See <http://example.com> for details.';
    render(<WysiwygEditor value={md} onChange={vi.fn()} />);
    await screen.findByTestId('wysiwyg-editor');
    // Not a <table>, so sanitizeEmbeddedHtml's regex must leave it alone —
    // the URL text should still be present somewhere in the rendered output.
    expect(document.querySelector('.editor-content').textContent).toContain('example.com');
  });
});

describe('WysiwygEditor — re-syncing when the value prop changes externally', () => {
  it('replaces the editor content when a different page/document is loaded', async () => {
    const { rerender } = render(<WysiwygEditor value={'Page one text'} onChange={vi.fn()} />);
    await screen.findByText('Page one text');

    rerender(<WysiwygEditor value={'Page two text'} onChange={vi.fn()} />);
    expect(await screen.findByText('Page two text')).toBeInTheDocument();
    expect(screen.queryByText('Page one text')).not.toBeInTheDocument();
  });
});
