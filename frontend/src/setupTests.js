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

// jsdom has no real layout engine, so Range/Element never report real
// geometry — ProseMirror (the engine behind the WYSIWYG editor) calls
// Range.getClientRects()/getBoundingClientRect() on every selection change
// (e.g. to scroll the caret into view), which otherwise throws
// "getClientRects is not a function" and aborts the whole editor command
// mid-chain. Zero-rect stubs are enough: tests assert on editor state/DOM
// content, never on actual pixel positions.
if (typeof Range !== 'undefined') {
  const zeroRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON() { return this; } });
  Range.prototype.getClientRects = function () { return [zeroRect()]; };
  Range.prototype.getBoundingClientRect = zeroRect;
}
if (typeof Element !== 'undefined' && typeof Element.prototype.getClientRects !== 'function') {
  Element.prototype.getClientRects = function () { return []; };
}
