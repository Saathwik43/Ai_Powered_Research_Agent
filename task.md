
Repo: Saathwik43/Ai_Powered_Research_Agent
Files: frontend/src/pages/ManuscriptBuilder.jsx, frontend/src/pages/ManuscriptBuilder.css (new file — currently no dedicated CSS, only inline styles + index.css)

Create ManuscriptBuilder.css and import it at top of ManuscriptBuilder.jsx. Add className hooks alongside existing inline styles (don't remove inline styles wholesale — additive classNames, CSS file handles the new layout/typography/motion rules).

═══ 1. EDITOR TYPOGRAPHY + READING WIDTH ═══
Find the main editor content area (the div containing the textarea at line ~538 and ReactMarkdown at line ~550 — both live in the same write/preview toggle container). Wrap both in a new className="manuscript-editor-surface":
.manuscript-editor-surface {
  max-width: 720px;
  margin: 0 auto;
  font-size: 17px;
  line-height: 1.7;
}
.manuscript-editor-surface textarea, .manuscript-editor-surface .pdf-markdown-body {
  font-size: inherit;
  line-height: inherit;
}
Add a sticky mini-toolbar above the textarea/preview toggle (word count using content[active].split(/\s+/).length, plus the existing Write/Preview toggle buttons moved into this bar):
className="manuscript-toolbar" — position: sticky; top: 0; background: var(--bg-card); z-index: 5; padding: 0.5rem 0; border-bottom: 1px solid var(--border); display:flex; justify-content:space-between; align-items:center;

═══ 2. AI REVISE → SLIDE-UP PANEL ═══
Currently "Revise Section with AI" (line ~578) is a static always-visible block. Change to on-demand slide-up:

- Add state: const [revisePanelOpen, setRevisePanelOpen] = useState(false)
- Add a small floating trigger button (fixed position, bottom-right of editor area, or inline icon button near the toolbar) with a Sparkles/Edit icon, onClick={() => setRevisePanelOpen(true)}
- Wrap the existing revise input+button block (line ~578-590) in a conditionally-rendered panel:
  className="manuscript-revise-panel" — position: fixed (or absolute relative to editor container); bottom: 0; left/right matching editor column width; background: var(--bg-card); border-top: 1px solid var(--border); border-radius: var(--radius-lg) var(--radius-lg) 0 0; box-shadow: 0 -4px 20px rgba(0,0,0,0.08); padding: 1.25rem; transform: translateY(0); transition: transform 0.25s cubic-bezier(0.32,0.72,0,1);
  When revisePanelOpen is false: transform: translateY(100%) (slides down out of view) — use a wrapping div always rendered so the transition animates both ways, don't conditionally unmount.
- Add a close (X) button inside the panel to setRevisePanelOpen(false).
- Auto-open the panel when pendingEdit is set (so diff view appearing also implies panel context is visible) — or keep independent, your call, but panel should close automatically after Accept/Reject on the diff.

═══ 3. DIFF VIEW RESTYLE (inline, not side-by-side) ═══
Current pendingEdit block (line 500-501 area, "manuscript-diff-view") — check current implementation structure first (likely two separate before/after boxes). Restyle to single inline flow:
.manuscript-diff-view del { background: rgba(229,28,35,0.12); color: var(--danger); text-decoration: line-through; padding: 0 2px; border-radius: 2px; }
.manuscript-diff-view ins { background: rgba(0,163,108,0.12); color: var(--success); text-decoration: none; padding: 0 2px; border-radius: 2px; }
If the current diff logic produces two separate strings (old/new) rather than a word-level diff, add a lightweight word-diff: check if the `diff` npm package is already installed (grep package.json); if not, add it (`diff` package, ~2KB, has `diffWords` export). Use Diff.diffWords(oldText, newText) to produce inline spans, wrap removed parts in <del></del>, added parts in <ins></ins>.

═══ 4. REFERENCES PANEL → RIGHT SLIDE-IN RAIL ═══
Currently inline block appended at bottom of main column (line 598, "manuscript-references-panel", marginTop 2rem). Convert to a collapsible right rail:

- Add state: const [refsOpen, setRefsOpen] = useState(false)
- Move the panel to render as a fixed/absolute right-side drawer (similar pattern to mobile sidebar drawer already built elsewhere in the app — reuse that transform/transition approach): width ~320px, position: fixed; right: 0; top: [below header]; height: calc(100vh - [header height]); transform: translateX(100%) when closed, translateX(0) when open; transition matching sidebar's cubic-bezier(0.32,0.72,0,1) 0.28s.
- Add a small persistent tab/button on the right edge of the screen (or near the step-count footer in the sidebar) labeled "References (N)" that toggles refsOpen — always visible once manuscriptRefs has entries.
- Add a semi-transparent backdrop when open on mobile widths only (reuse .mobile-overlay pattern from earlier sidebar work) — on desktop it can just push nothing (overlay drawer, doesn't shift editor).

═══ 5. SIDEBAR ACTIVE-STEP POLISH ═══
In the STEPS list render (before line ~330), add a left accent border on the active step:
className conditionally applied: active ? "manuscript-step-active" : ""
.manuscript-step-active { border-left: 3px solid var(--primary); padding-left: calc(0.75rem - 3px); background: var(--primary-light); }
Add crossfade when switching steps: wrap the editor content area in a key={active} with a CSS animation:
@keyframes stepFadeIn { from { opacity: 0; } to { opacity: 1; } }
.manuscript-editor-surface { animation: stepFadeIn 0.15s ease-out; }

Do NOT touch: generation logic, streaming, diff accept/reject logic itself (only its visual rendering), backend, API calls. CSS/layout/motion only.
Test at 1024px+ (desktop) and confirm references drawer + revise panel don't overlap or clip on smaller desktop widths (1280px is common minimum, check there specifically).
