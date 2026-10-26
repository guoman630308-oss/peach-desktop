/* 关注详情 `/posts/:id` 的数据与判定（ADR-0031）。
 *
 * 这一块是舞台岛（`../stage/`）树里的子组件；舞台本身、进出场、小窗与 Video.js 归舞台岛
 * （JAV 详情也在用）。条目归这里：先扫关注页岛已缓存的那几页（`findFollowItem`）与已取过的详情，
 * 扫不到才单条取 `GET /api/follow?item=<id>`，响应形状同列表那一页，只含这一条所在的组。 */
import type { QueryKey } from '@tanstack/react-query';
import { apiGet } from '../../api';
import {
  FOLLOW_CREDENTIALS_KEY, collectionItems, fetchFollowCredentials, findFollowItem, videoItems,
  type FollowContext, type FollowFeedHelpers, type FollowGroup, type FollowItem, type FollowMedia,
  type FollowMediaItem, type FollowPage, type FollowSource,
} from '../follow-feed/follow-feed';
import type { LightboxSlide } from '../photo-lightbox/photo-lightbox';
import { queryClient } from '../query';

/* ── 接口形状（`src/peach/web_follow.py`） ── */

/** 条目里嵌着的一份媒体：详情比卡片多读名字、字节数、类型与网盘分组。 */
export interface FollowDetailMedia extends FollowMediaItem {
  name?: string;
  size?: number;
  media_type?: string;
  resource_provider?: string;
  resource_group?: string;
  resource_group_label?: string;
}

/** 详情读到的条目字段；其余原样留在 `FollowItem` 的索引签名里。 */
export interface FollowDetailItem extends FollowItem {
  url?: string;
  summary?: string;
  author?: string;
  source_label?: string;
  media_type?: string;
  media_error?: string;
  media_needs_credential?: boolean;
  resource_urls?: string[];
  hidden_media?: FollowDetailMedia[];
  detail_tags?: string[];
  variant_kind?: string;
  variant_label?: string;
  has_media?: boolean;
  media_items?: FollowDetailMedia[];
}

/** 一条详情要的全部：条目、它所在的组，以及署名要读的来源与别名。 */
export interface FollowDetailData {
  item: FollowDetailItem;
  group: FollowGroup | null;
  sources: FollowSource[];
  aliases: unknown[];
}

/* ── 取数 ── */

export const followItemKey = (id: number): QueryKey => ['follow-item', id];

export const FOLLOW_ITEM_URL = (id: number) => `/api/follow?item=${encodeURIComponent(id)}`;
export const FOLLOW_SAVE_URL = '/api/follow/save';
export const FOLLOW_MEDIA_HIDE_URL = '/api/follow/media/hide';

/** 条目已不存在：重试也取不回来，详情给的是「回到关注」而不是重试。 */
export class FollowItemGone extends Error {
  constructor() { super('这条关注内容已不存在') }
}

/** 单条取：响应只含这一条所在的组。条目已不存在时服务端回空组，这里报成 `FollowItemGone`。 */
export async function fetchFollowItem(id: number, signal?: AbortSignal): Promise<FollowDetailData> {
  const page = await apiGet<FollowPage>(FOLLOW_ITEM_URL(id), signal);
  for (const group of page.groups || []) {
    const item = collectionItems(group).find((member) => member.id === id);
    if (item) return { item: item as FollowDetailItem, group, sources: page.sources || [], aliases: page.author_aliases || [] };
  }
  throw new FollowItemGone();
}

/** 已经取过的详情里，组里有这一条的那一份：合集换版本、多媒体换到组里另一条，组就是舞台上
 *  正摆着的那一份，单条取回来的也只是同一组。 */
function findDetailGroupItem(id: number): FollowDetailData | null {
  for (const [, data] of queryClient.getQueriesData<FollowDetailData>({ queryKey: ['follow-item'] })) {
    if (!data?.group) continue;
    const item = collectionItems(data.group).find((member) => member.id === id);
    if (item) return { ...data, item: item as FollowDetailItem };
  }
  return null;
}

