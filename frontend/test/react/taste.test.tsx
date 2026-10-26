/* 口味页的行为：两套证据怎么切、换范围时那一屏还在不在、后台那一趟什么时候算数、
 * 导入与移除各自走哪条路、点名次交回去的是什么。
 *
 * 外观（雷达图的网格、热力格的浓度档）是设计决定，由 `frontend/e2e/design-*.test.ts` 读
 * `getComputedStyle` 断言；这里只看结构、文字与请求。Recharts 在这里量不到容器尺寸，
 * 整页挂上去时雷达与排行条的图形画不出来，从外层的名字与纯函数上验；雷达的刻度用例单挂
 * 这张图，拿掉 `ResizeObserver` 让它按初始尺寸画。 */
import { act } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import * as legacyUi from '@peach/legacy/ui';

import { JOB_RUNNING_POLL_MS } from '../../src/react/background-job';
import { queryClient } from '../../src/react/query';
import {
  DEFAULT_WINDOW, FLOW_VIEWBOX, flowGraph, historySpan, prefetchTaste, radarRows, RANK_MAX, rankDetail,
  TASTE_IMPORT_URL, TASTE_REFRESH_URL, TASTE_SOURCE_URL, TASTE_URL, tasteDimensions, topScores,
  type TasteData, type TasteJob,
} from '../../src/react/taste/taste';
import { TasteRadar } from '../../src/react/taste/charts';
import { TastePage } from '../../src/react/taste/taste-page';

import { choose, click, mount, mountRoot, settle } from './render';

// 客户端是模块级的单例（所有 React 根共用一个），用例之间不清就互相喂数据。
afterEach(() => queryClient.clear());

/* Query 派发更新用的是它自己抓住的那个真 `setTimeout(0)`，假时钟推不动它：推完时钟
   断言到的还是上一帧的 DOM。用例里改成当场派发。 */
notifyManager.setScheduler((notify) => notify());

const payload = (overrides: Partial<TasteData> = {}): TasteData => ({
  summary: {
    history_visits: 900, history_sources: 2, peach_items: 24, peach_seconds: 7200,
    liked: 6, disliked: 1, range_start: '2026-01-01', range_end: '2026-09-01',
  },
  coverage: { tagged: 18, identified: 12, untagged: 6, unidentified: 12 },
  rankings: {
    browser_categories: [
      { name: '维度甲', score: 40 }, { name: '维度乙', score: 25 }, { name: '维度丙', score: 10 },
    ],
    browser_tags: [
      { name: '标签甲', web_visits: 12, peach_items: 3 },
      { name: '标签乙', web_visits: 5, peach_items: 0 },
    ],
    browser_creators: [{ name: '创作者甲', web_visits: 8, peach_items: 2, source_domain: 'example.com' }],
    domains: [{ name: 'example.com', visits: 30 }],
    peach_tags: [{ name: '标签丁', score: 9, peach_items: 4 }],
    peach_creators: [],
    peach_performers: [],
  },
  gaps: [{ name: '候选甲', web_visits: 4 }],
  sources: [
    { source_key: 'a1', browser: 'chrome', profile: '桌面 Chrome', host: 'desk', visits: 120 },
    { source_key: 'b2', browser: 'browserexport', profile: '手机 Firefox', host: 'phone', visits: 30 },
  ],
  analysis: {
    headline: '口味集中在两个维度上',
    confidence: { level: 'medium', label: '中等把握' },
    points: [{ label: '主维度', text: '维度甲' }],
    explore: [{ tag: '标签丙', title: '探索标签丙', detail: '浏览里出现过' }],
    next_steps: [{ route: '/review', title: '去复核', detail: '补齐身份' }],
  },
  activity: { timezone: 'Asia/Shanghai', days: [{ date: '2026-09-01', count: 3 }], hours: [{ weekday: 0, hour: 21, count: 5 }] },
  creator_flows: [{ source: 'example.com', target: '创作者甲', value: 6 }],
  storage: { exports: 2, bytes: 1048576 },
  window: 'all',
  updated_at: '2026-09-10T00:00:00Z',
  ...overrides,
});

/** 遗留层交出来的那几个能力，换成可辨认的最小实现。 */
const legacyProps = () => ({
  onSignal: vi.fn<(kind: string, name: string) => void>(),
  navigate: vi.fn<(route: string) => void>(),
  toast: vi.fn<(message: string) => void>(),
  onboarding: false,
});

/** 这一份永不回来：换范围时用它把「上一份还在不在」问清楚。 */
const HOLD = 'hold' as const;
type TasteReply = TasteData | null | typeof HOLD;

