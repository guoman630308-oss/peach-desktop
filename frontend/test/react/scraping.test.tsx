/* 来源和凭证页的行为：交什么、清什么、结果怎么说、后台那一趟跟到什么时候。
 *
 * 外观（页脚三键的主次、来源外链的 `rel`）是设计决定，由 `frontend/e2e/design-*.test.ts`
 * 读 `getComputedStyle` 断言；这里只看结构、文字与请求。 */
import { act } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { queryClient } from '../../src/react/query';
import { linkHost, networkOptions, ScrapingPage } from '../../src/react/scraping/scraping-page';
import { prefetchScraping } from '../../src/react/scraping/scraping';
import type { AmaneBridge, Check, CoverJob, Source } from '../../src/react/scraping/scraping';

import {
  buttonNamed, click, mount, mountRoot, section, settle, submit, type,
} from './render';

// 客户端是模块级的单例（所有 React 根共用一个），用例之间不清就互相喂数据。
afterEach(() => queryClient.clear());

/* Query 派发更新用的是它自己抓住的那个真 `setTimeout(0)`，假时钟推不动它：推完时钟
   断言到的还是上一帧的 DOM。用例里改成当场派发。 */
notifyManager.setScheduler((notify) => notify());

const source = (overrides: Partial<Source> = {}): Source => ({
  source: 'javbus',
  label: 'JavBus',
  login: 'https://www.javbus.com/',
  accepts_cookie: true,
  network: 'peach',
  cookie_saved: false,
  browser: false,
  ...overrides,
});

interface Reply {
  ok?: boolean;
  status?: number;
  body: unknown;
}

/** 按「方法 + 路径」应答。用例没造的请求当场失败，而不是静悄悄回一个空对象。
 *  应答回一个还没兑现的 Promise，就是这一问还在路上。 */
function serve(handlers: Record<string, (body: never) => Reply | Promise<Reply>>) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    const handler = handlers[`${method} ${path}`];
    if (!handler) throw new Error(`用例没有给 ${method} ${path} 造数据`);
    const reply = await handler(body as never);
    return { ok: reply.ok !== false, status: reply.status ?? 200, json: async () => reply.body };
  });
  vi.stubGlobal('fetch', fetcher);
  return { calls, fetcher };
}

/** 依次回这几份任务快照，最后一份之后一直回它。 */
function coverStates(...states: CoverJob[]) {
  let at = 0;
  return () => {
    const state = states[Math.min(at, states.length - 1)]!;
    at += 1;
    return { body: state };
  };
}

const page = (toast: (message: string) => void) => (
  <QueryClientProvider client={queryClient}><ScrapingPage toast={toast} /></QueryClientProvider>
);

/** 走完真实的首屏路径：先 prefetch 落进缓存，再挂载。 */
async function open(handlers: Record<string, (body: never) => Reply | Promise<Reply>>) {
  const toast = vi.fn();
  const served = serve(handlers);
  await prefetchScraping(new AbortController().signal).catch(() => {});
  return { ...served, toast, host: await mount(page(toast)) };
}

/** amane 桥那张卡的事实：已装好、没有在跑的重建。 */
const BRIDGE: AmaneBridge = {
  repository: 'https://github.com/sqzw-x/amane', license: 'GPL-3.0',
  revision: '79ecfa763cc786318e1964a3d7f4e244a7d5c96d', version: '0.16.1', installed: true,
  installed_version: '0.16.1',
  python: 'T:/tools/amane-bridge/.venv/Scripts/python.exe',
  sites: [{ source: 'fc2ppvdb', label: 'FC2PPVDB' }, { source: 'avsox', label: 'AVSOX' }],
  job: { status: 'idle' },
};

/** 一份只有一个来源、后台没有任务、amane 桥已装好的首屏。 */
const quiet = (overrides: Partial<Source> = {}) => ({
  'GET /api/scraping': () => ({ body: { sources: [source(overrides)] } }),
  'GET /api/scraping/cover': () => ({ body: { status: 'idle' } }),
  'GET /api/scraping/amane-bridge': () => ({ body: BRIDGE }),
  'GET /api/custom-metadata': () => ({ body: { items: [] } }),
});
/** 首屏来源与任务数据的请求顺序，和 `prefetchScraping` 一致。 */
const FIRST_SCREEN = ['/api/scraping', '/api/scraping/cover', '/api/scraping/amane-bridge', '/api/custom-metadata'];

