import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';

// Served from public/pdfjs/ (copied from node_modules/pdfjs-dist at
// `npm install` time by scripts/copy-pdfjs-assets.mjs — see that script's
// comment) rather than resolved through Vite's module graph, matching the
// exact setup confirmed working end-to-end in the C0 spike (a real
// browser, plain static URLs, no bundler-specific worker-import wiring).
pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdfjs/build/pdf.worker.mjs';
const WASM_URL = '/pdfjs/wasm/';

// Renders the PDF via pdf.js onto a <canvas>, one page at a time — not the
// browser's native PDF viewer (an <iframe>) this component used to wrap.
// That native approach exposed no scriptable API at all (there was no way
// to reliably know which page was actually showing, toolbar shown or
// hidden — a hard platform constraint, not a design choice), which is why
// it's replaced here.
//
// pdf.js was tried before and reverted, for what looked at the time like a
// real defect: it visibly rendered the invisible OCR text layer some
// scanned-and-OCR'd PDFs embed. Investigating that bug report directly (not
// just its headline) found it was a *misdiagnosed missing decoder
// configuration* in someone else's code, not a genuine pdf.js rendering
// defect — confirmed by the reporter themselves once they fixed it. The
// real, confirmed blocker on this project's actual documents was a
// different codec entirely: JBIG2 image decoding, a hard, version-specific
// gap in pdf.js v4's bundled decoder (confirmed directly in its source).
// Fixed by using the latest pdfjs-dist with `wasmUrl` pointed at its
// bundled WASM JBIG2/JPX decoders (a port of Chrome's own PDFium decoder —
// the same decoder Chrome's native viewer already uses successfully on
// this document) — verified via a real-browser (Puppeteer) render of the
// exact pages that motivated this investigation, with no garbling and no
// decode failures. See the plan file for the full investigation.
//
// Deliberately renders one page at a time (not a continuously-scrollable
// multi-page view): this makes "which page is showing" always exactly
// `pageNum` — no scroll-position tracking, no drift, nothing to lose track
// of — which was the whole problem this investigation set out to solve.
// Zoom is relative to "fit width" (zoomLevel === 1), not an absolute pdf.js
// scale — see the project plan's investigation into why the viewer looked
// blurry/unreadable and zooming did nothing visible.
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 3;
export const ZOOM_STEP = 0.15;

// Shared with the OCR editor pane's own zoom (ViewerPanel.jsx) so both panes
// use the same bounds/step and "feel" identical to zoom, per the UX ask that
// editor zoom behave just like PDF zoom.
export function clampZoom(z) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

