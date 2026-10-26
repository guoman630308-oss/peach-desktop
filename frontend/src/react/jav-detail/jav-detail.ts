import { apiGet } from '../../api';
import type { FaceRef } from '@peach/card-art';
import { queryClient } from '../query';

export interface JavDetail {
  code: string; title: string; state: 'ready' | 'partial'; message?: string;
  release_date?: string; duration?: number; score?: number; summary: string;
  studio?: string; director: string; publisher: string;
  performers: string; performer_refs: JavEntityRef[]; studio_refs: JavEntityRef[]; series_refs: JavEntityRef[];
  tags: string[]; has_cover: boolean; cover_version?: string; cover_url?: string;
  tag_refs?: { name: string; category: string; source_name: string }[];
  preview_video_url?: string;
  source_image: boolean; preview_count: number; comments_count: number;
  source_url: string; wanted: boolean; want_id: number | null;
}
export interface JavEntityRef extends FaceRef { name: string; gender?: 'female' | 'male' | 'unknown'; has_logo?: boolean; source_actor_id?: string; source_avatar?: boolean }
export interface JavReview { id: string; username: string; content: string; score?: number; created_at: string }
export interface JavReviews { ok: boolean; items: JavReview[]; page: number; more: boolean; message?: string; checked_at?: string; cache_stale?: boolean }
export interface JavDetailProps {
  code: string; readOnly: boolean;
  actions: { close(): void; navigate(path: string): void; openTag(tag: string): void; receipt(message: string): void;
    canonicalCode?(code: string): void };
}
export const javDetailKey = (code: string) => ['jav-detail', code] as const;
export const fetchJavDetail = (code: string, signal?: AbortSignal, refresh = false) =>
  apiGet<JavDetail>(`/api/jav/detail?code=${encodeURIComponent(code)}${refresh ? '&refresh=1' : ''}`, signal);
export const fetchJavReviews = (code: string, page: number, signal?: AbortSignal, refresh = false) =>
  apiGet<JavReviews>(`/api/jav/reviews?code=${encodeURIComponent(code)}&page=${page}${refresh ? '&refresh=1' : ''}`, signal);
export const prefetchJavDetail = (props: JavDetailProps, signal: AbortSignal) =>
  queryClient.ensureQueryData({ queryKey: javDetailKey(props.code), queryFn: () => fetchJavDetail(props.code, signal),
    staleTime: 6 * 3600_000, revalidateIfStale: true });

/** 同一评论身份只呈现一次；重复页结束翻页。 */
export function nextReviewPage(last: JavReviews, pages: JavReviews[]): number | undefined {
  if (!last.ok || !last.more) return undefined;
  const seen = new Set(pages.slice(0, -1).flatMap(page => page.items.map(item => item.id)));
  return last.items.some(item => !seen.has(item.id)) ? last.page + 1 : undefined;
}
