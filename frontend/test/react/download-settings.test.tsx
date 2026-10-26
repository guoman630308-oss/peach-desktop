import { act } from 'react';
import { expect, it, vi } from 'vitest';

import { DownloadSettings } from '../../src/react/settings/download-settings';
import type { DownloadCheckReport, DownloadSettingsState } from '../../src/react/bundle';
import { buttonNamed, click, fetchMock, mount, pending, section, sentBody, settle, submit, type } from './render';

vi.mock('../../src/react/settings/jav-browser-settings', () => ({ JavBrowserSettings: () => null }));

const state = (patch: Partial<DownloadSettingsState> = {}): DownloadSettingsState => ({
  available: true,
  config: { clouddrive_address: 'http://127.0.0.1:19798', targets: { '115': '/115/云下载' },
    pikpak_root: '', wait_hours: 168 },
  token_set: false,
  pikpak: { logged_in: false, username: '', remember: false, method: 'password' },
  pikpak_browser: { available: true, state: 'idle', message: '' },
  pikpak_roots: ['P:\\'],
  providers: [{ key: '115', label: '115' }, { key: 'pikpak', label: 'PikPak' }],
  max_wait_hours: 1440,
  ...patch,
});

const report = (patch: Partial<DownloadCheckReport> = {}): DownloadCheckReport => ({
  ok: true, address: 'http://127.0.0.1:19798', permissions: [], missing: [], root: '/', folder: null,
  quota: null, suggested_target: null, suggested_pikpak_root: '', problems: [], ...patch,
});

const unfilled = () => state({
  config: { clouddrive_address: 'http://127.0.0.1:19798', targets: {}, pikpak_root: '', wait_hours: 168 },
  pikpak_roots: ['A:\\'],
});

const field = (host: HTMLElement, label: string) =>
  [...host.querySelectorAll('label')].find((node) => node.textContent?.trim() === label)
    ?.closest('[data-input-size]')?.querySelector('input') ?? null;

it('保存时令牌随表单交给服务端，回来后清空，提示改成已保存', async () => {
  const fetcher = fetchMock(200, state({ token_set: true }));
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<DownloadSettings initial={state()} receipt={receipt} />);
  const token = field(host, 'CloudDrive2 API 令牌');
  expect(token?.type).toBe('password');
  await type(token, 'cd2-token');
  await submit(section(host, '云下载'));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/downloads');
  expect(sentBody(fetcher)).toMatchObject({
    clouddrive_address: 'http://127.0.0.1:19798', token: 'cd2-token', targets: { '115': '/115/云下载' },
  });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
  expect(field(host, 'CloudDrive2 API 令牌')?.value).toBe('');
  expect(field(host, 'CloudDrive2 API 令牌')?.placeholder).toBe('已保存，留空不改');
});

it('检查报出缺的权限、目标目录与剩余配额，保存与登录使用主按钮', async () => {
  vi.stubGlobal('fetch', fetchMock(200, {
    ok: true, address: 'http://127.0.0.1:19798', permissions: [{ name: 'a', label: '提交离线任务', granted: true },
      { name: 'b', label: '取消离线任务', granted: false }],
    missing: ['取消离线任务'], root: '/', folder: { path: '/115/云下载', can_offline: true, cloud: '115open' },
    quota: { total: 1500, used: 3, left: 1497 }, problems: [],
  }));
  const host = await mount(<DownloadSettings initial={state()} receipt={vi.fn()} />);
  for (const name of ['保存配置', '用浏览器登录', '登录']) {
    expect(buttonNamed(name, host)?.className, name).toContain('bg-button-primary');
  }
  expect(buttonNamed('检查连接', host)?.className).not.toContain('bg-button-primary');
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(host.textContent).toContain('缺少：取消离线任务');
  expect(host.textContent).toContain('/115/云下载 可以离线下载');
  expect(host.textContent).toContain('本月还剩 1497 条，共 1500 条');
  expect(host.textContent).not.toContain('探测到');
});

