/* 播放列表页的数据契约与三个写操作。
 *
 * 读 `/api/playlists`（`src/peach/web_playlists.py` 的 `q_playlists`，按最近改动排），写一律
 * POST `/api/playlist`。写端点回的是带 `items` 的整份列表，不是列表页那一行的形状，
 * 改名还会动 `updated_at`、换掉排序，所以写完一律让列表键重取，不拿回话拼缓存。
 *
 * 首页存完一份 Mix 再进来要看得到它：首屏 `prefetch` 每次都向服务端重取，不读缓存里
 * 上一次的那一份。 */
import { apiGet, apiSend } from '../../api';
import { queryClient } from '../query';

export const PLAYLISTS_URL = '/api/playlists';
export const PLAYLIST_URL = '/api/playlist';
export const PLAYLISTS_KEY = ['playlists'] as const;

/** 列表卡的署名：列表里出镜最多的那几位（服务端最多给三位）。 */
export interface PlaylistFace {
  kind: string;
  id: number;
  name: string;
  has_image: boolean;
  image_version?: string;
  avatar_focus?: unknown;
}

/** `/api/playlists` 的一行。 */
export interface PlaylistRow {
  id: number;
  name: string;
  /** `mix` 是从 Mix 存下来的，其余是手动建的。 */
  source_kind: string;
  source_seed_asset_id: number | null;
  current_asset_id: number | null;
  created_at: string;
  updated_at: string;
  item_count: number;
  /** 静止封面：续播点那一个，没有续播点时是第一个。空列表是 null。 */
  preview_asset_id: number | null;
  /** 悬停时翻过的封面，续播点那张在最前，最多五张。 */
  preview_ids: number[];
  faces: PlaylistFace[];
}

export interface PlaylistsData { items: PlaylistRow[] }

/** `/api/playlist?id=` 与写端点回话里的那一份整列表。这一页只用得到下面几项。 */
export interface PlaylistDetail {
  id: number;
  name: string;
  source_kind: string;
  source_seed_asset_id: number | null;
  current_asset_id: number | null;
  items: { id: number }[];
}

export const fetchPlaylists = (signal?: AbortSignal) => apiGet<PlaylistsData>(PLAYLISTS_URL, signal);

/** 首屏。`staleTime: 0` 写明：缓存里有上一次的列表也照样重取。 */
export async function prefetchPlaylists(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({ queryKey: PLAYLISTS_KEY, queryFn: () => fetchPlaylists(signal), staleTime: 0 });
}

/** 写完之后重读列表。 */
export const refreshPlaylists = () => queryClient.invalidateQueries({ queryKey: PLAYLISTS_KEY, exact: true });

/** 点卡片从哪一个接着播：续播点，没有就是第一个。空列表没有去处。 */
export const resumeAssetId = (row: PlaylistRow) => row.current_asset_id || row.preview_asset_id || null;

export const posterUrl = (id: number) => `/poster?id=${id}&c=4`;

type Written = { ok: true; playlist: PlaylistDetail };

export const createPlaylist = (name: string) =>
  apiSend<Written>(PLAYLIST_URL, { action: 'create', name, asset_ids: [] });

export const renamePlaylist = (id: number, name: string) =>
  apiSend<Written>(PLAYLIST_URL, { action: 'rename', id, name });

export const deletePlaylist = (id: number) =>
  apiSend<{ ok: true; deleted: number }>(PLAYLIST_URL, { action: 'delete', id });

/** 删之前先把内容取回来：`delete` 连 playlist_item 一起清，删完就没有地方能问出这份列表
 *  装着哪些视频。取不到就是 null，那一次删除不给撤销。 */
export const keepPlaylist = (id: number): Promise<PlaylistDetail | null> =>
  apiGet<PlaylistDetail>(`${PLAYLIST_URL}?id=${id}`).catch(() => null);

/** 撤销删除：重建一份新记录，装同一批视频、同一个来源。Mix 种子不在列表里了就不带，
 *  服务端要求种子必须在列表中。 */
export function recreatePlaylist(kept: PlaylistDetail) {
  const ids = (kept.items || []).map((entry) => entry.id);
  const seed = kept.source_seed_asset_id;
  return apiSend<Written>(PLAYLIST_URL, {
    action: 'create', name: kept.name, asset_ids: ids, source_kind: kept.source_kind,
    source_seed_asset_id: seed !== null && ids.includes(seed) ? seed : null,
  });
}
