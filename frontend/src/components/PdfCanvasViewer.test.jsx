import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PdfCanvasViewer from './PdfCanvasViewer';

// pdf.js itself (real WASM/JBIG2 decoding, real canvas rendering) is
// verified separately against real documents (see the plan file's C0
// spike — a real browser, not this jsdom test environment, which has no
// real <canvas> 2D context or WASM support). Here it's mocked so this
// component's own wiring — which page it asks for, how it reacts to
// pageNum/pdfSrc changes, error handling, nav button behavior — is what's
// actually under test.
const mockRender = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
const mockGetViewport = vi.fn(({ scale }) => ({ width: 100 * scale, height: 150 * scale }));
const mockGetPage = vi.fn(() => Promise.resolve({ getViewport: mockGetViewport, render: mockRender }));
const mockDestroy = vi.fn();
let mockNumPages = 5;
let mockGetDocumentError = null;

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: vi.fn(() => ({
    promise: mockGetDocumentError
      ? Promise.reject(mockGetDocumentError)
      : Promise.resolve({ numPages: mockNumPages, getPage: mockGetPage }),
    destroy: mockDestroy,
  })),
}));

import { getDocument } from 'pdfjs-dist';

// A rejected promise with no attached handler trips Vitest/Node's
// unhandled-rejection detector even when the component under test attaches
// its own handler a tick later — attaching a no-op catch directly to the
// same promise instance satisfies that detector without affecting the
// component's own await/catch on it (a promise can have multiple handlers).
function rejectedRenderTask(err) {
  const promise = Promise.reject(err);
  promise.catch(() => {});
  return { promise, cancel: vi.fn() };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockNumPages = 5;
  mockGetDocumentError = null;
  mockGetPage.mockImplementation(() => Promise.resolve({ getViewport: mockGetViewport, render: mockRender }));
  // pdfSrc is always a blob: URL in real usage — the component reads it
  // into an ArrayBuffer on the main thread (see PdfCanvasViewer.jsx's
  // comment on why: pdf.js's worker can't reliably fetch a blob: URL
  // itself) before ever calling pdf.js's getDocument().
  global.fetch = vi.fn(() => Promise.resolve({ arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) }));
});

describe('PdfCanvasViewer', () => {
  it('loads the document via pdf.js, reading a blob: src into an ArrayBuffer on the main thread first, with a wasmUrl configured', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('blob:fake-url'));
    await waitFor(() => expect(getDocument).toHaveBeenCalledWith({ data: expect.any(Uint8Array), wasmUrl: '/pdfjs/wasm/' }));
  });

  it('loads a non-blob src (a plain URL) directly via pdf.js\'s own url option, without an extra fetch', async () => {
    render(<PdfCanvasViewer pdfSrc="https://example.test/doc.pdf" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(getDocument).toHaveBeenCalledWith({ url: 'https://example.test/doc.pdf', wasmUrl: '/pdfjs/wasm/' }));
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('renders the requested page onto the canvas once the document loads', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={3} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(3));
    await waitFor(() => expect(mockRender).toHaveBeenCalled());
    expect(screen.getByTestId('pdf-canvas')).toBeInTheDocument();
  });

  it('shows the current/total page label', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={3} totalPages={5} />);
    await waitFor(() => expect(screen.getByText('Page 3 / 5')).toBeInTheDocument());
  });

  it('clamps a page number beyond the document length', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={999} totalPages={5} />);
    await waitFor(() => expect(screen.getByText('Page 5 / 5')).toBeInTheDocument());
  });

  it('does not attempt to load a document when there is no pdfSrc yet', () => {
    render(<PdfCanvasViewer pdfSrc={null} pageNum={1} totalPages={5} />);
    expect(getDocument).not.toHaveBeenCalled();
  });

  it('re-fetches and re-renders the page when pageNum changes', async () => {
    const { rerender } = render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(1));
    rerender(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={2} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(2));
  });

  it('reloads the document when pdfSrc changes', async () => {
    const { rerender } = render(<PdfCanvasViewer pdfSrc="blob:fake-url-1" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(getDocument).toHaveBeenCalledTimes(1));
    rerender(<PdfCanvasViewer pdfSrc="blob:fake-url-2" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(getDocument).toHaveBeenCalledTimes(2));
    expect(getDocument).toHaveBeenLastCalledWith({ data: expect.any(Uint8Array), wasmUrl: '/pdfjs/wasm/' });
    expect(global.fetch).toHaveBeenCalledWith('blob:fake-url-2');
  });

  it('shows an error message when the document fails to load', async () => {
    mockGetDocumentError = new Error('corrupt PDF');
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(screen.getByTestId('pdf-canvas-error')).toHaveTextContent('corrupt PDF'));
  });

  it('shows an error message when rendering the page itself fails', async () => {
    mockRender.mockReturnValueOnce(rejectedRenderTask(new Error('render exploded')));
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(screen.getByTestId('pdf-canvas-error')).toHaveTextContent('render exploded'));
  });

  it('does not surface a RenderingCancelledException as an error (expected when superseded by a newer page)', async () => {
    const cancelledError = new Error('cancelled');
    cancelledError.name = 'RenderingCancelledException';
    mockRender.mockReturnValueOnce(rejectedRenderTask(cancelledError));
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalled());
    expect(screen.queryByTestId('pdf-canvas-error')).not.toBeInTheDocument();
  });

  it('prev/next buttons call onPageChange with the clamped target page', async () => {
    const onPageChange = vi.fn();
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={3} totalPages={5} onPageChange={onPageChange} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(3));
    fireEvent.click(screen.getByTestId('pdf-next-page'));
    expect(onPageChange).toHaveBeenCalledWith(4);
    fireEvent.click(screen.getByTestId('pdf-prev-page'));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it('disables prev on page 1 and next on the last page', async () => {
    const { rerender } = render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(1));
    expect(screen.getByTestId('pdf-prev-page')).toBeDisabled();
    expect(screen.getByTestId('pdf-next-page')).not.toBeDisabled();

    rerender(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={5} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(5));
    expect(screen.getByTestId('pdf-next-page')).toBeDisabled();
  });

  it('destroys the previous loading task when pdfSrc changes or the component unmounts', async () => {
    const { rerender, unmount } = render(<PdfCanvasViewer pdfSrc="blob:fake-url-1" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(getDocument).toHaveBeenCalledTimes(1));
    rerender(<PdfCanvasViewer pdfSrc="blob:fake-url-2" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockDestroy).toHaveBeenCalledTimes(1));
    // The blob: fetch-into-ArrayBuffer step (see PdfCanvasViewer.jsx) means
    // the new loadingTask isn't assigned synchronously — wait for it to
    // actually exist before unmounting, or there'd be nothing yet to destroy.
    await waitFor(() => expect(getDocument).toHaveBeenCalledTimes(2));
    unmount();
    expect(mockDestroy).toHaveBeenCalledTimes(2);
  });
});

