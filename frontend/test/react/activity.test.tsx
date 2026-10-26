/* 活动页的行为：三段怎么分、轮询多久一次、失败时留下什么。
 *
 * 外观（状态徽章的三档颜色、失败卡的框线）是设计决定，由 `frontend/e2e/design-*.test.ts`
 * 读 `getComputedStyle` 断言；这里只看结构、文字与请求。 */
import { act } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { ActivityPage } from '../../src/react/activity/activity-page';
import { DOWNLOADS_URL } from '../../src/react/activity/downloads-panel';
import { ErrorExcerpt } from '../../src/react/activity/error-excerpt';
import {
  elapsedText, errorHeadline, prefetchTasks, progressText, summaryText, TASKS_URL,
} from '../../src/react/activity/tasks';
import type { ActivityData, FinishedPage, TaskRunPayload } from '../../src/react/activity/tasks';
import type { DownloadsSnapshot } from '../../src/react/bundle';
import { queryClient } from '../../src/react/query';

import { buttonNamed, click, mount, mountRoot, settle } from './render';

// 客户端是模块级的单例（所有 React 根共用一个），用例之间不清就互相喂数据。
afterEach(() => queryClient.clear());

/* Query 派发更新用的是它自己抓住的那个真 `setTimeout(0)`，假时钟推不动它：推完时钟
   断言到的还是上一帧的 DOM。用例里改成当场派发。 */
notifyManager.setScheduler((notify) => notify());

const run = (overrides: Partial<TaskRunPayload> = {}): TaskRunPayload => ({
  id: 1,
  task_key: 'follow-check',
  task_label: '追更检查',
  trigger: 'manual',
  status: 'running',
  host: 'desk',
  started_at: '2026-09-11T10:00:00Z',
  finished_at: null,
  elapsed_seconds: 75,
  progress_current: 3,
  progress_total: 12,
  progress_label: '正在查第三个来源',
  result_summary: {},
  error: '',
  parent_run_id: null,
  root_run_id: null,
  followup_key: '',
  followup_depth: 0,
  ...overrides,
});

/** 一条后继（ADR-0040）：跑完的一行，挂在 `parent` 那一轮下面。 */
const followup = (parent: number, overrides: Partial<TaskRunPayload> = {}): TaskRunPayload =>
  run({
    id: 90, task_key: 'entity-avatar', task_label: '补全资料图片', status: 'succeeded',
    progress_total: null, progress_current: null, progress_label: '补全资料图片：涼森れむ',
    finished_at: '2026-09-11T10:04:00Z', parent_run_id: parent, root_run_id: parent,
    followup_key: 'entity-avatar:performer:12', followup_depth: 1,
    result_summary: { outcome: '已装上' }, ...overrides,
  });

const payload = (overrides: Partial<ActivityData> = {}): ActivityData => ({
  available: true, running: [], skipped: [], finished: [], ...overrides,
});

/* 云下载段自己取 `/api/downloads`。这里的用例只数任务中心的请求，云下载那一路另答一份空表，那一段就不画。 */
const DOWNLOADS_IDLE: DownloadsSnapshot = { available: true, providers: [], tasks: [] };
function stubFetch(tasks: (input: string, init?: RequestInit) => Promise<unknown>) {
  vi.stubGlobal('fetch', (input: string, init?: RequestInit) => (input.startsWith(DOWNLOADS_URL)
    ? Promise.resolve({ ok: true, status: 200, json: async () => DOWNLOADS_IDLE })
    : tasks(input, init)));
}

/** 依次回这几份数据，最后一份之后一直回它。`null` 那一份回 500。 */
function serve(...responses: (ActivityData | null)[]) {
  let at = 0;
  const fetcher = vi.fn(async (_input: string, _init?: RequestInit) => {
    const body = responses[Math.min(at, responses.length - 1)] ?? null;
    at += 1;
    return body
      ? { ok: true, status: 200, json: async () => body }
      : { ok: false, status: 500, json: async () => ({ message: '数据库当前只能浏览' }) };
  });
  stubFetch(fetcher);
  return fetcher;
}

const page = () => <QueryClientProvider client={queryClient}><ActivityPage /></QueryClientProvider>;

/** 走完真实的首屏路径：先 prefetch 落进缓存，再挂载。 */
async function open(...responses: (ActivityData | null)[]) {
  const fetcher = serve(...responses);
  await prefetchTasks(new AbortController().signal).catch(() => {});
  return { fetcher, host: await mount(page()) };
}

const sections = (host: HTMLElement) => [...host.querySelectorAll('section')]
  .map((section) => section.querySelector('h3')?.textContent);