// forwardRef + useImperativeHandle expose {zoomIn, zoomOut, resetZoom} so a
// parent (ViewerPanel, for its floating zoom HUD) can drive this component's
// zoom from outside without lifting the zoom state itself up — the
// reset-on-new-document and persist-across-page-navigation behaviors below
// stay exactly as they are, entirely internal. `onZoomLevelChange` is purely
// informational (lets the parent mirror the current percentage for display);
// it never feeds back in. Both are optional — omitting them keeps this
// exactly as before for every other caller (the non-Studio dual-viewport
// panes, which have no external zoom UI of their own).
const PdfCanvasViewer = forwardRef(function PdfCanvasViewer(
  { pdfSrc, pageNum, totalPages, onPageChange, showPageNav = true, showZoomControls = true, onZoomLevelChange },
  ref
) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const docRef = useRef(null);
  const renderTaskRef = useRef(null);
  // The current page's natural (scale-1) viewport, set alongside the fit-
  // width `scale` computation below — lets fitPage() read the page's real
  // width/height synchronously (no second async loadedDoc.getPage() call)
  // to compute a zoomLevel that fits BOTH dimensions, not just width.
  const baseViewportRef = useRef(null);
  const [loadedDoc, setLoadedDoc] = useState(null);
  const [error, setError] = useState(null);
  const [zoomLevel, setZoomLevel] = useState(1);

  const effectiveTotalPages = totalPages || loadedDoc?.numPages || 0;
  const currentPage = Math.min(Math.max(pageNum || 1, 1), effectiveTotalPages || pageNum || 1);

  // Load the document whenever pdfSrc changes.
  useEffect(() => {
    docRef.current = null;
    setLoadedDoc(null);
    setError(null);
    // A new document (or a different chunk of the same one) starts back at
    // "fit width" — zoom only persists across page navigation within the
    // same document, matching every mainstream PDF viewer's own behavior.
    setZoomLevel(1);
    if (!pdfSrc) return undefined;

    let cancelled = false;
    let loadingTask = null;

    (async () => {
      try {
        // pdf.js fetches a `url:` source from inside its own worker thread
        // (see the pdf.worker.mjs wiring above) — a blob: URL created on
        // the main thread isn't reliably fetchable from there, which
        // surfaces as an opaque "Unexpected server response (0)" failure
        // rather than a real decode/network error. Reading the blob into
        // an ArrayBuffer on the main thread first (where blob: URLs always
        // resolve) and handing pdf.js the raw bytes via `data:` sidesteps
        // the worker ever needing to fetch the blob URL itself. A plain
        // http(s) URL has no such problem, so it still goes through
        // pdf.js's own worker-side fetch, unchanged.
        const options = pdfSrc.startsWith('blob:')
          ? { data: new Uint8Array(await (await fetch(pdfSrc)).arrayBuffer()), wasmUrl: WASM_URL }
          : { url: pdfSrc, wasmUrl: WASM_URL };
        if (cancelled) return;
        loadingTask = pdfjsLib.getDocument(options);
        docRef.current = loadingTask;
        const pdfDocument = await loadingTask.promise;
        if (cancelled) return;
        docRef.current = pdfDocument;
        setLoadedDoc(pdfDocument);
      } catch (err) {
        if (!cancelled) setError(err?.message || String(err));
      }
    })();

    return () => {
      cancelled = true;
      if (loadingTask) loadingTask.destroy();
    };
  }, [pdfSrc]);

  // Render the current page whenever the loaded document or target page changes.
  useEffect(() => {
    if (!loadedDoc) return undefined;
    let cancelled = false;
    (async () => {
      let page;
      try {
        page = await loadedDoc.getPage(currentPage);
      } catch (err) {
        if (!cancelled) setError(err?.message || String(err));
        return;
      }
      if (cancelled) return;

      const containerWidth = containerRef.current?.clientWidth || 800;
      const baseViewport = page.getViewport({ scale: 1 });
      baseViewportRef.current = baseViewport;
      // containerRef is the scroll viewport (overflow: auto), not the
      // content — its clientWidth reflects the visible area regardless of
      // how large the canvas itself has grown from zooming, so this stays
      // a stable "fit width" baseline to multiply zoomLevel against.
      const scale = (containerWidth / baseViewport.width) * zoomLevel;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;

      // HiDPI-correct rendering (the Mozilla-documented pdf.js pattern):
      // size the actual pixel buffer at device-pixel resolution, but keep
      // the on-screen CSS size at the viewport's own (CSS-pixel) size, and
      // tell pdf.js about the extra density via `transform` so its own
      // drawing operations account for it. Without this, every render -
      // zoomed or not - is blurry on any display with devicePixelRatio > 1
      // (most modern laptop/phone screens), since the backing buffer never
      // held more detail than the CSS size to begin with.
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null;
      const context = canvas.getContext('2d');

      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
      }
      const renderTask = page.render({ canvasContext: context, viewport, transform });
      renderTaskRef.current = renderTask;
      try {
        await renderTask.promise;
        if (!cancelled) setError(null);
      } catch (err) {
        // A render we ourselves cancelled (superseded by a newer page
        // request) is expected, not a real failure.
        if (!cancelled && err?.name !== 'RenderingCancelledException') {
          setError(err?.message || String(err));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [loadedDoc, currentPage, zoomLevel]);

  // Ctrl+scroll drives the app's own zoom (a real pdf.js re-render at the
  // new scale) instead of the browser's native page zoom, which would
  // just stretch the already-rasterized canvas and look pixelated - the
  // exact complaint this whole fix addresses. Only intercepts the gesture
  // when Ctrl is actually held, so plain scrolling to pan the page is
  // completely unaffected.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    function handleWheel(e) {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setZoomLevel((z) => clampZoom(z + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
    }
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, []);

  function zoomIn() {
    setZoomLevel((z) => clampZoom(z + ZOOM_STEP));
  }

  function zoomOut() {
    setZoomLevel((z) => clampZoom(z - ZOOM_STEP));
  }

  function resetZoom() {
    setZoomLevel(1);
  }

  // Unlike resetZoom (fit-WIDTH, zoomLevel=1 — unchanged, still what the
  // inline/HUD "100%" button does), this fits the WHOLE page — both
  // dimensions — by finding the zoomLevel multiplier that makes the
  // existing fit-width formula land on min(widthFitRatio, heightFitRatio)
  // instead of always widthFitRatio. A no-op if nothing has rendered yet.
  function fitPage() {
    const container = containerRef.current;
    const base = baseViewportRef.current;
    if (!container || !base) return;
    const widthFitRatio = (container.clientWidth || 800) / base.width;
    const heightFitRatio = (container.clientHeight || 600) / base.height;
    const targetRatio = Math.min(widthFitRatio, heightFitRatio);
    setZoomLevel(clampZoom(targetRatio / widthFitRatio));
  }

  useEffect(() => {
    if (onZoomLevelChange) onZoomLevelChange(zoomLevel);
  }, [zoomLevel, onZoomLevelChange]);

  useImperativeHandle(ref, () => ({ zoomIn, zoomOut, resetZoom, fitPage }));

  function goToPage(n) {
    const clamped = Math.min(Math.max(n, 1), effectiveTotalPages || n);
    if (onPageChange) onPageChange(clamped);
  }

  return (
    <div className="pdf-canvas-wrap" data-testid="pdf-canvas-viewer">
      <div className="pdf-canvas-scroll" ref={containerRef}>
        {error && (
          <div className="pdf-canvas-error" data-testid="pdf-canvas-error">
            Failed to render page {currentPage}: {error}
          </div>
        )}
        <canvas ref={canvasRef} data-testid="pdf-canvas" style={{ display: error ? 'none' : 'block' }} />
      </div>
      <div className="pdf-canvas-nav">
        {showPageNav && (
          <>
            <button
              className="btn btn-sm btn-secondary"
              onClick={() => goToPage(currentPage - 1)}
              disabled={currentPage <= 1}
              data-testid="pdf-prev-page"
            >
              <i className="fa-solid fa-chevron-left" />
            </button>
            <span className="pdf-canvas-page-label">
              Page {currentPage}{effectiveTotalPages ? ` / ${effectiveTotalPages}` : ''}
            </span>
            <button
              className="btn btn-sm btn-secondary"
              onClick={() => goToPage(currentPage + 1)}
              disabled={!!effectiveTotalPages && currentPage >= effectiveTotalPages}
              data-testid="pdf-next-page"
            >
              <i className="fa-solid fa-chevron-right" />
            </button>
          </>
        )}
        {showZoomControls && (
        <span className="pdf-canvas-zoom-group">
          <button
            className="btn btn-sm btn-secondary"
            onClick={zoomOut}
            disabled={zoomLevel <= ZOOM_MIN}
            title="Zoom out"
            data-testid="pdf-zoom-out"
          >
            <i className="fa-solid fa-magnifying-glass-minus" />
          </button>
          <button
            className="btn btn-sm btn-secondary pdf-canvas-zoom-label"
            onClick={resetZoom}
            title="Reset to fit width"
            data-testid="pdf-zoom-reset"
          >
            {Math.round(zoomLevel * 100)}%
          </button>
          <button
            className="btn btn-sm btn-secondary"
            onClick={zoomIn}
            disabled={zoomLevel >= ZOOM_MAX}
            title="Zoom in"
            data-testid="pdf-zoom-in"
          >
            <i className="fa-solid fa-magnifying-glass-plus" />
          </button>
        </span>
        )}
      </div>
    </div>
  );
});

export default PdfCanvasViewer;