describe('PdfCanvasViewer — zoom', () => {
  it('starts at 100% (fit width) and zooms in/out in fixed steps, re-rendering each time', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('100%');

    fireEvent.click(screen.getByTestId('pdf-zoom-in'));
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('115%');

    fireEvent.click(screen.getByTestId('pdf-zoom-out'));
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(3));
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('100%');
  });

  it('clamps zoom at the minimum and maximum bounds', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));

    // Zoom out well past the floor (50%) — 4 clicks of 15% from 100% would
    // go below it without clamping.
    for (let i = 0; i < 6; i++) fireEvent.click(screen.getByTestId('pdf-zoom-out'));
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-out')).toBeDisabled());
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('50%');

    fireEvent.click(screen.getByTestId('pdf-zoom-reset')); // back to 100%
    for (let i = 0; i < 20; i++) fireEvent.click(screen.getByTestId('pdf-zoom-in'));
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-in')).toBeDisabled());
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('300%');
  });

  it('resets to 100% when clicking the zoom label', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('pdf-zoom-in'));
    fireEvent.click(screen.getByTestId('pdf-zoom-in'));
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('130%'));
    fireEvent.click(screen.getByTestId('pdf-zoom-reset'));
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('100%'));
  });

  it('preserves zoom when navigating pages within the same document', async () => {
    const { rerender } = render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(1));
    fireEvent.click(screen.getByTestId('pdf-zoom-in'));
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('115%'));

    rerender(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={2} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(2));
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('115%');
  });

  it('resets zoom to 100% when pdfSrc changes (a different document/chunk)', async () => {
    const { rerender } = render(<PdfCanvasViewer pdfSrc="blob:fake-url-1" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockGetPage).toHaveBeenCalledWith(1));
    fireEvent.click(screen.getByTestId('pdf-zoom-in'));
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('115%'));

    rerender(<PdfCanvasViewer pdfSrc="blob:fake-url-2" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('100%'));
  });

  it('Ctrl+wheel zooms in/out instead of letting the browser\'s native page zoom fire', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));

    fireEvent.wheel(screen.getByTestId('pdf-canvas-viewer').querySelector('.pdf-canvas-scroll'), {
      deltaY: -100, ctrlKey: true,
    });
    await waitFor(() => expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('115%'));
  });

  it('does not zoom on a plain (non-Ctrl) wheel event — normal scrolling is unaffected', async () => {
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));

    fireEvent.wheel(screen.getByTestId('pdf-canvas-viewer').querySelector('.pdf-canvas-scroll'), {
      deltaY: -100, ctrlKey: false,
    });
    expect(screen.getByTestId('pdf-zoom-reset')).toHaveTextContent('100%');
  });
});

describe('PdfCanvasViewer — HiDPI rendering', () => {
  let originalDpr;

  beforeEach(() => {
    originalDpr = window.devicePixelRatio;
  });

  afterEach(() => {
    Object.defineProperty(window, 'devicePixelRatio', { value: originalDpr, configurable: true });
  });

  it('sizes the canvas backing buffer at devicePixelRatio and passes a transform, so rendering is sharp on HiDPI displays', async () => {
    Object.defineProperty(window, 'devicePixelRatio', { value: 2, configurable: true });
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));

    const canvas = screen.getByTestId('pdf-canvas');
    // At zoomLevel=1, scale=8 (800 test-container fallback / 100 base
    // width) -> viewport 800x1200 CSS px, doubled for a 2x backing buffer.
    expect(canvas.width).toBe(1600);
    expect(canvas.height).toBe(2400);
    expect(canvas.style.width).toBe('800px');
    expect(canvas.style.height).toBe('1200px');
    expect(mockRender).toHaveBeenLastCalledWith(
      expect.objectContaining({ transform: [2, 0, 0, 2, 0, 0] })
    );
  });

  it('passes no transform on a standard-density display (devicePixelRatio 1)', async () => {
    Object.defineProperty(window, 'devicePixelRatio', { value: 1, configurable: true });
    render(<PdfCanvasViewer pdfSrc="blob:fake-url" pageNum={1} totalPages={5} />);
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(1));

    const canvas = screen.getByTestId('pdf-canvas');
    expect(canvas.width).toBe(800);
    expect(canvas.style.width).toBe('800px');
    expect(mockRender).toHaveBeenLastCalledWith(expect.objectContaining({ transform: null }));
  });
});
