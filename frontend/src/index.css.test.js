import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const css = fs.readFileSync(path.resolve(__dirname, './index.css'), 'utf-8');

function ruleBlocks(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g');
  const blocks = [];
  let match;
  while ((match = re.exec(css)) !== null) blocks.push(match[1]);
  return blocks;
}

describe('index.css — sidebar-collapse layout blowout fix', () => {
  it('.viewer-panel allows its grid track to shrink below its content size', () => {
    const blocks = ruleBlocks('.viewer-panel');
    expect(blocks.some((b) => b.includes('min-width: 0'))).toBe(true);
  });

  it('.view-box allows its flex item to shrink below its content size', () => {
    const blocks = ruleBlocks('.view-box');
    expect(blocks.some((b) => b.includes('min-width: 0'))).toBe(true);
  });

  it('.markdown-body table cannot force a blowout via unbroken content', () => {
    const blocks = ruleBlocks('.markdown-body table');
    expect(blocks.some((b) => b.includes('table-layout: fixed'))).toBe(true);
  });
});

describe('index.css — Studio UX Round 5 rules', () => {
  it('.studio-pane has border-right and no transparent background override', () => {
    const blocks = ruleBlocks('.studio-pane');
    expect(blocks.some((b) => b.includes('border-right: 1px solid var(--border)'))).toBe(true);
    expect(blocks.every((b) => !b.includes('background: transparent'))).toBe(true);
  });

  it('.structure-sidebar .view-box-header matches 18px content inset', () => {
    const blocks = ruleBlocks('.structure-sidebar .view-box-header');
    expect(blocks.some((b) => b.includes('padding: 0 18px') || (b.includes('padding-left: 18px') && b.includes('padding-right: 18px')))).toBe(true);
  });

  it('.app-header-wordmark uses solid text color instead of gradient clip', () => {
    const blocks = ruleBlocks('.app-header-wordmark');
    expect(blocks.some((b) => b.includes('color: var(--text)'))).toBe(true);
    expect(blocks.every((b) => !b.includes('-webkit-background-clip: text'))).toBe(true);
  });

  it('.user-menu has no top margin to prevent vertical misalignment', () => {
    const blocks = ruleBlocks('.user-menu');
    expect(blocks.every((b) => !b.includes('margin-top'))).toBe(true);
  });

  it('dark mode :root variables provide elevated contrast', () => {
    const blocks = ruleBlocks(':root');
    expect(blocks.some((b) => b.includes('--border: rgba(255, 255, 255, 0.18)'))).toBe(true);
    expect(blocks.some((b) => b.includes('--surface-rail: #12131f'))).toBe(true);
    expect(blocks.some((b) => b.includes('--surface-drawer: #181a29'))).toBe(true);
    expect(blocks.some((b) => b.includes('--surface-pane: #161826'))).toBe(true);
    expect(blocks.some((b) => b.includes('--surface-header: #1f2235'))).toBe(true);
  });

  it('.ocr-page-block is capped at the pane height so the editor toolbar cannot scroll away with it', () => {
    // min-height: 100% let this block grow taller than the pane on a long
    // OCR page, dragging .editor-header (the formatting toolbar) off-screen
    // with it since the real scroll then happened one level up at
    // .view-box-content instead of inside .editor-content. height: 100%
    // caps it so only .editor-content scrolls and the toolbar stays pinned.
    const blocks = ruleBlocks('.ocr-page-block');
    expect(blocks.some((b) => /(?<!min-)height:\s*100%/.test(b))).toBe(true);
    expect(blocks.every((b) => !b.includes('min-height: 100%'))).toBe(true);
  });

  it('.structure-tree-toolbar is pinned to the top of the panel instead of scrolling away with the tree', () => {
    const blocks = ruleBlocks('.structure-tree-toolbar');
    expect(blocks.some((b) => b.includes('position: sticky'))).toBe(true);
    expect(blocks.some((b) => /top:\s*-?\d/.test(b))).toBe(true);
  });

  it('.structure-tree-label-text clamps long headings to 2 lines instead of truncating to a single line', () => {
    const blocks = ruleBlocks('.structure-tree-label-text');
    expect(blocks.some((b) => b.includes('-webkit-line-clamp: 2'))).toBe(true);
    expect(blocks.every((b) => !b.includes('white-space: nowrap'))).toBe(true);
  });

  it('studio full-bleed shell and drawer rules', () => {
    const studioShellBlocks = ruleBlocks('.app-shell-container.is-studio');
    expect(studioShellBlocks.some((b) => b.includes('height: 100vh') && b.includes('overflow: hidden'))).toBe(true);

    const viewerPanelStudioBlocks = ruleBlocks('.viewer-panel.studio-mode');
    expect(viewerPanelStudioBlocks.some((b) => b.includes('border: none') && b.includes('padding: 0'))).toBe(true);

    const reviewHeaderBlocks = ruleBlocks('.review-queue-header');
    expect(reviewHeaderBlocks.some((b) => b.includes('height: 48px'))).toBe(true);

    const drawerCloseBlocks = ruleBlocks('.drawer-close-btn');
    expect(drawerCloseBlocks.some((b) => b.includes('cursor: pointer'))).toBe(true);
  });
});
