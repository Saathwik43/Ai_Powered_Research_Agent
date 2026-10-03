import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { BookOpen, CheckCircle2, ChevronRight, Copy, Download, ExternalLink, FileText, Filter, GitBranch, List, Save, Search, Sparkles, User, X, Loader2, Bookmark, Unlock, ChevronDown, Square, Pin } from 'lucide-react';
import { Button } from '../components/ui/button';
import PageHeader from '../components/PageHeader';
import TopBar from '../components/TopBar';
import { AnimatePresence, LayoutGroup, motion } from 'motion/react';
import './LiteratureSurvey.css';
import { useAuth } from '../context/AuthContext';
import { useAppContext } from '../context/AppContext';
import { Spinner, SkeletonList } from '../components/Loader';
import ScientificText from '../components/ScientificText';
import {
  validateSearchQuery,
  normalizeSearchQuery,
  isAbortError,
} from '../utils/searchHeuristics';
import { useSearchRequest } from '../hooks/useSearchRequest';
import { useApiQuery } from '../hooks/useApiQuery';
import { invalidate } from '../lib/queryCache';
import { SavedItemMenu, RenameField } from '../components/SavedItemActions';
import { splitPinned, displayName } from '../lib/savedItems';

// The Dashboard's "recent surveys" rail reads this same key.
const SURVEY_LIST_KEY = 'literature:list';

// Progressive fetch: classify one page at a time. Raising limit on Load more
// reuses search_all + relevance caches so only the new window slice is paid for.
const INITIAL_LIMIT = 15;
const MAX_LIMIT = 100;

const SOURCE_LABELS = {
  SemanticScholar: 'Semantic Scholar',
  OpenAlex: 'OpenAlex',
  Crossref: 'Crossref',
  PubMed: 'PubMed',
  arXiv: 'arXiv',
  GitHub: 'GitHub',
  Springer: 'Springer',
  CORE: 'CORE',
  BASE: 'BASE',
  EuropePMC: 'Europe PMC',
  DOAJ: 'DOAJ',
  OpenReview: 'OpenReview',
  ACLAnthology: 'ACL Anthology',
  Zenodo: 'Zenodo',
};

function sourceLabel(name) {
  return SOURCE_LABELS[name] || name;
}

// How many of the reader's top results seed a citation expansion. The server
// caps it too — this is the number the status line quotes back.
const SNOWBALL_SEEDS = 10;

// Provenance the server attaches to a snowballed paper. Copied onto a row the
// search had already returned, so one card can say both "from search" and "3 of
// your results cite this" instead of the second answer being thrown away.
function pickProvenance(paper) {
  return {
    snowball_direction: paper.snowball_direction,
    snowball_seeds: paper.snowball_seeds,
    snowball_seed_count: paper.snowball_seed_count,
  };
}

// The one-line justification for a snowballed row, in the reader's terms rather
// than the graph's: "cited by" means their own results cite it (it is prior
// work), "cites" means it builds on their results (it is newer work).
function citationProvenance(paper) {
  const n = paper.snowball_seed_count || 0;
  const of = `${n} result${n === 1 ? '' : 's'}`;
  if (paper.snowball_direction === 'backward') return `Cited by ${of}`;
  if (paper.snowball_direction === 'forward') return `Cites ${of}`;
  return `Linked to ${of}`;
}

function surveyScreenedCount(survey) {
  return survey.screened ?? survey.papers?.length ?? 0;
}

/** Stable Saved-tab date label, or null when the clock is missing/invalid. */
function surveySavedDateLabel(survey) {
  if (!survey?.saved_at) return null;
  const d = new Date(survey.saved_at);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString();
}

// Fold the snowball's per-graph outcomes into the search's, keyed by name. The
// panel reports what each database did for this result set; after an expansion
// OpenAlex and Semantic Scholar have done two things, and the citation pass is
// the more recent one.
function mergeOutcomes(existing, incoming) {
  const byName = new Map((existing || []).map((o) => [o.name, o]));
  for (const outcome of incoming) {
    const before = byName.get(outcome.name);
    byName.set(outcome.name, {
      ...before,
      ...outcome,
      count: (before?.count || 0) + (outcome.count || 0),
    });
  }
  return Array.from(byName.values());
}

const BRIEF_LABELS = ['About', 'Method', 'Finding', 'Results', 'Limit'];
const BRIEF_FILL = ['About', 'Finding', 'Method'];
const NO_ABSTRACT = new Set([
  '',
  'No abstract available',
  'No abstract available.',
  'Abstract not available via PubMed summary API.',
]);
// `Next` only ever comes from the full-text path, where the authors state it.
const BRIEF_LINE_RE = /^(About|Method|Finding|Results|Limit|Next):\s*([\s\S]*)$/i;
const BRIEF_SOFT = 180;
const BRIEF_HARD = 260;
const BRIEF_MIN_CLIP = 80;
const ELLIPSIS = '…';
// A sentence end, ignoring the abbreviations that look like one.
const SENTENCE_END_RE = /(?<!\be\.g)(?<!\bi\.e)(?<!\bet al)(?<!\bvs)(?<!\bFig)(?<!\bEq)(?<!\bRef)(?<!\bcf)(?<!\bapprox)[.!?]["'”)\]]?(?=\s|$)/g;

// Keep whole sentences. A card that ends "using multiple." looks like a finished
// claim when it is really a cut; an ellipsis says the sentence goes on.
function clipBrief(text, soft = BRIEF_SOFT, hard = BRIEF_HARD) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (t.length <= soft) return t;
  const window = t.slice(0, hard);
  let cut = 0;
  for (const m of window.matchAll(SENTENCE_END_RE)) cut = m.index + m[0].length;
  if (cut >= BRIEF_MIN_CLIP) return window.slice(0, cut).trim();
  const slice = t.slice(0, soft);
  const sp = slice.lastIndexOf(' ');
  const stem = sp > BRIEF_MIN_CLIP ? slice.slice(0, sp) : slice;
  return `${stem.replace(/[,;:.\s]+$/, '')}${ELLIPSIS}`;
}

function endsSentence(text) {
  const stripped = String(text || '').trimEnd();
  if (!stripped) return false;
  if (stripped.endsWith(ELLIPSIS)) return true;
  return new RegExp(SENTENCE_END_RE.source).test(stripped.slice(-3));
}