/** 轮询是组件里的定时器，推进时钟会引起重画，得在 act 里推。 */
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) });

it('首屏用 prefetch 落进缓存的那一份画出来，挂载时不再请求一次', async () => {
  const { fetcher, host } = await open(payload({ running: [run()] }));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe(TASKS_URL);
  expect(host.querySelector('[data-task-key=follow-check]')).not.toBeNull();
});

it('在跑的那一轮给出真实计数的进度条与已跑时长', async () => {
  const { host } = await open(payload({ running: [run()] }));
  const bar = host.querySelector('[role=progressbar]')!;
  expect(bar.getAttribute('aria-valuenow')).toBe('3');
  expect(bar.getAttribute('aria-valuemax')).toBe('12');
  expect(host.textContent).toContain('3 / 12 项');
  expect(host.textContent).toContain('已跑 1 分 15 秒');
  expect(host.querySelector('strong')?.textContent).toBe('追更检查');
});

it('总量未知时不画假进度条，只说正在做什么', async () => {
  const { host } = await open(payload({ running: [run({ progress_total: null, progress_current: null })] }));
  expect(host.querySelector('[role=progressbar]')).toBeNull();
  expect(host.querySelector('[role=status]')?.textContent).toContain('正在查第三个来源');
});

it('被挡下的那一轮单独成段，不在最近完成里再出现一次', async () => {
  const blocked = run({
    id: 7, status: 'cancelled', trigger: 'scheduled', progress_total: null,
    finished_at: '2026-09-11T10:05:00Z', elapsed_seconds: null,
    result_summary: { blocked_by: 3 }, error: '与第 3 轮（手动触发）冲突，本次跳过',
  });
  const { host } = await open(payload({ skipped: [blocked], finished: [blocked] }));
  expect(sections(host)).toEqual(['正在进行', '被挡下的']);
  expect(host.querySelectorAll('li')).toHaveLength(1);
  // 原因落在卡片底部的说明区，状态由徽章说。
  expect(host.querySelector('[data-status=cancelled]')?.textContent).toContain('与第 3 轮');
  expect(host.querySelector('[data-status=cancelled] span')?.textContent).toBe('已取消');
});

it('跑完那一轮显示摘要，失败那一轮显示原因', async () => {
  const { host } = await open(payload({ finished: [
    run({ id: 2, status: 'succeeded', finished_at: '2026-09-11T10:02:00Z',
          progress_total: null, result_summary: { checked: 7, results: [1, 2] } }),
    run({ id: 3, status: 'failed', task_label: '扫描与采集', finished_at: '2026-09-11T10:03:00Z',
          progress_total: null, error: 'RuntimeError: 上游挡回来了' }),
  ] }));
  expect(host.querySelector('[data-status=succeeded]')?.textContent).toContain('已检查 7');
  expect(host.querySelector('[data-status=failed]')?.textContent).toContain('上游挡回来了');
  expect(host.querySelector('[data-status=succeeded] span')?.textContent).toBe('已完成');
  // 跑成功那张没有说明区：没有原因可写时不留一条空条子。
  expect(host.querySelectorAll('[data-status=succeeded] p')).toHaveLength(2);
});

it('后继挂在派出它的那一轮下面，不另占一张卡', async () => {
  const parent = run({ id: 4, status: 'succeeded', task_label: '扫描与采集',
                       progress_total: null, finished_at: '2026-09-11T10:03:00Z',
                       result_summary: { followups: 1 } });
  const { host } = await open(payload({ finished: [parent, followup(parent.id)] }));
  expect(host.querySelectorAll('[data-task-key]')).toHaveLength(1);
  const row = host.querySelector('[data-followup-key]')!;
  expect(host.querySelector('[data-task-key=follow-check]')!.contains(row)).toBe(true);
  expect([...row.querySelectorAll('span')].map((node) => node.textContent))
    .toEqual(['补全资料图片', '已完成', '涼森れむ · 结果 已装上']);
  expect(host.textContent).toContain('派出后继 1');
});

it('完成的任务明细默认收起，展开和收起不丢失后继结果', async () => {
  const parent = run({ id: 4, status: 'succeeded', finished_at: '2026-09-11T10:03:00Z' });
  const { host } = await open(payload({ finished: [parent, followup(parent.id)] }));
  const details = host.querySelector('details')!;
  const trigger = details.querySelector('summary')!;
  expect(trigger.textContent).toBe('任务明细 · 1 项');
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
  await click(trigger);
  expect(trigger.getAttribute('aria-expanded')).toBe('true');
  expect(details.open).toBe(true);
  expect(details.textContent).toContain('涼森れむ · 结果 已装上');
  await click(trigger);
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
});