interface Plan {
  taste?: TasteReply[];
  /** 读取任务的快照队列。放一个还没兑现的 Promise，就是那一问还在路上。 */
  job?: (TasteJob | Promise<TasteJob>)[];
  imported?: { dashboard: TasteData };
  removed?: { removed: number; dashboard: TasteData };
}

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

/** 按端点分流的假 fetch：每个端点各有自己的队列，最后一份之后一直回它。 */
function serve(plan: Plan) {
  const tastes = [...(plan.taste ?? [payload()])];
  const jobs = [...(plan.job ?? [{ status: 'idle' } as TasteJob])];
  const next = <T,>(queue: T[]): T => (queue.length > 1 ? queue.shift()! : queue[0]!);
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    if (input === TASTE_IMPORT_URL) return ok(plan.imported ?? { dashboard: payload() });
    if (input === TASTE_SOURCE_URL) return ok(plan.removed ?? { removed: 1, dashboard: payload() });
    if (input === TASTE_REFRESH_URL) {
      // 起一趟回的是这一趟 running 的快照，和 `BackgroundJob.start` 一致。
      if (init?.method) return ok({ status: 'running', stage: 'discovering' });
      return ok(await next(jobs));
    }
    if (input.startsWith(TASTE_URL)) {
      const reply = next(tastes);
      if (reply === HOLD) return new Promise<never>(() => {});
      return reply
        ? ok(reply)
        : { ok: false, status: 500, json: async () => ({ message: '数据库当前只能浏览' }) };
    }
    throw new Error(`没有安排这个端点：${input}${init?.method ? ` ${init.method}` : ''}`);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

type Fetcher = ReturnType<typeof serve>;

const tasteGets = (fetcher: Fetcher) =>
  fetcher.mock.calls.filter(([input]) => String(input).startsWith(`${TASTE_URL}?`));

const callTo = (fetcher: Fetcher, url: string) =>
  fetcher.mock.calls.filter(([input]) => String(input) === url);

/** 走完真实的首屏路径：先 prefetch 落进缓存，再挂载。 */
async function open(plan: Plan = {}, props = legacyProps()) {
  const fetcher = serve(plan);
  await prefetchTaste(DEFAULT_WINDOW, new AbortController().signal).catch(() => {});
  const mounted = await mountRoot(
    <QueryClientProvider client={queryClient}><TastePage {...props} /></QueryClientProvider>);
  await settle();
  return { fetcher, props, ...mounted };
}

/** 轮询是组件里的定时器，推进时钟会引起重画，得在 act 里推。 */
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) });

/** 按可见文字找那一枚控件。名次行与线索行都是整行一个按钮。 */
const rowNamed = (root: ParentNode, text: string) =>
  [...root.querySelectorAll('button')].find((button) => button.textContent?.includes(text)) ?? null;

/** 原生文件框在 happy-dom 里点不出 change：直接把选中的文件放上去再派发。 */
const choosePeachFile = (input: HTMLInputElement, chosen: File) => act(async () => {
  Object.defineProperty(input, 'files', { configurable: true, value: [chosen] });
  input.dispatchEvent(new Event('change', { bubbles: true }));
});

it('首屏读 prefetch 落进缓存的那一份，两套证据是页签不是并排', async () => {
  const { fetcher, host } = await open();
  expect(tasteGets(fetcher)).toHaveLength(1);
  expect(tasteGets(fetcher)[0]?.[0]).toBe(`${TASTE_URL}?window=all`);
  expect([...host.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).slice(0, 2))
    .toEqual(['浏览器记录', 'Peach 内部']);
  expect(host.textContent).toContain('900');
  expect(host.textContent).not.toContain('个作品有内部行为证据');
});

it('口味维度取分数最高的几个：雷达三到六个，排行条最多八条', () => {
  const rows = Array.from({ length: 10 }, (_, index) => ({ name: `维度${index}`, score: index }));
  expect(topScores(rows, RANK_MAX).map((row) => row.value)).toEqual([9, 8, 7, 6, 5, 4, 3, 2]);
  expect(radarRows(rows).map((row) => row.name)).toEqual(['维度9', '维度8', '维度7', '维度6', '维度5', '维度4']);
  expect(radarRows([{ name: '甲', score: 3 }, { name: '乙', score: 1 }, { name: '丙' }])).toEqual([]);
});

it('「口味维度」读数只数分数为正的维度，和雷达、排行条画的是同一批', () => {
  expect(tasteDimensions([{ name: '负', score: -2 }, { name: '乙', score: 3 }, { name: '零', score: 0 },
    { name: '甲', score: 9 }, { name: '缺' }]).map((row) => row.name)).toEqual(['甲', '乙']);
});

