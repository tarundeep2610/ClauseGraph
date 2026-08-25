const STORAGE_KEY = 'ocr-ui-settings';

export function loadPersistedSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (e) {
    return {};
  }
}

export function savePersistedSettings(partial) {
  try {
    const current = loadPersistedSettings();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...current, ...partial }));
  } catch (e) {
    // ignore — localStorage may be unavailable (e.g. private browsing)
  }
}