/** 首屏：列表缓存里有就直接用（关掉详情回列表也不重取）；这一条取过、或者就在舞台上那一组里，
 *  也不再问服务端——点下去到换过去之间没有等待。都没有才单条取；凭据没取过才取。 */
export async function prefetchFollowDetail(props: { id: number }, signal: AbortSignal): Promise<void> {
  const key = followItemKey(props.id);
  const hit = findFollowItem(props.id);
  const credentials = queryClient.getQueryData(FOLLOW_CREDENTIALS_KEY)
    ? null
    : queryClient.fetchQuery({ queryKey: FOLLOW_CREDENTIALS_KEY, queryFn: () => fetchFollowCredentials(signal) });
  const cached = hit ? { ...hit, item: hit.item as FollowDetailItem }
    : queryClient.getQueryData<FollowDetailData>(key) ?? findDetailGroupItem(props.id);
  if (cached) queryClient.setQueryData<FollowDetailData>(key, cached);
  else await queryClient.fetchQuery({ queryKey: key, queryFn: () => fetchFollowItem(props.id, signal) });
  await credentials;
}

/* ── 壳递进来的那几样 ── */

export interface FollowDetailActions {
  /** 关闭详情回到来处（壳的 `closeDetail`：退场动画、拆舞台、推回列表地址）。 */
  close(): void;
  /** 换到组里的另一条：经壳换地址（`preserveReturn` 语义），舞台原地换内容。 */
  openItem(id: number, mediaIndex?: number | null): void;
  openAuthor(name: string): void;
  /** 点详情里的一枚标签：壳切换那一枚筛选，回到带上它的列表。 */
  openTag(tag: string): void;
  /** 这一条、这一份媒体画出来了：壳据此更新小窗元数据、侧栏标签抽屉与舞台的氛围光、剧场模式。 */
  present(item: FollowDetailItem, kind: string): void;
  /** 把媒体框交给舞台挂播放器（同作品详情：`<video>` 由舞台建在框里）。`onError` 是片源报错，
   *  详情据此说明这一份没放出来。返回的清理在这块媒体区卸下时调。 */
  mountPlayer(frame: HTMLElement, item: FollowDetailItem, media: FollowDetailMedia | null,
    options: { onError(): void }): () => void;
  /** 操作回执；给了 `undo` 就带一颗撤销键。失败走 `failure`。 */
  toast(message: string, options?: { undo?: () => Promise<void> }): void;
  failure(action: string, error: unknown): void;
}

export interface FollowDetailProps {
  id: number;
  /** 进来时要看的那份媒体（`media_items[].index`）；空就按当前视图挑第一份。 */
  mediaIndex: number | null;
  /** 列表停在视频还是图片：多媒体帖先挑这种媒体。 */
  mediaView: FollowMedia;
  relatedLimit: number;
  helpers: FollowFeedHelpers;
  actions: FollowDetailActions;
}

/* ── 媒体判定 ── */

export interface DetailMedia {
  embedded: FollowDetailMedia[];
  selected: FollowDetailMedia | null;
  kind: string;
  images: FollowDetailMedia[];
  /** 当前这张在图片里的位置；不是图片时是 -1。 */
  position: number;
  carousel: boolean;
  /** 多份媒体又不是纯图轮播：右边摆多媒体队列。 */
  embeddedQueue: boolean;
  /** 条目自己没有嵌入媒体、组里却有几条可播视频：右边摆合集队列。 */
  collection: FollowGroup | null;
  /** 画框比例跟整组图走、不跟当前这张：换图时详情不忽高忽低，图没加载完也先占住位置。
   *  有尺寸的图里取最高的那张（宽高比最小）；一张都没有时轮播用方框，单图由图片自己撑开。 */
  frameRatio: number;
  /** 正片地址（`/follow-stream`）；条目不可播时是空串。 */
  src: string;
}