it('进行中和失败的后继明细默认展开', async () => {
  for (const status of ['running', 'failed']) {
    queryClient.clear();
    const parent = run({ id: 4, status: 'succeeded', finished_at: '2026-09-11T10:03:00Z' });
    const child = followup(parent.id, { status });
    const { host } = await open(payload({ finished: [parent, child] }));
    expect(host.querySelector('details')?.open).toBe(true);
    expect(host.querySelector('summary')?.getAttribute('aria-expanded')).toBe('true');
  }
});

it('后继还在跑时，派出它的那一轮整张卡留在正在进行，那一行只说做到哪一部', async () => {
  const parent = run({ id: 4, status: 'succeeded', task_label: '订阅源拉取',
                       progress_total: null, finished_at: '2026-09-11T10:03:00Z',
                       result_summary: { followups: 1 } });
  const scraping = followup(parent.id, {
    task_key: 'feed-scrape', task_label: '取新作资料', status: 'running', finished_at: null,
    progress_label: '取新作资料：MIZD-441', progress_current: 13, progress_total: 22,
    followup_key: 'feed-scrape:MIZD-441', result_summary: {} });
  const { host } = await open(payload({ running: [scraping], finished: [parent] }));
  expect(sections(host)).toEqual(['正在进行']);
  const row = host.querySelector('[data-followup-key]')!;
  expect([...row.querySelectorAll('span')].map((node) => node.textContent))
    .toEqual(['取新作资料', '进行中', 'MIZD-441 · 13 / 22 项']);
});

it('连着几轮什么也没发生的定时任务按种类各并成一张卡，有事的那一轮截断并单独摆', async () => {
  const hourly = (id: number, overrides: Partial<TaskRunPayload> = {}) => done(id, {
    trigger: 'scheduled', result_summary: { checked: 83, total: 83 }, ...overrides });
  const feed = (id: number, overrides: Partial<TaskRunPayload> = {}) => hourly(id, {
    task_key: 'feed-check', task_label: '订阅源拉取', result_summary: { checked: 2, added: 0 }, ...overrides });
  const { host } = await open(payload({ finished: [
    hourly(9), feed(8), hourly(7), feed(6), hourly(5),
    feed(4, { result_summary: { checked: 2, added: 3 } }),
    hourly(3), hourly(2),
  ] }));
  expect(shownIds(host)).toEqual([9, 8, 4, 3]);
  const first = host.querySelector('[data-run-id="9"]')!;
  expect(first.textContent).toContain('定时 · 近 3 轮 · ');
  expect(first.textContent).toContain('已检查 83 · 总数 83');
  expect(host.querySelector('[data-run-id="8"]')!.textContent).toContain('近 2 轮');
  expect(host.querySelector('[data-run-id="4"]')!.textContent).toContain('新增 3');
  expect(host.querySelector('[data-run-id="3"]')!.textContent).toContain('近 2 轮');
});

it('父任务不在这一屏上时，后继照常单独摆出来', async () => {
  const { host } = await open(payload({ finished: [followup(4)] }));
  expect(host.querySelectorAll('[data-task-key=entity-avatar]')).toHaveLength(1);
  expect(host.querySelector('[data-followup-key]')).toBeNull();
});

it('一条记录都没有时给空态，不是一片白', async () => {
  const { host } = await open(payload());
  expect(host.querySelector('h3')?.textContent).toBe('还没有任务记录');
  // 任务中心那三段都不画；云下载没有任务，也不画。
  expect(sections(host)).toEqual([]);
});

it('数据库上还没有这张表时说清怎么办，不当成故障', async () => {
  const { host } = await open(payload({
    available: false, message: '数据库缺少任务记录表，请按运行文档检查备份后执行 peach migrate upgrade --yes' }));
  expect(host.querySelector('[role=note]')?.textContent).toContain('migrate upgrade --yes');
  expect(host.querySelector('[role=alert]')).toBeNull();
});

it('首屏取数就失败时只剩一条错误，不画空的三段', async () => {
  const { host } = await open(null);
  // 文案走遗留层的 `requestErrorMessage`：服务端给了中文原因就用它，页面不另说一遍。
  expect(host.querySelector('[role=alert]')?.textContent).toContain('数据库当前只能浏览');
  expect(host.querySelector('section')).toBeNull();
});

