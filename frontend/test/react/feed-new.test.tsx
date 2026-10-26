/* 首页那一行新作（`feed-new` 岛）：取哪一份、画进哪儿、卡收完怎么报给壳、换代次重取。
 *
 * 卡片上那三颗键的去留与资料页那一行同一个组件，在 `entity-hero.test.tsx` 量；骨架交接与离开目录页
 * 收起要真浏览器，在 `frontend/e2e/home-loading.test.ts` 与 `feed-cover.test.ts` 里量。 */
import { act, useState } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { feedNewKey, prefetchFeedNew, type FeedDiscoveries, type FeedNewProps } from '../../src/react/feed-new/feed-new';
import { FeedNewPage } from '../../src/react/feed-new/feed-new-page';
import { queryClient } from '../../src/react/query';
import { click, mountRoot, settle } from './render';
import { FeedNewRow } from '../../src/react/feed-new/feed-new-row';
import { feedIdentity } from '../../src/card-art/feed-identity';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const rowHtml = (data: FeedDiscoveries | null) => `<div class="feednewrow">${(data?.items || []).map((one) =>
  `<div data-feed-id="${one.id}"><button data-feed-action="ignore">不想看</button></div>`).join('')}</div>`;

/** 假服务端：发现列表按轮次回给定的条目，写接口一律成功。 */
function serve(...rounds: number[][]) {
  let round = 0;
  const fetcher = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.startsWith('/api/feeds/discoveries')) {
      const ids = rounds[Math.min(round, rounds.length - 1)] || [];
      round += 1;
      return { ok: true, status: 200, json: async () => ({ items: ids.map((id) => ({ id, has_cover: true })) }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
const reads = (fetcher: ReturnType<typeof serve>) =>
  fetcher.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith('/api/feeds/discoveries'));

it('新作来源共演头像经详情同一查询加载并在忽略后可卸载', async () => {
  vi.stubGlobal('IntersectionObserver', undefined);
  const fetcher = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () => ({ ok: true, image: 'data:image/jpeg;base64,cGl4ZWxz' }) }));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('section');
  document.body.append(host);
  const html = `<div class="feednewrow"><article data-feed-id="1">${feedIdentity({ code: 'EXM-001', performer_refs: [
    { name: '甲', source_actor_id: 'A', source_avatar: true },
    { name: '乙', source_actor_id: 'B', source_avatar: true }] })}<button data-feed-action="ignore">忽略</button></article></div>`;
  const mounted = await mountRoot(<QueryClientProvider client={queryClient}><FeedNewRow host={host} feedNew={{ items: [{ id: 1 }], html }}
    wire={() => {}} act={async () => {}} /></QueryClientProvider>);
  await settle();
  expect(mounted.host.querySelectorAll('.mav img')).toHaveLength(2);
  expect(fetcher.mock.calls.map(([url]) => url).sort()).toEqual([
    '/api/jav/image?code=EXM-001&image=actor%3AA', '/api/jav/image?code=EXM-001&image=actor%3AB']);
  await click(mounted.host.querySelector('[data-feed-action]'));
  await settle();
  expect(mounted.host.querySelector('[data-feed-id]')).toBeNull();
});

function props(patch: Partial<FeedNewProps> = {}): FeedNewProps {
  const host = document.createElement('section');
  host.hidden = true;
  document.body.append(host);
  return {
    host, revision: 0, helpers: { feedRowHtml: rowHtml, wireFeedRow: vi.fn() }, actions: { settled: vi.fn() }, ...patch,
  };
}

async function open(given: FeedNewProps) {
  await prefetchFeedNew(given);
  const root = await mountRoot(<QueryClientProvider client={queryClient}><FeedNewPage {...given} /></QueryClientProvider>);
  await settle();
  return root;
}

describe('取数', () => {
  it('首页那一行不分人：只带条数，与资料页那一行各占一个键', async () => {
    const fetcher = serve([1, 2]);
    await open(props());
    expect(reads(fetcher)).toEqual(['/api/feeds/discoveries?limit=12']);
    expect(feedNewKey(null)).toEqual(['feed-new', 'home']);
    expect(feedNewKey(7)).toEqual(['feed-new', 7]);
  });

  it('每次挂上都是新的一趟，不读上一次留下的缓存', async () => {
    const fetcher = serve([1], [1, 2]);
    await prefetchFeedNew(props());
    const given = props();
    await open(given);
    expect(reads(fetcher)).toHaveLength(2);
    expect(given.host.querySelectorAll('[data-feed-id]')).toHaveLength(2);
  });
});

describe('画进宿主', () => {
  it('卡片落在宿主本身，不在 React 根里；画出来就亮出宿主、撤掉等待态，报给壳「有」', async () => {
    serve([1, 2]);
    const given = props();
    given.host.setAttribute('aria-busy', 'true');
    const { host: root } = await open(given);
    expect(given.host.hidden).toBe(false);
    expect(given.host.hasAttribute('aria-busy')).toBe(false);
    expect(given.host.querySelectorAll('[data-feed-id]')).toHaveLength(2);
    expect(root.querySelector('[data-feed-id]')).toBeNull();
    expect(given.helpers.wireFeedRow).toHaveBeenCalledWith(given.host.querySelector('.feednewrow'));
    expect(given.actions.settled).toHaveBeenLastCalledWith(true);
  });

  it('一条都没有就整块不出，报给壳「没有」', async () => {
    serve([]);
    const given = props();
    await open(given);
    expect(given.host.hidden).toBe(true);
    expect(given.host.querySelector('[data-feed-id]')).toBeNull();
    expect(given.actions.settled).toHaveBeenLastCalledWith(false);
  });

  it('最后一张收起时宿主藏起，壳下次进首页不再铺骨架', async () => {
    const fetcher = serve([5]);
    const given = props();
    await open(given);
    await click(given.host.querySelector('[data-feed-action="ignore"]'));
    await settle();
    expect(fetcher.mock.calls.some(([url, init]) => url === '/api/feeds/discovery'
      && JSON.parse(String(init?.body)).ids[0] === 5)).toBe(true);
    expect(given.host.hidden).toBe(true);
    expect(given.actions.settled).toHaveBeenLastCalledWith(false);
  });
});

/* 壳经 `updateManagedRoute` 推补丁，这里用一层状态替它。 */
let push: (patch: Partial<FeedNewProps>) => void = () => {};
function Harness(initial: FeedNewProps) {
  const [value, set] = useState(initial);
  push = (patch) => set((current) => ({ ...current, ...patch }));
  return <QueryClientProvider client={queryClient}><FeedNewPage {...value} /></QueryClientProvider>;
}

describe('换代次', () => {
  it('壳推来新的代次就重取；代次不变的补丁不发请求', async () => {
    const fetcher = serve([1], [1, 3]);
    const given = props();
    await prefetchFeedNew(given);
    await mountRoot(<Harness {...given} />);
    await settle();
    expect(reads(fetcher)).toHaveLength(1);
    await act(async () => push({ revision: 0 }));
    await settle();
    expect(reads(fetcher)).toHaveLength(1);
    await act(async () => push({ revision: 1 }));
    await settle();
    expect(reads(fetcher)).toHaveLength(2);
    expect(given.host.querySelectorAll('[data-feed-id]')).toHaveLength(2);
  });
});
