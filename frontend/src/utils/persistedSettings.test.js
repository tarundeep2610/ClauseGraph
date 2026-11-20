import { describe, it, expect, vi, beforeEach } from 'vitest';
import { loadPersistedSettings, savePersistedSettings } from './persistedSettings';

describe('persistedSettings', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('round-trips saved values through loadPersistedSettings', () => {
    savePersistedSettings({ form: { engine: 'glm', scale: '2.0' }, viewMode: 'pdf' });
    expect(loadPersistedSettings()).toEqual({ form: { engine: 'glm', scale: '2.0' }, viewMode: 'pdf' });
  });

  it('merges successive saves instead of overwriting the whole stored object', () => {
    savePersistedSettings({ form: { engine: 'glm' } });
    savePersistedSettings({ viewMode: 'result' });
    expect(loadPersistedSettings()).toEqual({ form: { engine: 'glm' }, viewMode: 'result' });
  });

  it('returns {} when nothing has been stored yet', () => {
    expect(loadPersistedSettings()).toEqual({});
  });

  it('returns {} instead of throwing when the stored value is corrupted JSON', () => {
    localStorage.setItem('ocr-ui-settings', 'not json');
    expect(loadPersistedSettings()).toEqual({});
  });

  it('swallows errors instead of throwing when localStorage access fails', () => {
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });

    try {
      expect(() => savePersistedSettings({ viewMode: 'split' })).not.toThrow();
      expect(() => loadPersistedSettings()).not.toThrow();
      expect(loadPersistedSettings()).toEqual({});
    } finally {
      getItemSpy.mockRestore();
      setItemSpy.mockRestore();
    }
  });
});