export function detailMedia(item: FollowDetailItem, group: FollowGroup | null, mediaIndex: number | null, view: FollowMedia): DetailMedia {
  const embedded = item.media_items || [];
  const preferredKind = view === 'images' ? 'image' : 'video';
  const preferred = embedded.find((media) => media.media_kind === preferredKind) || embedded[0];
  const selected = embedded.length
    ? embedded.find((media) => media.index === (mediaIndex ?? preferred!.index)) || preferred!
    : null;
  const images = embedded.filter((media) => media.media_kind === 'image');
  const position = images.findIndex((media) => media.index === selected?.index);
  const carousel = images.length > 1 && position >= 0;
  const embeddedQueue = embedded.length > 1 && !carousel;
  const collection = !embedded.length && group && videoItems(group).length > 1 ? group : null;
  const kind = selected?.media_kind || item.media_kind || '';
  const owners = (carousel ? [...images, item] : [selected, item])
    .filter((owner): owner is FollowDetailMedia | FollowDetailItem => !!owner && (owner.width || 0) > 0 && (owner.height || 0) > 0);
  const frameRatio = kind !== 'image' ? 0
    : owners.length ? Math.min(...owners.map((owner) => owner.width! / owner.height!)) : carousel ? 1 : 0;
  const src = item.playable ? `/follow-stream?id=${item.id}${selected ? `&media=${selected.index}` : ''}` : '';
  return { embedded, selected, kind, images, position, carousel, embeddedQueue, collection, frameRatio, src };
}

/** 组里挂着网盘分组媒体的那一条：合集队列改列它的媒体，不列回复。 */
export const groupedMediaOwner = (group: FollowGroup): FollowDetailItem | undefined =>
  collectionItems(group).find((item) => ((item as FollowDetailItem).media_items || [])
    .some((media) => media.resource_group)) as FollowDetailItem | undefined;

/* ── 文案 ── */

const RESOURCE_HOSTS: Record<string, string> = {
  'gofile.io': 'Gofile', 'pixeldrain.com': 'Pixeldrain', 'mega.nz': 'MEGA', 'mega.io': 'MEGA',
  'mediafire.com': 'MediaFire', 'drive.google.com': 'Google Drive',
};
const RESOURCE_PROVIDERS: Record<string, string> = {
  gofile: 'Gofile', pixeldrain: 'Pixeldrain', mega: 'MEGA', mediafire: 'MediaFire', google_drive: 'Google Drive',
};

/** 外部文件页那一排的站名：认得的网盘写品牌名，其余写主机名。 */
export function resourceLabel(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    return RESOURCE_HOSTS[host] || host;
  } catch {
    return '外部文件页';
  }
}

/** 灯箱角上那一行出处：网盘分组的媒体写网盘，其余写来源站。 */
export function mediaSourceLabel(media: FollowDetailMedia | null | undefined, item: FollowDetailItem): string {
  return RESOURCE_PROVIDERS[media?.resource_provider || ''] || item.provider_label || item.provider || '在线图片';
}

/** 合集队列一行的行首标记与正文。线程标题不能冒充每条回复的正文，行首说明它与主条目的关系；
 *  发布时间单独显示，不能再伪装成版本或类型标签。 */
export function collectionCopy(group: FollowGroup, item: FollowDetailItem, mark = ''): { label: string; title: string } {
  let label = mark;
  if (!label && group.is_release) label = item.variant_label || item.variant_kind || '';
  if (!label) label = item.variant_kind === 'wip' ? 'WIP' : (item.variant_label || item.variant_kind || '视频');
  const body = group.is_release ? (item.summary || (item.has_media ? '（仅附件）' : '（无正文）')) : item.title;
  return { label, title: group.is_release && item.author ? `${item.author}：${body}` : body };
}

