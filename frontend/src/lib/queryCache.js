/**
 * A small shared query cache (1.13).
 *
 * Two problems this replaces.
 *
 * **The 5MB crash.** The Dashboard mirrored its search results and related
 * papers into `sessionStorage` on every state change so a navigation away and
 * back did not re-run the search. Papers carry full abstracts; a wide search is
 * hundreds of kilobytes, and `sessionStorage` is a ~5MB per-origin quota that
 * throws `QuotaExceededError` on the *write* — so the failure landed as an
 * unhandled exception in a `useEffect`, not as a degraded cache. Nothing is
 * serialised to disk now: the cache is a module-level Map, which lives exactly
 * as long as the tab's JS context, which is the same lifetime `sessionStorage`
 * was being used for here.
 *
 * **Refetch on every mount.** `/api/literature/list` is fetched by both the
 * Dashboard and the Literature Survey page, on every mount, with no sharing.
 * `useApiQuery` gives them one entry, one in-flight request, and a TTL.
 *
 * This is deliberately not React Query. The whole surface the app needs is a
 * keyed cache, request de-duplication and invalidation — the ticket's stated
 * goal is *smaller* JS, and a dependency an order of magnitude larger than this
 * file would work against it.
 */

const DEFAULT_TTL = 60_000;

/** key -> { value, expiresAt } */
const cache = new Map();
/** key -> Promise, so concurrent mounts share one request */
const inflight = new Map();
/** key -> Set<callback>, so an invalidation reaches mounted hooks */
const subscribers = new Map();

export function readCache(key) {
  const entry = cache.get(key);
  if (!entry) return undefined;
  if (entry.expiresAt <= Date.now()) {
    cache.delete(key);
    return undefined;
  }
  return entry.value;
}

export function writeCache(key, value, ttl = DEFAULT_TTL) {
  cache.set(key, { value, expiresAt: Date.now() + ttl });
  notify(key, value);
}

/** Drop one key, or every key starting with *prefix* when it ends in ':'. */
export function invalidate(key) {
  if (key.endsWith(':')) {
    for (const existing of [...cache.keys()]) {
      if (existing.startsWith(key)) cache.delete(existing);
    }
    for (const existing of [...subscribers.keys()]) {
      if (existing.startsWith(key)) notify(existing, undefined);
    }
    return;
  }
  cache.delete(key);
  notify(key, undefined);
}

export function clearCache() {
  cache.clear();
  inflight.clear();
}

function notify(key, value) {
  const listeners = subscribers.get(key);
  if (!listeners) return;
  for (const listener of listeners) listener(value);
}

export function subscribe(key, listener) {
  if (!subscribers.has(key)) subscribers.set(key, new Set());
  subscribers.get(key).add(listener);
  return () => {
    const listeners = subscribers.get(key);
    if (!listeners) return;
    listeners.delete(listener);
    if (listeners.size === 0) subscribers.delete(key);
  };
}

/**
 * Resolve *key*, sharing one request between concurrent callers.
 *
 * `force` skips the cached value but still de-duplicates: two components asking
 * for a refresh in the same tick get one round trip, not two.
 */
export function fetchQuery(key, fetcher, { ttl = DEFAULT_TTL, force = false } = {}) {
  if (!force) {
    const cached = readCache(key);
    if (cached !== undefined) return Promise.resolve(cached);
  }

  const pending = inflight.get(key);
  if (pending) return pending;

  const promise = Promise.resolve()
    .then(fetcher)
    .then((value) => {
      writeCache(key, value, ttl);
      return value;
    })
    .finally(() => {
      inflight.delete(key);
    });

  inflight.set(key, promise);
  return promise;
}
