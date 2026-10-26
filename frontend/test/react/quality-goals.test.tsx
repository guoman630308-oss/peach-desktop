/* 高清版目标页的行为：一张卡上有什么、点哪里打开作品、进出这一页各发几次请求。
 *
 * 外观（封面的宽度与比例、长标题的中间省略）是设计决定，由 `frontend/e2e/design-*.test.ts`
 * 读 `getComputedStyle` 断言；这里只看结构、文字与请求。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { queryClient } from '../../src/react/query';
import { QualityGoalsPage } from '../../src/react/quality-goals/quality-goals-page';
import {
  prefetchQualityGoals, QUALITY_GOALS_URL,
  type QualityGoal, type QualityGoalsData,
} from '../../src/react/quality-goals/quality-goals';

import { click, mount, mountRoot, settle } from './render';

// 客户端是模块级的单例（所有 React 根共用一个），用例之间不清就互相喂数据。
afterEach(() => queryClient.clear());

/* Query 派发更新用的是它自己抓住的那个真 `setTimeout(0)`，假时钟推不动它：推完时钟
   断言到的还是上一帧的 DOM。用例里改成当场派发。 */
notifyManager.setScheduler((notify) => notify());

const goal = (overrides: Partial<QualityGoal> = {}): QualityGoal => ({
  id: 1,
  name: 'one.mp4',
  code: null,
  location: 'local',
  size: 2147483648,
  duration: 3725,
  reason: null,
  cost: 'free',
  has_thumb: true,
  has_cover: false,
  ...overrides,
});

const payload = (items: QualityGoal[], total = items.length): QualityGoalsData =>
  ({ total, items, offset: 0, has_more: false });

/** 遗留层交出来的那几个助手，换成可辨认的最小实现。 */
const legacyProps = () => ({
  toast: vi.fn(),
  openItem: vi.fn<(id: number) => void>(),
  javTitleHtml: (item: QualityGoal) => `<strong class="ui-javcode">${item.name}</strong>`,
  javDisplayName: (item: QualityGoal) => `名称 ${item.name}`,
  srcBadge: (location: string, cost: string) => `<span class="src ${cost}" data-location="${location}"></span>`,
});

