import { emptyStateHtml } from '@peach/legacy/ui';

type Request = (url: string) => Promise<any>;
const FILTERS = ['loc', 'creator', 'performer', 'studio', 'series', 'agency', 'tag', 'tag_match', 'len', 'dur_min', 'dur_max', 'orient', 'region', 'state', 'jav', 'thumb'];

/** 推荐与当前列表共用筛选，并以实际搜索命中作为展示条件。 */
export async function catalogSuggestions(state: Record<string, string>, request: Request): Promise<string[]> {
  const params = new URLSearchParams();
  for (const key of FILTERS) if (state[key]) params.set(key, state[key]);
  const [facets, sample] = await Promise.all([
    request('/api/facets?' + params), request('/api/items?' + params + '&limit=5'),
  ]);
  const candidates: string[] = [...new Set<string>([
    ...(facets.creators || []), ...(facets.tagperformers || []), ...(facets.tags || []),
  ].map(row => String(row.k || '')).concat((sample.items || []).map((row: { code?: string; name?: string }) => row.code || row.name || '')))]
    .filter(value => value.length > 1).slice(0, 10);
  const hits = await Promise.all(candidates.map(async value => {
    const query = new URLSearchParams(params); query.set('q', value); query.set('limit', '1');
    const result = await request('/api/items?' + query);
    return result.total > 0 ? value : '';
  }));
  return hits.filter(Boolean);
}

/** 空馆藏、筛选无结果与资料尚未建立分别给出可执行入口。 */
export function catalogEmptyHtml({ kind = 'catalog', filtered = false, jav = false, configurable = false, online = false } = {}): string {
  const add = configurable ? '<button class="geist-button primary" data-empty-settings>添加内容</button>' : '';
  const follow = '<a class="geist-button' + (!configurable || online ? ' primary' : '') + '" href="/subscriptions?tab=add">添加关注</a>';
  if (filtered || jav) return emptyStateHtml('search', jav ? '还没有符合条件的 JAV 作品' : '没有符合条件的内容',
    jav ? '已扫描但尚未补充发行资料的视频可在全部内容中查看。' : '清除筛选或搜索条件后查看全部内容。',
    { actions: '<a class="geist-button primary" href="/?loc=&thumb=0">查看全部内容</a>' });
  if (kind !== 'catalog') {
    const labels: Record<string, string> = { tags: '标签', performers: '艺人', creators: '卖家', studios: '厂牌', agencies: '事务所', series: '系列' };
    // 在线那一档数的是来源上的东西，名字也跟着来源的说法：艺人页在线摆的是关注来源里的创作者。
    const onlineLabels: Record<string, string> = { tags: '标签', performers: '创作者' };
    const label = (online ? onlineLabels[kind] : '') || labels[kind] || '资料';
    return emptyStateHtml(kind === 'tags' ? 'tags' : 'user-round', '还没有' + label,
      online ? '添加关注来源并获取内容后，这里会显示来源上的' + label + '。' : '添加内容并补充资料后，这里会显示对应信息。',
      { actions: online ? follow : add + follow });
  }
  return emptyStateHtml('play', '还没有视频', '添加媒体目录或关注来源，开始建立你的馆藏。', { actions: add + follow });
}
