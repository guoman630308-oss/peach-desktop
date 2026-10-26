import { act } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { rememberLocalPlayerPolicy, resetLocalPlayerPolicy } from '../../src/local-player';
import { LocalPlayerControls, mountLocalPlayer } from '../../src/react/stage/local-player';
import { buttonNamed, click, mount, settle } from './render';

afterEach(resetLocalPlayerPolicy);
const options = { kind: 'item' as const, item: { id: 7 }, media: null };
const preference = () => rememberLocalPlayerPolicy({ available: true, mode: 'external', executable: 'player.exe', label: 'PotPlayer' });

it('默认只交给外部播放器，内置播放由按钮明确启动', async () => {
  preference();
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({ opened: true, player: 'PotPlayer' }) }));
  vi.stubGlobal('fetch', fetcher);
  const internal = vi.fn(), external = vi.fn();
  const host = await mount(<LocalPlayerControls options={options} onInternal={internal} onExternal={external} isReleased={() => false} />);
  await settle();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/local-player/play');
  expect(internal).not.toHaveBeenCalled();
  expect(host.querySelector('video')).toBeNull();
  await click(buttonNamed('内置播放', host));
  expect(internal).toHaveBeenCalledTimes(1);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('外部启动失败保留重试和内置入口，取消的旧条目不唤起播放器', async () => {
  preference();
  const fetcher = vi.fn(async () => ({ ok: false, status: 400, json: async () => ({ detail: '请选择播放器' }) }));
  vi.stubGlobal('fetch', fetcher);
  const internal = vi.fn();
  const host = await mount(<LocalPlayerControls options={options} onInternal={internal} onExternal={() => {}} isReleased={() => false} />);
  await settle();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('请选择播放器');
  expect(internal).not.toHaveBeenCalled();
  await click(buttonNamed('内置播放', host));
  expect(internal).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[role="alert"]')).toBeNull();
  fetcher.mockClear();
  await mount(<LocalPlayerControls options={options} onInternal={internal} onExternal={() => {}} isReleased={() => true} />);
  expect(fetcher).not.toHaveBeenCalled();
});

it('切回外部和卸载会停止手动内置播放器', async () => {
  preference();
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ opened: true, player: 'PotPlayer' }) })));
  const frame = document.body.appendChild(document.createElement('div'));
  const detach = vi.fn(), internal = vi.fn(() => detach);
  let release!: () => void;
  await act(async () => { release = mountLocalPlayer(frame, options, internal) });
  await settle();
  expect(internal).not.toHaveBeenCalled();
  await click(buttonNamed('内置播放', frame));
  expect(internal).toHaveBeenCalledTimes(1);
  await click(buttonNamed('PotPlayer 播放', frame));
  expect(detach).toHaveBeenCalledTimes(1);
  await act(async () => { release(); release() });
  expect(frame.children).toHaveLength(0);
});