it('地址留空时检查把探测到的本机地址填回表单，保存配置才带上它', async () => {
  const blank = state({ config: { clouddrive_address: '', targets: { '115': '/115/云下载' },
    pikpak_root: '', wait_hours: 168 } });
  const fetcher = fetchMock(200, {
    ok: true, address: 'http://127.0.0.1:29798', permissions: [], missing: [], root: '/',
    folder: null, quota: null, problems: [],
  });
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={blank} receipt={vi.fn()} />);
  expect(field(host, 'CloudDrive2 地址')?.placeholder).toBe('留空自动查找');
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(sentBody(fetcher)).toMatchObject({ clouddrive_address: '' });
  expect(field(host, 'CloudDrive2 地址')?.value).toBe('http://127.0.0.1:29798');
  expect(host.textContent).toContain('探测到 http://127.0.0.1:29798，保存配置后生效');
  expect(fetcher).toHaveBeenCalledTimes(1);
  fetcher.mockImplementation(async () => ({ ok: true, status: 200, json: async () => blank }));
  await submit(section(host, '云下载'));
  await settle();
  expect(fetcher.mock.calls[1]?.[0]).toBe('/api/configuration/downloads');
  expect(sentBody(fetcher, 1)).toMatchObject({ clouddrive_address: 'http://127.0.0.1:29798' });
});

it('目标目录与 PikPak 根留空时检查按推送发现填回，保存配置才带上它们', async () => {
  const fetcher = fetchMock(200, report({
    suggested_target: { path: '/115open/云下载', exists: true }, suggested_pikpak_root: 'A:\\',
    folder: { path: '/115open/云下载', can_offline: true, cloud: '115open' },
  }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={unfilled()} receipt={vi.fn()} />);
  expect(field(host, '115 目标目录')?.placeholder).toBe('留空时按推送发现自动填写');
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(sentBody(fetcher)).toMatchObject({ target: '', pikpak_root: '', pikpak_account: false });
  expect(field(host, '115 目标目录')?.value).toBe('/115open/云下载');
  expect(host.textContent).toContain('按推送发现填入 /115open/云下载，保存配置后生效');
  expect(host.textContent).toContain('按推送发现填入 A:\\，保存配置后生效');
  expect(fetcher).toHaveBeenCalledTimes(1);
  fetcher.mockImplementation(async () => ({ ok: true, status: 200, json: async () => unfilled() }));
  await submit(section(host, '云下载'));
  await settle();
  expect(fetcher.mock.calls[1]?.[0]).toBe('/api/configuration/downloads');
  expect(sentBody(fetcher, 1)).toMatchObject({ targets: { '115': '/115open/云下载' }, pikpak_root: 'A:\\' });
});

it('用户填过的目标目录不被建议值覆盖', async () => {
  vi.stubGlobal('fetch', fetchMock(200, report({ suggested_target: { path: '/115open/云下载', exists: true } })));
  const host = await mount(<DownloadSettings initial={state()} receipt={vi.fn()} />);
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(field(host, '115 目标目录')?.value).toBe('/115/云下载');
  expect(host.textContent).not.toContain('按推送发现填入');
});

it('在账号框里填了 PikPak 账号或已登录时，检查才问 PikPak 根', async () => {
  const fetcher = fetchMock(200, report());
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={unfilled()} receipt={vi.fn()} />);
  await type(field(host, '账号'), 'me@example.com');
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(sentBody(fetcher)).toMatchObject({ pikpak_account: true });
  const signedIn = await mount(<DownloadSettings receipt={vi.fn()}
    initial={state({ pikpak: { logged_in: true, username: 'me@example.com', remember: false, method: 'password' } })} />);
  await click(buttonNamed('检查连接', signedIn));
  await settle();
  expect(sentBody(fetcher, 1)).toMatchObject({ pikpak_account: true });
});