// Results needs a stated outcome, not an evaluation setup ("we evaluate on three
// datasets") and not background prose carrying an outcome word ("increasingly
// used"). Either a number, or a comparative verb owned by the work.
const RESULTS_NUM_RE = /(\d+(\.\d+)?\s*%|\b\d+(\.\d+)?\s*x\b|\b(accuracy|auc|f1|bleu|rouge|precision|recall|error rate|mae|rmse)\b[^.]{0,40}\d)/i;
const RESULTS_VERB_RE = /\b(outperform|surpass|achiev|attain|improv|reduc|boost|yield)\w*\b/i;
const RESULTS_SUBJECT_RE = /\b(our|we|proposed|baseline|state[- ]of[- ]the[- ]art|sota|results?|experiments?|evaluation)\b/i;

function isResultChunk(text) {
  if (RESULTS_NUM_RE.test(text)) return true;
  if (!RESULTS_VERB_RE.test(text)) return false;
  return RESULTS_SUBJECT_RE.test(text) || /\d/.test(text);
}

function classifyBriefChunk(text) {
  if (/\b(limit|however|cannot|do not|future work|only when|does not yet)\b/i.test(text)) return 'Limit';
  if (isResultChunk(text)) return 'Results';
  if (/\b(we (propose|present|introduce|develop|use|apply|focus|train|design|evaluate|benchmark|test)|method|approach|algorithm|framework|evaluat|experiment|benchmark|dataset|\busing\b)/i.test(text)) {
    return 'Method';
  }
  if (/\b(we (show|find|demonstrate|observe|conclude|investigate|study)|this paper (shows|presents|argues)|contribution)\b/i.test(text)) {
    return 'Finding';
  }
  return null;
}

// Make a clause split read as a sentence, not as a severed middle.
function tidyChunk(raw) {
  let text = String(raw || '').replace(/\s+/g, ' ').trim().replace(/^[\s,;:\-–—]+|[\s,;:\-–—]+$/g, '');
  text = text.replace(/[\s,;:]*\b(and|or|but|which|that|while|whereas|as well as)\s*$/i, '').replace(/[\s,;:]+$/, '');
  if (text && text[0] !== text[0].toUpperCase()) text = text[0].toUpperCase() + text.slice(1);
  if (text && !endsSentence(text)) text += '.';
  return text;
}

function explodeAbstract(raw) {
  const text = String(raw || '').replace(/\s+/g, ' ').trim();
  if (NO_ABSTRACT.has(text)) return [];
  const coarse = text
    .split(/(?<=[.!?])\s+|(?<=;)\s+|(?=\bWe\b)|(?=\bThis paper\b)|(?=\bOur\b)/i)
    .map(tidyChunk)
    .filter((part) => part.length > 20);
  const parts = [];
  coarse.forEach((part) => {
    if (part.length > 180 && part.includes(':')) {
      const idx = part.indexOf(':');
      const left = tidyChunk(part.slice(0, idx));
      const right = tidyChunk(part.slice(idx + 1));
      if (left.length > 25) parts.push(left);
      if (right.length > 25) parts.push(right);
    } else {
      parts.push(part);
    }
  });
  // Source APIs often hand back an abstract that is itself cut short, so the
  // last chunk is a fragment. Mark it rather than presenting it as finished.
  if (parts.length && !endsSentence(text)) {
    parts[parts.length - 1] = `${parts[parts.length - 1].replace(/[,;:.\s]+$/, '')}${ELLIPSIS}`;
  }
  return parts;
}

function extractiveBrief(paper) {
  const leftover = explodeAbstract(paper?.abstract);
  const byLabel = {};
  const unused = [];
  leftover.forEach((chunk) => {
    const label = classifyBriefChunk(chunk);
    if (label && !byLabel[label]) byLabel[label] = chunk;
    else unused.push(chunk);
  });
  BRIEF_FILL.forEach((label) => {
    if (!byLabel[label] && unused.length) byLabel[label] = unused.shift();
  });
  if (!byLabel.About) {
    const title = String(paper?.title || '').replace(/\s+/g, ' ').trim();
    if (title) byLabel.About = title;
  }
  return BRIEF_LABELS.filter((label) => byLabel[label]).map(
    (label) => `${label}: ${clipBrief(byLabel[label])}`,
  );
}

function parseBriefLine(line) {
  const match = String(line || '').match(BRIEF_LINE_RE);
  if (!match) return { label: '', text: String(line || '').trim() };
  return { label: match[1], text: match[2].trim() };
}

// Three sources, best first: bullets fetched for this card, bullets the search
// response already carried, and the abstract split we can do without the model.
function briefBullets(paper, brief) {
  if (brief?.bullets?.length) return { bullets: brief.bullets, fromModel: true };
  if (paper?.brief?.length) return { bullets: paper.brief, fromModel: true };
  return { bullets: extractiveBrief(paper), fromModel: false };
}

// Which of the evidence ladder's rungs answered, in the reader's words.
const FULL_TEXT_SOURCES = {
  'arxiv-html': 'the full text on arXiv',
  'arxiv-latex': "the paper's LaTeX source",
  'europepmc-fulltext': 'the full text on Europe PMC',
  'pdf-structure': 'the open-access PDF',
};

function PaperBrief({ paper, brief, deep, onDeepen }) {
  const shallow = briefBullets(paper, brief);
  const deepBullets = deep?.bullets?.length ? deep.bullets : null;
  const bullets = deepBullets || shallow.bullets;
  const fullTextSource = deepBullets ? FULL_TEXT_SOURCES[deep.source] : null;
  // The ladder ran and found nothing readable — closed access, most likely.
  // Say so once rather than leaving a button that will never do anything.
  const noFullText = Boolean(deep && !deep.loading && !deep.error && !fullTextSource);
  if (!bullets.length) return null;

  let note = 'Split from the abstract. A tighter briefing loads when the model is free.';
  if (fullTextSource) note = `Read from ${fullTextSource}.`;
  else if (noFullText) note = 'No readable full text — skimmed from the abstract.';
  else if (shallow.fromModel) note = 'Skimmed from the abstract, not the full paper.';

  return (
    <div className="lit-result-brief-wrap">
      <ul className="lit-result-brief">
        {bullets.map((line) => {
          const { label, text } = parseBriefLine(line);
          return (
            <li key={line}>
              {label ? <span className="lit-brief-label">{label}</span> : null}
              <ScientificText className="lit-brief-text" text={text || line} />
            </li>
          );
        })}
      </ul>
      <div className="lit-result-brief-foot">
        <p className="lit-result-brief-note">
          {deep?.error ? 'Could not read the full text. Showing the abstract briefing.' : note}
        </p>
        {!deepBullets && !noFullText && (
          <button
            type="button"
            className="lit-brief-deepen"
            onClick={() => onDeepen(paper)}
            disabled={deep?.loading}
          >
            {deep?.loading
              ? <><Loader2 size={12} className="lit-brief-spin" /> Reading the paper…</>
              : <><BookOpen size={12} /> Read the full text</>}
          </button>
        )}
      </div>
    </div>
  );
}