it('轮询间隔跟着内容走：有东西在跑两秒一次，全是终态十秒一次', async () => {
  vi.useFakeTimers();
  const fetcher = serve(payload({ running: [run()] }), payload({ finished: [run({ status: 'succeeded' })] }));
  await prefetchTasks(new AbortController().signal);
  await mount(page());
  expect(fetcher).toHaveBeenCalledTimes(1);

  await tick(2000);
  expect(fetcher, '有任务在跑时两秒一次').toHaveBeenCalledTimes(2);

  // 这一份全是终态，下一轮要等十秒。
  await tick(2000);
  expect(fetcher, '没东西在跑还两秒敲一次库').toHaveBeenCalledTimes(2);
  await tick(8000);
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('一轮请求失败不擦掉上一份数据，只在页内多一条原因', async () => {
  vi.useFakeTimers();
  const fetcher = serve(payload({ finished: [run({ id: 5, status: 'succeeded', task_label: '扫描与采集' })] }), null);
  await prefetchTasks(new AbortController().signal);
  const host = await mount(page());

  await tick(10_000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(host.querySelector('[role=alert]')?.textContent).toContain('数据库当前只能浏览');
  expect(host.textContent, '失败一次就把上一份结果擦掉，页面比原来知道得更少').toContain('扫描与采集');
});

it('卸载之后不再敲库：轮询跟着这棵根一起走', async () => {
  vi.useFakeTimers();
  const fetcher = serve(payload({ running: [run()] }));
  await prefetchTasks(new AbortController().signal);
  const mounted = await mountRoot(page());

  await tick(2000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await mounted.unmount();
  await tick(60_000);
  expect(fetcher, '卸载之后还在轮询').toHaveBeenCalledTimes(2);
});

/** 结束于 10:mm 的一轮。分钟越大越新，id 顺着分钟走。 */
const done = (minute: number, overrides: Partial<TaskRunPayload> = {}) => run({
  id: minute, status: 'succeeded', progress_total: null, progress_current: null,
  finished_at: `2026-09-11T10:${String(minute).padStart(2, '0')}:00.000Z`, ...overrides,
});

/** 轮询与往前翻各有一串回话：地址里带游标的走 `pages`，其余走 `polls`。`null` 那一份回 500。 */
function route(polls: ActivityData[], pages: (FinishedPage | null)[]) {
  let polled = 0, paged = 0;
  const fetcher = vi.fn(async (input: string, _init?: RequestInit) => {
    if (!input.includes('before_id=')) {
      const body = polls[Math.min(polled, polls.length - 1)];
      polled += 1;
      return { ok: true, status: 200, json: async () => body };
    }
    const body = pages[Math.min(paged, pages.length - 1)] ?? null;
    paged += 1;
    return body
      ? { ok: true, status: 200, json: async () => body }
      : { ok: false, status: 500, json: async () => ({ message: '数据库当前只能浏览' }) };
  });
  stubFetch(fetcher);
  return fetcher;
}

const shownIds = (host: HTMLElement) =>
  [...host.querySelectorAll('[data-run-id]')].map((node) => Number(node.getAttribute('data-run-id')));

const cursorOf = (fetcher: ReturnType<typeof route>, call: number) =>
  new URL(fetcher.mock.calls[call]![0], 'http://peach').searchParams;

it('「加载更早」以最旧那一行为游标往后接一页，到头就不再给这个键', async () => {
  const fetcher = route(
    [payload({ finished: [done(9), done(8)], finished_has_more: true })],
    [{ finished: [done(7), done(6)], finished_has_more: true },
     { finished: [done(5)], finished_has_more: false }]);
  await prefetchTasks(new AbortController().signal);
  const host = await mount(page());
  expect(shownIds(host)).toEqual([9, 8]);

  await click(buttonNamed('加载更早', host));
  await settle();
  expect(cursorOf(fetcher, 1).get('before_id')).toBe('8');
  expect(cursorOf(fetcher, 1).get('before_finished_at')).toBe('2026-09-11T10:08:00.000Z');
  expect(shownIds(host)).toEqual([9, 8, 7, 6]);

  await click(buttonNamed('加载更早', host));
  await settle();
  expect(cursorOf(fetcher, 2).get('before_id')).toBe('6');
  expect(shownIds(host)).toEqual([9, 8, 7, 6, 5]);
  expect(buttonNamed('加载更早', host), '已经到头还留着「加载更早」').toBeNull();
});

it('第一页之后没有更早的就不给「加载更早」', async () => {
  const { host } = await open(payload({ finished: [done(9)], finished_has_more: false }));
  expect(buttonNamed('加载更早', host)).toBeNull();
});

it('轮询刷新之后翻过的行还在，被新完成挤出第一页的那一轮也不丢、不重复', async () => {
  vi.useFakeTimers();
  route(
    [payload({ finished: [done(9), done(8)], finished_has_more: true }),
     // 10:10 又结束了一轮，第一页末尾的 8 被挤了出去。
     payload({ finished: [done(10), done(9)], finished_has_more: true }),
     payload({ finished: [done(11), done(10)], finished_has_more: true }),
     // 翻页之后才进第一页的 10 也要留住：它被挤出去时，哪一页的回话里都没有它。
     payload({ finished: [done(12), done(11)], finished_has_more: true })],
    [{ finished: [done(7), done(6)], finished_has_more: true }]);
  await prefetchTasks(new AbortController().signal);
  const host = await mount(page());
  await click(buttonNamed('加载更早', host));
  await settle();
  expect(shownIds(host)).toEqual([9, 8, 7, 6]);

  await tick(10_000);
  expect(shownIds(host)).toEqual([10, 9, 8, 7, 6]);
  await tick(10_000);
  await tick(10_000);
  expect(shownIds(host)).toEqual([12, 11, 10, 9, 8, 7, 6]);
  expect(buttonNamed('加载更早', host), '刷新一次就把往前翻的进度丢了').not.toBeNull();
});

it('往前翻失败时原位说原因，已有的行不动，再点一次照常接上', async () => {
  route([payload({ finished: [done(9)], finished_has_more: true })],
        [null, { finished: [done(7)], finished_has_more: false }]);
  await prefetchTasks(new AbortController().signal);
  const host = await mount(page());

  await click(buttonNamed('加载更早', host));
  await settle();
  expect(host.querySelector('[role=alert]')?.textContent).toContain('数据库当前只能浏览');
  expect(shownIds(host)).toEqual([9]);

  await click(buttonNamed('加载更早', host));
  await settle();
  expect(host.querySelector('[role=alert]')).toBeNull();
  expect(shownIds(host)).toEqual([9, 7]);
});

it('时长与摘要的折算各自成立', () => {
  expect(elapsedText(0)).toBe('0 秒');
  expect(elapsedText(59)).toBe('59 秒');
  expect(elapsedText(75)).toBe('1 分 15 秒');
  expect(elapsedText(3725)).toBe('1 小时 2 分');
  // 满 48 小时按天说：「73 小时 1 分」读不出是三天。
  expect(elapsedText(47 * 3600 + 59 * 60)).toBe('47 小时 59 分');
  expect(elapsedText(73 * 3600 + 60)).toBe('3 天 1 小时');
  expect(elapsedText(null)).toBe('');
  expect(summaryText({ checked: 1234567 })).toBe('已检查 1,234,567');
  // 进度按总数封顶，带千分位。
  expect(progressText(1500, 1000)).toBe('1,000 / 1,000');
  expect(progressText(-3, 10)).toBe('0 / 10');
  expect(progressText(null, null)).toBe('0 / 0');
  // 明细与「谁挡的」不进这一行：前者太长，后者已经写在错误那一句里了。
  expect(summaryText({ checked: 7, operation: 'like', rows: [1], blocked_by: 3 }))
    .toBe('已检查 7 · 操作 like');
  // 追更检查结算时带着整份状态字典，簿记字段不能漏到卡片上。
  expect(summaryText({
    status: 'complete', run_id: 351, started_at: 1790138114.86, checked: 83, total: 83,
    request_id: '417dab8d', completed_at: 1790138495.48,
  })).toBe('已检查 83 · 总数 83');
});

it('报错在列表里只露一行：traceback 取最后一行，别的取第一行非空', () => {
  const stack = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nValueError: 坏了';
  expect(errorHeadline(stack)).toBe('ValueError: 坏了');
  expect(errorHeadline('\n  连接超时\n详情见日志')).toBe('连接超时');
  expect(errorHeadline('')).toBe('');
  expect(errorHeadline(null)).toBe('');
});

it('报错摘要平时只露一行，「查看详情」展开全文、再点收起；只有一行时不给这颗键', async () => {
  const stack = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: 坏了';
  const host = await mount(<ErrorExcerpt text={stack} />);
  expect(host.textContent).toBe('ValueError: 坏了查看详情');
  await click(buttonNamed('查看详情', host));
  expect(host.textContent).toContain('File "x.py", line 1');
  expect(buttonNamed('收起详情', host)?.getAttribute('aria-expanded')).toBe('true');
  await click(buttonNamed('收起详情', host));
  expect(host.textContent).toBe('ValueError: 坏了查看详情');

  const single = await mount(<ErrorExcerpt text="只有一行" />);
  expect(single.textContent).toBe('只有一行');
  expect(buttonNamed('查看详情', single)).toBeNull();
});