it('推出的目录不存在时标成无效，点了「新建这个目录」才去建，建好后解除', async () => {
  const fetcher = fetchMock(200, report({ suggested_target: { path: '/115open/云下载', exists: false } }));
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<DownloadSettings initial={unfilled()} receipt={receipt} />);
  await click(buttonNamed('检查连接', host));
  await settle();
  const input = field(host, '115 目标目录');
  expect(input?.value).toBe('/115open/云下载');
  expect(input?.getAttribute('aria-invalid')).toBe('true');
  expect(host.textContent).toContain('这个目录不存在');
  expect(host.textContent).toContain('/115open/云下载 不存在');
  await submit(section(host, '云下载'));
  await settle();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('先新建这个目录，或填写已有目录');

  const hold = pending<unknown>();
  fetcher.mockImplementation(() => hold.answer);
  await click(buttonNamed('新建这个目录', host));
  expect(fetcher.mock.calls[1]?.[0]).toBe('/api/configuration/downloads/folder');
  expect(sentBody(fetcher, 1)).toMatchObject({ path: '/115open/云下载', clouddrive_address: 'http://127.0.0.1:19798' });
  expect(buttonNamed('新建这个目录', host)?.getAttribute('aria-busy')).toBe('true');
  expect(receipt).not.toHaveBeenCalled();
  await hold.release({ ok: true, status: 200, json: async () => report({
    folder: { path: '/115open/云下载', can_offline: true, cloud: '115open' } }) });
  await settle();
  expect(receipt).toHaveBeenCalledWith('已新建目录');
  expect(buttonNamed('新建这个目录', host)).toBeNull();
  expect(field(host, '115 目标目录')?.getAttribute('aria-invalid')).not.toBe('true');
  expect(host.textContent).toContain('/115open/云下载 可以离线下载');
  expect(host.textContent).toContain('按推送发现填入 /115open/云下载，保存配置后生效');
});

it('地址和令牌都已保存时一打开就按已保存的配置检查，检查键不转', async () => {
  const hold = pending<unknown>();
  const fetcher = vi.fn<(...call: [string, RequestInit | undefined]) => Promise<unknown>>(() => hold.answer);
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={state({ token_set: true })} receipt={vi.fn()} />);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/downloads/check');
  expect(fetcher.mock.calls[0]?.[1]?.method).toBeUndefined();
  expect(fetcher.mock.calls[0]?.[1]?.body).toBeUndefined();
  expect(host.textContent).toContain('正在检查 CloudDrive2');
  expect(buttonNamed('检查连接', host)?.getAttribute('aria-busy')).not.toBe('true');
  await hold.release({ ok: true, status: 200, json: async () => report({
    permissions: [{ name: 'a', label: '提交离线任务', granted: true }],
    folder: { path: '/115/云下载', can_offline: true, cloud: '115open' },
    quota: { total: 1500, used: 3, left: 1497 } }) });
  await settle();
  expect(host.textContent).not.toContain('正在检查 CloudDrive2');
  expect(host.textContent).toContain('/115/云下载 可以离线下载');
  expect(host.textContent).toContain('本月还剩 1497 条，共 1500 条');
});

it('没有保存令牌或地址时打开不检查', async () => {
  const fetcher = fetchMock(200, report());
  vi.stubGlobal('fetch', fetcher);
  await mount(<DownloadSettings initial={state()} receipt={vi.fn()} />);
  await mount(<DownloadSettings receipt={vi.fn()} initial={state({ token_set: true,
    config: { clouddrive_address: '', targets: {}, pikpak_root: '', wait_hours: 168 } })} />);
  await settle();
  expect(fetcher).not.toHaveBeenCalled();
});

it('目标目录留空保存后直接渲染响应里的检查报告，服务端填上的目录不再说保存后生效', async () => {
  const saved = state({ token_set: true,
    config: { clouddrive_address: 'http://127.0.0.1:19798', targets: { '115': '/115open/云下载' },
      pikpak_root: '', wait_hours: 168 } });
  const fetcher = fetchMock(200, { ...saved, report: report({
    suggested_target: { path: '/115open/云下载', exists: true },
    folder: { path: '/115open/云下载', can_offline: true, cloud: '115open' },
    quota: { total: 1500, used: 3, left: 1497 } }) });
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<DownloadSettings initial={unfilled()} receipt={receipt} />);
  await type(field(host, 'CloudDrive2 API 令牌'), 'cd2-token');
  await submit(section(host, '云下载'));
  await settle();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/downloads');
  expect(sentBody(fetcher)).toMatchObject({ targets: {}, token: 'cd2-token', pikpak_account: false });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
  expect(field(host, '115 目标目录')?.value).toBe('/115open/云下载');
  expect(field(host, '115 目标目录')?.getAttribute('aria-invalid')).not.toBe('true');
  expect(host.textContent).toContain('已按推送发现填入 /115open/云下载');
  expect(host.textContent).not.toContain('/115open/云下载，保存配置后生效');
  expect(host.textContent).toContain('/115open/云下载 可以离线下载');
  expect(host.textContent).toContain('本月还剩 1497 条，共 1500 条');
});

