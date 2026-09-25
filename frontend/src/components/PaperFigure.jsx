import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { pdfjs } from 'react-pdf';
import { ImageOff } from 'lucide-react';

/**
 * Renders a figure from the paper itself, cropped out of the pdf.js document
 * the reader already has open (RP-12).
 *
 * Nothing is fetched and nothing is stored: the backend ships a bbox and a
 * caption on `structure.figures` -- a few hundred bytes -- and the crop is a
 * clip of a page this browser has already parsed. There is no image endpoint,
 * no object-store write and no extra bandwidth behind any of this.
 */

const PaperFiguresContext = createContext(null);

// Backing-store width in device pixels. The canvas is laid out at 100% of its
// container and scaled by CSS, so this is about sharpness, not layout: a small
// schematic still gets rendered well above its natural size, and a full-width
// figure is capped before it costs several megabytes of canvas.
const TARGET_CANVAS_WIDTH = 1100;
const MIN_RENDER_SCALE = 1.5;
const MAX_RENDER_SCALE = 4;

const LABEL_WORDS = {
  fig: 'Figure', figure: 'Figure', table: 'Table', chart: 'Chart', scheme: 'Scheme',
};

/** `("Fig.", "3") -> "Figure 3"`, matching the labels the backend emits. */
export function canonicalFigureLabel(word, number) {
  const key = String(word || '').toLowerCase().replace(/\.$/, '');
  const name = LABEL_WORDS[key];
  if (!name) return null;
  const num = /^\d+$/.test(number) ? number : String(number).toUpperCase();
  return `${name} ${num}`;
}

export function PaperFiguresProvider({ file, blobUrl, figures, onJumpToPage, children }) {
  const [doc, setDoc] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let loaded = null;
    let task = null;

    const hasLocalFile = Boolean(file && file.size);
    if (!hasLocalFile && !blobUrl) {
      setDoc(null);
      return undefined;
    }

    (async () => {
      try {
        // A restored chat has only a blob URL; a fresh upload has the File in
        // hand and never needs to round-trip through the server for it.
        const source = hasLocalFile
          ? { data: new Uint8Array(await file.arrayBuffer()) }
          : { url: blobUrl };
        task = pdfjs.getDocument(source);
        loaded = await task.promise;
        if (cancelled) {
          loaded.destroy();
          return;
        }
        setDoc(loaded);
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to open PDF for figure rendering', err);
          setDoc(null);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (loaded) loaded.destroy();
      else if (task?.destroy) task.destroy();
    };
  }, [file, blobUrl]);

  const byLabel = useMemo(() => {
    const map = new Map();
    (Array.isArray(figures) ? figures : []).forEach((figure) => {
      if (figure && figure.label) map.set(figure.label, figure);
    });
    return map;
  }, [figures]);

  const value = useMemo(
    () => ({ doc, byLabel, onJumpToPage }),
    [doc, byLabel, onJumpToPage],
  );

  return (
    <PaperFiguresContext.Provider value={value}>
      {children}
    </PaperFiguresContext.Provider>
  );
}

export function usePaperFigures() {
  return useContext(PaperFiguresContext);
}

/** Does this label name a figure the paper actually has? */
export function useHasFigure() {
  const ctx = useContext(PaperFiguresContext);
  return useMemo(
    () => (label) => Boolean(ctx?.byLabel?.has(label)),
    [ctx],
  );
}