function SourceOutcomes({ sources }) {
  if (!sources?.length) return null;
  const ok = sources.filter(s => s.status === 'ok').length;
  const troubled = sources.filter(s => s.status === 'error' || s.status === 'timeout');
  return (
    <details className="lit-source-outcomes">
      <summary>
        {ok} of {sources.length} databases returned papers
        {troubled.length > 0 && (
          <span className="lit-source-outcomes-warn">
            {' '}· {troubled.map(s => sourceLabel(s.name)).join(', ')} failed
          </span>
        )}
      </summary>
      <ul>
        {sources.map((s) => (
          <li key={s.name} className={`is-${s.status}`}>
            <span className="lit-source-name">{sourceLabel(s.name)}</span>
            <span className="lit-source-stat">
              {s.status === 'ok' && `${s.count} paper${s.count === 1 ? '' : 's'}`}
              {s.status === 'empty' && 'no matches'}
              {s.status === 'timeout' && 'timed out'}
              {s.status === 'error' && (s.error || 'failed')}
              {s.status === 'skipped' && 'skipped'}
              {s.ms != null && s.status !== 'skipped' && ` · ${s.ms} ms`}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

const HTTP_RE = /^https?:\/\//i;

// Where a reader can open the paper, and where its PDF actually is. Paper
// dicts arrive straight from twelve sources, so every locator field is present
// on some of them and missing on others — hence the ladders rather than one
// field. Only used by the PDF export; the cards render whichever links they
// happen to have.
function paperLink(p) {
  if (HTTP_RE.test(p?.url || '')) return p.url;
  if (p?.doi) return `https://doi.org/${String(p.doi).replace(/^https?:\/\/doi\.org\//i, '')}`;
  if (HTTP_RE.test(p?.arxiv_url || '')) return p.arxiv_url;
  if (p?.arxiv_id) return `https://arxiv.org/abs/${p.arxiv_id}`;
  if (p?.pmcid) return `https://www.ncbi.nlm.nih.gov/pmc/articles/${p.pmcid}/`;
  // OpenAlex and Semantic Scholar ids are URLs; GitHub's are not.
  if (HTTP_RE.test(p?.id || '')) return p.id;
  return '';
}

function paperPdfLink(p) {
  if (HTTP_RE.test(p?.pdf_url || '')) return p.pdf_url;
  // Not guaranteed to be a PDF — it is the best open-access pointer we hold,
  // which is why the column is headed "PDF / open access".
  if (HTTP_RE.test(p?.oa_url || '')) return p.oa_url;
  if (p?.arxiv_id) return `https://arxiv.org/pdf/${p.arxiv_id}`;
  if (HTTP_RE.test(p?.arxiv_url || '')) return p.arxiv_url.replace('/abs/', '/pdf/');
  return '';
}

// Printed in the table cell. The scheme is dropped so the URL has a chance of
// fitting the column, but the rest is left intact so it stays retypeable off
// a paper copy — the hyperlink behind it carries the full address.
function linkLabel(url) {
  return url.replace(HTTP_RE, '').replace(/^www\./i, '');
}

export default function LiteratureSurvey() {
  const { api } = useAuth();
  const { literatureState } = useAppContext();
  const {
    query, setQuery,
    papers, setPapers,
    loading, setLoading,
    activeTab, setActiveTab,
    searchError, setSearchError,
    hasSearched, setHasSearched,
    lastQuery, setLastQuery,
    filterYear, setFilterYear,
    filterSource, setFilterSource,
    visibleCount, setVisibleCount
  } = literatureState;

  const [loadingMore, setLoadingMore] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [savedSurveys, setSavedSurveys] = useState([]);
  const [loadingSaved, setLoadingSaved] = useState(false);
  // ROW-2: inline rename on a saved survey, and a failed pin / rename.
  const [renamingSurveyId, setRenamingSurveyId] = useState(null);
  const [savedActionError, setSavedActionError] = useState('');
  const savedGroups = useMemo(() => splitPinned(savedSurveys), [savedSurveys]);
  const [serverHasMore, setServerHasMore] = useState(false);
  const [fetchedLimit, setFetchedLimit] = useState(INITIAL_LIMIT);
  // Set when the server answered from a semantically similar query's cache.
  const [matchedQuery, setMatchedQuery] = useState('');
  const [sourceOutcomes, setSourceOutcomes] = useState([]);
  const [briefs, setBriefs] = useState({});
  // Full-text briefings, one per card the reader opened. Never prefetched:
  // a fetch-and-parse is seconds of wall clock and megabytes of transfer.
  const [deepBriefs, setDeepBriefs] = useState({});
  // Citation snowballing (2.2): '' | 'loading' | a summary line to show once.
  const [snowballStatus, setSnowballStatus] = useState('');
  const [snowballError, setSnowballError] = useState('');
  const briefRequestedRef = useRef(new Set());
  const { run: runSearch, stop: stopRequest } = useSearchRequest();

  const PAGE_SIZE = 15;

  const paperKey = (p) =>
    p.doi || p.id || p.url || `${p.title}|${p.year}|${p.source}|${p.authors}`;

  // Same cache entry the Dashboard's "recent surveys" rail reads (1.13), so
  // switching to this tab after visiting the Dashboard costs no round trip.
  const savedQuery = useApiQuery(
    SURVEY_LIST_KEY,
    () => api.get('/api/literature/list').then((d) => d.data || []),
    { enabled: activeTab === 'saved' },
  );

  const fetchSavedSurveys = savedQuery.refresh;

  useEffect(() => {
    setSavedSurveys(savedQuery.data || []);
    setLoadingSaved(savedQuery.loading);
  }, [savedQuery.data, savedQuery.loading]);

  const location = useLocation();
  const navigate = useNavigate();
  const autoSearchKeyRef = useRef('');

  // Explicit user stop. Owns both the abort and the resulting UI state, so the
  // aborted request's own catch/finally can stay silent (see search()).
  const stopSearch = useCallback(() => {
    if (stopRequest()) setSearchError('Search stopped.');
    setLoading(false);
  }, [stopRequest, setLoading, setSearchError]);

  const clearSearch = useCallback(() => {
    stopSearch();
    setQuery('');
    setPapers([]);
    setSearchError('');
    setHasSearched(false);
    setLastQuery('');
    setFilterYear('All');
    setFilterSource('All');
    setVisibleCount(15);
    setSaveStatus('');
    setMatchedQuery('');
    setSourceOutcomes([]);
    setSnowballStatus('');
    setSnowballError('');
  }, [
    stopSearch, setQuery, setPapers, setSearchError, setHasSearched,
    setLastQuery, setFilterYear, setFilterSource, setVisibleCount,
  ]);

  // `fresh` bypasses the server's semantic cache — sent by the "Search instead
  // for X" link when the user rejects a semantically matched result.
  const search = async (q = query, newSearch = true, fresh = false) => {
    const check = validateSearchQuery(q);
    if (!check.ok) {
      setSearchError(check.message);
      if (check.code === 'empty') setHasSearched(false);
      return;
    }

    // useSearchRequest owns cancellation: it drops duplicate submits for the
    // same key, aborts the previous run, and hands each run an isCurrent()
    // so a superseded run cannot write over the run that replaced it.
    await runSearch(normalizeSearchQuery(check.query), async ({ signal, isCurrent }) => {
      setQuery(check.query);
      setLoading(true);
      if (newSearch) setPapers([]);
      setVisibleCount(15);
      setSaveStatus('');
      setSearchError('');
      setHasSearched(true);
      setLastQuery(check.query);
      setFilterYear('All');
      setFilterSource('All');
      setServerHasMore(false);
      setFetchedLimit(INITIAL_LIMIT);
      setMatchedQuery('');
      setSourceOutcomes([]);
      setSnowballStatus('');
      setSnowballError('');
      setBriefs({});
      setDeepBriefs({});
      briefRequestedRef.current = new Set();

      try {
        const res = await api.raw(
          `/api/literature?query=${encodeURIComponent(check.query)}&limit=${INITIAL_LIMIT}${fresh ? '&fresh=true' : ''}`,
          { signal }
        );
        if (!isCurrent()) return;
        if (res.status === 429 || res.status === 503) {
          setSearchError('Rate limit exceeded. Please wait a minute before trying again.');
          setPapers([]);
          return;
        }
        if (!res.ok) {
          setSearchError('Failed to fetch literature. Please try again.');
          setPapers([]);
          return;
        }
        const data = await res.json();
        if (!isCurrent()) return;
        if (data.coherence_check === 'failed') {
          // Server-side guardrail rejected the query. Say so rather than
          // showing an empty result set, which reads as "nothing published".
          setSearchError(`"${check.query}" doesn't look like a research topic. Try a specific field or subject area.`);
          setPapers([]);
          setServerHasMore(false);
          return;
        }
        setPapers(data.data || []);
        setServerHasMore(Boolean(data.has_more));
        setFetchedLimit(data.limit || INITIAL_LIMIT);
        // Non-null only when the server answered from a *different* query's
        // cached result. Surfacing it is mandatory — see services/semantic_cache.py.
        setMatchedQuery(data.matched_query || '');
        setSourceOutcomes(Array.isArray(data.sources) ? data.sources : []);
      } catch (e) {
        // Superseded/cancelled runs report nothing — whoever aborted them owns
        // the UI now. Without this, an aborted run's error and loading reset
        // landed on top of the search that replaced it.
        if (!isCurrent()) return;
        if (isAbortError(e)) return;
        console.error(e);
        setSearchError('Network error. Please try again.');
        setPapers([]);
      } finally {
        if (isCurrent()) setLoading(false);
      }
    });
  };

  // Deep-links from the Dashboard shelf: either open the Saved Surveys tab, or
  // auto-run a search for an incoming query. Both clear nav state afterwards so
  // refresh/back does not re-fire.
  useEffect(() => {
    const incomingTab = location.state?.tab;
    const incomingQuery = location.state?.query;

    if (incomingTab === 'saved') {
      const key = `tab:saved::${location.key || ''}`;
      if (autoSearchKeyRef.current === key) return;
      autoSearchKeyRef.current = key;
      setActiveTab('saved');
      navigate(location.pathname, { replace: true, state: {} });
      return;
    }

    if (!incomingQuery || !String(incomingQuery).trim()) return;

    const key = `${incomingQuery}::${location.key || ''}`;
    if (autoSearchKeyRef.current === key) return;
    autoSearchKeyRef.current = key;

    setActiveTab('search');
    setQuery(incomingQuery);
    void search(incomingQuery, true);
    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state, location.key]);

  const loadMore = async () => {
    if (visibleCount < filteredPapers.length) {
      setVisibleCount(prev => Math.min(prev + PAGE_SIZE, filteredPapers.length));
      return;
    }
    if (!serverHasMore || loadingMore || !lastQuery) return;

    const nextLimit = Math.min(MAX_LIMIT, fetchedLimit + PAGE_SIZE);
    if (nextLimit <= fetchedLimit) return;

    setLoadingMore(true);
    setSearchError('');
    try {
      const res = await api.raw(
        `/api/literature?query=${encodeURIComponent(lastQuery)}&limit=${nextLimit}`
      );
      if (res.status === 429 || res.status === 503) {
        setSearchError('Rate limit exceeded. Please wait a minute before trying again.');
        return;
      }
      if (!res.ok) {
        setSearchError('Failed to load more results. Please try again.');
        return;
      }
      const data = await res.json();
      // Append what is new instead of replacing the list. Replacing threw away
      // the rendered rows and remounted every card, which reset scroll
      // position mid-read; it also briefly emptied `papers` if the wider
      // request came back with a shorter relevant set.
      const incoming = data.data || [];
      setPapers(prev => {
        const seen = new Set(prev.map(paperKey));
        const fresh = incoming.filter(p => !seen.has(paperKey(p)));
        return fresh.length ? [...prev, ...fresh] : prev;
      });
      setServerHasMore(Boolean(data.has_more));
      setFetchedLimit(data.limit || nextLimit);
      setVisibleCount(prev => prev + PAGE_SIZE);
      if (Array.isArray(data.sources) && data.sources.length) {
        setSourceOutcomes(data.sources);
      }
    } catch (e) {
      console.error(e);
      setSearchError('Network error while loading more. Please try again.');
    } finally {
      setLoadingMore(false);
    }
  };

  const filteredPapers = papers.filter(p => {
    if (filterYear !== 'All') {
      const year = p.year === 'Unknown' ? p.published : p.year;
      if (filterYear === 'Last 5 Years') {
        const y = parseInt(year);
        if (isNaN(y) || new Date().getFullYear() - y > 5) return false;
      } else if (String(year) !== filterYear) {
        return false;
      }
    }
    if (filterSource !== 'All') {
      if (p.source !== filterSource) return false;
    }
    return true;
  });

  // ─── Citation snowballing (2.2) ─────────────────────────────────────────────
  //
  // Keyword search finds papers whose words match the query. The literature's
  // own citation graph finds the papers those results are built on and the ones
  // built on them since — which is how a survey is actually assembled, and it
  // reaches work whose title shares not one word with the query.
  //
  // Seeded from the top of what the reader is currently looking at, filters
  // included: if they have narrowed to 2024 onwards, the expansion should
  // follow those papers' citations, not the ones they filtered away.
  const expandByCitations = async () => {
    if (snowballStatus === 'loading') return;
    const seeds = filteredPapers.slice(0, SNOWBALL_SEEDS).map((p) => ({
      title: p.title || '',
      doi: p.doi || '',
      id: p.id || '',
      url: p.url || '',
      arxiv_id: p.arxiv_id || '',
    }));
    if (!seeds.length) return;

    setSnowballStatus('loading');
    setSnowballError('');
    try {
      const data = await api.post('/api/literature/snowball', {
        seeds,
        direction: 'both',
        query: lastQuery,
      });
      const incoming = data.data || [];
      let added = 0;
      setPapers((prev) => {
        const seen = new Set(prev.map(paperKey));
        // A snowballed paper the search already returned is not new — but it
        // *is* now explained by the citation graph, so the provenance is folded
        // onto the row the reader already has rather than dropped with it.
        const merged = prev.map((p) => {
          const hit = incoming.find((c) => paperKey(c) === paperKey(p));
          return hit ? { ...p, ...pickProvenance(hit) } : p;
        });
        const fresh = incoming.filter((p) => !seen.has(paperKey(p)));
        added = fresh.length;
        return fresh.length ? [...merged, ...fresh] : merged;
      });

      const unreachable = Number(data.seeds_unresolvable || 0);
      setSnowballStatus(
        added
          ? `Added ${added} paper${added === 1 ? '' : 's'} from the references and citing works of your top ${data.seeds_used} result${data.seeds_used === 1 ? '' : 's'}.`
          : 'No new papers — the citation graphs returned only papers already in these results.'
      );
      if (unreachable) {
        // Almost always a paper with neither a DOI nor an arXiv id: neither
        // graph is addressable for it. Saying so beats letting the reader
        // wonder why ten seeds produced four papers.
        setSnowballError(
          `${unreachable} of your top results could not be traced — no DOI or arXiv id to look up.`
        );
      }
      if (Array.isArray(data.sources) && data.sources.length) {
        setSourceOutcomes((prev) => mergeOutcomes(prev, data.sources));
      }
    } catch (e) {
      console.error(e);
      setSnowballStatus('');
      // 5/minute on this route, and a citation walk is 40 upstream requests —
      // a second press inside the minute is the likely failure, so say which
      // one it is rather than offering a retry that will fail the same way.
      setSnowballError(
        e?.status === 429 || e?.status === 503
          ? 'Too many citation searches in the last minute. Wait a moment and try again.'
          : 'Could not follow citations. Please try again.'
      );
    }
  };

  const displayedPapers = filteredPapers.slice(0, visibleCount);
  const hasMoreFiltered = visibleCount < filteredPapers.length || serverHasMore;
  const visibleIds = displayedPapers.map(paperKey).join('\n');

  useEffect(() => {
    const needed = displayedPapers.filter((p) => {
      const key = paperKey(p);
      if (!p.abstract || p.abstract === 'No abstract available') return false;
      // /api/literature briefs the head of the first page inline — asking for
      // those again would spend a call to replace bullets we already have.
      if (p.brief?.length) return false;
      if (briefRequestedRef.current.has(key)) return false;
      return true;
    });
    if (!needed.length) return;

    needed.forEach((p) => briefRequestedRef.current.add(paperKey(p)));

    let cancelled = false;
    const BATCH = 3;

    const run = async () => {
      for (let i = 0; i < needed.length; i += BATCH) {
        if (cancelled) return;
        const chunk = needed.slice(i, i + BATCH);
        try {
          const data = await api.post('/api/literature/brief', {
            papers: chunk.map((p) => ({
              title: p.title || '',
              abstract: p.abstract || '',
              doi: p.doi || undefined,
              id: p.id || undefined,
              url: p.url || undefined,
              arxiv_id: p.arxiv_id || undefined,
            })),
          });
          if (cancelled) return;
          const list = data.briefs || [];
          setBriefs((prev) => {
            const next = { ...prev };
            chunk.forEach((p, idx) => {
              const bullets = list[idx]?.bullets || [];
              if (bullets.length) next[paperKey(p)] = { bullets };
            });
            return next;
          });
        } catch {
          chunk.forEach((p) => briefRequestedRef.current.delete(paperKey(p)));
        }
      }
    };
    run();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleIds, api]);

  // One paper, because the reader asked for it. The server tries arXiv and
  // Europe PMC before it downloads a PDF, but this is still seconds of work —
  // it must never run for a page of cards, only for a card someone opened.
  const deepenBrief = useCallback(async (paper) => {
    const key = paperKey(paper);
    setDeepBriefs((prev) => (prev[key]?.loading ? prev : { ...prev, [key]: { loading: true } }));
    try {
      const data = await api.post('/api/literature/deep-brief', {
        paper: {
          title: paper.title || '',
          abstract: paper.abstract || '',
          doi: paper.doi || undefined,
          id: paper.id || undefined,
          url: paper.url || undefined,
          arxiv_id: paper.arxiv_id || undefined,
          arxiv_url: paper.arxiv_url || undefined,
          oa_url: paper.oa_url || undefined,
          pdf_url: paper.pdf_url || undefined,
          pmcid: paper.pmcid || undefined,
        },
      });
      setDeepBriefs((prev) => ({
        ...prev,
        [key]: { bullets: data?.bullets || [], source: data?.source || '' },
      }));
    } catch (e) {
      console.error(e);
      setDeepBriefs((prev) => ({ ...prev, [key]: { error: true } }));
    }
  }, [api]);

  const exportSurveyToPDF = async (papersToExport, queryName) => {
    if (!papersToExport || !papersToExport.length) return;
    const [{ default: jsPDF }, autoTableMod] = await Promise.all([
      import('jspdf'),
      import('jspdf-autotable'),
    ]);
    const autoTable = autoTableMod.default;
    const doc = new jsPDF();
    doc.setFontSize(16);
    doc.text(`Literature Survey: ${queryName}`, 14, 22);
    doc.setFontSize(10);
    doc.text(`Generated on: ${new Date().toLocaleDateString()}`, 14, 30);
    
    const tableColumn = ["Title", "Authors", "Year", "Cites", "Paper link", "PDF / open access"];
    const tableRows = [];
    // The URLs the cells hyperlink to, parallel to tableRows. The printed text
    // is shortened for width, so the full address has to be carried here.
    const rowLinks = [];

    papersToExport.forEach(p => {
      const link = paperLink(p);
      const pdf = paperPdfLink(p);
      rowLinks.push({ paper: link, pdf });
      tableRows.push([
        p.title || 'N/A',
        p.authors || 'N/A',
        p.year === 'Unknown' ? (p.published || 'N/A') : (p.year || 'N/A'),
        p.citations || 0,
        link ? linkLabel(link) : '—',
        pdf ? linkLabel(pdf) : '—',
      ]);
    });

    // Which body columns carry a clickable address, and which side of
    // rowLinks each one reads.
    const LINK_COLUMNS = { 4: 'paper', 5: 'pdf' };
    const LINK_BLUE = [21, 101, 192];

    autoTable(doc, {
      startY: 35,
      // Aligns the table with the heading above it, and makes the body exactly
      // 182mm wide — the column widths below add up to that, and autoTable
      // shrinks columns silently if they do not fit.
      margin: { left: 14, right: 14 },
      head: [tableColumn],
      body: tableRows,
      styles: { fontSize: 8, cellPadding: 2, overflow: 'linebreak' },
      headStyles: { fillColor: [41, 128, 185] },
      columnStyles: {
        0: { cellWidth: 48 },
        1: { cellWidth: 32 },
        2: { cellWidth: 13 },
        3: { cellWidth: 13 },
        4: { cellWidth: 38, fontSize: 6.5, textColor: LINK_BLUE },
        5: { cellWidth: 38, fontSize: 6.5, textColor: LINK_BLUE },
      },
      // autoTable draws text, not links. The annotation has to be laid over
      // the cell after the fact, once its final box is known.
      didDrawCell: (data) => {
        if (data.section !== 'body') return;
        const kind = LINK_COLUMNS[data.column.index];
        if (!kind) return;
        const url = rowLinks[data.row.index]?.[kind];
        if (!url) return;
        doc.link(data.cell.x, data.cell.y, data.cell.width, data.cell.height, { url });
      },
    });

    doc.save(`survey-${queryName.replace(/\s+/g, '-')}.pdf`);
  };

  const exportSurvey = () => {
    exportSurveyToPDF(papers, query);
  };

  const saveSurvey = async () => {
    if (!query || !papers.length) return;
    setSaveStatus('saving');
    try {
      await api.post('/api/literature/save', {
        query, papers, screened: papers.length, sources: sourceOutcomes,
      });
      setSaveStatus('saved');
      // The list the Dashboard rail and the Saved tab both read is now stale.
      invalidate(SURVEY_LIST_KEY);
      setTimeout(() => setSaveStatus(''), 3000);
    } catch { setSaveStatus('error'); }
  };

  /** Confirmed by SavedItemMenu's dialog (was window.confirm); a throw keeps it open. */
  const deleteSurvey = async (survey) => {
    try {
      await api.del(`/api/literature/delete/${encodeURIComponent(survey.query)}`);
    } catch (e) {
      // 404: already gone, which is what was asked for.
      if (e?.status !== 404) throw new Error('That survey could not be deleted. Try again.');
    }
    invalidate(SURVEY_LIST_KEY);
    fetchSavedSurveys();
  };

  /** Pin / rename (ROW-1); the list is shared with the Dashboard rail. */
  const patchSurvey = async (survey, changes) => {
    setSavedActionError('');
    try {
      await api.patch(`/api/literature/surveys/${survey.id}`, changes);
    } catch {
      setSavedActionError('title' in changes
        ? 'That survey could not be renamed. Try again.'
        : 'That survey could not be pinned. Try again.');
    }
    invalidate(SURVEY_LIST_KEY);
    fetchSavedSurveys();
  };

  const renderSavedSurvey = (survey, i) => {
    const screened = surveyScreenedCount(survey);
    const savedDate = surveySavedDateLabel(survey);
    const name = displayName(survey, 'query');
    return (
      <div
        key={survey.id || survey.query}
        className="lit-result-card lit-saved-card animate-slide-up group"
        style={{ animationDelay: `${i * 0.04}s` }}
      >
        <span className="lit-saved-idx" aria-hidden="true">
          {survey.pinned ? <Pin size={12} /> : String(i + 1).padStart(2, '0')}
        </span>
        {renamingSurveyId === survey.id ? (
          <RenameField
            className="lit-saved-rename"
            initial={name}
            label={`Rename ${name}`}
            onSubmit={(value) => { setRenamingSurveyId(null); patchSurvey(survey, { title: value }); }}
            onCancel={() => setRenamingSurveyId(null)}
          />
        ) : (
          <h3 className="lit-saved-title" title={survey.title ? `${name} (${survey.query})` : name}>{name}</h3>
        )}
        <p className="lit-saved-meta">
          <span>{screened} papers</span>
          {savedDate ? (
            <>
              <span className="lit-saved-sep" aria-hidden="true">·</span>
              <span>{savedDate}</span>
            </>
          ) : null}
        </p>
        <div className="lit-saved-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => exportSurveyToPDF(survey.papers, survey.query)}
          >
            <Download size={14} /> PDF
          </button>
          <SavedItemMenu
            name={name}
            kind="survey"
            pinned={survey.pinned === true}
            onTogglePin={survey.id ? () => patchSurvey(survey, { pinned: !survey.pinned }) : undefined}
            onRename={survey.id ? () => setRenamingSurveyId(survey.id) : undefined}
            onDelete={() => deleteSurvey(survey)}
            deleteDetail="and its saved papers will be removed."
          />
        </div>
      </div>
    );
  };

  return (
    <div className="animate-fade-in lit-page">
      <TopBar
        crumbs={[{ label: 'Discover' }, { label: 'Literature Survey' }]}
        context={(hasSearched && query) || undefined}
      />

      <PageHeader
        kicker="Literature desk"
        title="Literature Survey"
        lede="Search many academic sources at once, screen what comes back, and save the survey."
      />

      <LayoutGroup id="lit-tabs">
        <div className="lit-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'search'}
            className={`lit-tab${activeTab === 'search' ? ' is-active' : ''}`}
            onClick={() => setActiveTab('search')}
          >
            {activeTab === 'search' && (
              <motion.span
                layoutId="lit-tab-ink"
                className="lit-tab-ink"
                transition={{ type: 'spring', visualDuration: 0.22, bounce: 0.16 }}
                aria-hidden="true"
              />
            )}
            <Search size={15} /> Search
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'saved'}
            className={`lit-tab${activeTab === 'saved' ? ' is-active' : ''}`}
            onClick={() => setActiveTab('saved')}
          >
            {activeTab === 'saved' && (
              <motion.span
                layoutId="lit-tab-ink"
                className="lit-tab-ink"
                transition={{ type: 'spring', visualDuration: 0.22, bounce: 0.16 }}
                aria-hidden="true"
              />
            )}
            <Bookmark size={15} /> <span className="lit-tab-label-full">Saved&nbsp;</span>Surveys
          </button>
        </div>
      </LayoutGroup>

      {activeTab === 'search' ? (
        <>
      {/* Search desk */}
      <div className="lit-search-desk">
        <div className="lit-search-field">
          <Search size={15} />
          <input
            placeholder="Topic, keyword, or author…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') search();
              if (e.key === 'Escape' && loading) stopSearch();
            }}
            aria-label="Search literature"
          />
          {(query || hasSearched) && (
            <button
              type="button"
              className="lit-search-clear"
              onClick={clearSearch}
              aria-label="Clear search"
              title="Clear search"
            >
              <X size={14} />
            </button>
          )}
        </div>
        {loading ? (
          <Button
            variant="outline"
            size="lg"
            className="lit-search-stop"
            onClick={stopSearch}
            aria-label="Stop search"
            title="Stop"
          >
            <Square size={12} fill="currentColor" /> Stop
          </Button>
        ) : (
          <Button size="lg" className="lit-search-go" onClick={() => search()}>
            <Search size={14} /> Search
          </Button>
        )}
      </div>

      {searchError && (
        <div className={`lit-search-alert${searchError === 'Search stopped.' ? ' is-muted' : ''}`} role="alert">
          <span className="lit-search-alert-text"><X size={15} /> {searchError}</span>
          <button type="button" className="lit-search-alert-dismiss" onClick={() => setSearchError('')} aria-label="Dismiss">
            Dismiss
          </button>
        </div>
      )}

      {/* The server answered with a semantically similar query's results.
          Never render these papers without this banner — the user must be able
          to see the substitution and reject it. */}
      {matchedQuery && !loading && (
        <div className="lit-matched-query" role="status">
          <span className="lit-matched-query-text">
            Showing results for <strong>{matchedQuery}</strong>
          </span>
          <button
            type="button"
            className="lit-matched-query-action"
            onClick={() => search(lastQuery, true, true)}
          >
            Search instead for “{lastQuery}” <ChevronRight size={13} />
          </button>
        </div>
      )}

      {!loading && <SourceOutcomes sources={sourceOutcomes} />}

      {/* Toolbar and Filters */}
      {papers.length > 0 && (
        <div className="lit-filter-bar">
          <div className="lit-filter-top">
            <div className="lit-filter-summary">
              <BookOpen size={15} color="var(--primary)" />
              <span className="lit-filter-count">{filteredPapers.length} results</span>
              <span className="lit-filter-query">for &ldquo;{lastQuery}&rdquo;</span>
            </div>
            <div className="lit-filter-actions">
              <button
                className="btn btn-secondary"
                onClick={expandByCitations}
                disabled={snowballStatus === 'loading'}
                title={`Find the papers your top ${SNOWBALL_SEEDS} results cite, and the papers citing them`}
              >
                {snowballStatus === 'loading'
                  ? <><Spinner size={14} /> Following…</>
                  : <><GitBranch size={14} /> Follow citations</>}
              </button>
              <button className="btn btn-secondary" onClick={saveSurvey} disabled={saveStatus === 'saving'}>
                <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={exportSurvey}>
                <Download size={14} /> Export
              </button>
            </div>
          </div>

          {(snowballStatus && snowballStatus !== 'loading') || snowballError ? (
            <div className="lit-snowball-note" role="status">
              {snowballStatus && snowballStatus !== 'loading' && (
                <span className="lit-snowball-note-text">{snowballStatus}</span>
              )}
              {snowballError && (
                <span className="lit-snowball-note-warn">{snowballError}</span>
              )}
              <button
                type="button"
                className="lit-snowball-note-dismiss"
                onClick={() => { setSnowballStatus(''); setSnowballError(''); }}
                aria-label="Dismiss"
              >
                <X size={13} />
              </button>
            </div>
          ) : null}

          <div className="lit-filter-row">
            <div className="lit-filter-field">
              <label htmlFor="lit-filter-year">Year</label>
              <select id="lit-filter-year" value={filterYear} onChange={e => { setFilterYear(e.target.value); setVisibleCount(15); }}>
                <option value="All">All Years</option>
                <option value="Last 5 Years">Last 5 Years</option>
                <option value="2026">2026</option>
                <option value="2025">2025</option>
                <option value="2024">2024</option>
                <option value="2023">2023</option>
                <option value="2022">2022</option>
                <option value="2021">2021</option>
              </select>
            </div>
            <div className="lit-filter-field">
              <label htmlFor="lit-filter-source">Source</label>
              <select id="lit-filter-source" value={filterSource} onChange={e => { setFilterSource(e.target.value); setVisibleCount(15); }}>
                <option value="All">All Sources</option>
                <option value="Semantic Scholar">Semantic Scholar</option>
                <option value="IEEE">IEEE</option>
                <option value="Springer">Springer</option>
                <option value="CORE">CORE</option>
                <option value="OpenAlex">OpenAlex</option>
                <option value="PubMed">PubMed</option>
                <option value="arXiv">arXiv</option>
                <option value="Crossref">Crossref</option>
                <option value="GitHub">GitHub</option>
                <option value="OpenReview">OpenReview</option>
                <option value="ACLAnthology">ACL Anthology</option>
                <option value="Zenodo">Zenodo</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Papers */}
      <div className="lit-results">
        {loading && (
          <div className="lit-loading">
            <div className="lit-loading-banner">
              <div className="lit-loading-icon">
                <Sparkles size={22} />
              </div>
              <div>
                <h2>Searching Literature...</h2>
                <p>
                  <Spinner size={12} /> Querying multiple academic libraries simultaneously. This deep search may take a few seconds...
                </p>
              </div>
            </div>

            <div className="skeleton-card" />
            <div className="skeleton-card" style={{ animationDelay: '0.1s' }} />
            <div className="skeleton-card" style={{ animationDelay: '0.2s' }} />
            <div className="skeleton-card" style={{ animationDelay: '0.3s' }} />
          </div>
        )}

        {!loading && papers.length === 0 && !hasSearched && !searchError && (
          <div className="lit-empty">
            <BookOpen size={28} />
            Enter a topic to discover relevant research.
          </div>
        )}

        {!loading && papers.length === 0 && hasSearched && !searchError && (
          <div className="lit-empty">
            <BookOpen size={28} />
            No results for '{lastQuery}'. Try a different term.
          </div>
        )}

        {!loading && papers.length > 0 && filteredPapers.length === 0 && (
          <div className="lit-empty">
            <BookOpen size={28} />
            No papers match your selected filters.
          </div>
        )}

        <AnimatePresence mode="popLayout" initial={false}>
        {displayedPapers.map((p) => (
          <motion.article
            key={paperKey(p)}
            className="lit-result-card"
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{
              layout: { type: 'spring', visualDuration: 0.28, bounce: 0.16 },
              opacity: { duration: 0.18 },
              y: { duration: 0.2 },
            }}
          >
            <div className="lit-result-head">
              <ScientificText as="h3" className="lit-result-title" text={p.title} />
              <div className="lit-result-actions">
                {p.oa_url && (
                  <a href={p.oa_url} target="_blank" rel="noreferrer" className="lit-badge lit-badge-oa"
                    title="Open Access — free full text available"
                  >
                    <Unlock size={11} /> Open Access
                  </a>
                )}
                {p.citations > 0 && (
                  <span className="lit-badge">
                    {p.citations.toLocaleString()} citations
                  </span>
                )}
                {/* Why this paper is here at all. A snowballed row does not
                    have to match the query — its justification is the citation
                    link, so the card has to state it. */}
                {p.snowball_seed_count > 0 && (
                  <span
                    className="lit-badge lit-badge-snowball"
                    title={`${citationProvenance(p)}:\n${(p.snowball_seeds || []).join('\n')}`}
                  >
                    <GitBranch size={11} /> {citationProvenance(p)}
                  </span>
                )}
              </div>
            </div>

            <p className="lit-result-meta">
              {p.authors}
              {p.year && p.year !== 'N/A' && <span> · {p.year}</span>}
              {p.published && p.year === 'Unknown' && <span> · {p.published}</span>}
            </p>

            <PaperBrief
              paper={p}
              brief={briefs[paperKey(p)]}
              deep={deepBriefs[paperKey(p)]}
              onDeepen={deepenBrief}
            />

            <div className="lit-result-links">
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-ghost lit-link-btn">
                  <ExternalLink size={12} /> View
                </a>
              )}
              {p.pdf_url && p.pdf_url !== p.url && (
                <a href={p.pdf_url} target="_blank" rel="noreferrer" className="btn btn-ghost lit-link-btn">
                  <FileText size={12} /> PDF
                </a>
              )}
              {p.oa_url && p.oa_url !== p.url && p.oa_url !== p.pdf_url && (
                <a href={p.oa_url} target="_blank" rel="noreferrer" className="btn btn-ghost lit-link-btn lit-link-oa">
                  <Unlock size={12} /> Full Text (OA)
                </a>
              )}
              <a
                href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`}
                target="_blank"
                rel="noreferrer"
                className="btn btn-ghost lit-link-btn"
              >
                <Search size={12} /> Scholar
              </a>
            </div>
          </motion.article>
        ))}
        </AnimatePresence>

        {hasMoreFiltered && !loading && (
          <div className="lit-load-more">
            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? <Spinner size={16} /> : <><ChevronDown size={16} /> Load more results</>}
            </button>
          </div>
        )}
      </div>
        </>
      ) : (
        <div className="lit-results">
          {loadingSaved ? (
            <div className="lit-saved-loading"><Spinner /></div>
          ) : savedSurveys.length === 0 ? (
            <div className="lit-empty">
              <Bookmark size={28} />
              You haven't saved any surveys yet.
            </div>
          ) : (
            <>
              {savedActionError && (
                <div className="lit-saved-error" role="alert">{savedActionError}</div>
              )}
              {[['Pinned', savedGroups.pinned], ['Saved', savedGroups.rest]]
                .filter(([, rows]) => rows.length > 0)
                .map(([label, rows], g) => (
                  <section key={label} aria-label={label}>
                    {savedGroups.pinned.length > 0 && <h3 className="lit-saved-group">{label}</h3>}
                    {rows.map((survey, i) => renderSavedSurvey(
                      survey, g === 0 ? i : savedGroups.pinned.length + i,
                    ))}
                  </section>
                ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