/** 依次回这几份数据，最后一份之后一直回它。`null` 那一份回 500。 */
function serve(...responses: (QualityGoalsData | null)[]) {
  let at = 0;
  const fetcher = vi.fn(async (input: string, _init?: RequestInit) => {
    // 云下载弹层自己取下载配置，那一路回一份没配渠道的空表，不占这几份数据的次序。
    if (input.startsWith('/api/downloads')) {
      return { ok: true, status: 200, json: async () => ({ available: true, providers: [], tasks: [] }) };
    }
    const body = responses[Math.min(at, responses.length - 1)] ?? null;
    at += 1;
    return body
      ? { ok: true, status: 200, json: async () => body }
      : { ok: false, status: 500, json: async () => ({ message: '数据库当前只能浏览' }) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

/** 走完真实的首屏路径：先 prefetch 落进缓存，再挂载。 */
async function open(...responses: (QualityGoalsData | null)[]) {
  const fetcher = serve(...responses);
  const props = legacyProps();
  await prefetchQualityGoals(new AbortController().signal).catch(() => {});
  const mounted = await mountRoot(
    <QueryClientProvider client={queryClient}><QualityGoalsPage {...props} /></QueryClientProvider>);
  return { fetcher, props, ...mounted };
}

it('首屏用 prefetch 落进缓存的那一份画出来，挂载时不再请求一次', async () => {
  const { fetcher, host } = await open(payload([goal()]));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe(QUALITY_GOALS_URL);
  expect(host.querySelectorAll('li[data-goal-id]')).toHaveLength(1);
});

it('汇总说的是服务端的总数，不是这一页截到的条数', async () => {
  const { host } = await open(payload([goal(), goal({ id: 2, name: 'two.mp4' })], 37));
  expect(host.querySelector('[data-collection-summary] strong')?.textContent).toBe('37 部作品');
  expect(host.querySelectorAll('li[data-goal-id]')).toHaveLength(2);
});

it('每条目标一张卡片，读数走遗留层同一套格式化口径', async () => {
  const { host } = await open(payload([goal({
    id: 42, name: 'one.mp4', location: '115', cost: 'metered',
    duration: 3725, size: 2147483648, reason: '只有 720p',
  })]));
  const card = host.querySelector('li[data-goal-id="42"]')!;
  // 第一格是来源徽标的宿主：它只放 HTML，不带文字。
  expect([...card.querySelectorAll('h3 ~ p > span')].map((node) => node.textContent))
    .toEqual(['', '115', '1:02:05', '2.0 GB']);
  expect(card.querySelector('.src')?.getAttribute('data-location')).toBe('115');
  expect(card.querySelector('.src')?.classList.contains('metered')).toBe(true);
  expect(card.querySelector('h3 .ui-javcode')?.textContent).toBe('one.mp4');
  expect(card.querySelector('h3 button')?.hasAttribute('data-middle-truncate')).toBe(false);
  expect(card.querySelector('h3 button')?.getAttribute('title')).toBe('名称 one.mp4');
  expect([...card.querySelectorAll('p')].at(-1)?.textContent).toBe('只有 720p');
});

it('探测失败的时长显示占位，不显示 0 也不显示负数', async () => {
  const { host } = await open(payload([goal({ duration: -1, size: 0 })]));
  expect([...host.querySelectorAll('h3 ~ p > span')].map((node) => node.textContent))
    .toEqual(['', '本地', '—', '大小未知']);
});

it('有番号封面就用番号封面，否则退回海报', async () => {
  const withCover = await open(payload([goal({ has_cover: true, code: 'ABC-123' })]));
  expect(withCover.host.querySelector('img')?.getAttribute('src')).toBe('/cover?code=ABC-123');
  await withCover.unmount();
  queryClient.clear();

  const withoutCover = await open(payload([goal({ id: 7, has_cover: false })]));
  expect(withoutCover.host.querySelector('img')?.getAttribute('src')).toBe('/poster?id=7&c=4');
});

it('封面取不到就把图摘掉，兜底文字还在，卡片其余部分照常显示', async () => {
  const { host } = await open(payload([goal()]));
  host.querySelector('img')!.dispatchEvent(new Event('error'));
  expect(host.querySelector('img')).toBeNull();
  expect(host.querySelector('li[data-goal-id] button')?.textContent).toBe('暂无预览');
});

it('封面、标题和页脚三处都打开同一部作品，无障碍名称用纯文本形态', async () => {
  const { host, props } = await open(payload([goal({ id: 42 })]));
  const cover = host.querySelector<HTMLButtonElement>('li[data-goal-id] button')!;
  expect(cover.getAttribute('aria-label')).toBe('打开 名称 one.mp4');
  await click(cover);
  await click(host.querySelector('h3 button'));
  await click([...host.querySelectorAll('button')].find((node) => node.textContent === '查看版本'));
  expect(props.openItem.mock.calls).toEqual([[42], [42], [42]]);
});

it('搜索资源原地打开云下载弹层，带入番号与原版本目标；无番号只提供查看版本', async () => {
  const { host } = await open(payload([goal({ id: 42, code: 'ABC-123', reason: '中字' }), goal({ id: 43 })]));
  const buttons = [...host.querySelectorAll('button')].filter((node) => node.textContent === '查找资源');
  expect(buttons).toHaveLength(1);
  await click(buttons[0]);
  await settle();
  const dialog = document.querySelector('[role=dialog]');
  expect(dialog?.textContent).toContain('ABC-123 · 名称 one.mp4');
});

it('一条目标都没有时给空态，不是一片白', async () => {
  const { host } = await open(payload([]));
  expect(host.querySelector('h3')?.textContent).toBe('没有标记中的高清版目标');
  expect(host.querySelector('li[data-goal-id]')).toBeNull();
});

it('首屏取数就失败时只剩一条错误，不画空列表', async () => {
  const { host } = await open(null);
  const alert = host.querySelector('[role=alert]');
  // 文案走遗留层的 `requestErrorMessage`：服务端给了中文原因就用它，页面不另说一遍。
  expect(alert?.textContent).toContain('读取失败');
  expect(alert?.textContent).toContain('数据库当前只能浏览');
  expect(host.querySelector('ul')).toBeNull();
});

it('重新进这一页会重取：这一页的刷新就是重新进来一次，不吃缓存', async () => {
  const first = await open(payload([goal({ id: 1, name: 'cached.mp4' })]),
    payload([goal({ id: 2, name: 'fresh.mp4' })]));
  expect(first.fetcher).toHaveBeenCalledTimes(1);
  await first.unmount();

  // 壳再次打开这一页：`openManagedRoute` 先 prefetch 再画。
  await prefetchQualityGoals(new AbortController().signal);
  expect(first.fetcher).toHaveBeenCalledTimes(2);
  const host = await mount(
    <QueryClientProvider client={queryClient}><QualityGoalsPage {...legacyProps()} /></QueryClientProvider>);
  expect(first.fetcher).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain('fresh.mp4');
});

it('卸载之后不再敲库：这一页没有轮询，也不该留下别的定时器', async () => {
  vi.useFakeTimers();
  const { fetcher, unmount } = await open(payload([goal()]));
  expect(fetcher).toHaveBeenCalledTimes(1);
  await unmount();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetcher, '卸载之后还在取数').toHaveBeenCalledTimes(1);
});