const password = (root: ParentNode) => root.querySelector<HTMLInputElement>('input[type=password]');
const radio = (value: string) => document.querySelector<HTMLInputElement>(`input[type=radio][value="${value}"]`);
/** 轮询是组件里的定时器，推进时钟会引起重画，得在 act 里推。 */
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) });

it('首屏用 prefetch 缓存画出来源与任务，挂载时不重复请求', async () => {
  const { calls, host } = await open(quiet());
  expect(calls.map((call) => call.path)).toEqual(FIRST_SCREEN);
  expect(section(host, 'JavBus')).not.toBeNull();
  expect(section(host, '高清封面')).not.toBeNull();
  const bridge = section(host, 'amane');
  expect(bridge).not.toBeNull();
  expect(bridge?.textContent).toContain('已安装版本0.16.1');
  expect(bridge?.textContent).toContain('FC2PPVDB、AVSOX');
  expect(buttonNamed('重新安装', bridge!)).not.toBeNull();
  // 连接方式是 BoardUI 的 Select，画出来的是按钮；原生 select 是 React Aria 藏在后面
  // 供表单取值的那一个，不是这一行的长相。
  expect(host.querySelector('[aria-haspopup=listbox]')?.textContent?.trim()).toBe('Peach 代理');
  expect(host.querySelector<HTMLAnchorElement>('a[href="/settings/library#peachProxy"]')?.textContent)
    .toContain('配置 Peach 代理');
  expect(host.querySelector<HTMLAnchorElement>('a[href="https://www.javbus.com/"]')?.target).toBe('_blank');
});

it('走本机浏览器的来源只说明验证怎么过，不画 Cookie 输入', async () => {
  const { host } = await open(quiet({ source: 'fc2ppvdb', label: 'FC2PPV-DB', browser: true }));
  const card = section(host, 'FC2PPV-DB');
  expect(card?.textContent).toContain('人机验证由本机浏览器自动完成');
  expect(card?.textContent).toContain('这台机器上不需要 Cookie');
  expect(password(host), '浏览器自己带 Cookie，这一格不该出现').toBeNull();
  expect(card?.textContent).not.toContain('任选一种方式提供 Cookie');
  expect(host.querySelector('[aria-haspopup=listbox]'), '连接方式仍由用户选，浏览器按它换出口').not.toBeNull();
});

it('保存后清空秘密输入，列表就地换成服务端回的那一条，撤销随之可操作', async () => {
  const { calls, host, toast } = await open({
    ...quiet(),
    'POST /api/scraping/settings': () => ({ body: { saved: source({ cookie_saved: true }) } }),
  });
  await type(password(host), 'session=fixture');
  await submit(section(host, 'JavBus'));
  await settle();

  expect(calls[FIRST_SCREEN.length]).toEqual({
    path: '/api/scraping/settings',
    method: 'POST',
    body: {
      source: 'javbus', network: 'peach', cookie: 'session=fixture', cookies_text: '', revoke: false,
    },
  });
  expect(password(host)?.value, '保存回来之后页面上不再留着刚交上去的那一份').toBe('');
  expect(host.textContent).toContain('登录是否有效要到抓取时才知道');
  expect(buttonNamed('撤销 Cookie', host)).not.toBeNull();
  expect(toast).toHaveBeenCalledWith('来源设置已保存');
  expect(calls.filter((call) => call.path === '/api/scraping' && call.method === 'GET'),
    '保存换的是列表里的一条，不为它把整页重取一遍').toHaveLength(1);
});

it('撤销走同一条写入，回执说的是撤销', async () => {
  const { calls, host, toast } = await open({
    ...quiet({ cookie_saved: true }),
    'POST /api/scraping/settings': () => ({ body: { saved: source({ cookie_saved: false }) } }),
  });
  await click(buttonNamed('撤销 Cookie', host));
  await settle();
  expect(calls[FIRST_SCREEN.length]?.body).toMatchObject({ source: 'javbus', revoke: true });
  expect(toast).toHaveBeenCalledWith('Cookie 已撤销');
  expect(buttonNamed('撤销 Cookie', host), '撤销之后这颗键没有对象可撤了').toBeNull();
});

