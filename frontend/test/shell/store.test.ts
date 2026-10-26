/* 壳的内存状态单例（`src/shell/`）：活绑定、整体换引用与原地改字段两种写法、`barsContext.filters` 与
 * `state` 的别名、两个常驻的选择集，以及订阅与版本号。每个用例拿一份新加载的模块，初值互不干扰。 */
import { beforeEach, expect, it, vi } from 'vitest';

type Shell = typeof import('../../src/shell');
let shell: Shell;
beforeEach(async () => {
  vi.resetModules();
  shell = await import('../../src/shell');
});

const home = (filters: Record<string, string>) => ({ type: 'home' as const, filters });

it('初值与壳启动之前一致：口径还没写入，来处指向首页与关注页', () => {
  expect(shell.state).toBeUndefined();
  expect(shell.barsContext).toBeUndefined();
  expect(shell.detailReturnBarsContext).toBeNull();
  expect([shell.selectMode, shell.lastSelectedId, shell.followLastSelectedId, shell.selectSurface]).toEqual([false, null, null, '']);
  expect([shell.detailReturnPath, shell.followDetailReturnPath]).toEqual(['/', '/follow']);
  expect([shell.detailOriginAnchor, shell.detailOriginAbove, shell.detailReturnNeedsRestore]).toEqual([null, false, false]);
  expect([shell.activeQueue, shell.pendingQueueRoute, shell.presentedItem]).toEqual([null, null, null]);
  expect([shell.configurationRequestedSection, shell.runtimeConfigurable, shell.cameFromSetup]).toEqual(['', null, false]);
  expect(shell.selected.size + shell.followSelected.size).toBe(0);
  expect(shell.shellVersion()).toBe(0);
});

it('writeShell 只换补丁里出现的项，值是 undefined 也照写，每次调用通知一次', () => {
  const heard: number[] = [];
  shell.subscribeShell(() => heard.push(shell.shellVersion()));
  shell.writeShell({ detailReturnPath: '/stats', selectMode: true, selectSurface: 'catalog' });
  expect([shell.detailReturnPath, shell.selectMode, shell.selectSurface]).toEqual(['/stats', true, 'catalog']);
  expect(shell.followDetailReturnPath).toBe('/follow');
  shell.writeShell({ state: { loc: 'local' } });
  shell.writeShell({ state: undefined });
  expect(shell.state).toBeUndefined();
  expect(heard).toEqual([1, 2, 3]);
});

it('整体重建 state 不碰 barsContext：语境仍指旧对象，直到写点把它指回新的那一份', () => {
  const first = { loc: 'local', tag: '' };
  shell.writeShell({ state: first });
  shell.writeShell({ barsContext: home(shell.state!) });
  expect(shell.barsContext!.filters).toBe(shell.state);

  shell.writeShell({ state: { ...shell.state!, tag: '剧情' } });
  expect(shell.state).not.toBe(first);
  expect(shell.barsContext!.filters).toBe(first);
  expect(first.tag).toBe('');

  shell.writeShell({ barsContext: home(shell.state!) });
  expect(shell.barsContext!.filters).toBe(shell.state);
});

it('原地改字段时语境与 state 是同一个对象，两边同时看见；notifyShell 让版本号加一', () => {
  shell.writeShell({ state: { loc: 'local', sort: 'seed' } });
  shell.writeShell({ barsContext: home(shell.state!) });
  const before = shell.shellVersion();
  shell.state!.sort = 'new';
  shell.notifyShell();
  expect(shell.barsContext!.filters.sort).toBe('new');
  expect(shell.shellVersion()).toBe(before + 1);
});

it('打开详情时记下的首页语境关掉后原样还原，filters 仍是那份 state', () => {
  shell.writeShell({ state: { loc: 'local' } });
  shell.writeShell({ barsContext: home(shell.state!) });
  const opened = shell.barsContext!;
  shell.writeShell({ detailReturnBarsContext: opened });
  shell.writeShell({ barsContext: { type: 'item', id: 7, filters: { tag: '' } } });
  expect(shell.barsContext!.filters).not.toBe(shell.state);

  shell.writeShell({ barsContext: shell.detailReturnBarsContext ?? home(shell.state!) });
  shell.writeShell({ detailReturnBarsContext: null });
  expect(shell.barsContext).toBe(opened);
  expect(shell.barsContext!.filters).toBe(shell.state);
});

it('两个选择集是常驻实例：增删、清空都在同一个 Set 上', () => {
  const { selected, followSelected } = shell;
  selected.add(1);
  selected.add(2);
  followSelected.add(9);
  shell.writeShell({ lastSelectedId: 2 });
  selected.clear();
  followSelected.clear();
  shell.writeShell({ lastSelectedId: null, followLastSelectedId: null });
  expect(shell.selected).toBe(selected);
  expect(shell.followSelected).toBe(followSelected);
  expect(selected.size + followSelected.size).toBe(0);
  expect(shell.lastSelectedId).toBeNull();
});

it('退订之后不再收到通知；版本号单调递增', () => {
  const listener = vi.fn();
  const stop = shell.subscribeShell(listener);
  shell.notifyShell();
  stop();
  shell.writeShell({ configurationRequestedSection: 'media' });
  shell.notifyShell();
  expect(listener).toHaveBeenCalledTimes(1);
  expect(shell.shellVersion()).toBe(3);
});

it('共享 shell 入口交出同一份活绑定：从入口写，状态模块当场读到', async () => {
  const sharedShell = await import('@peach/shell');
  sharedShell.writeShell({ configurationRequestedSection: '网络与访问' });
  expect(shell.configurationRequestedSection).toBe('网络与访问');
  expect(sharedShell.selected).toBe(shell.selected);
  expect(sharedShell.configurationRequestedSection).toBe('网络与访问');
});
