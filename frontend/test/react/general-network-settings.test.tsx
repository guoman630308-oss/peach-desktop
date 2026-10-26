import { expect, it, vi } from 'vitest';

import { StartupSettings } from '../../src/react/settings/general-settings';
import { PeachProxy } from '../../src/react/settings/network-settings';
import { buttonNamed, choose, click, fetchMock, mount, sentBody, settle, submit, switches, type } from './render';

const startup = { available: true, enabled: false, silent: true, message: '', desktop: false, desktop_message: '' };

it('开机自启：静默启动跟着开机自启解锁，保存发出三颗开关的实际值', async () => {
  const fetcher = fetchMock(200, {});
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<StartupSettings startup={startup} receipt={receipt} />);
  expect(switches(host).map((input) => input.getAttribute('aria-label')))
    .toEqual(['开机后启动 Peach', '只启动托盘，不打开网页', '在桌面创建快捷方式']);
  expect(switches(host).map((input) => input.disabled)).toEqual([false, true, false]);
  expect(buttonNamed('保存配置', host)).not.toBeNull();
  await click(switches(host)[0]);
  expect(switches(host)[1]!.disabled).toBe(false);
  expect(fetcher).not.toHaveBeenCalled();
  await submit(host.querySelector('form'));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/startup');
  expect(sentBody(fetcher)).toEqual({ enabled: true, silent: true, desktop: false });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
});

it('桌面快捷方式被别的安装占着时只锁那一颗，并说出原因', async () => {
  // 桌面上摆着另一份 Peach 的图标不该连开机自启都不让改，那一格自己还是好的。
  const host = await mount(<StartupSettings
    startup={{ ...startup, enabled: true, desktop_message: '桌面快捷方式属于另一份 Peach 安装' }} receipt={vi.fn()} />);
  expect(switches(host).map((input) => input.disabled)).toEqual([false, false, true]);
  expect(buttonNamed('保存配置', host)?.disabled).toBe(false);
  expect(host.textContent).toContain('桌面快捷方式属于另一份 Peach 安装');
});

it('Peach 代理：选自定义才出地址栏，保存后清空地址并记住已保存', async () => {
  const fetcher = fetchMock(200, { mode: 'proxy', proxy_saved: true, needs_selection: false });
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<PeachProxy initial={{ mode: 'direct', proxy_saved: false, needs_selection: true }} receipt={receipt} />);
  expect(host.querySelector('[role="note"]')?.textContent).toContain('请选择公共连接方式');
  expect(host.querySelector('#peachProxyAddress')).toBeNull();
  await choose(host.querySelector('[aria-haspopup="listbox"]'), '自定义');
  const address = host.querySelector<HTMLInputElement>('#peachProxyAddress');
  expect(address?.placeholder).toBe('http://127.0.0.1:7890');
  await type(address, 'http://127.0.0.1:7890');
  await submit(host.querySelector('form'));
  await settle();
  expect(sentBody(fetcher)).toEqual({ mode: 'proxy', proxy: 'http://127.0.0.1:7890' });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
  const saved = host.querySelector<HTMLInputElement>('#peachProxyAddress');
  expect(saved?.value).toBe('');
  expect(saved?.placeholder).toBe('已保存，留空保留');
  expect(host.querySelector('[role="note"]')).toBeNull();
});
