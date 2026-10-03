import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Minus, Plus } from 'lucide-react';
import { Document, Page, pdfjs } from 'react-pdf';
// Bundled, not fetched. The worker used to come from unpkg over a
// protocol-relative URL, which resolves to http:// on an http origin --
// and the CSP allows only https://unpkg.com, so it was blocked outright.
// pdf.js then fell back to a 'fake worker' that dynamically imports the
// same blocked URL, so that failed too: no worker, no PDF, and Read mode
// showed 'Failed to load PDF file.' Serving it from 'self' needs no CSP
// allowance, no network, and cannot drift from react-pdf's pdfjs version.
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import { Spinner } from './Loader';

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

/**
 * Continuous-scroll PDF reader (PDF-1).
 *
 * Every page is a sheet in one scrolling column, sized from the page's own
 * viewport before anything is painted, so the column has its final length up
 * front and nothing jumps while canvases arrive. Only the sheets within about
 * a screen of the viewport mount a <Page>; the rest are blank sheets of the
 * right size, which keeps a 60-page paper from holding 60 canvases.
 *
 * `file` must be a File or Blob, never a blob: URL -- the CSP's connect-src
 * does not allow blob:, and pdf.js fetches URLs (PDF-2).
 */

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.5;
const ZOOM_STEP = 0.1;
// Fit-to-width stops here: wider than this, a line of a two-column paper is
// longer than anyone reads comfortably, and the canvas cost keeps climbing.
const MAX_FIT_WIDTH = 960;
const SHEET_GAP = 20; // matches .pdf-pages-stack gap (1.25rem)
const LETTER_ASPECT = 11 / 8.5;