/** 多媒体队列一行的名字：关注媒体不是 JAV，只去掉首尾空白（同遗留层 `javDisplayName` 的非 JAV 分支）。 */
export const mediaName = (media: FollowDetailMedia) => String(media.name || '').trim();

/** 来源记下的标签类型；没记的是 `unknown`，保持中性色——不按词形猜类型是关注标签的既有门槛。 */
export const tagType = (item: FollowDetailItem, tag: string) => item.tag_types?.[tag] || 'unknown';

/** 标签着色那一档（`data-tag-cat`，色板在 `../styles.css`）：来源认得的五类各有颜色，没记类型的
 *  是中性色，其余类型（booru 各站自己多出来的）按 general 着色。 */
const COLORED_TYPES = new Set(['artist', 'character', 'copyright', 'metadata', 'unknown']);
export function tagCategory(item: FollowDetailItem, tag: string): string {
  const type = tagType(item, tag);
  return `r34-${COLORED_TYPES.has(type) ? type : 'general'}`;
}

/* 详情标签按 rule34.xxx 帖子页 `#tag-sidebar` 的类型顺序分组，组内按名升序——证据见
   docs/reference-snapshots/rule34-follow-tags-and-collections.md，缺的类型直接跳过不占位。
   卡片只消费 general 内容投影；详情保留来源记录的全部类型。 */
const TAG_ORDER = ['copyright', 'character', 'artist', 'general', 'metadata'];
export function detailTags(item: FollowDetailItem, label: (tag: string) => string): string[] {
  const tags = item.detail_tags || item.tags || [];
  const rank = (tag: string) => {
    const at = TAG_ORDER.indexOf(tagType(item, tag));
    return at < 0 ? TAG_ORDER.length : at;
  };
  return [...tags].sort((a, b) => rank(a) - rank(b) || label(a).localeCompare(label(b)));
}

/** 署名那一组来源：同一创作者的全部来源，认不出创作者时只有这一条自己的来源。 */
export function authorSources(item: Pick<FollowItem, 'source_id'>, sources: readonly FollowSource[]): FollowSource[] {
  const source = sources.find((row) => row.id === item.source_id);
  const group = sources.filter((row) => source?.author_key && row.author_key === source.author_key);
  if (!group.length && source) group.push(source);
  return group;
}

export const detailContext = (data: FollowDetailData, credentials: ReadonlySet<string>): FollowContext => ({
  sources: data.sources, aliases: data.aliases, credentials,
});

/* ── 灯箱 ──
   详情里点图开大图，跟女优页同一个灯箱。多图时把整组交进去，左右翻页就能看完一条帖子的所有图；
   取不到正片就退而用缩略图——看小图总比点了没反应强。 */
const sized = (media: FollowDetailMedia | null) => (media?.size ? { size: media.size } : {});

export function detailSlides(item: FollowDetailItem, media: DetailMedia): LightboxSlide[] {
  const { images, kind, src, selected } = media;
  if (images.length) {
    return images.map((image) => ({
      src: `/follow-stream?id=${item.id}&media=${image.index}`,
      thumb: image.thumb_url || item.thumb_url || `/follow-stream?id=${item.id}&media=${image.index}`,
      name: image.name || item.title, source: mediaSourceLabel(image, item), ...sized(image),
    }));
  }
  if (kind === 'image' && src) {
    return [{ src, thumb: item.thumb_url || src, name: item.title, source: mediaSourceLabel(selected, item),
      ...sized(selected) }];
  }
  return item.thumb_url ? [{ src: item.thumb_url, thumb: item.thumb_url, name: item.title,
    source: item.provider_label || item.provider || '在线图片' }] : [];
}

/* ── 写完之后 ── */

/** 状态回执的说法与撤销：保存进数据库的不给撤销，那是另一件事。 */
export const statusReceipt = (to: string) => (to === 'seen' ? '已标记看过' : '已更新关注状态');
