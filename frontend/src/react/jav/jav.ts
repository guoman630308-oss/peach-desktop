import { apiGet } from '../../api';
import { queryClient } from '../query';
import { fetchWants, WANTS_KEY } from '../wants/wants';
import type { FaceRef, CoverItem } from '@peach/card-art';
import { coverImage, javArtwork } from '@peach/card-art';
import { esc } from '@peach/legacy/core';
import type { DiscoveryDirection, DiscoverySort } from './discovery-sorts';

export { JAV_TABS, javTab, javPath, javLibraryPath } from './jav-navigation';
export interface Discovery extends CoverItem {
  id: number; code: string; title: string; performers: string; release_date: string;
  studio: string; link: string; cover_url: string; has_cover: boolean; wanted: boolean;
  performer_refs?: (FaceRef & { name: string; source_actor_id?: string; source_avatar?: boolean })[];
  asset_id?: number | null; tags?: string[]; reasons?: string[]; score?: number;
  taste_score?: number | null; ranking_score?: number | null; rank?: number;
  source_score?: number | null; duration?: number | null;
  metadata_state?: 'pending' | 'ready' | 'partial' | 'unavailable' | 'stale'; metadata_message?: string;
}
export type DiscoveryScope = 'external' | 'library';
export interface DiscoveryMetadataJob {
  job_id: string; status: 'running' | 'complete' | 'stopped' | 'failed'; completed: number; total: number;
  failed: number; requests: number; bytes: number; stop_reason?: string; message?: string;
}
export interface DiscoveryResult {
  items: Discovery[]; candidate_total: number; limited: boolean; ranking_method?: 'mmr' | 'field-sort';
  metadata_job?: DiscoveryMetadataJob | null; poll_after_ms?: number;
}
export const discoveryKey = (scope: DiscoveryScope, sort: DiscoverySort = 'taste', direction: DiscoveryDirection = 'desc') =>
  ['jav-discoveries', scope, sort, direction] as const;
export function fetchDiscoveries(scope: DiscoveryScope, signal: AbortSignal, options: {
  sort?: DiscoverySort; direction?: DiscoveryDirection; jobId?: string; refreshMetadata?: boolean; cancelMetadata?: boolean;
} = {}) {
  const params = new URLSearchParams({ scope, limit: '100', sort: options.sort || 'taste', direction: options.direction || 'desc' });
  if (options.jobId) params.set('metadata_job_id', options.jobId);
  if (options.refreshMetadata) params.set('metadata_refresh', '1');
  if (options.cancelMetadata) params.set('metadata_cancel', '1');
  return apiGet<DiscoveryResult>(`/api/jav/discoveries?${params}`, signal);
}

export function discoveryArtwork(item: Discovery, layout: 'big' | 'small', javImage: unknown): string {
  const local = javArtwork({ ...item, is_jav: true }, layout, false, javImage);
  if (local.html || !item.cover_url) return local.html;
  return coverImage(item, layout).replace(/ src="[^"]*"/,
    ` src="${esc(item.cover_url)}" referrerpolicy="no-referrer" data-jav-image="${item.id}" data-jav-cover="${esc(item.cover_url)}" data-jav-thumb="" data-jav-image-layout="${layout}"${layout === 'big' ? ' data-panel-prior="1"' : ''}`);
}

export async function prefetchJav(tab: string, signal: AbortSignal) {
  if (tab === 'wants') {
    await queryClient.ensureQueryData({ queryKey: WANTS_KEY, queryFn: () => fetchWants(signal),
      staleTime: 30_000, revalidateIfStale: true }); return;
  }
  if (tab === 'discover') {
    await queryClient.ensureQueryData({ queryKey: discoveryKey('external'), queryFn: () => fetchDiscoveries('external', signal),
      staleTime: 30_000, revalidateIfStale: true }); return;
  }
}
