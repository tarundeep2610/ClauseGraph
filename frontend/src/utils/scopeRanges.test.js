import { describe, it, expect } from 'vitest';
import { buildRangeIndex, findRangeForPage, findActivePathKeys } from './scopeRanges';

describe('buildRangeIndex', () => {
  it('groups ranges by key, preserving multiple disjoint entries for the same key', () => {
    const ranges = [
      { key: 'schedule:2', start_page: 1, end_page: 1, depth: 0 },
      { key: 'annexure:a-9', start_page: 2, end_page: 3, depth: 0 },
      { key: 'schedule:2', start_page: 4, end_page: 4, depth: 0 },
    ];
    const index = buildRangeIndex(ranges);
    expect(index.get('schedule:2')).toHaveLength(2);
    expect(index.get('annexure:a-9')).toHaveLength(1);
    expect(index.get('missing')).toBeUndefined();
  });

  it('returns an empty map for no ranges', () => {
    expect(buildRangeIndex([]).size).toBe(0);
    expect(buildRangeIndex(undefined).size).toBe(0);
  });
});

describe('findRangeForPage', () => {
  const ranges = [
    { key: 'annexure:a-2', start_page: 400, end_page: 450, depth: 0 },
    { key: 'article:2', start_page: 410, end_page: 420, depth: 1 },
    { key: 'section:3', start_page: 412, end_page: 415, depth: 2 },
  ];

  it('prefers the deepest range covering a page, not array order', () => {
    expect(findRangeForPage(ranges, 413).key).toBe('section:3');
    expect(findRangeForPage(ranges, 411).key).toBe('article:2');
    expect(findRangeForPage(ranges, 405).key).toBe('annexure:a-2');
  });

  it('returns null when no range covers the page', () => {
    expect(findRangeForPage(ranges, 999)).toBeNull();
  });

  it('returns null for an empty/undefined range list', () => {
    expect(findRangeForPage([], 1)).toBeNull();
    expect(findRangeForPage(undefined, 1)).toBeNull();
  });
});

describe('findActivePathKeys', () => {
  const ranges = [
    { key: 'annexure:a-2', scope_key: '', start_page: 400, end_page: 450, depth: 0 },
    { key: 'article:2', scope_key: 'annexure:a-2', start_page: 410, end_page: 420, depth: 1 },
    { key: 'section:3', scope_key: 'annexure:a-2/article:2', start_page: 412, end_page: 415, depth: 2 },
  ];

  it('returns the full ancestor-plus-self key chain for the deepest covering range', () => {
    expect(findActivePathKeys(ranges, 413)).toEqual(['annexure:a-2', 'article:2', 'section:3']);
    expect(findActivePathKeys(ranges, 411)).toEqual(['annexure:a-2', 'article:2']);
    expect(findActivePathKeys(ranges, 405)).toEqual(['annexure:a-2']);
  });

  it('returns null when no range covers the page', () => {
    expect(findActivePathKeys(ranges, 999)).toBeNull();
  });

  it('returns null for an empty/undefined range list or page', () => {
    expect(findActivePathKeys([], 1)).toBeNull();
    expect(findActivePathKeys(undefined, 1)).toBeNull();
  });
});
