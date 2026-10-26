import { expect, it, vi } from 'vitest';

import { PushDiscoveryForm } from '../../src/react/settings/push-discovery-settings';
import type { PushDiscoveryState } from '../../src/react/bundle';
import { buttonNamed, click, fetchMock, mount, settle, switches } from './render';

const TOML = '[global_params]\nbase_url = "https://192.0.2.10"\n';

const state = (patch: Partial<PushDiscoveryState> = {}): PushDiscoveryState => ({
  enabled: true, watch_local: true, cloud: true, prefixes: [{ prefix: '/115', root: 'B:\\' }],
  available: true, secret: 'tok', secret_set: true, endpoint: '/api/inbox/clouddrive',
  local_running: true, local_message: '', local_roots: ['R:\\media'],
  queue: { pending: 0, submitted: 3, ingested: 3, missing: 0, dropped: 0, timed_out: 0, failed: 0 },
  media_roots: ['B:\\', 'R:\\media'], origin: 'https://192.0.2.10', config_toml: TOML, ...patch,
});

const clipboard = () => {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return writeText;
};

it('配置块给的是服务端拼好的那一段，复制键把它整个写进剪贴板', async () => {
  // 地址和密钥不再单独列成读数：抄两处就会抄漏一处，而 CloudDrive2 要的本来就是整段。
  const writeText = clipboard();
  const receipt = vi.fn();
  const host = await mount(<PushDiscoveryForm initial={state()} receipt={receipt} />);
  // 卡片自己就是 `secondary`，代码块用同一档等于没有块——底色要比它亮一级才看得出来。
  expect(host.querySelector('[data-code-block]')?.className).toContain('bg-background-tertiary-default');
  expect(host.querySelector('pre')?.textContent).toBe(TOML);
  expect(host.textContent).not.toContain('共享密钥');
  expect(host.textContent).not.toContain('通知地址');
  await click(buttonNamed('复制配置', host));
  await settle();
  expect(writeText).toHaveBeenCalledWith(TOML);
  expect(receipt).toHaveBeenCalledWith('已复制 CloudDrive2 配置');
});

it('配置块默认收起，横向滚动用全站的覆盖式滑块', async () => {
  /* 抄它的人不用读它：三十行里只有地址和密钥两处跟这台机器有关，两处都已经填好了。
     纵向不设上限是因为这一页本来就在设置面板的滚动区里，再套一层的话滚轮落在哪一层
     要看指针停在哪儿。横向那条不挂覆盖式的话，Windows 上露的是占一列宽的系统滚动条，
     正好盖住右侧圆角。 */
  const host = await mount(<PushDiscoveryForm initial={state()} receipt={vi.fn()} />);
  const pre = host.querySelector('pre')!;
  const fold = pre.closest('details');
  expect(fold?.open).toBe(false);
  expect(fold?.querySelector('summary')?.textContent).toContain('查看配置');
  expect(pre.className).not.toContain('max-h');
  expect(pre.dataset.overlayScrollbar).toBe('true');
  expect(pre.parentElement?.querySelector('.ovtrack')).not.toBeNull();
});

it('这台机器没有对外的 HTTPS 地址时说出原因，不发一段填了也不通的配置', async () => {
  const host = await mount(
    <PushDiscoveryForm initial={state({ origin: '', config_toml: '' })} receipt={vi.fn()} />);
  expect(host.querySelector('pre')).toBeNull();
  expect(buttonNamed('复制配置', host)).toBeNull();
  expect(host.textContent).toContain('要先有 CloudDrive2 能访问的 HTTPS 地址');
});

it('配置块跟着已保存的开关走，拨一下不算数', async () => {
  /* 拨开关就跟着收起的话，写的还是上一次保存的地址与密钥——那段配置此刻并不适用于
     屏幕上这个状态，而它看上去仍然可以直接抄走。 */
  const host = await mount(<PushDiscoveryForm initial={state()} receipt={vi.fn()} />);
  await click(switches(host)[2]);
  expect(host.querySelector('pre')?.textContent).toBe(TOML);
});

it('换过密钥之后配置块换成新的那一段', async () => {
  const next = state({ secret: 'new', config_toml: '[global_params]\nbase_url = "https://h"\n' });
  vi.stubGlobal('fetch', fetchMock(200, next));
  const host = await mount(<PushDiscoveryForm initial={state()} receipt={vi.fn()} />);
  await click(buttonNamed('更换密钥', host));
  await settle();
  expect(host.querySelector('pre')?.textContent).toBe(next.config_toml);
});
