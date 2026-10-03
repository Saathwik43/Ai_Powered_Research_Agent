import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion } from 'motion/react';
import {
  Search, TrendingUp, ArrowUpRight, ExternalLink, FileText, X, ArrowRight, Pin,
  Brain, Shield, Cpu, Database, Atom, Eye, Square,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonList } from '../components/Loader';
import PageHeader from '../components/PageHeader';
import TopBar from '../components/TopBar';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { SavedItemMenu, RenameField } from '../components/SavedItemActions';
import { displayName } from '../lib/savedItems';
import { useNavigate } from 'react-router-dom';
import {
  validateSearchQuery,
  normalizeSearchQuery,
  isAbortError,
} from '../utils/searchHeuristics';
import { useSearchRequest } from '../hooks/useSearchRequest';
import { useApiQuery } from '../hooks/useApiQuery';
import { invalidate, readCache, writeCache } from '../lib/queryCache';
import './Dashboard.css';

// Where the last search lives between visits to this page, and for how long.
const DASH_STATE_KEY = 'dashboard:last-search';
const DASH_STATE_TTL = 30 * 60_000;
const SURVEY_LIST_KEY = 'literature:list';

// `short` is the segmented-control label. The field used to be a row in a
// second left rail, which cost ~230px of chrome next to the app sidebar for six
// links; as a segmented control it sits in the reading column and the arXiv code
// moves into the tooltip rather than taking a line of its own.
const CATEGORIES = [
  { title: 'Artificial Intelligence', short: 'AI', subtitle: 'LLMs, agents & reasoning', arxiv: 'cs.AI', query: 'artificial intelligence', Icon: Brain },
  { title: 'Cybersecurity', short: 'Security', subtitle: 'Threat detection & privacy', arxiv: 'cs.CR', query: 'cybersecurity', Icon: Shield },
  { title: 'Machine Learning', short: 'ML', subtitle: 'Models, training & evaluation', arxiv: 'cs.LG', query: 'machine learning', Icon: Cpu },
  { title: 'Data Science', short: 'Data', subtitle: 'Analytics & big data', arxiv: 'cs.DS', query: 'data science', Icon: Database },
  { title: 'Quantum Computing', short: 'Quantum', subtitle: 'Qubits & algorithms', arxiv: 'quant-ph', query: 'quantum computing', Icon: Atom },
  { title: 'Computer Vision', short: 'Vision', subtitle: 'Images, video & perception', arxiv: 'cs.CV', query: 'computer vision', Icon: Eye },
];

// A curated list of starting queries, not a measurement. It previously carried
// "+12%" / "+24%" deltas that no endpoint produced — invented signal, the same
// problem as the hardcoded topic counter removed in SD-1. If a real trend
// series is ever wanted it needs a backend ID of its own.
const STARTING_POINTS = [
  { title: 'Machine Learning in Healthcare', field: 'cs.LG / q-bio' },
  { title: 'Quantum Computing Algorithms', field: 'quant-ph / cs.CC' },
  { title: 'LLM Alignment and Safety', field: 'cs.AI / cs.CL' },
  { title: 'CRISPR Gene Editing', field: 'q-bio.GN' },
];

const impactScore = (i) => ({ 'Very High': 4, 'High': 3, 'Medium': 2, 'Low': 1 }[i] || 1);
const impactHint = (i) => ({
  'Very High': 'Top-priority signal — strong survey candidate.',
  'High': 'Strong signal across recent papers.',
  'Medium': 'Emerging theme — validate with related work.',
  'Low': 'Niche angle for a narrower question.',
}[i] || 'Open a survey to dig deeper.');
const RELATED_PAGE_SIZE = 5;

// One entrance curve for the whole page, so panels and rows share a rhythm
// instead of each section inventing its own. `motion` is configured app-wide
// with reducedMotion="user" (App.jsx), so this is inert under
// prefers-reduced-motion and needs no per-page media query.
const RISE_EASE = [0.2, 0.8, 0.2, 1];
const rise = (i = 0) => ({
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.22, delay: Math.min(i, 8) * 0.03, ease: RISE_EASE },
});

