/* 关注来源上的两份词表：来源里出现过的创作者，与上游 booru 给条目打的标签。
 *
 * 两份都归关注那一侧：它们数的是还没入库的在线更新，点开去的也是关注页。艺人与标签两张
 * 索引页只是把它们摆成名册和字母表，所以键建在这里，不建在索引模块里：哪天关注页也要摆
 * 同一份词表，读的就是同一个 `queryKey`。
 *
 * 分页照 `/api/index` 的形状：`limit`／`offset` 翻页，响应带 `has_more`。 */
import { apiGet } from '../../api';

export const FOLLOW_AUTHORS_URL = '/api/follow/authors';
export const FOLLOW_TAGS_URL = '/api/follow/tags';

/** 来源上的一位创作者。`key` 是关注页筛选用的那套创作者键：建过档的是 `entity:<id>`，
 *  还没建档的按名字归组。`held_by` 是数据库里同名的那位创作者，等用户在那一页认过才绑定。 */
export interface OnlineAuthor {
  k: string;
  key: string;
  n: number;
  entity_id: number | null;
  held_by: string;
  /** 官方主页上的头像；取不到时是空串。 */
  avatar: string;
  /** 归档站的头像，主页那张失败时换它。 */
  avatar_fallback: string;
  providers: string[];
}

export interface OnlineAuthorsPage {
  items: OnlineAuthor[];
  has_more: boolean;
  total?: number;
}

/** 上游 booru 的一枚标签。`cat` 是上游的 tag_type（general／artist／character／copyright／metadata）。 */
export interface OnlineTag {
  k: string;
  n: number;
  cat: string;
}

export interface OnlineTagsPage {
  items: OnlineTag[];
  has_more: boolean;
  categories?: Record<string, number>;
}

export const onlineAuthorsKey = (q: string) => ['follow', 'authors', q] as const;
export const onlineTagsKey = (q: string, type: string) => ['follow', 'tags', q, type] as const;

export function onlineAuthorsUrl(q: string, limit: number, offset: number): string {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q) params.set('q', q);
  return `${FOLLOW_AUTHORS_URL}?${params}`;
}

/** `type` 为 `all` 时不带这一项：服务端缺省就是全部类型。 */
export function onlineTagsUrl(q: string, type: string, limit: number, offset: number): string {
  const params = new URLSearchParams({ types: 'all', limit: String(limit), offset: String(offset) });
  if (type && type !== 'all') params.set('type', type);
  if (q) params.set('q', q);
  return `${FOLLOW_TAGS_URL}?${params}`;
}

export const fetchOnlineAuthors = (q: string, limit: number, offset: number, signal?: AbortSignal) =>
  apiGet<OnlineAuthorsPage>(onlineAuthorsUrl(q, limit, offset), signal);

export const fetchOnlineTags = (q: string, type: string, limit: number, offset: number, signal?: AbortSignal) =>
  apiGet<OnlineTagsPage>(onlineTagsUrl(q, type, limit, offset), signal);
