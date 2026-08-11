import { useCallback, useEffect, useRef } from 'react';

/**
 * Owns the lifecycle of a cancellable, self-superseding request.
 *
 * Three bugs kept recurring in the pages that did this by hand, and all three
 * are handled here once:
 *
 * 1. A superseded request's `catch`/`finally` landed on the request that
 *    replaced it — clearing the new spinner and showing "Search stopped." while
 *    the new search was still in flight. Every callback gets `isCurrent()`;
 *    a run that no longer owns the controller must touch no shared state.
 * 2. The duplicate-submit guard read `loading` from the render closure, which
 *    is a render behind on rapid submits, so the duplicate went through anyway.
 *    The guard here reads a ref.
 * 3. In-flight requests outlived the component. Unmount aborts.
 *
 * The caller keeps its own loading/error state — this hook deliberately owns
 * cancellation only, because "what does the UI show while loading" differs on
 * every page that uses it.
 *
 * @example
 * const { run, stop, isRunning } = useSearchRequest();
 *
 * run(normalizedQuery, async ({ signal, isCurrent }) => {
 *   setLoading(true);
 *   try {
 *     const res = await authFetch(url, { signal });
 *     if (!isCurrent()) return;
 *     setData(await res.json());
 *   } catch (e) {
 *     if (!isCurrent() || isAbortError(e)) return;
 *     setError('Network error.');
 *   } finally {
 *     if (isCurrent()) setLoading(false);
 *   }
 * });
 */
export function useSearchRequest() {
  const abortRef = useRef(null);
  const keyRef = useRef('');

  useEffect(() => () => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  /** True while a request is in flight. Ref-based, so never a render stale. */
  const isRunning = useCallback(() => abortRef.current !== null, []);

  /**
   * Abort the in-flight request, if any.
   * @returns {boolean} whether something was actually aborted — callers use
   *   this to decide whether to show a "stopped" message.
   */
  const stop = useCallback(() => {
    if (!abortRef.current) return false;
    abortRef.current.abort();
    abortRef.current = null;
    keyRef.current = '';
    return true;
  }, []);

  /**
   * Run `task` as the current request, superseding any in-flight one.
   *
   * @param {string} key Identity of this request. A second call with the same
   *   key while the first is still running is ignored as a duplicate submit.
   * @param {(ctx: {signal: AbortSignal, isCurrent: () => boolean}) => Promise<void>} task
   * @returns {Promise<boolean>} false if the call was skipped as a duplicate.
   */
  const run = useCallback(async (key, task) => {
    if (abortRef.current && keyRef.current === key) return false;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    keyRef.current = key;

    const isCurrent = () => abortRef.current === controller;

    try {
      await task({ signal: controller.signal, isCurrent });
    } finally {
      // Only clear if we are still the current run — a task that was
      // superseded must not release the newer run's controller.
      if (isCurrent()) {
        abortRef.current = null;
        keyRef.current = '';
      }
    }
    return true;
  }, []);

  return { run, stop, isRunning };
}

export default useSearchRequest;
