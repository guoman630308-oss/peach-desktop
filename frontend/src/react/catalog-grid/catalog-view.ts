import { useSyncExternalStore } from 'react';

export type CatalogView = 'cards' | 'list';
const KEY = 'peach.catalog-view.v1';
const listeners = new Set<() => void>();
let fallback: CatalogView = 'cards';

export function catalogView(): CatalogView {
  try { return localStorage.getItem(KEY) === 'list' ? 'list' : 'cards' }
  catch { return fallback }
}

export function setCatalogView(view: string) {
  fallback = view === 'list' ? 'list' : 'cards';
  try { localStorage.setItem(KEY, fallback) } catch { /* 当前页面保留所选视图。 */ }
  listeners.forEach(listener => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => { if (event.key === KEY || event.key === null) listener() };
  addEventListener('storage', changed);
  return () => { listeners.delete(listener); removeEventListener('storage', changed) };
}

export const useCatalogView = () => useSyncExternalStore(subscribe, catalogView, () => 'cards' as const);
