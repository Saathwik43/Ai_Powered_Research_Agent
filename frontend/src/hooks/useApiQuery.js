import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchQuery, invalidate, readCache, subscribe } from '../lib/queryCache';

/**
 * Read a cached API query (1.13).
 *
 * Serves the cached value synchronously on mount when one is live, so
 * navigating back to a page does not flash a spinner over data the tab already
 * has. A second component asking for the same key while a request is in flight
 * joins it instead of starting another.
 *
 * `enabled: false` holds the request back — used where the key is not known
 * yet (a draft that has not been chosen, a topic that has not been typed).
 */
export function useApiQuery(key, fetcher, { ttl, enabled = true } = {}) {
  const [data, setData] = useState(() => (key ? readCache(key) : undefined));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // The fetcher is usually an inline arrow, so depending on it directly would
  // re-run the effect on every render of the parent.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(
    async (force = false) => {
      if (!key || !enabled) return undefined;
      setLoading(true);
      setError(null);
      try {
        const value = await fetchQuery(key, () => fetcherRef.current(), { ttl, force });
        setData(value);
        return value;
      } catch (e) {
        setError(e);
        return undefined;
      } finally {
        setLoading(false);
      }
    },
    [key, enabled, ttl],
  );

  useEffect(() => {
    if (!key) return undefined;
    // An invalidation elsewhere (a save, a delete) drops the entry; re-read so
    // the mounted list is not showing something that was just removed.
    return subscribe(key, (value) => setData(value));
  }, [key]);

  useEffect(() => {
    if (!key || !enabled) return;
    const cached = readCache(key);
    if (cached !== undefined) {
      setData(cached);
      return;
    }
    void load();
  }, [key, enabled, load]);

  const refresh = useCallback(() => load(true), [load]);
  const drop = useCallback(() => key && invalidate(key), [key]);

  return { data, loading, error, refresh, invalidate: drop, setData };
}