export default function PdfReader({ file, active = true, jump, onError }) {
  const scrollRef = useRef(null);
  const sheetRefs = useRef(new Map());
  // Where the reader is, as page + fraction through it. Kept on every scroll so
  // a zoom, a resize or coming back from another tab can put the reader back
  // on the same line instead of at the same pixel offset.
  const anchorRef = useRef({ page: 1, fraction: 0 });
  const handledJumpRef = useRef(null);
  const frameRef = useRef(0);

  const [aspects, setAspects] = useState([]);
  const [fitWidth, setFitWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [currentPage, setCurrentPage] = useState(1);
  const [renderRange, setRenderRange] = useState([1, 2]);

  const numPages = aspects.length;
  const pageWidth = fitWidth ? Math.round(Math.min(fitWidth, MAX_FIT_WIDTH) * zoom) : 0;

  useEffect(() => {
    setAspects([]);
    setCurrentPage(1);
    setRenderRange([1, 2]);
    anchorRef.current = { page: 1, fraction: 0 };
  }, [file]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      // Hidden (another studio mode is showing) reports 0. Keep the last real
      // width so coming back does not re-render every canvas at a new size.
      if (width > 0) setFitWidth((prev) => (Math.abs(prev - width) < 8 ? prev : width));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const handleLoadSuccess = useCallback(async (pdf) => {
    // Each page's own shape, so mixed-size documents (a landscape table page)
    // get the right sheet. getPage parses the page dictionary only; nothing is
    // rendered here.
    const shapes = await Promise.all(
      Array.from({ length: pdf.numPages }, async (_, i) => {
        try {
          const page = await pdf.getPage(i + 1);
          const { width, height } = page.getViewport({ scale: 1 });
          return height / width;
        } catch {
          return LETTER_ASPECT;
        }
      }),
    );
    setAspects(shapes);
  }, []);

  /** Scroll offset of a page's top edge inside the scroll container. */
  const pageTop = useCallback((page) => sheetRefs.current.get(page)?.offsetTop ?? 0, []);

  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !numPages) return;
    const { scrollTop, clientHeight } = el;
    const probe = scrollTop + clientHeight * 0.35;

    let page = 1;
    for (let p = 1; p <= numPages; p += 1) {
      if (pageTop(p) <= probe) page = p;
      else break;
    }
    const sheet = sheetRefs.current.get(page);
    const height = sheet?.offsetHeight || 1;
    anchorRef.current = {
      page,
      fraction: Math.min(1, Math.max(0, (scrollTop - pageTop(page)) / height)),
    };
    setCurrentPage(page);

    // Render one screen above and below what is visible.
    let first = page;
    let last = page;
    while (first > 1 && pageTop(first) > scrollTop - clientHeight) first -= 1;
    while (last < numPages && pageTop(last + 1) < scrollTop + clientHeight * 2) last += 1;
    setRenderRange((prev) => (prev[0] === first && prev[1] === last ? prev : [first, last]));
  }, [numPages, pageTop]);

  const handleScroll = useCallback(() => {
    if (frameRef.current) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = 0;
      measure();
    });
  }, [measure]);

  useEffect(() => () => cancelAnimationFrame(frameRef.current), []);

  const scrollToPage = useCallback((page, behavior = 'auto', fraction = 0) => {
    const el = scrollRef.current;
    const sheet = sheetRefs.current.get(page);
    if (!el || !sheet) return;
    const offset = page === 1 && fraction === 0 ? 0 : sheet.offsetTop - 12 + sheet.offsetHeight * fraction;
    el.scrollTo({ top: Math.max(0, offset), behavior });
  }, []);

  // Size changed (zoom, resize, first layout) or the reader became visible
  // again: put it back where it was, then work out what to render.
  useLayoutEffect(() => {
    if (!active || !pageWidth || !numPages) return;
    const { page, fraction } = anchorRef.current;
    if (page > 1 || fraction > 0) scrollToPage(page, 'auto', fraction);
    measure();
  }, [active, pageWidth, numPages, scrollToPage, measure]);

  // A citation, TOC entry or figure asked for a page. Declared after the
  // restore above so, on the commit that both shows the reader and jumps, the
  // jump wins.
  useLayoutEffect(() => {
    if (!active || !jump || !pageWidth || !numPages) return;
    if (handledJumpRef.current === jump.seq) return;
    handledJumpRef.current = jump.seq;
    const page = Math.min(Math.max(1, jump.page), numPages);
    scrollToPage(page);
    measure();
  }, [active, jump, pageWidth, numPages, scrollToPage, measure]);

  const changeZoom = (next) => {
    const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(next * 10) / 10));
    setZoom(clamped);
  };

  const loading = (
    <div className="pdf-viewer-loading">
      <Spinner size={24} />
    </div>
  );

  return (
    <div className="pdf-viewer-pane">
      <div
        className="pdf-viewer-scroll"
        ref={scrollRef}
        onScroll={handleScroll}
        tabIndex={0}
        aria-label="PDF pages"
      >
        <Document
          file={file}
          onLoadSuccess={handleLoadSuccess}
          onLoadError={onError}
          loading={loading}
          error={
            <div className="pdf-viewer-missing">
              <p>Could not open this PDF.</p>
            </div>
          }
        >
          {pageWidth > 0 && numPages > 0 && (
            <div className="pdf-pages-stack" style={{ gap: SHEET_GAP }}>
              {aspects.map((aspect, i) => {
                const page = i + 1;
                const inRange = page >= renderRange[0] && page <= renderRange[1];
                return (
                  <div
                    key={page}
                    ref={(node) => {
                      if (node) sheetRefs.current.set(page, node);
                      else sheetRefs.current.delete(page);
                    }}
                    className="pdf-page-sheet"
                    style={{ width: pageWidth, height: Math.round(pageWidth * aspect) }}
                    data-page={page}
                  >
                    {inRange && (
                      <Page
                        pageNumber={page}
                        width={pageWidth}
                        renderTextLayer
                        renderAnnotationLayer
                        loading={null}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Document>
      </div>

      {numPages > 0 && (
        <div className="pdf-viewer-controls">
          <div className="pdf-control-cluster">
            <button
              type="button"
              className="pdf-control-btn"
              onClick={() => changeZoom(zoom - ZOOM_STEP)}
              disabled={zoom <= ZOOM_MIN}
              aria-label="Zoom out"
            >
              <Minus size={15} />
            </button>
            <button
              type="button"
              className="pdf-control-label pdf-control-reset"
              onClick={() => changeZoom(1)}
              title="Fit to width"
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              className="pdf-control-btn"
              onClick={() => changeZoom(zoom + ZOOM_STEP)}
              disabled={zoom >= ZOOM_MAX}
              aria-label="Zoom in"
            >
              <Plus size={15} />
            </button>
          </div>
          <div className="pdf-control-divider" />
          <span className="pdf-control-label" aria-live="polite">
            Page {currentPage} / {numPages}
          </span>
        </div>
      )}
    </div>
  );
}
