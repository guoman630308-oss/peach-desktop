import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, VIEWPORTS } from './harness.ts';
import { openItemPage } from './item-fixture.ts';

describe('共享提示的文字与锚点', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 保存的筛选名称悬停时直接可读`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-catalog-frame]' });
      try {
        await opened.page.route('**/api/library-shortcuts', route => route.fulfill({ json: {
          favorites: [], filters: [{ id: 'clear', name: '高清馆藏', href: '/?quality=hd' }],
        } }));
        await opened.page.locator('[data-shortcuts-trigger]').click();
        await opened.page.getByRole('tab', { name: '保存的筛选', exact: true }).click();
        const saved = opened.page.getByRole('link', { name: '高清馆藏', exact: true });
        await saved.hover();
        await opened.page.waitForTimeout(500);
        assert.equal(await saved.getAttribute('title'), null);
        assert.equal(await opened.page.locator('#board-control-tooltip:visible').count(), 0);
      } finally { await opened.close() }
    });
    it(`${viewport.name} 完整文字无需提示，截断文字提供原文`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-catalog-frame]' });
      try {
        await opened.page.evaluate(() => {
          const probe = document.createElement('div');
          probe.style.cssText = 'position:fixed;left:30px;top:200px;z-index:9999;background:white;display:grid;gap:20px;width:280px';
          probe.innerHTML = '<a id="tip-readable" href="#" title="高清馆藏">高清馆藏</a><a id="tip-clipped" href="#" title="这是需要查看完整名称的收藏筛选" style="display:block;width:80px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">这是需要查看完整名称的收藏筛选</a>';
          document.body.append(probe);
        });
        await opened.page.locator('#tip-readable').focus();
        await opened.page.waitForTimeout(100);
        assert.equal(await opened.page.locator('#board-control-tooltip:visible').count(), 0);
        assert.equal(await opened.page.locator('#tip-readable').getAttribute('title'), null);
        assert.equal(await opened.page.locator('#tip-readable').getAttribute('aria-describedby'), null);
        await opened.page.locator('#tip-clipped').focus();
        await opened.page.locator('#board-control-tooltip').waitFor({ state: 'visible' });
        assert.equal(await opened.page.locator('#board-control-tooltip').textContent(), '这是需要查看完整名称的收藏筛选');
        assert.equal(await opened.page.locator('#tip-readable').getAttribute('title'), '高清馆藏');
      } finally { await opened.close() }
    });
    it(`${viewport.name} 嵌套控件的补充提示按鼠标目标切换`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-catalog-frame]' });
      try {
        await opened.page.evaluate(() => {
          const probe = document.createElement('div');
          probe.title = '父级说明';
          probe.style.cssText = 'position:fixed;left:30px;top:200px;z-index:9999;background:white;display:flex;gap:20px';
          probe.innerHTML = '<button id="tip-parent">外层</button><button id="tip-child" title="子级说明">内层</button>';
          document.body.append(probe);
        });
        await opened.page.locator('#tip-parent').focus();
        await opened.page.locator('#board-control-tooltip').waitFor({ state: 'visible' });
        await opened.page.locator('#tip-child').hover();
        await opened.page.waitForFunction(() => document.querySelector('#board-control-tooltip')?.textContent === '子级说明');
        assert.equal(await opened.page.locator('#tip-parent').getAttribute('aria-label'), null);
      } finally { await opened.close() }
    });
    it(`${viewport.name} 弹窗中的图标提示跟随可见锚点`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-catalog-frame]' });
      try {
        await opened.page.evaluate(() => {
          const modal = document.createElement('dialog');
          modal.id = 'tip-modal';
          modal.style.cssText = 'width:300px;height:200px;padding:40px;transform:translateY(20px)';
          modal.innerHTML = '<span id="tip-contents" title="筛选作品来源" style="display:contents"><button id="tip-icon" aria-label="来源" style="width:40px;height:40px"><svg width="20" height="20"><circle cx="10" cy="10" r="8"/></svg></button></span>';
          document.body.append(modal); modal.showModal();
        });
        await opened.page.locator('#tip-icon').focus();
        await opened.page.locator('#board-control-tooltip').waitFor({ state: 'visible' });
        const geometry = await opened.page.evaluate(() => {
          const tip = document.querySelector('#board-control-tooltip')!.getBoundingClientRect();
          const icon = document.querySelector('#tip-icon')!.getBoundingClientRect();
          return { tip: { x: tip.x, y: tip.y, w: tip.width, h: tip.height }, icon: { x: icon.x, y: icon.y, w: icon.width, h: icon.height }, inModal: !!document.querySelector('#tip-modal #board-control-tooltip') };
        });
        assert(geometry.inModal);
        assert(Math.abs(geometry.tip.x + geometry.tip.w / 2 - geometry.icon.x - geometry.icon.w / 2) <= 1, JSON.stringify(geometry));
        assert(Math.abs(geometry.tip.y + geometry.tip.h + 8 - geometry.icon.y) <= 1, JSON.stringify(geometry));
      } finally { await opened.close() }
    });
  }
});