function FigureCrop({ figure, doc, onFailed }) {
  const canvasRef = useRef(null);
  // Which render owns the canvas. A canvas can only be painted by one
  // `page.render()` at a time -- pdf.js throws outright if a second starts
  // before the first is cancelled, and setting `canvas.width` resets the
  // bitmap under a render already in flight. React remounts this component
  // freely (StrictMode double-invokes effects, and ReactMarkdown re-renders
  // the message tree), so "one at a time" has to be enforced rather than
  // assumed.
  const generation = useRef(0);
  const inFlight = useRef(null);
  const [status, setStatus] = useState('loading');

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!doc || !canvas) return undefined;

    const mine = generation.current + 1;
    generation.current = mine;
    let cancelled = false;

    const superseded = () => cancelled || generation.current !== mine;

    (async () => {
      try {
        // Whatever was painting this canvas stops before anything else starts.
        if (inFlight.current) {
          try {
            inFlight.current.cancel();
          } catch {
            /* already settled */
          }
          inFlight.current = null;
        }

        const page = await doc.getPage(figure.page);
        if (superseded()) return;

        const base = page.getViewport({ scale: 1 });
        // `page_size` travels with the bbox so a disagreement about the page
        // box rescales rather than cropping the wrong part of the page.
        const declared = figure.page_size?.width;
        const unit = declared ? base.width / declared : 1;

        const [x0, y0, x1, y1] = figure.bbox.map((value) => value * unit);
        const width = Math.max(1, x1 - x0);
        const height = Math.max(1, y1 - y0);

        const scale = Math.min(
          Math.max(TARGET_CANVAS_WIDTH / width, MIN_RENDER_SCALE),
          MAX_RENDER_SCALE,
        );

        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.style.aspectRatio = `${width} / ${height}`;

        const context = canvas.getContext('2d');
        if (!context) throw new Error('2d context unavailable');
        context.clearRect(0, 0, canvas.width, canvas.height);

        const task = page.render({
          canvasContext: context,
          viewport: page.getViewport({ scale }),
          // Slide the page under the canvas so the bbox's top-left lands at
          // the canvas origin. Applied before the viewport transform, which is
          // what makes this a crop rather than a scaled-down whole page.
          transform: [1, 0, 0, 1, -x0 * scale, -y0 * scale],
        });
        inFlight.current = task;
        await task.promise;
        if (superseded()) return;
        inFlight.current = null;
        setStatus('ready');
      } catch (err) {
        if (superseded() || err?.name === 'RenderingCancelledException') return;
        // Named, so the console says which figure and why rather than leaving
        // a blank rectangle to be reverse-engineered.
        console.error(
          `[PaperFigure] ${figure.label} (page ${figure.page}, bbox ${figure.bbox}) `
          + `failed to render:`, err,
        );
        setStatus('failed');
        if (onFailed) onFailed();
      }
    })();

    return () => {
      cancelled = true;
      if (inFlight.current) {
        try {
          inFlight.current.cancel();
        } catch {
          /* already finished */
        }
        inFlight.current = null;
      }
    };
    // `figure` is a stable reference: it comes from the provider's memoised
    // label map, so this re-runs when the paper changes and not on every render.
  }, [doc, figure, onFailed]);

  // Nothing is returned for a failure: the parent swaps in the page-link
  // fallback, so a figure that cannot be painted still offers the caption and
  // a way to reach it. Returning null here used to leave a bordered box with
  // an empty hole in it and no explanation anywhere.
  if (status === 'failed') return null;

  return (
    <canvas
      ref={canvasRef}
      className={`paper-figure-canvas ${status === 'ready' ? 'is-ready' : ''}`}
      role="img"
      aria-label={figure.caption || figure.label}
    />
  );
}

/**
 * One `[Figure 3]` citation, rendered as the figure it names.
 *
 * A label the paper does not have renders as plain text, not as a broken card:
 * the model is told only listed labels exist, and when it invents one anyway
 * the honest outcome is that nothing appears to have been cited.
 */
export default function PaperFigure({ label }) {
  const ctx = usePaperFigures();
  const [renderFailed, setRenderFailed] = useState(false);
  const figure = ctx?.byLabel?.get(label);
  const onFailed = useCallback(() => setRenderFailed(true), []);

  useEffect(() => setRenderFailed(false), [figure]);

  if (!figure) return <span className="paper-figure-plain">[{label}]</span>;

  const jump = ctx?.onJumpToPage;
  const canCrop =
    !renderFailed
    && Array.isArray(figure.bbox)
    && figure.bbox.length === 4
    && ctx?.doc;

  return (
    <span className="paper-figure">
      {canCrop ? (
        <FigureCrop figure={figure} doc={ctx.doc} onFailed={onFailed} />
      ) : (
        // A scan, a caption with nothing locatable next to it, or a crop that
        // would not paint. The caption is real and the page is real, so both
        // are still offered rather than showing an empty frame.
        <span className="paper-figure-missing">
          <ImageOff size={14} />
          {renderFailed
            ? `Could not render this figure — see page ${figure.page}`
            : `Shown on page ${figure.page} of the PDF`}
        </span>
      )}
      <span className="paper-figure-caption">
        <button
          type="button"
          className="paper-figure-jump"
          onClick={() => jump && jump(figure.page)}
          disabled={!jump}
          title={`Open page ${figure.page} in Read`}
        >
          {figure.label}
        </button>
        {figure.caption ? <span>{stripLabel(figure.caption, figure.label)}</span> : null}
      </span>
    </span>
  );
}

/** The printed caption repeats its own label; the jump button already shows it. */
function stripLabel(caption, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return caption.replace(new RegExp(`^${escaped}\\s*[.:\\u2013\\u2014-]?\\s*`, 'i'), '');
}
