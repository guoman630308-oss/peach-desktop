/* 未入库的新作那一行（ADR-0042）：首页那一行与资料页那一行共用的取数、查询键与写操作。
 *
 * 两行读同一个端点（`/api/feeds/discoveries`），资料页多带一个 `entity`；查询键同族
 * （`['feed-new', 实体 id | 'home']`），一处改了合集开关，按 `['feed-new']` 一次就能把两行都作废。
 * 卡片的 HTML 仍由壳拼（`feedRowHtml`）：封面取景、版次字样与那条连接方式的 Note 都是遗留层
 * 那一份，取图失败时的兜底链也直接改节点。 */
import { api } from '@peach/legacy/core';

import { queryClient } from '../query';

/** `/api/feeds/discoveries` 的回话：条目与最近一轮取封面断在连接上的部数。 */
export interface FeedDiscoveries { items?: { id: number; has_cover?: boolean; cover_fetching?: boolean }[]; cover_network?: number }

/** 新作那一行取回来的样子：条目（只用来判有没有）与壳拼好的整段 HTML。 */
export interface FeedNew {
  items: { id: number }[];
  html: string;
  fetching?: boolean;
}

/** 壳拼的整段 HTML：连不上图片主机时的 Note 与一排卡（`feedNewCardHtml`）。 */
export type FeedRowHtml = (data: FeedDiscoveries | null) => string;

/** 资料页那一行用实体 id，首页那一行不分人。 */
export const feedNewKey = (entityId: number | null) => ['feed-new', entityId ?? 'home'] as const;

const FEED_SKELETON_CARDS = 8;
const FEED_COVER_WAIT = 1500;

/** 把一段 HTML 里头 `count` 张图先取进缓存，最多等 `ms` 毫秒。插进页面时它们已经 `complete`，
 *  遗留层的等待微光不再给它们挂上。 */
export function preloadImages(html: string, count: number, ms: number): Promise<unknown> {
  const probe = document.createElement('template');
  probe.innerHTML = html;
  const loads = [...probe.content.querySelectorAll('img[src]')].slice(0, count).map((source) => {
    const img = new Image();
    img.referrerPolicy = source.getAttribute('referrerpolicy') || '';
    img.src = source.getAttribute('src') || '';
    return img.decode().catch(() => {});
  });
  return Promise.race([Promise.all(loads), new Promise((resolve) => { setTimeout(resolve, ms) })]);
}

/** 拉取由定时器做，页面只读已经发现的那些：进这一页顺手发一轮请求，等于把每次刷新都变成对别人
 *  服务器的一次拉取。骨架还占着时（首屏）先把头几张封面取到手再整行换掉：否则骨架退场、真卡进来，
 *  封面格里又是一轮微光，同一行等了两遍。慢的那几张不等满，到点照换。 */
export async function fetchFeedNew(entityId: number | null, rowHtml: FeedRowHtml, preload: boolean): Promise<FeedNew> {
  const query = new URLSearchParams({ limit: '12' });
  if (entityId) query.set('entity', String(entityId));
  const data = await (api(`/api/feeds/discoveries?${query}`).catch(() => null) as Promise<FeedDiscoveries | null>);
  const ok = data && !(data as { error?: string }).error ? data : null;
  const items = ok?.items || [];
  const html = rowHtml(ok);
  if (preload && items.length) await preloadImages(html, FEED_SKELETON_CARDS, FEED_COVER_WAIT);
  return { items, html, fetching: items.some(item => item.cover_fetching && !item.has_cover) };
}

export const feedNewOptions = (entityId: number | null, rowHtml: FeedRowHtml, preload = false) => ({
  queryKey: feedNewKey(entityId),
  queryFn: () => fetchFeedNew(entityId, rowHtml, preload),
  staleTime: Infinity,
  refetchInterval: (query: { state: { data?: FeedNew } }) => query.state.data?.fetching ? 2000 : false,
});

/** 卡上那三颗键（想要、不想看、标为已看过）。都是标记，写失败了卡片照样换样子：这一行下次取数时
 *  会按服务端的现状重排。 */
export const postFeedAction = (feedId: number, action: string): Promise<void> =>
  (api('/api/feeds/discovery', { method: 'POST', body: JSON.stringify({ action, ids: [feedId] }) }) as Promise<unknown>)
    .then(() => undefined, () => undefined);

/** 首页那一行（`feed-new` island）。宿主是 `index.html` 里那块常驻的 `#feedNew`：岛的根挂在它
 *  里面的 `.peach-react` 上，卡片 portal 回宿主本身，落在 Preflight 的范围外。 */
export interface FeedNewProps {
  host: HTMLElement;
  /** 合集开关改了就换一个代次，这一行照新的筛法重取。 */
  revision: number;
  helpers: {
    feedRowHtml: FeedRowHtml;
    /** 拖动、滚轮，按设置接自动滚动。 */
    wireFeedRow(row: Element | null): void;
  };
  actions: {
    /** 这一行有没有卡：取回来、或最后一张被收起时报一次，壳按它决定下次进首页铺不铺骨架。 */
    settled(hasItems: boolean): void;
  };
}

/** 每次进目录页都是新的一趟（与资料页同一口径）：先清掉上一次的缓存，骨架还占着就连头几张
 *  封面一起等。 */
export async function prefetchFeedNew(props: FeedNewProps): Promise<void> {
  queryClient.removeQueries({ queryKey: feedNewKey(null) });
  await queryClient.fetchQuery(feedNewOptions(null, props.helpers.feedRowHtml,
    props.host.getAttribute('aria-busy') === 'true'));
}
