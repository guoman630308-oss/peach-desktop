/* 管理区页头的视图模型与启动骨架（`src/manage-header.ts`）：标题取哪个名字、哪几页画面包屑、
 * 窄屏标题收档的判据、回收站说明行的三态，以及骨架转义。岛怎么接手由 `react/manage-header.test.tsx` 测。 */
import { describe, expect, it } from 'vitest';

import { manageHeaderSkeletonHtml, manageHeaderView, type ManageEntry, type ManageHeaderProps } from '../src/manage-header';

const SECTIONS: ManageEntry[] = [
  ['stats', '统计', 'chart'], ['cleanup', '数据管理', 'database'], ['trash', '回收站', 'trash'],
  ['configuration', 'Peach 配置', 'settings'],
];
const MENU = SECTIONS.filter(([key]) => key !== 'trash');

const props = (section: string, path: string, patch: Partial<ManageHeaderProps> = {}): ManageHeaderProps =>
  ({ section, path, sections: SECTIONS, menu: MENU, trash: null, ...patch });

describe('页头判据', () => {
  it('诊断分区共用配置页标题', () => {
    expect(manageHeaderView(props('configuration', '/settings/diagnostics'))).toMatchObject({ title: 'Peach 配置', compact: true });
  });
  it('不在管理区时整块不画', () => {
    expect(manageHeaderView(props('', '/'))).toBeNull();
    expect(manageHeaderSkeletonHtml(props('', '/'))).toBe('');
  });

  it('数据管理之下的子页画面包屑，标题取子页的名字；hub 自己与别的管理区不画', () => {
    expect(manageHeaderView(props('cleanup', '/junk-files'))).toMatchObject({ title: '垃圾文件', crumb: '垃圾文件' });
    expect(manageHeaderView(props('cleanup', '/sources'))).toMatchObject({ title: '来源和凭证', crumb: '来源和凭证' });
    expect(manageHeaderView(props('cleanup', '/data-cleanup'))).toMatchObject({ title: '数据管理', crumb: '' });
    expect(manageHeaderView(props('stats', '/stats'))).toMatchObject({ title: '统计', crumb: '' });
    expect(manageHeaderView(props('trash', '/trash'))).toMatchObject({ title: '回收站', crumb: '回收站' });
  });

  it('窄屏标题收一档的只有数据管理 hub、来源和凭证与配置', () => {
    const compact = (section: string, path: string) => manageHeaderView(props(section, path))!.compact;
    expect([compact('cleanup', '/data-cleanup'), compact('cleanup', '/sources'), compact('configuration', '/settings/library')])
      .toEqual([true, true, true]);
    expect([compact('cleanup', '/junk-files'), compact('cleanup', '/duplicates'), compact('stats', '/stats')])
      .toEqual([false, false, false]);
  });

  it('说明行只在回收站：读数到之前是占位，读到以后是读数，回收站空时不给清空键', () => {
    expect(manageHeaderView(props('stats', '/stats'))!.lede).toEqual({ kind: 'none' });
    expect(manageHeaderView(props('trash', '/trash'))!.lede).toEqual({ kind: 'skeleton' });
    expect(manageHeaderView(props('trash', '/trash', { trash: { total: 1234, shown: 8 } }))!.lede)
      .toEqual({ kind: 'count', text: '1,234 个符合 · 显示 8', total: 1234 });
    const empty = document.createElement('div');
    empty.innerHTML = manageHeaderSkeletonHtml(props('trash', '/trash', { trash: { total: 0, shown: 0 } }));
    expect(empty.querySelector('[data-lede-text]')!.textContent).toBe('0 个符合 · 显示 0');
    expect(empty.querySelector('[data-empty-trash]')).toBeNull();
  });
});

describe('启动骨架', () => {
  it('管理导航、面包屑和页面标题按层级排列', () => {
    const node = document.createElement('div');
    node.innerHTML = manageHeaderSkeletonHtml(props('cleanup', '/review'));
    expect([...node.children].map(child => child.tagName)).toEqual(['NAV', 'NAV', 'H2']);
    expect(node.firstElementChild?.hasAttribute('data-manage-bar')).toBe(true);
    expect(node.children[1]?.hasAttribute('data-manage-crumb')).toBe(true);
  });
  it('管理条标出当前项，回收站读数到之前铺两格占位', () => {
    const node = document.createElement('div');
    node.innerHTML = manageHeaderSkeletonHtml(props('trash', '/trash'));
    expect([...node.querySelectorAll<HTMLElement>('[data-manage]')].map((button) => button.dataset.manage))
      .toEqual(['stats', 'cleanup', 'configuration']);
    expect([...node.querySelectorAll<HTMLElement>('[aria-pressed="true"]')].map((button) => button.dataset.manage))
      .toEqual(['cleanup']);
    expect([...node.querySelectorAll<HTMLElement>('[data-trash-lede-skeleton]')].map((cell) => cell.dataset.trashLedeSkeleton))
      .toEqual(['text', 'action']);
    expect(node.querySelector('[data-manage-crumb] a')!.getAttribute('href')).toBe('/data-cleanup');
    node.innerHTML = manageHeaderSkeletonHtml(props('stats', '/stats'));
    const pressed = node.querySelector('[data-manage="stats"]')!;
    expect([pressed.getAttribute('aria-pressed'), pressed.getAttribute('aria-current')]).toEqual(['true', 'page']);
    expect(node.querySelector('[data-manage-lede]')).toBeNull();
  });

  it('名字照字面写进去', () => {
    const node = document.createElement('div');
    node.innerHTML = manageHeaderSkeletonHtml({ ...props('x', '/x'), sections: [['x', '<img src=x>', 'a"b']], menu: [['x', '<img src=x>', 'a"b']] });
    expect(node.querySelector('img')).toBeNull();
    expect(node.querySelector('[data-manage-title]')!.textContent).toBe('<img src=x>');
  });
});