// arXiv reports `citations: 0` because it has no citation index, not because a
// paper has none. Unknown and genuinely-zero are indistinguishable here, so
// both render as an em dash rather than as a measured zero.
const citeCount = (paper) => (Number(paper?.citations) > 0 ? Number(paper.citations).toLocaleString() : '—');

// Every field below is already on the paper dicts the search endpoints return
// (title, authors, year, venue, citations, source, doi, url, pdf_url). None of
// this needs a backend change — the rows were simply not showing it.
const paperLinks = (paper) => {
  const links = [];
  if (paper.url) links.push({ label: 'Abstract', href: paper.url });
  if (paper.pdf_url) links.push({ label: 'PDF', href: paper.pdf_url });
  if (paper.doi) links.push({ label: 'DOI', href: `https://doi.org/${paper.doi}` });
  if (!links.length) {
    links.push({ label: 'Scholar', href: `https://scholar.google.com/scholar?q=${encodeURIComponent(paper.title)}` });
  }
  return links;
};

export default function Dashboard() {
  const { api } = useAuth();
  // The last search is remembered in memory, not sessionStorage (1.13).
  // Abstracts are large: a wide search serialised to ~hundreds of KB, and
  // sessionStorage's ~5MB per-origin quota throws on the *write*, so the
  // failure arrived as an unhandled exception inside a useEffect rather than
  // as a lost cache. This survives navigating away and back within the tab,
  // which is the only thing the storage was ever buying.
  const restored = readCache(DASH_STATE_KEY) || {};
  const [topic, setTopic] = useState(restored.topic || '');
  const [suggestions, setSuggestions] = useState([]);
  const [showSug, setShowSug] = useState(false);
  // Highlighted suggestion for arrow-key navigation. -1 means "none", so Enter
  // runs the typed query rather than a row the user never moved onto.
  const [sugIndex, setSugIndex] = useState(-1);
  const [results, setResults] = useState(restored.results || []);
  const [relatedPapers, setRelatedPapers] = useState(restored.relatedPapers || []);
  const [visibleRelatedCount, setVisibleRelatedCount] = useState(RELATED_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [papersLoading, setPapersLoading] = useState(false);
  const [activeCategory, setActiveCategory] = useState(null);
  const [categoryPapers, setCategoryPapers] = useState([]);
  const [catLoading, setCatLoading] = useState(false);
  const [error, setError] = useState('');
  const [hasSearched, setHasSearched] = useState(Boolean(restored.hasSearched));
  const [recentSurveys, setRecentSurveys] = useState([]);
  const [loadingRecent, setLoadingRecent] = useState(false);
  // Survey whose name is being edited inline (ROW-2).
  const [renamingSurveyId, setRenamingSurveyId] = useState(null);
  const [surveyActionError, setSurveyActionError] = useState('');

  useEffect(() => {
    writeCache(DASH_STATE_KEY, { topic, results, relatedPapers, hasSearched }, DASH_STATE_TTL);
  }, [topic, results, relatedPapers, hasSearched]);

  useEffect(() => {
    setVisibleRelatedCount(RELATED_PAGE_SIZE);
  }, [relatedPapers]);

  const debounce = useRef(null);
  const inputWrap = useRef(null);
  const navigate = useNavigate();
  // Two independent request slots: the topic/paper search and the arXiv
  // category feed run concurrently and must cancel independently.
  const { run: runDiscover, stop: stopDiscoverRequest } = useSearchRequest();
  const { run: runCategoryFeed, stop: stopCategoryFeed } = useSearchRequest();
  const { run: runSuggest } = useSearchRequest();

  useEffect(() => () => clearTimeout(debounce.current), []);

  // Suggestions come from /api/suggest, which ranks prefix, word-prefix and
  // initials matches over queries this deployment has actually run. The old
  // client-side `SUGGESTIONS.filter(s => s.includes(...))` could not match an
  // acronym at all — "CNN" and "ML" returned nothing.
  const handleInputChange = useCallback((val) => {
    setTopic(val);
    setSugIndex(-1);
    clearTimeout(debounce.current);
    if (!val.trim()) { setSuggestions([]); setShowSug(false); return; }
    debounce.current = setTimeout(() => {
      void runSuggest(val.trim().toLowerCase(), async ({ signal, isCurrent }) => {
        try {
          const res = await api.raw(
            `/api/suggest?q=${encodeURIComponent(val.trim())}`,
            { signal }
          );
          if (!res.ok || !isCurrent()) return;
          const data = await res.json();
          if (!isCurrent()) return;
          const found = data.data || [];
          setSuggestions(found);
          setShowSug(found.length > 0);
          setSugIndex(-1);
        } catch (e) {
          // A failed suggestion lookup is not worth surfacing — the user is
          // mid-typing and the search itself still works.
          if (isCurrent() && !isAbortError(e)) setShowSug(false);
        }
      });
    }, 180);
  }, [api, runSuggest]);

  // Explicit user stop. Owns both the abort and the resulting UI state, so the
  // aborted request's own catch/finally can stay silent (see discover()).
  const stopDiscover = useCallback(() => {
    if (stopDiscoverRequest()) setError('Search stopped.');
    setLoading(false);
    setPapersLoading(false);
  }, [stopDiscoverRequest]);

  const closeFeed = useCallback(() => {
    stopCategoryFeed();
    setCatLoading(false);
    setActiveCategory(null);
    setCategoryPapers([]);
  }, [stopCategoryFeed]);

  const clearSearch = useCallback(() => {
    stopDiscover();
    stopCategoryFeed();
    setCatLoading(false);
    clearTimeout(debounce.current);
    setTopic('');
    setSuggestions([]);
    setShowSug(false);
    setSugIndex(-1);
    setResults([]);
    setRelatedPapers([]);
    setError('');
    setHasSearched(false);
    setActiveCategory(null);
    setCategoryPapers([]);
    invalidate(DASH_STATE_KEY);
  }, [stopDiscover, stopCategoryFeed]);

  const discover = async (q = topic) => {
    const check = validateSearchQuery(q);
    if (!check.ok) {
      setError(check.message);
      if (check.code === 'empty') setHasSearched(false);
      return;
    }
    // useSearchRequest drops duplicate submits for the same key, aborts the
    // previous run, and hands each run an isCurrent() so a superseded run
    // cannot write over the run that replaced it.
    await runDiscover(normalizeSearchQuery(check.query), async ({ signal, isCurrent }) => {
      clearTimeout(debounce.current);
      setTopic(check.query);
      setShowSug(false);
      setSugIndex(-1);
      setLoading(true);
      setPapersLoading(true);
      setRelatedPapers([]);
      setError('');
      setHasSearched(true);
      try {
        const [topicRes, paperRes] = await Promise.all([
          api.raw(`/api/topics?intent=${encodeURIComponent(check.query)}`, {
            signal,
          }),
          api.raw(`/api/literature?query=${encodeURIComponent(check.query)}&limit=6`, {
            signal,
          }),
        ]);

        if (!isCurrent()) return;

        if (topicRes.status === 429 || paperRes.status === 429 || topicRes.status === 503 || paperRes.status === 503) {
          if (topicRes.status === 503 || paperRes.status === 503) {
            try {
              const data = await (topicRes.status === 503 ? topicRes : paperRes).json();
              if (data?.detail?.verification_unavailable) {
                setError('Verification temporarily unavailable, please try again shortly.');
                setResults([]);
                setRelatedPapers([]);
                return;
              }
            } catch (e) {}
          }
          setError('Rate limit exceeded. Please wait a minute before trying again.');
          return;
        }
        if (!topicRes.ok) {
          const topicData = await topicRes.json().catch(() => ({}));
          setError(topicData.detail || 'Failed to discover topics. Please try again.');
          return;
        }
        if (!paperRes.ok) {
          setError('Failed to fetch literature data. Please try again.');
          return;
        }

        const topicData = await topicRes.json();
        if (topicData.coherence_check === 'failed') {
          setError(`"${check.query}" doesn't look like a research topic. Try a specific field or subject area.`);
          setResults([]);
          setRelatedPapers([]);
          return;
        }

        const paperData = await paperRes.json();
        if (!isCurrent()) return;
        setResults(topicData.data || []);
        setRelatedPapers(paperData.data || []);
      } catch (e) {
        // Superseded/cancelled runs report nothing — whoever aborted them owns
        // the UI now. Without this, an aborted run's error and loading reset
        // landed on top of the search that replaced it.
        if (!isCurrent()) return;
        if (isAbortError(e)) return;
        console.error(e);
        setError('Network error. Please try again.');
      } finally {
        if (isCurrent()) {
          setLoading(false);
          setPapersLoading(false);
        }
      }
    });
  };

  const openCategory = async (cat) => {
    setActiveCategory(cat);
    setCategoryPapers([]);
    setCatLoading(true);
    discover(cat.query);

    // Keyed by category so clicking the same rail item twice is a no-op while
    // its feed is loading, but switching categories cancels the previous one.
    await runCategoryFeed(cat.arxiv, async ({ signal, isCurrent }) => {
      try {
        const res = await api.raw(
          `/api/arxiv/feed?category=${cat.arxiv}&limit=9`,
          { signal }
        );
        const data = await res.json();
        if (!isCurrent()) return;
        setCategoryPapers(data.data || []);
      } catch (e) {
        if (!isCurrent() || isAbortError(e)) return;
        console.error(e);
      } finally {
        if (isCurrent()) setCatLoading(false);
      }
    });
  };

  // The segmented control is the only field selector now, so "All" is where
  // the old "Close feed" button used to be: it drops the arXiv feed and leaves
  // the current search results standing.
  const onFieldChange = (value) => {
    if (!value) return;
    if (value === 'all') { closeFeed(); return; }
    const cat = CATEGORIES.find((c) => c.arxiv === value);
    if (cat) openCategory(cat);
  };

  const onQueryKeyDown = (e) => {
    const open = showSug && suggestions.length > 0;
    if (e.key === 'ArrowDown' && open) {
      e.preventDefault();
      setSugIndex((i) => (i + 1) % suggestions.length);
      return;
    }
    if (e.key === 'ArrowUp' && open) {
      e.preventDefault();
      setSugIndex((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
      return;
    }
    if (e.key === 'Enter') {
      if (open && sugIndex >= 0) discover(suggestions[sugIndex]);
      else discover();
      return;
    }
    if (e.key === 'Escape') {
      if (loading) stopDiscover();
      else { setShowSug(false); setSugIndex(-1); }
    }
  };

  useEffect(() => {
    const h = (e) => { if (!inputWrap.current?.contains(e.target)) setShowSug(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  // Shared with the Literature Survey page: both used to fetch this list on
  // every mount, so opening one after the other was two round trips for the
  // same rows.
  const surveyQuery = useApiQuery(
    SURVEY_LIST_KEY,
    () => api.get('/api/literature/list').then((d) => d.data || []),
  );

  useEffect(() => {
    setRecentSurveys((surveyQuery.data || []).slice(0, 3));
    setLoadingRecent(surveyQuery.loading);
  }, [surveyQuery.data, surveyQuery.loading]);

  /** Confirmed by SavedItemMenu's dialog; a throw keeps the dialog open with it. */
  const deleteSurvey = async (survey) => {
    try {
      await api.del(`/api/literature/delete/${encodeURIComponent(survey.query)}`);
    } catch (err) {
      // 404: already gone, which is what was asked for.
      if (err?.status !== 404) throw new Error('That survey could not be deleted. Try again.');
    }
    setRecentSurveys((prev) => prev.filter((s) => s.query !== survey.query));
    invalidate(SURVEY_LIST_KEY);
  };

  /** Pin / rename (ROW-1). The list is shared with Literature Survey, so refetch it. */
  const patchSurvey = async (survey, changes) => {
    setSurveyActionError('');
    try {
      await api.patch(`/api/literature/surveys/${survey.id}`, changes);
    } catch {
      setSurveyActionError('title' in changes
        ? 'That survey could not be renamed. Try again.'
        : 'That survey could not be pinned. Try again.');
    }
    invalidate(SURVEY_LIST_KEY);
  };

  const showWelcome = !loading && results.length === 0 && !error && !activeCategory;
  const showResults = !loading && results.length > 0;

  // Impact is an ordinal label, so it maps onto Badge's variants rather than a
  // colour of its own.
  const impactVariant = (impact) => (
    { 'Very High': 'default', High: 'secondary', Medium: 'outline', Low: 'outline' }[impact]
    || 'outline'
  );

  return (
    <div className="dsb">
      <TopBar
        crumbs={[{ label: 'Discover' }, { label: 'Topic Discovery' }]}
        context={activeCategory ? `${activeCategory.arxiv} feed` : (hasSearched && topic) || undefined}
      />

      <div className="dsb-page">
        <PageHeader
          kicker="Literature desk"
          title="Topic Discovery"
          lede="Search a field, method or question. Ranked directions first, then the papers behind them."
          facts={[
            <><FileText size={11} /> {recentSurveys.length} saved {recentSurveys.length === 1 ? 'survey' : 'surveys'}</>,
            ...(results.length ? [<><TrendingUp size={11} /> {results.length} directions</>] : []),
          ]}
        />

        {/* ── Search desk: sticks under the context bar so the query stays
            reachable while reading down a long result set. ── */}
        <div className="dsb-desk">
          <div className="dsb-query">
            <div ref={inputWrap} className="dsb-query-field">
              <Search size={16} className="dsb-query-icon" />
              <Input
                className="dsb-query-input"
                placeholder="Search a field, method, or research question…"
                value={topic}
                onChange={e => handleInputChange(e.target.value)}
                onKeyDown={onQueryKeyDown}
                onFocus={() => suggestions.length && setShowSug(true)}
                aria-label="Discover research topics"
                role="combobox"
                aria-expanded={showSug}
                aria-controls="dsb-suggestions"
                aria-activedescendant={sugIndex >= 0 ? `dsb-sug-${sugIndex}` : undefined}
                autoComplete="off"
              />
              {(topic || hasSearched) && (
                <button
                  type="button"
                  className="dsb-query-clear"
                  onClick={clearSearch}
                  aria-label="Clear search"
                  title="Clear search"
                >
                  <X size={14} />
                </button>
              )}
              {loading ? (
                <Button
                  variant="outline"
                  size="lg"
                  className="dsb-query-go"
                  onClick={stopDiscover}
                  aria-label="Stop search"
                  title="Stop"
                >
                  <Square size={12} fill="currentColor" /> Stop
                </Button>
              ) : (
                <Button size="lg" className="dsb-query-go" onClick={() => discover()}>
                  Discover
                  <ArrowRight size={14} />
                </Button>
              )}
              {showSug && (
                <div className="dsb-suggestions" id="dsb-suggestions" role="listbox">
                  {suggestions.map((s, i) => (
                    <button
                      key={i}
                      id={`dsb-sug-${i}`}
                      type="button"
                      role="option"
                      aria-selected={i === sugIndex}
                      className={`dsb-suggestion${i === sugIndex ? ' is-active' : ''}`}
                      onMouseEnter={() => setSugIndex(i)}
                      onMouseDown={() => discover(s)}
                    >
                      <Search size={12} /> {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="dsb-fields-scroll">
            <ToggleGroup
              type="single"
              spacing={0}
              value={activeCategory?.arxiv || 'all'}
              onValueChange={onFieldChange}
              className="dsb-fields"
              aria-label="Research fields"
            >
              <ToggleGroupItem value="all" className="dsb-field">All fields</ToggleGroupItem>
              {CATEGORIES.map((cat) => (
                <Tooltip key={cat.arxiv}>
                  <TooltipTrigger asChild>
                    <ToggleGroupItem value={cat.arxiv} className="dsb-field">
                      <cat.Icon size={14} />
                      {cat.short}
                    </ToggleGroupItem>
                  </TooltipTrigger>
                  <TooltipContent>
                    {cat.title} · <span className="dsb-mono">{cat.arxiv}</span>
                  </TooltipContent>
                </Tooltip>
              ))}
            </ToggleGroup>
          </div>
        </div>

        {error && (
          <div className={`dsb-error${error === 'Search stopped.' ? ' is-muted' : ''}`} role="alert">
            <span className="dsb-error-text"><X size={14} /> {error}</span>
            <button type="button" className="dsb-error-dismiss" onClick={() => setError('')} aria-label="Dismiss">
              Dismiss
            </button>
          </div>
        )}

        {loading && (
          <motion.div {...rise()} className="dsb-block">
            <Card>
              <CardHeader className="dsb-head">
                <CardTitle className="dsb-title">
                  <Spinner size={15} /> Scanning literature…
                </CardTitle>
              </CardHeader>
              <CardContent>
                <SkeletonList count={4} />
              </CardContent>
            </Card>
          </motion.div>
        )}

        {showWelcome && (
          <motion.section {...rise()} className="dsb-block">
            <Card>
              <CardHeader className="dsb-head">
                <CardTitle className="dsb-title">
                  <TrendingUp size={15} /> Starting points
                </CardTitle>
                <CardAction>
                  <Badge variant="outline">Curated, not ranked</Badge>
                </CardAction>
              </CardHeader>
              <CardContent className="dsb-body-flush">
                <ol className="dsb-rows">
                  {STARTING_POINTS.map((item, idx) => (
                    <motion.li key={item.title} {...rise(idx)}>
                      {/* One line, not two stacked: the field code is a second
                          column on the right rather than a sub-line, so the row
                          uses the column's width instead of leaving half of it
                          empty. */}
                      <button type="button" className="dsb-row" onClick={() => discover(item.title)}>
                        <span className="dsb-idx">{String(idx + 1).padStart(2, '0')}</span>
                        <span className="dsb-row-title">{item.title}</span>
                        <span className="dsb-row-meta">{item.field}</span>
                        <ArrowRight size={14} className="dsb-row-go" />
                      </button>
                    </motion.li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          </motion.section>
        )}

        {showResults && (
          <>
            <motion.section {...rise()} className="dsb-block">
              <Card>
                <CardHeader className="dsb-head">
                  <CardTitle className="dsb-title">
                    Directions for <em>{topic}</em>
                  </CardTitle>
                  <CardAction>
                    <Badge variant="outline">{results.length} ranked</Badge>
                  </CardAction>
                </CardHeader>
                <CardContent className="dsb-body-flush">
                  <ol className="dsb-rows">
                    {results.map((t, i) => {
                      const score = impactScore(t.impact);
                      // Search the raw extracted phrase, not the Title-Cased
                      // label — `title` is for display only.
                      const surveyQ = t.query || t.title;
                      const openSurvey = () => navigate('/literature-survey', {
                        state: { query: surveyQ, autoSearch: true },
                      });
                      return (
                        <motion.li key={i} {...rise(i)}>
                          <div
                            className="dsb-direction"
                            role="button"
                            tabIndex={0}
                            onClick={openSurvey}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                openSurvey();
                              }
                            }}
                          >
                            <span className="dsb-idx">{String(i + 1).padStart(2, '0')}</span>
                            <div className="dsb-direction-body">
                              <div className="dsb-direction-top">
                                <h3 className="dsb-direction-title">{t.title}</h3>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Badge variant={impactVariant(t.impact)}>{t.impact}</Badge>
                                  </TooltipTrigger>
                                  <TooltipContent>{impactHint(t.impact)}</TooltipContent>
                                </Tooltip>
                              </div>
                              <div className="dsb-direction-foot">
                                <div
                                  className="dsb-meter"
                                  role="img"
                                  aria-label={`Impact ${score} of 4`}
                                >
                                  {[1, 2, 3, 4].map((level) => (
                                    <span key={level} className={`dsb-meter-seg${level <= score ? ' is-on' : ''}`} />
                                  ))}
                                </div>
                              </div>
                              <div className="dsb-direction-actions">
                                <Button
                                  size="sm"
                                  onClick={(e) => { e.stopPropagation(); openSurvey(); }}
                                >
                                  Start survey <ArrowRight size={12} />
                                </Button>
                                <a
                                  href={`https://scholar.google.com/scholar?q=${encodeURIComponent(t.title)}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="dsb-link"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  Scholar <ExternalLink size={11} />
                                </a>
                              </div>
                            </div>
                          </div>
                        </motion.li>
                      );
                    })}
                  </ol>
                </CardContent>
              </Card>
            </motion.section>

            <motion.section {...rise(1)} className="dsb-block">
              <Card>
                <CardHeader className="dsb-head">
                  <CardTitle className="dsb-title">
                    <FileText size={15} /> Related papers
                  </CardTitle>
                  <CardAction>
                    <Button
                      variant="link"
                      size="xs"
                      onClick={() => navigate('/literature-survey', { state: { query: topic, autoSearch: true } })}
                    >
                      Literature survey <ArrowUpRight size={13} />
                    </Button>
                  </CardAction>
                </CardHeader>
                <CardContent className="dsb-body-flush">
                  {papersLoading && (
                    <p className="dsb-status"><Spinner size={15} /> Loading papers…</p>
                  )}
                  {!papersLoading && relatedPapers.length === 0 && (
                    <p className="dsb-status">No related papers found for this query.</p>
                  )}
                  {!papersLoading && relatedPapers.length > 0 && (
                    <>
                      {/* Table ships its own `overflow-x-auto` container, so
                          the page does not add a second scroller around it. */}
                      <Table className="dsb-table">
                          <TableHeader>
                            <TableRow>
                              <TableHead className="col-num">#</TableHead>
                              <TableHead className="col-paper">Paper</TableHead>
                              <TableHead className="col-venue">Venue</TableHead>
                              <TableHead className="col-year">Year</TableHead>
                              <TableHead className="col-cited">Cited</TableHead>
                              <TableHead className="col-source">Source</TableHead>
                              <TableHead className="col-links">Read</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {relatedPapers.slice(0, visibleRelatedCount).map((paper, i) => (
                              <TableRow key={paper.id || `${paper.title}-${i}`}>
                                <TableCell className="col-num">[{i + 1}]</TableCell>
                                <TableCell className="col-paper">
                                  <a
                                    className="dsb-cite-title"
                                    href={paper.url || `https://scholar.google.com/scholar?q=${encodeURIComponent(paper.title)}`}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={paper.title}
                                  >
                                    {paper.title}
                                  </a>
                                  <span className="dsb-cite-meta">{paper.authors || 'Authors unavailable'}</span>
                                </TableCell>
                                <TableCell className="col-venue" title={paper.venue || ''}>
                                  {paper.venue || '—'}
                                </TableCell>
                                <TableCell className="col-year">{paper.year || '—'}</TableCell>
                                <TableCell className="col-cited">{citeCount(paper)}</TableCell>
                                <TableCell className="col-source">{paper.source || '—'}</TableCell>
                                <TableCell className="col-links">
                                  <span className="dsb-links">
                                    {paperLinks(paper).map((link) => (
                                      <a key={link.label} href={link.href} target="_blank" rel="noreferrer">
                                        {link.label}
                                      </a>
                                    ))}
                                  </span>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                      </Table>
                      {visibleRelatedCount < relatedPapers.length && (
                        <div className="dsb-more">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setVisibleRelatedCount((n) => Math.min(n + RELATED_PAGE_SIZE, relatedPapers.length))}
                          >
                            Load more
                            <span className="dsb-mono">{visibleRelatedCount}/{relatedPapers.length}</span>
                          </Button>
                        </div>
                      )}
                    </>
                  )}
                </CardContent>
              </Card>
            </motion.section>
          </>
        )}

        {activeCategory && (
          <motion.section {...rise(2)} className="dsb-block">
            <Card>
              <CardHeader className="dsb-head">
                <CardTitle className="dsb-title">
                  Latest in {activeCategory.title}
                  <Badge variant="outline" className="dsb-mono">{activeCategory.arxiv}</Badge>
                </CardTitle>
                <CardAction>
                  <Button variant="link" size="xs" onClick={closeFeed}>
                    <X size={13} /> Close feed
                  </Button>
                </CardAction>
              </CardHeader>
              <CardContent className="dsb-body-flush">
                {catLoading && (
                  <p className="dsb-status"><Spinner size={15} /> Loading archive…</p>
                )}
                {!catLoading && categoryPapers.length === 0 && (
                  <p className="dsb-status">No recent papers in this category.</p>
                )}
                {!catLoading && categoryPapers.length > 0 && (
                  <div className="dsb-rows">
                    {categoryPapers.map((p, i) => (
                      <motion.article key={i} {...rise(i)} className="dsb-feed-item">
                        <span className="dsb-idx">{String(i + 1).padStart(2, '0')}</span>
                        <div className="dsb-feed-body">
                          <h3 className="dsb-cite-title">{p.title}</h3>
                          <p className="dsb-cite-meta">{p.authors}</p>
                          <p className="dsb-feed-facts">
                            {p.published ? <span>{p.published}</span> : null}
                            {p.venue ? <span>{p.venue}</span> : null}
                            {(p.categories || []).slice(0, 3).map((c) => (
                              <span key={c} className="dsb-feed-cat">{c}</span>
                            ))}
                          </p>
                          {p.abstract && (
                            <p className="dsb-feed-abstract">{p.abstract.substring(0, 160)}…</p>
                          )}
                          <div className="dsb-links">
                            {paperLinks(p).map((link) => (
                              <a key={link.label} href={link.href} target="_blank" rel="noreferrer">
                                {link.label}
                              </a>
                            ))}
                            <a
                              href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Scholar
                            </a>
                          </div>
                        </div>
                      </motion.article>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.section>
        )}

        {!results.length && !loading && !activeCategory && hasSearched && !error && (
          <motion.div {...rise()} className="dsb-block">
            <div className="dsb-empty">
              <Search size={18} />
              <h3>No results for &ldquo;{topic}&rdquo;</h3>
              <p>Try a more specific field, or pick one of the fields above.</p>
            </div>
          </motion.div>
        )}

        {/* ── Saved surveys: the "continue where I left off" shelf. It was a
            second-rail section; as the last panel it keeps one reading column
            and still answers "what should I read next". ── */}
        <motion.section {...rise(3)} className="dsb-block">
          <Card>
            <CardHeader className="dsb-head">
              <CardTitle className="dsb-title">
                <FileText size={15} /> Saved surveys
              </CardTitle>
              <CardAction>
                <Button
                  variant="link"
                  size="xs"
                  onClick={() => navigate('/literature-survey', { state: { tab: 'saved' } })}
                >
                  View all <ArrowUpRight size={13} />
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="dsb-body-flush">
              {loadingRecent ? (
                <div className="dsb-pad">
                  <SkeletonList count={2} />
                </div>
              ) : recentSurveys.length === 0 ? (
                <p className="dsb-status">
                  Saved literature surveys appear here. Discover a topic and start one to fill this shelf.
                </p>
              ) : (
                <ul className="dsb-rows">
                  {surveyActionError && (
                    <li className="dsb-status" role="alert">{surveyActionError}</li>
                  )}
                  {recentSurveys.map((survey, i) => {
                    const name = displayName(survey, 'query');
                    return (
                      <li key={survey.id || survey.query} className="dsb-survey group">
                        {renamingSurveyId === survey.id ? (
                          <div className="dsb-survey-main">
                            <span className="dsb-idx">{String(i + 1).padStart(2, '0')}</span>
                            <RenameField
                              initial={name}
                              label={`Rename ${name}`}
                              onSubmit={(value) => { setRenamingSurveyId(null); patchSurvey(survey, { title: value }); }}
                              onCancel={() => setRenamingSurveyId(null)}
                            />
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="dsb-survey-main"
                            title={survey.title ? `${name} (${survey.query})` : name}
                            onClick={() => navigate('/literature-survey', { state: { query: survey.query, autoSearch: true } })}
                          >
                            <span className="dsb-idx">
                              {survey.pinned ? <Pin size={12} aria-label="Pinned" /> : String(i + 1).padStart(2, '0')}
                            </span>
                            <span className="dsb-row-title">{name}</span>
                            <span className="dsb-row-meta">{survey.papers?.length || 0} papers</span>
                          </button>
                        )}
                        <SavedItemMenu
                          className="dsb-survey-menu"
                          name={name}
                          kind="survey"
                          pinned={survey.pinned === true}
                          onTogglePin={survey.id ? () => patchSurvey(survey, { pinned: !survey.pinned }) : undefined}
                          onRename={survey.id ? () => setRenamingSurveyId(survey.id) : undefined}
                          onDelete={() => deleteSurvey(survey)}
                          deleteDetail="and its saved papers will be removed."
                        />
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </motion.section>
      </div>

    </div>
  );
}
