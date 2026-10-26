import { describe, expect, it } from 'vitest';
import { cleanupSkeletonHtml, REPAIR_CARD_TEXT, SCAN_CARD_TEXT } from '../src/management';

describe('数据管理首屏', () => {
  const skeleton = () => {
    const root = document.createElement('div');
    root.innerHTML = cleanupSkeletonHtml();
    return root;
  };
  it('读数卡复用最终排版，名字与图标立即呈现，只有读数画占位', () => {
    const stats = skeleton().querySelectorAll('.ui-cleanupstats > .board-plain-stat');
    expect([...stats].map(card => card.querySelector('.ui-board-stat-tile use')?.getAttribute('href')))
      .toEqual(['#i-square-check-big', '#i-sparkles', '#i-file-stack', '#i-file-archive', '#i-trash']);
    expect([...stats].map(card => card.querySelector('.ui-board-plain-stat-head')?.textContent))
      .toEqual(['人工复核', '高清版', '重复文件', '垃圾文件', '回收站']);
    for (const card of stats) {
      expect(card.querySelector('strong > .skeleton-text')).not.toBeNull();
      expect(card.querySelector('.cleanupmeta > .skeleton-text')).not.toBeNull();
    }
  });
  it('任务卡按最终顺序排好：扫描与采集、媒体修复两张岛卡，再是整理', () => {
    const grid = skeleton().querySelector('.cleanupgrid')!;
    expect([...grid.children].map(card => card.className))
      .toEqual(['cleanupscraping', 'cleanupmediarepair', 'cleanupfieldset cleanuporganize']);
    for (const card of grid.querySelectorAll(':scope > .cleanupscraping, :scope > .cleanupmediarepair')) {
      expect(card.firstElementChild?.classList.contains('peach-react')).toBe(true);
    }
    const scan = grid.querySelector('.cleanupscraping')!;
    expect(scan.querySelector('p')?.textContent).toBe(SCAN_CARD_TEXT);
    expect(scan.querySelector('.skeleton')).toBeNull();
    expect(scan.querySelector('footer > a')?.getAttribute('href')).toBe('/sources');
    expect(grid.querySelector('.cleanupmediarepair p')?.textContent).toBe(REPAIR_CARD_TEXT);
  });
  it('等数据才能执行的键保持最终变体与尺寸，用原生 disabled 挡住并标成骨架操作键', () => {
    const root = skeleton();
    const split = root.querySelector('.cleanupscraping [data-split-button]')!;
    expect(split.getAttribute('data-variant')).toBe('primary');
    expect([...split.querySelectorAll('button')].map(button => button.classList.contains('bg-button-primary')))
      .toEqual([true, true]);
    expect(split.querySelector('button use')?.getAttribute('href')).toBe('#i-database');
    const repair = root.querySelector('.cleanupmediarepair footer > button')!;
    expect(repair.textContent).toBe('开始修复');
    expect(repair.classList.contains('bg-button-primary')).toBe(true);
    expect(repair.hasAttribute('data-skeleton-action')).toBe(true);
    const legacy = root.querySelectorAll('.cleanupfieldset footer > button, .resourcesyncfooter > button');
    expect([...legacy].map(button => button.textContent)).toEqual(['预览', '检查死链', '检查文件']);
    for (const button of legacy) expect(button.classList.contains('primary')).toBe(true);
    const actions = root.querySelectorAll('button:not(.board-plain-stat, [aria-haspopup="listbox"])');
    expect([...actions].map(button => button.textContent || button.getAttribute('aria-label')))
      .toEqual(['同步媒体库', '更多同步方式', '开始修复', '预览', '检查死链', '检查文件']);
    for (const button of actions) {
      expect(button.hasAttribute('disabled')).toBe(true);
      expect(button.hasAttribute('data-skeleton-action')).toBe(true);
    }
  });
  it('链接管理与资源同步的标题、正文立即呈现，不预留同步面板', () => {
    const root = skeleton();
    expect(Array.from(root.querySelectorAll('.ui-resourcesync > h2'), title => title.textContent)).toEqual(['链接管理', '资源同步']);
    expect([...root.querySelectorAll('.ui-linkstats > div > span')].map(term => term.textContent))
      .toEqual(['链接总数', '官网/事务所', '社交账号', '作品资料站']);
    expect(root.querySelector('#resource-sync')).toBeNull();
  });
});
