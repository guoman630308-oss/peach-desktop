import { expect, it, vi } from 'vitest';

import { EntryLinksForm } from '../../src/react/settings/entry-links-settings';
import type { EntryLinksState } from '../../src/react/bundle';
import { fetchMock, mount, sentBody, settle, submit, switches, type } from './render';

const state: EntryLinksState = {
  sites: [
    { key: 'javdb', label: 'JavDB', host: '', default_host: 'javdb.com' },
    { key: 'missav', label: 'MISSAV', host: 'missav.ai', default_host: 'missav.ws' },
  ],
};

it('外部入口：能换镜像的两站各一个地址框，默认留空，保存发出框里的值', async () => {
  const fetcher = fetchMock(200, state);
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<EntryLinksForm initial={state} receipt={receipt} />);
  // 入口出不出现由数据库里有没有站点 id 决定，三枚按钮自己就在资料页上，这里没有开关。
  expect(switches(host)).toEqual([]);
  // みんなのAV 只此一家，没有域名可换，它不出现在这一页上。
  expect(host.querySelector('#entry-link-minnano-av')).toBeNull();
  const javdb = host.querySelector<HTMLInputElement>('#entry-link-javdb');
  // 默认值不要人先抄一遍：框是空的，当前在用的那个域名写在占位符里。
  expect(javdb?.value).toBe('');
  expect(javdb?.placeholder).toBe('javdb.com');
  expect(host.textContent).toContain('JavDB 地址');
  expect(host.textContent).not.toContain('地址模板');
  await type(javdb, 'javdb521.com');
  expect(fetcher).not.toHaveBeenCalled();
  await submit(host.querySelector('form'));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/entry-links');
  expect(sentBody(fetcher)).toEqual({
    sites: { javdb: { host: 'javdb521.com' }, missav: { host: 'missav.ai' } },
  });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
});

it('外部入口：说明就是卡里的头一块，它上面不画线', async () => {
  const host = await mount(<EntryLinksForm initial={state} receipt={vi.fn()} />);
  const help = [...host.querySelectorAll('p')]
    .find((node) => node.textContent?.startsWith('JavDB 或 MISSAV 打不开时'));
  const block = help?.parentElement;
  // 那道线是给排在设置行后面的块用的，分隔的是它和上面那组行。这张卡里它自己就是头一块，
  // 线画出来上面什么也没有，读成分区标题自带一条下边线。
  expect(block?.previousElementSibling).toBeNull();
  expect(block?.className).not.toContain('border-t');
});

it('服务端没有站点时不补地址字段', async () => {
  // 站点清单由服务端给。前端自己兜一份默认的话，那份迟早和 `peach.entry_links` 分叉。
  const host = await mount(<EntryLinksForm initial={{ sites: [] }} receipt={vi.fn()} />);
  expect(host.querySelectorAll('input')).toHaveLength(0);
});
