import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement scrollIntoView (a real DOM method every browser
// has) — stubbed as a no-op so code under test can call it without crashing;
// a test that needs to assert it was called can still override per-element.
if (typeof Element !== 'undefined' && typeof Element.prototype.scrollIntoView !== 'function') {
  Element.prototype.scrollIntoView = function () {};
}

// jsdom doesn't implement canvas 2D rendering at all (would need the
// native `canvas` npm package) — PdfCanvasViewer.jsx calls
// canvas.getContext('2d') and hands it to a (mocked, in tests) pdf.js
// render call that never actually draws with it, so a plain stub object is
// enough to avoid jsdom's noisy "not implemented" console error.
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = function () {
    return {};
  };
}