it('保存失败时原因留在卡内，列表和刚填的内容都不动', async () => {
  const { host, toast } = await open({
    ...quiet(),
    'POST /api/scraping/settings': () => ({ ok: false, status: 400, body: { message: 'Cookie 不是 Netscape 格式' } }),
  });
  await type(password(host), 'session=fixture');
  await submit(section(host, 'JavBus'));
  await settle();

  expect(host.querySelector('[role=alert]')?.textContent).toContain('Cookie 不是 Netscape 格式');
  expect(password(host)?.value, '失败了还清空输入，等于让人重打一遍').toBe('session=fixture');
  expect(buttonNamed('撤销 Cookie', host)).toBeNull();
  expect(toast).not.toHaveBeenCalled();
});

it('连接结果按来源名称与这一跳查的是什么说成一句话', async () => {
  const results: Check[] = [
    { label: '来源页面', ok: true, width: 800, height: 538 },
    { label: '高清图片 CDN', ok: false, status: 403, message: '需要登录' },
  ];
  const { host } = await open({
    ...quiet(),
    'POST /api/scraping/check': () => ({ body: { results } }),
  });
  await click(buttonNamed('检查连接', host));
  await settle();

  expect(host.querySelector('[role=status]')?.textContent).toBe('JavBus：可连接 · 800 × 538');
  expect(host.querySelector('[role=alert]')?.textContent).toBe('JavBus 高清图片：不能连接。需要登录');
});

it('Cookie 二选一：切过去的那一种才交，另一种连输入都不留', async () => {
  const { calls, host } = await open({
    ...quiet(),
    'POST /api/scraping/settings': () => ({ body: { saved: source() } }),
  });
  expect(host.querySelector('input[type=file]')).toBeNull();
  await type(password(host), 'private');
  await click(radio('file'));
  expect(password(host), '粘贴框还在页面上就会跟着提交').toBeNull();
  expect(host.querySelector('input[type=file]')).not.toBeNull();
  expect(host.textContent).toContain('未选择文件');

  await submit(section(host, 'JavBus'));
  await settle();
  expect(calls[FIRST_SCREEN.length]?.body).toMatchObject({ cookie: '', cookies_text: '' });

  await click(radio('paste'));
  expect(password(host)?.value).toBe('');
});

it('Cookie 文件超过上限时当场拦住，不拿它去占一次请求', async () => {
  const { calls, host } = await open(quiet());
  await click(radio('file'));
  const input = host.querySelector<HTMLInputElement>('input[type=file]')!;
  const oversized = new File(['x'.repeat(256 * 1024 + 1)], 'cookies.txt', { type: 'text/plain' });
  Object.defineProperty(input, 'files', { configurable: true, value: [oversized] });
  await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })) });
  await settle();

  expect(host.querySelector('[role=alert]')?.textContent).toBe('Cookie 文本超过 256 KiB');
  expect(host.textContent).toContain('未选择文件');
  expect(calls).toHaveLength(FIRST_SCREEN.length);
});

it('首屏读到的旧结果不冒充新结果：不画、也不发回执', async () => {
  const { host, toast } = await open({
    ...quiet(),
    'GET /api/scraping/cover': () => ({ body: { status: 'complete', result: '上一趟的封面' } }),
  });
  expect(toast).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('上一趟的封面');
  expect(host.textContent).not.toContain('正在抓取封面');
});