it('数据源与名次底下的数加千分位，没有起止日期时只读数据源数', () => {
  expect(historySpan({ history_sources: 12345 })).toBe(`${(12345).toLocaleString()} 个数据源`);
  expect(historySpan({ history_sources: 2, range_start: '2026-01-01', range_end: '2026-09-01' }))
    .toMatch(/^2 个数据源 · .+–.+$/);
  expect(rankDetail({ name: '甲', web_visits: 12345, peach_items: 6789 }))
    .toBe(`浏览 ${(12345).toLocaleString()} · Peach ${(6789).toLocaleString()}`);
  expect(rankDetail({ name: '乙', web_visits: 5, web_bookmarks: 3 })).toBe('浏览 2 · 书签 3');
});

it('流向图一侧超过十二个节点时并掉尾部，画布跟着节点数加高，节点不挤在一起', () => {
  const flows = Array.from({ length: 20 }, (_, at) => ({ source: 'example.com', target: `创作者${at}`, value: 40 - at }));
  const graph = flowGraph(flows)!;
  const targets = graph.nodes.filter((node) => node.side === 'target');
  expect(targets).toHaveLength(12);
  expect(targets.find((node) => node.name === '其余 9 位创作者')?.value).toBe(29 + 28 + 27 + 26 + 25 + 24 + 23 + 22 + 21);
  expect(graph.height).toBeGreaterThan(FLOW_VIEWBOX.height);
  const centers = targets.map((node) => node.y + node.height / 2).sort((a, b) => a - b);
  for (let at = 1; at < centers.length; at += 1) expect(centers[at]! - centers[at - 1]!).toBeGreaterThanOrEqual(30);
  expect(flowGraph(flows.slice(0, 3))!.height).toBe(FLOW_VIEWBOX.height);
});

it('维度不足三个时画像卡不出雷达，排行条照常出', async () => {
  const { host } = await open({ taste: [payload({ rankings: {
    ...payload().rankings, browser_categories: [{ name: '维度甲', score: 4 }, { name: '维度乙', score: 2 }],
  } })] });
  const portrait = host.querySelector('section[aria-label="浏览器画像"]')!;
  expect(portrait.querySelector('div[role=img]')).toBeNull();
  expect(portrait.querySelector('section[aria-label="口味维度排名"]')).not.toBeNull();
});

it('没有名次的榜给一句怎么补，不重复「暂无足够证据」', async () => {
  const { host } = await open();
  await click([...host.querySelectorAll('[role=tab]')].find((tab) => tab.textContent === 'Peach 内部') ?? null);
  await click([...host.querySelectorAll('[role=tab]')].find((tab) => tab.textContent === '创作者') ?? null);
  const panel = [...host.querySelectorAll('[role=tabpanel]')].at(-1)!;
  expect(panel.textContent).toContain('暂无足够证据');
  expect(panel.textContent).toContain('在 Peach 里播放或评价作品后，这里会列出对应的创作者。');
});

it('雷达半径按平方根刻度：最大的维度落在外圈，浮层读的仍是原始次数', async () => {
  // Recharts 靠 ResizeObserver 量容器，拿掉它就按 EvilCharts 给的初始尺寸 320 × 200 画；
  // 声明减少动态效果，Recharts 不播生长动画，多边形直接是终态。
  vi.stubGlobal('ResizeObserver', undefined);
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {},
  }));
  const counts = [657, 68, 56, 4, 1];
  const host = await mount(
    <TasteRadar rows={counts.map((score, index) => ({ name: `维度${index}`, score }))} label="主要口味维度" />);
  const points = (path: Element | null | undefined) =>
    [...(path?.getAttribute('d') ?? '').matchAll(/(-?[\d.]+),\s*(-?[\d.]+)/g)]
      .map(([, x, y]) => ({ x: Number(x), y: Number(y) }));
  const rings = [...host.querySelectorAll('.recharts-polar-grid-concentric-polygon')].map(points);
  // 半径 0 那一圈退化成圆心；网格四等分，最外一圈就是半径轴的 1。
  expect(rings).toHaveLength(5);
  const center = rings[0]![0]!;
  const reach = (point: { x: number; y: number }) => Math.hypot(point.x - center.x, point.y - center.y);
  const outer = reach(rings.at(-1)![0]!);
  const radar = points(host.querySelector('.recharts-radar-polygon path')).slice(0, counts.length);
  expect(radar.map((point) => reach(point) / outer))
    .toEqual(counts.map((count) => expect.closeTo(Math.sqrt(count / 657), 3)));

  // 指到正上方那个顶点（最大的维度），浮层给的是 657 次，不是画图用的半径 1。
  await act(async () => {
    host.querySelector('.recharts-wrapper')!.dispatchEvent(new MouseEvent('mousemove',
      { bubbles: true, clientX: center.x, clientY: center.y - outer / 2 }));
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
  const tip = host.querySelector('.recharts-tooltip-wrapper')!;
  expect(tip.textContent).toContain('维度0');
  expect([...tip.querySelectorAll('span')].map((span) => span.textContent)).toEqual(['标签命中', '657']);
});

