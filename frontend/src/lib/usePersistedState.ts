import { useEffect, useRef, useState } from "react";

/**
 * Like useState, but survives page reloads by mirroring the value to localStorage.
 *
 * - `key`   : localStorage key (use a project-prefixed name to avoid collisions)
 * - `initial`: returned on first render and on localStorage parse failure
 *
 * Notes:
 * - The setter is referentially stable across renders.
 * - We write *after* the state update via a ref to avoid re-render loops.
 * - All errors (storage quota, JSON parse, disabled storage) are swallowed silently
 *   so the app keeps working in incognito / SSR / etc.
 */
export function usePersistedState<T>(key: string, initial: T): [T, (v: T | ((cur: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw == null) return initial;
      return JSON.parse(raw) as T;
    } catch {
      return initial;
    }
  });

  // Debounce writes a bit so rapid updates (e.g. every photo_progress) don't
  // hammer localStorage on every keystroke. 200ms is a good trade-off.
  const writeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (writeTimerRef.current) clearTimeout(writeTimerRef.current);
    writeTimerRef.current = setTimeout(() => {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {
        // quota exceeded or storage disabled — silently drop
      }
    }, 200);
    return () => {
      if (writeTimerRef.current) clearTimeout(writeTimerRef.current);
    };
  }, [key, value]);

  return [value, setValue];
}

/**
 * Drop a persisted key. Useful for "clear my history" actions.
 */
export function clearPersisted(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // ignore
  }
}
