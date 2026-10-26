/* 印着本机路径的那一行：递出去的是哪一串，弹窗不在这台机器上时怎么回执，开不了怎么说。 */
import { expect, it, vi } from 'vitest';

import { PathLine } from '../../src/react/components/path-line';

import { click, fetchMock, mount, sentBody } from './render';

const LOG = 'C:\\peach-data\\state\\library-processing-one.issues.jsonl';

it('定位请求只递这条路径，成功回执一句', async () => {
  const fetcher = fetchMock(200, { ok: true, path: LOG });
  vi.stubGlobal('fetch', fetcher);
  const revealed = vi.fn();
  const host = await mount(<PathLine path={LOG} prefix="完整记录：" onRevealed={revealed} />);

  expect(host.textContent).toContain(`完整记录：${LOG}`);
  await click(host.querySelector('button[aria-label="在资源管理器中显示"]'));

  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/reveal');
  expect(sentBody(fetcher)).toEqual({ path: LOG });
  /* 资源管理器弹在跑 Peach 那台机器上：从别的设备看这一页时，屏幕上不会有任何动静。 */
  expect(revealed).toHaveBeenCalledWith('已在资源管理器中显示');
});

it('开不了时原因留在路径旁边，不冒充成功的回执', async () => {
  vi.stubGlobal('fetch', fetchMock(410, { error: 'file missing', message: '这个位置已经不在了' }));
  const revealed = vi.fn();
  const host = await mount(<PathLine path={LOG} onRevealed={revealed} />);

  await click(host.querySelector('button[aria-label="在资源管理器中显示"]'));

  expect(host.textContent).toContain('这个位置已经不在了');
  expect(revealed).not.toHaveBeenCalled();
});

/* 403 在全站错误映射里有一句通用的「当前设备没有权限」，越界的路径不是那回事；
   服务端自带的那句中文要能盖过它，否则人会去换一台电脑重试。 */
it('服务端不认这条路径时照直说，不落进通用的权限说法', async () => {
  vi.stubGlobal('fetch', fetchMock(403, {
    error: 'path not in the data root', message: '这个位置不归 Peach 管，只能自己打开',
  }));
  const host = await mount(<PathLine path={'C:\\Windows'} />);

  await click(host.querySelector('button[aria-label="在资源管理器中显示"]'));

  expect(host.textContent).toContain('这个位置不归 Peach 管，只能自己打开');
});

it('路径和那颗键排在同一条中线上', async () => {
  // 键比这行小字高一截，顶对齐时它自己看着往上飘。
  const host = await mount(<PathLine path={LOG} prefix="完整记录：" />);

  expect(host.querySelector('p')!.parentElement!.className).toContain('items-center');
});