it('保存时推出的目录不存在，照常保存其他字段，目录填回表单标成无效并给出新建键', async () => {
  const fetcher = fetchMock(200, { ...unfilled(), token_set: true,
    report: report({ suggested_target: { path: '/115open/云下载', exists: false } }) });
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={unfilled()} receipt={vi.fn()} />);
  await type(field(host, '账号'), 'me@example.com');
  await submit(section(host, '云下载'));
  await settle();
  expect(sentBody(fetcher)).toMatchObject({ pikpak_account: true });
  const input = field(host, '115 目标目录');
  expect(input?.value).toBe('/115open/云下载');
  expect(input?.getAttribute('aria-invalid')).toBe('true');
  expect(host.textContent).toContain('/115open/云下载 不存在');
  expect(buttonNamed('新建这个目录', host)).not.toBeNull();
  await submit(section(host, '云下载'));
  await settle();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('先新建这个目录，或填写已有目录');
});

it('保存的响应不带检查报告时收起上一次的报告', async () => {
  const fetcher = fetchMock(200, report({ folder: { path: '/115/云下载', can_offline: true, cloud: '115open' } }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={state()} receipt={vi.fn()} />);
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(host.textContent).toContain('/115/云下载 可以离线下载');
  fetcher.mockImplementation(async () => ({ ok: true, status: 200, json: async () => ({ ...state(), report: null }) }));
  await submit(section(host, '云下载'));
  await settle();
  expect(host.textContent).not.toContain('可以离线下载');
});

it('两个本机端口都不应答时原样报出服务端列的地址，表单仍留空', async () => {
  const blank = state({ config: { clouddrive_address: '', targets: {}, pikpak_root: '', wait_hours: 168 } });
  const problem = '没有填 CloudDrive2 地址，本机 http://127.0.0.1:19798、http://127.0.0.1:29798 都没有应答。';
  vi.stubGlobal('fetch', fetchMock(200, {
    ok: false, address: '', permissions: [], missing: [], root: '', folder: null, quota: null,
    problems: [problem],
  }));
  const host = await mount(<DownloadSettings initial={blank} receipt={vi.fn()} />);
  await click(buttonNamed('检查连接', host));
  await settle();
  expect(host.textContent).toContain(problem);
  expect(field(host, 'CloudDrive2 地址')?.value).toBe('');
});

it('PikPak 要人机验证时给出验证页，完成后再登录', async () => {
  vi.stubGlobal('fetch', fetchMock(200, {
    ok: false, captcha_url: 'https://user.mypikpak.com/captcha?x=1', settings: state(),
  }));
  const host = await mount(<DownloadSettings initial={state()} receipt={vi.fn()} />);
  await type(field(host, '账号'), 'me@example.com');
  await type(field(host, '密码'), 'pw');
  await submit(section(host, 'PikPak 账号'));
  await settle();
  const link = host.querySelector<HTMLAnchorElement>('a[href^="https://user.mypikpak.com/captcha"]');
  expect(link?.target).toBe('_blank');
  expect(host.textContent).toContain('回到这里再点一次「登录」');
});

it('登录之后只显示账号与密码保存与否，可以登出', async () => {
  const fetcher = fetchMock(200, state());
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<DownloadSettings receipt={receipt}
    initial={state({ pikpak: { logged_in: true, username: 'me@example.com', remember: false, method: 'password' } })} />);
  expect(host.textContent).toContain('me@example.com');
  expect(field(host, '密码')).toBeNull();
  await click(buttonNamed('登出', host));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/downloads/pikpak/logout');
  expect(receipt).toHaveBeenCalledWith('已登出 PikPak');
  expect(buttonNamed('登录', host)).not.toBeNull();
});

const BROWSER_URL = '/api/configuration/downloads/pikpak/browser-login';
const browserAt = (stage: DownloadSettingsState['pikpak_browser']['state'], message = '') =>
  state({ pikpak_browser: { available: true, state: stage, message } });
const browserDone = () => state({
  pikpak: { logged_in: true, username: '', remember: false, method: 'browser' },
  pikpak_browser: { available: true, state: 'done', message: '已用浏览器登录 PikPak' },
});
/** 依次回这几份设置块，回完以后一直回最后一份。 */
const replies = (...bodies: unknown[]) => {
  const fetcher = fetchMock(200, bodies.at(-1));
  for (const body of bodies) {
    fetcher.mockImplementationOnce(async () => ({ ok: true, status: 200, json: async () => body }));
  }
  return fetcher;
};
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) });