it('抓封面跟到终态：跑的时候两秒一次，跑完发一次回执就不再问', async () => {
  vi.useFakeTimers();
  const toast = vi.fn();
  const { calls } = serve({
    ...quiet(),
    'GET /api/scraping/cover': coverStates(
      { status: 'idle' }, { status: 'running' }, { status: 'complete', result: '已取得 1600 × 1077 封面' }),
    'POST /api/scraping/cover': () => ({ body: { status: 'running' } }),
  });
  await prefetchScraping(new AbortController().signal);
  const host = await mount(page(toast));

  await type(host.querySelector<HTMLInputElement>('input[aria-label=馆藏番号]'), 'ABW-232');
  await click(buttonNamed('抓取封面', host));
  await settle();
  expect(calls[FIRST_SCREEN.length]).toMatchObject({ path: '/api/scraping/cover', method: 'POST', body: { code: 'ABW-232' } });
  expect(host.textContent).toContain('正在抓取封面');

  await tick(2000);
  expect(host.textContent).toContain('已取得 1600 × 1077 封面');
  expect(host.textContent).not.toContain('正在抓取封面');
  expect(toast).toHaveBeenCalledTimes(1);
  expect(toast).toHaveBeenCalledWith('已取得 1600 × 1077 封面');

  const asked = calls.length;
  await tick(60_000);
  expect(calls.length, '任务已经结束，不必接着敲后台').toBe(asked);
  expect(toast).toHaveBeenCalledTimes(1);
});

it('卸载之后不再敲后台：轮询跟着这棵根一起走', async () => {
  vi.useFakeTimers();
  const { calls } = serve({
    ...quiet(),
    'GET /api/scraping/cover': () => ({ body: { status: 'running' } }),
  });
  await prefetchScraping(new AbortController().signal);
  const mounted = await mountRoot(page(vi.fn()));

  await tick(2000);
  const asked = calls.length;
  expect(asked).toBeGreaterThan(FIRST_SCREEN.length);
  await mounted.unmount();
  await tick(60_000);
  expect(calls.length, '卸载之后还在轮询').toBe(asked);
});

it('amane 桥：检查上游只填「上游最新版本」那一行，重建跟到终态并发一次回执', async () => {
  vi.useFakeTimers();
  const toast = vi.fn();
  const bridgeStates = coverStates({ status: 'idle' }, { status: 'running' },
    { status: 'complete', result: 'amane 桥已按 79ecfa763cc7 重建' });
  const { calls } = serve({
    ...quiet(),
    'GET /api/scraping/amane-bridge': () => ({ body: { ...BRIDGE, job: bridgeStates().body } }),
    'POST /api/scraping/amane-bridge/check': () => ({ body: { ok: true, latest: 'v0.17.0' } }),
    'POST /api/scraping/amane-bridge/rebuild': () => ({ body: { status: 'running' } }),
  });
  await prefetchScraping(new AbortController().signal);
  const host = await mount(page(toast));
  const bridge = section(host, 'amane')!;
  expect(bridge.textContent).toContain('尚未检查');

  await click(buttonNamed('检查上游版本', bridge));
  await settle();
  expect(calls[FIRST_SCREEN.length]).toMatchObject({ path: '/api/scraping/amane-bridge/check', method: 'POST' });
  expect(bridge.textContent).toContain('v0.17.0');
  expect(bridge.textContent, '检查上游不改变已安装版本').toContain('已安装版本0.16.1');

  await click(buttonNamed('重新安装', bridge));
  await settle();
  expect(calls[FIRST_SCREEN.length + 1]).toMatchObject({ path: '/api/scraping/amane-bridge/rebuild', method: 'POST' });
  expect(bridge.textContent).toContain('正在重建运行环境');
  await tick(2000);
  expect(bridge.textContent).toContain('amane 桥已按 79ecfa763cc7 重建');
  expect(toast).toHaveBeenCalledTimes(1);
  expect(toast).toHaveBeenCalledWith('amane 桥已按 79ecfa763cc7 重建');
});

it('登录外链只写主机名；数据库里认不出的连接方式原样列成一项，下拉框不落到空占位', () => {
  expect(linkHost('https://www.javbus.com/login?next=%2F')).toBe('www.javbus.com');
  expect(linkHost('不是地址')).toBe('不是地址');
  expect(networkOptions('peach').map(([key]) => key)).toEqual(['peach', 'direct']);
  expect(networkOptions('socks-legacy')).toEqual([['peach', 'Peach 代理'], ['direct', '直接连接'], ['socks-legacy', 'socks-legacy']]);
});
