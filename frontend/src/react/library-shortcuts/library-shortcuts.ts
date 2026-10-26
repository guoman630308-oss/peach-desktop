import { useMutation, useQuery } from '@tanstack/react-query';
import { apiGet, apiSend } from '../../api';
import { queryClient } from '../query';
import { entityPath } from '@peach/legacy/core';

export interface Favorite { key?: string; entity_id?: number; kind: string; name: string; favorite?: boolean; image_url?: string; image_label?: string; image_fallbacks?: string[] }
export interface SavedFilter { id: string; name: string; href: string }
export interface Shortcuts { favorites: Favorite[]; filters: SavedFilter[] }
export const SHORTCUTS_KEY = ['library-shortcuts'] as const;
const URL = '/api/library-shortcuts';

export function useShortcuts(enabled = true) {
  return useQuery({ queryKey: SHORTCUTS_KEY, queryFn: ({ signal }) => apiGet<Shortcuts>(URL, signal),
    enabled, refetchOnMount: enabled ? 'always' : false });
}
export function useShortcutWrite() {
  return useMutation({ mutationKey: SHORTCUTS_KEY,
    mutationFn: (body: Record<string, unknown>) => apiSend<Shortcuts>(URL, body),
    onSuccess: (data) => { queryClient.setQueryData(SHORTCUTS_KEY, data) },
  });
}
export function isFavorite(rows: Favorite[], item: Favorite): boolean {
  return rows.some((row) => item.entity_id ? row.entity_id === item.entity_id : row.kind === item.kind && row.name === item.name);
}
export function favoriteHref(item: Favorite): string {
  return entityPath(item.kind, item.name, item.entity_id);
}
