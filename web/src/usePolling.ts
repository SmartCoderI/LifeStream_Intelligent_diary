import { useCallback, useEffect, useRef, useState } from "react";

/** Loads data, and reloads every `intervalMs` while `shouldPoll(data)` is true. */
export function usePolling<T>(load: () => Promise<T>, shouldPoll: (data: T) => boolean, intervalMs = 2000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const pollRef = useRef(shouldPoll);
  pollRef.current = shouldPoll;

  const refresh = useCallback(async () => {
    try {
      const result = await loadRef.current();
      setData(result);
      setError(null);
      return result;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const tick = async () => {
      const result = await refresh();
      if (!cancelled && (result === null || pollRef.current(result))) {
        timer = setTimeout(tick, intervalMs);
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refresh, intervalMs]);

  return { data, error, refresh };
}