it('账号密码表单直接显示，两种登录方式共用底栏', async () => {
  const host = await mount(<DownloadSettings initial={state()} receipt={vi.fn()} />);
  expect(host.querySelector('details')).toBeNull();
  expect(field(host, '账号')).not.toBeNull();
  expect(buttonNamed('登录', host)?.parentElement).toBe(buttonNamed('用浏览器登录', host)?.parentElement);
  expect(buttonNamed('用浏览器登录', host)?.type).toBe('button');
});

it('这台电脑没有浏览器时只剩账号密码登录，直接摊开', async () => {
  const host = await mount(<DownloadSettings receipt={vi.fn()}
    initial={state({ pikpak_browser: { available: false, state: 'idle', message: '' } })} />);
  expect(buttonNamed('用浏览器登录', host)).toBeNull();
  expect(host.querySelector('details')).toBeNull();
  expect(field(host, '账号')).not.toBeNull();
  expect(buttonNamed('登录', host)?.className).toContain('bg-button-primary');
});

it('用浏览器登录拉起窗口后轮询，登好就显示登录方式并停下', async () => {
  vi.useFakeTimers();
  const fetcher = replies(browserAt('waiting', '浏览器窗口已打开，在里面登录 PikPak'),
    browserAt('waiting', '浏览器窗口已打开，在里面登录 PikPak'), browserDone());
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<DownloadSettings initial={state()} receipt={receipt} />);
  await click(buttonNamed('用浏览器登录', host));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe(BROWSER_URL);
  expect(fetcher.mock.calls[0]?.[1]?.method).toBe('POST');
  expect(host.querySelector('[role="status"]')?.textContent).toContain('浏览器窗口已打开，在里面登录 PikPak');
  expect(buttonNamed('用浏览器登录', host)).toBeNull();
  expect(buttonNamed('登录', host)?.disabled).toBe(true);
  await tick(2000);
  await settle();
  expect(fetcher.mock.calls[1]?.[0]).toBe(BROWSER_URL);
  expect(fetcher.mock.calls[1]?.[1]?.method).toBeUndefined();
  expect(receipt).not.toHaveBeenCalled();
  await tick(2000);
  await settle();
  expect(receipt).toHaveBeenCalledWith('已登录 PikPak');
  expect(host.textContent).toContain('浏览器登录，由 Peach 续期');
  expect(host.textContent).not.toContain('登录过期后需要回到这里重新登录');
  expect(buttonNamed('登出', host)).not.toBeNull();
  await tick(6000);
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('刷新页面时登录窗口还开着就接着轮询，点取消回到可以重新拉起', async () => {
  vi.useFakeTimers();
  const fetcher = replies(browserAt('cancelled', '已取消浏览器登录'));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<DownloadSettings initial={browserAt('waiting')} receipt={vi.fn()} />);
  expect(host.querySelector('[role="status"]')?.textContent).toContain('等你在浏览器窗口里登录 PikPak');
  await click(buttonNamed('取消', host));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe(`${BROWSER_URL}/cancel`);
  expect(host.textContent).toContain('已取消浏览器登录');
  expect(buttonNamed('用浏览器登录', host)).not.toBeNull();
  await tick(6000);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('等太久没登录时报出原因，上一次留在服务端的结果不当成新消息', async () => {
  vi.useFakeTimers();
  const message = '10 分钟内没有完成登录，窗口已关闭。可以再点一次「用浏览器登录」';
  vi.stubGlobal('fetch', replies(browserAt('timeout', message)));
  const stale = await mount(<DownloadSettings initial={browserAt('timeout', message)} receipt={vi.fn()} />);
  expect(stale.textContent).not.toContain(message);
  const host = await mount(<DownloadSettings initial={browserAt('waiting')} receipt={vi.fn()} />);
  await tick(2000);
  await settle();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(message);
});

it('只读端整块不可操作，并说明原因', async () => {
  const host = await mount(<DownloadSettings initial={state({ available: false })} receipt={vi.fn()} />);
  expect(buttonNamed('保存配置', host)?.disabled).toBe(true);
  expect(host.textContent).toContain('云下载只在允许修改数据库的设备上可用');
});