it('浏览器画像的雷达、排行与浏览活跃热力图都按这一份数画', async () => {
  const { host } = await open();
  const portrait = host.querySelector('section[aria-label="浏览器画像"]')!;
  expect(portrait.querySelector('div[role=img]')?.getAttribute('aria-label'))
    .toBe('主要口味维度：维度甲，维度乙，维度丙');
  expect(portrait.querySelector('section[aria-label="口味维度排名"]')).not.toBeNull();
  const heats = [...host.querySelectorAll('svg[role=img]')].map((heat) => heat.getAttribute('aria-label'));
  expect(heats).toEqual(expect.arrayContaining(['浏览活跃时间', '每日活跃']));
  expect(host.querySelector('rect[aria-label="周一 21:00，5 次访问"]')).not.toBeNull();
});

it('换分析范围时留住上一份：这一屏不退回等待态，只等新的数回来', async () => {
  const { fetcher, host } = await open({ taste: [payload(), HOLD] });
  await choose(host.querySelector('[aria-haspopup="listbox"]'), '最近 7 天');
  await settle();
  expect(tasteGets(fetcher).map(([input]) => String(input)))
    .toEqual([`${TASTE_URL}?window=all`, `${TASTE_URL}?window=7d`]);
  // 上一份仍在屏幕上：读数、页签和名次都还读得到，没有退回「读取失败」。
  expect(host.textContent).toContain('900');
  expect(rowNamed(host, '标签甲')).not.toBeNull();
  expect(host.querySelector('[role=alert]')).toBeNull();
});

it('首屏读到的旧终态不冒充新结果：不发回执，也不重取 dashboard', async () => {
  const { fetcher, props, host } = await open({ job: [{ status: 'complete' }] });
  expect(props.toast).not.toHaveBeenCalled();
  expect(tasteGets(fetcher)).toHaveLength(1);
  expect(host.querySelector('[role=alert]')).toBeNull();
});

it('本次亲眼见过它在跑，跑完才发回执并重取 dashboard', async () => {
  vi.useFakeTimers();
  const { fetcher, props, host } = await open({
    job: [{ status: 'running', message: '正在读取浏览记录' }, { status: 'complete' }],
  });
  expect(host.textContent).toContain('正在读取浏览记录');
  const actions = [...host.querySelectorAll('[data-split-button] > button')];
  expect(actions).toHaveLength(2);
  expect(actions.every(button => button.getAttribute('aria-disabled') === 'true')).toBe(true);
  await click(actions[1]);
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(host.querySelector('[data-task-progress]')).not.toBeNull();
  await tick(JOB_RUNNING_POLL_MS);
  await settle();
  expect(props.toast.mock.calls).toEqual([['已更新口味分析']]);
  expect(tasteGets(fetcher).length).toBeGreaterThan(1);
});

it('导入按 octet-stream 发原文件，文件名走请求头，回来的那一份直接换进「全部时间」', async () => {
  const { fetcher, props, host } = await open({
    imported: { dashboard: payload({ summary: { history_visits: 4321, history_sources: 3 } }) },
  });
  const input = host.querySelector<HTMLInputElement>('input[type=file]')!;
  await choosePeachFile(input, new File(['x'], '历史.zip'));
  await settle();
  const sent = callTo(fetcher, TASTE_IMPORT_URL);
  expect(sent).toHaveLength(1);
  const init = sent[0]?.[1];
  const headers = init?.headers as Record<string, string>;
  expect(init?.method).toBe('POST');
  expect(headers['Content-Type']).toBe('application/octet-stream');
  expect(headers['X-Peach-Filename']).toBe(encodeURIComponent('历史.zip'));
  expect(props.toast.mock.calls).toEqual([['已导入口味数据']]);
  // 换进缓存就够了，不为一次导入再问一遍 `/api/taste`。
  expect(host.textContent).toContain('4,321');
  expect(tasteGets(fetcher)).toHaveLength(1);
});

