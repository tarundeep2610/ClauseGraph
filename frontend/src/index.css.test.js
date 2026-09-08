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
