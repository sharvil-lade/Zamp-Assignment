import { useCallback, useEffect, useState } from "react";
import useAppStore from "../store";

/**
 * Requests already in the air, by cache key.
 *
 * Two mounts of the same page must not become two identical fetches. React's
 * StrictMode does exactly that in development, and a fast back-and-forth can do
 * it in production; either way the second request is asking a question the
 * first has already asked.
 */
const inFlight = new Map();

function once(key, run) {
  if (!key) return run();
  const pending = inFlight.get(key);
  if (pending) return pending;
  const promise = run().finally(() => inFlight.delete(key));
  inFlight.set(key, promise);
  return promise;
}

/**
 * Load something from the API and keep the three states every page needs:
 * loading, error, and the data. `reload` re-fetches after a mutation.
 *
 * `deps` is the dependency list for the loader, the same way useEffect works.
 *
 * `key` opts this resource into the shared cache. With one, a page you have
 * already visited renders its last known data immediately and revalidates in
 * the background, so going back does not mean watching a spinner redraw the
 * same table. Without one — the vendor portal, which is a different person on a
 * different device — nothing is remembered.
 */
export function useResource(loader, deps = [], key = null) {
  const cached = useAppStore((s) => (key ? s.cache[key] : undefined));
  const put = useAppStore((s) => s.put);

  const [data, setData] = useState(cached ?? null);
  const [error, setError] = useState(null);
  // Only a cold load is "loading". A revalidation of something already on
  // screen must not blank the page out.
  const [loading, setLoading] = useState(cached === undefined);
  const [nonce, setNonce] = useState(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps);

  useEffect(() => {
    let live = true;
    if (data === null) setLoading(true);
    once(nonce ? null : key, run)
      .then((result) => {
        if (!live) return;
        setData(result);
        setError(null);
        if (key) put(key, result);
      })
      .catch((err) => live && setError(err))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, nonce, key]);

  // Keep the cache in step when a caller writes directly — the run page polls
  // this way, so navigating away and back shows the latest stage, not the first.
  const write = useCallback((next) => {
    setData(next);
    if (key) put(key, next);
  }, [key, put]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1),
           setData: write };
}

export default useResource;