it('书签文件上传后显示独立计数与来源', async () => {
  const { host, fetcher } = await open({ imported: { dashboard: payload({
    summary: { history_visits: 0, bookmarks: 27 },
    sources: [{ source_key: 'b2', browser: 'bookmarks', profile: '书签.html', host: 'desktop', bookmarks: 27 }],
  }) } });
  await choosePeachFile(host.querySelector<HTMLInputElement>('[data-bookmark-import]')!,
    new File(['<!DOCTYPE NETSCAPE-Bookmark-file-1>'], '书签.html'));
  await settle();
  expect(callTo(fetcher, TASTE_IMPORT_URL)).toHaveLength(1);
  expect(host.textContent).toContain('浏览器书签');
  expect(host.textContent).toContain('27 枚书签');
});

it('导入之后回到导入前看过的范围，读的是导入后的数', async () => {
  const { fetcher, host } = await open({
    // 导入前的 90 天、导入回的「全部时间」、导入后的 90 天：三份数各不相同，认得出哪一份。
    taste: [
      payload(),
      payload({ summary: { history_visits: 111, history_sources: 2 } }),
      payload({ summary: { history_visits: 4321, history_sources: 3 } }),
      payload({ summary: { history_visits: 999, history_sources: 3 } }),
    ],
    imported: { dashboard: payload({ summary: { history_visits: 4321, history_sources: 3 } }) },
  });
  await choose(host.querySelector('[aria-haspopup="listbox"]'), '最近 90 天');
  await settle();
  expect(host.textContent).toContain('111');

  await choosePeachFile(host.querySelector<HTMLInputElement>('input[type=file]')!,
    new File(['x'], '历史.zip'));
  await settle();
  expect(host.textContent).toContain('4,321');

  await choose(host.querySelector('[aria-haspopup="listbox"]'), '最近 90 天');
  await settle();
  /* 换范围一律重取：缓存里那份是导入前的，直接拿来用就是把导入的结果藏起来。
     导入之后那一趟 `window=all` 也在这里——回的是同一份，它确认的是服务端也已经落地。 */
  expect(tasteGets(fetcher).map(([input]) => String(input))).toEqual([
    `${TASTE_URL}?window=all`, `${TASTE_URL}?window=90d`,
    `${TASTE_URL}?window=all`, `${TASTE_URL}?window=90d`,
  ]);
  expect(host.textContent).toContain('999');
  expect(host.textContent).not.toContain('111');
});

it('移除数据源用回来的那一份就地换掉，不为一次移除重取整页', async () => {
  const modal = vi.spyOn(legacyUi, 'confirmModal').mockImplementation(async (options) => {
    await options.onConfirm?.();
    return { confirmed: true };
  });
  const rest = payload({
    sources: [{ source_key: 'b2', browser: 'browserexport', profile: '手机 Firefox', host: 'phone', visits: 30 }],
  });
  const { fetcher, props, host } = await open({ removed: { removed: 1, dashboard: rest } });
  expect(host.textContent).toContain('桌面 Chrome');
  await click(host.querySelector('[aria-label="移除 桌面 Chrome"]'));
  await settle();
  expect(modal).toHaveBeenCalledTimes(1);
  const sent = callTo(fetcher, TASTE_SOURCE_URL);
  expect(sent).toHaveLength(1);
  expect(JSON.parse(String(sent[0]?.[1]?.body)))
    .toEqual({ operation: 'remove', source_key: 'a1', window: 'all' });
  expect(props.toast.mock.calls).toEqual([['已移除口味数据源']]);
  expect(host.textContent).not.toContain('桌面 Chrome');
  expect(tasteGets(fetcher)).toHaveLength(1);
});

it('馆藏里有对应条目的名次才点得动，点了把信号交回给壳，页面自己不跳转', async () => {
  const { props, host } = await open();
  expect(rowNamed(host, '标签乙')).toBeNull();
  await click(rowNamed(host, '标签甲'));
  expect(props.onSignal.mock.calls).toEqual([['tag', '标签甲']]);
  expect(props.navigate).not.toHaveBeenCalled();
});

it('口味总结那几条各走各的出口：探索交信号，下一步交路由', async () => {
  const { props, host } = await open();
  await click(rowNamed(host, '探索标签丙'));
  expect(props.onSignal.mock.calls).toEqual([['tag', '标签丙']]);
  await click(rowNamed(host, '去复核'));
  expect(props.navigate.mock.calls).toEqual([['/review']]);
});

it('卸载之后不再敲库：后台那一趟的轮询跟着这棵根走', async () => {
  vi.useFakeTimers();
  const { fetcher, unmount } = await open();
  const before = fetcher.mock.calls.length;
  await unmount();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetcher.mock.calls.length, '卸载之后还在取数').toBe(before);
});
