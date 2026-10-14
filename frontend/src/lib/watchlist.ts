import { useCallback, useState } from 'react';

const LS_KEY = 'watchlist';

function load(): string[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    const arr = raw ? (JSON.parse(raw) as unknown) : null;
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

/**
 * 自选品种（本地记忆，localStorage）。
 * 返回 favorites 数组、Set 便于过滤，以及 toggle。
 */
export function useWatchlist() {
  const [favorites, setFavorites] = useState<string[]>(load);

  const toggle = useCallback((name: string) => {
    setFavorites((prev) => {
      const next = prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name];
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return { favorites, favSet: new Set(favorites), toggle };
}
