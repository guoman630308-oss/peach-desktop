import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, VIEWPORTS } from './harness.ts';
import { openItemPage } from './item-fixture.ts';

describe('我的收藏弹层', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  it('创作者头像依次尝试本地来源代理，全部失败显示身份字形', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0], { ready: '[data-catalog-frame]' });
    const { page } = opened;
    try {
      await page.route(url => url.pathname === '/api/library-shortcuts', route => route.fulfill({ json: { favorites: [
        { key: 'entity:9567', entity_id: 9567, kind: 'creator', name: 'InitialA', image_url: '/test-avatar/official', image_fallbacks: ['/test-avatar/archive'] },
        { key: 'entity:9568', entity_id: 9568, kind: 'creator', name: '未取得头像', image_url: '/test-avatar/missing', image_fallbacks: ['/test-avatar/missing-last'] },
      ], filters: [] } }));
      await page.route('**/test-avatar/*', route => route.request().url().endsWith('/archive')
        ? route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"><circle cx="18" cy="18" r="18" fill="gray"/></svg>' })
        : route.fulfill({ status: 404, body: '' }));
      await page.getByRole('button', { name: '我的收藏', exact: true }).click();
      const avatar = page.getByRole('link', { name: 'InitialA', exact: true }).locator('[data-shortcuts-avatar] img');
      await page.waitForFunction(() => document.querySelector<HTMLImageElement>('img[src="/test-avatar/archive"]')?.naturalWidth);
      assert.equal(await avatar.getAttribute('src'), '/test-avatar/archive');
      const missing = page.getByRole('link', { name: '未取得头像', exact: true }).locator('[data-shortcuts-avatar]');
      await missing.locator('svg').waitFor({ state: 'visible' });
      assert.equal(await missing.locator('img').count(), 0);
    } finally { await opened.close() }
  });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 保存、应用、删除筛选及收藏分类`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, '/watch-later?loc=&tag=VR&jav=1', viewport, { ready: '[data-catalog-frame]' });
      const page = opened.page;
      let favorites = [{ key: 'entity:7', entity_id: 7, kind: 'studio', name: '厂牌长名称'.repeat(12), image_url: '/logo?studio=test' }];
      let filters: { id: string; name: string; href: string }[] = [];
      await page.route(url => url.pathname === '/api/library-shortcuts', async route => {
        if (route.request().method() === 'POST') {
          const body = route.request().postDataJSON();
          if (body.action === 'save_filter') filters.push({ id: 'test', name: body.name, href: body.href });
          if (body.action === 'delete_filter') filters = [];
          if (body.action === 'favorite') favorites = [];
        }
        await route.fulfill({ json: { favorites, filters } });
      });
      try {
        await page.locator('#settingsBtn').evaluate((button: HTMLElement) => button.click());
        await page.locator('#settingsPanel:not([hidden])').waitFor();
        const setting = await page.evaluate((mobile) => {
          const card = document.querySelector('[data-settings-card]')!, nav = card.querySelector('[data-settings-nav]')!, heading = card.querySelector('h2')!;
          const surface = getComputedStyle(card), glass = getComputedStyle(nav), title = getComputedStyle(heading);
          const pick = getComputedStyle(nav.querySelector('[data-settings-glide]')!);
          const action = getComputedStyle(nav.querySelector('[data-settings-service] button')!);
          return { pick: { radius: pick.borderRadius, image: pick.backgroundImage, shadow: pick.boxShadow, backdrop: pick.backdropFilter },
            action: mobile ? null : { padding: action.padding, radius: action.borderRadius, font: action.fontFamily, size: action.fontSize, weight: action.fontWeight, line: action.lineHeight, color: action.color, height: action.minHeight, gap: action.gap },
            width: card.getBoundingClientRect().width, height: card.getBoundingClientRect().height,
            radius: surface.borderRadius, background: surface.backgroundColor, shadow: surface.boxShadow,
            image: glass.backgroundImage, backdrop: glass.backdropFilter.replace(/#peach-optic-\d+/g, '#peach-optic'), drift: glass.animationName,
            font: title.fontSize, weight: title.fontWeight, line: title.lineHeight };
        }, viewport.mobile);
        await page.locator('#settingsClose').click();
        await page.locator('#settingsPanel').waitFor({ state: 'hidden' });
        await page.getByRole('button', { name: '我的收藏', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '我的收藏', exact: true });
        await page.waitForFunction(() => {
          const pane = document.querySelector<HTMLElement>('[data-shortcuts-glide]');
          return pane && !pane.hidden && pane.getBoundingClientRect().width > 0
            && !!document.querySelector('[data-shortcuts-panel] [data-glass-pane][data-optic-glass="true"]');
        });
        const collection = await dialog.evaluate((node, mobile) => {
          const card = node.closest('[data-modal-surface]')!, nav = node.querySelector(mobile ? '[data-shortcuts-categories]' : '[data-shortcuts-sidebar]')!, heading = node.querySelector('h2')!;
          const surface = getComputedStyle(card), glass = getComputedStyle(nav), title = getComputedStyle(heading);
          const pick = getComputedStyle(node.querySelector('[data-shortcuts-glide]')!);
          const action = getComputedStyle(node.querySelector('[data-shortcuts-footer] a')!);
          return { pick: { radius: pick.borderRadius, image: pick.backgroundImage, shadow: pick.boxShadow, backdrop: pick.backdropFilter },
            action: mobile ? null : { padding: action.padding, radius: action.borderRadius, font: action.fontFamily, size: action.fontSize, weight: action.fontWeight, line: action.lineHeight, color: action.color, height: action.minHeight, gap: action.gap },
            width: card.getBoundingClientRect().width, height: card.getBoundingClientRect().height,
            radius: surface.borderRadius, background: surface.backgroundColor, shadow: surface.boxShadow,
            image: glass.backgroundImage, backdrop: glass.backdropFilter.replace(/#peach-optic-\d+/g, '#peach-optic'), drift: glass.animationName,
            font: title.fontSize, weight: title.fontWeight, line: title.lineHeight };
        }, viewport.mobile);
        assert.deepEqual(collection, setting, '收藏弹层与设置共用尺寸、材质、落影与标题排版');
        const iconColors = await dialog.locator('[data-shortcuts-categories] button svg, [data-shortcuts-footer] a svg').evaluateAll(icons => icons.map(icon => getComputedStyle(icon).color));
        const selectedColor = await dialog.locator('[data-shortcuts-categories] [aria-selected="true"]').evaluate(tab => getComputedStyle(tab).color);
        assert.ok(iconColors.every(color => color === selectedColor), JSON.stringify(iconColors));
        if (!viewport.mobile) {
          const clipping = await dialog.evaluate(node => {
            const sidebar = node.querySelector('[data-shortcuts-sidebar]')!.getBoundingClientRect();
            const categories = node.querySelector('[data-shortcuts-categories]')!.getBoundingClientRect();
            return [categories.left - sidebar.left, sidebar.right - categories.right];
          });
          assert.ok(clipping.every(edge => Math.abs(edge) < 1), JSON.stringify(clipping));
        }
        assert.equal(await dialog.getByRole('link', { name: '已标记', exact: true }).getAttribute('href'), '/flagged');
        assert.equal(await dialog.getByRole('link', { name: '稍后看', exact: true }).getAttribute('href'), '/watch-later');
        assert.equal(await dialog.getByRole('textbox', { name: '筛选名称' }).count(), 0);
        await dialog.getByRole('tab', { name: '片商与厂牌', exact: true }).click();
        await page.waitForFunction(() => {
          const pane = document.querySelector('[data-shortcuts-glide]')!.getBoundingClientRect();
          const tab = document.querySelector('[data-shortcuts-categories] [aria-selected="true"]')!.getBoundingClientRect();
          return Math.abs(pane.left - tab.left) < 1 && Math.abs(pane.top - tab.top) < 1 && Math.abs(pane.width - tab.width) < 1 && Math.abs(pane.height - tab.height) < 1;
        });
        await dialog.getByRole('link', { name: favorites[0].name, exact: true }).waitFor();
        await dialog.locator('[data-shortcuts-avatar] img').waitFor();
        await page.waitForFunction(() => !!document.querySelector<HTMLImageElement>('[data-shortcuts-avatar] img')?.naturalWidth);
        const size = await layout(page);
        assert.equal(size.viewportWidth, viewport.width);
        assert(size.scrollWidth <= size.viewportWidth + 1, JSON.stringify(size));
        assert.deepEqual(size.offenders, []);
        await page.screenshot({ path: resolve('../build', `shortcuts-${viewport.name}.png`) });
        await dialog.getByRole('tab', { name: '保存的筛选', exact: true }).click();
        await dialog.getByRole('tab', { name: '保存的筛选', exact: true }).press('Home');
        assert.equal(await dialog.getByRole('tab', { name: '全部', exact: true }).getAttribute('aria-selected'), 'true');
        await dialog.getByRole('tab', { name: '全部', exact: true }).press('End');
        assert.equal(await dialog.getByRole('tab', { name: '保存的筛选', exact: true }).getAttribute('aria-selected'), 'true');
        assert.equal(await dialog.getByRole('textbox', { name: '搜索收藏' }).count(), 0);
        await dialog.getByRole('textbox', { name: '筛选名称' }).fill('VR 待看');
        await dialog.getByRole('button', { name: '保存当前筛选', exact: true }).click();
        const link = dialog.getByRole('link', { name: 'VR 待看', exact: true });
        await link.waitFor();
        const href = await link.getAttribute('href');
        const address = new URL(href!, page.url());
        assert.equal(address.pathname, '/watch-later');
        assert.equal(address.searchParams.get('loc'), '');
        assert.equal(address.searchParams.get('tag'), 'VR');
        assert.equal(address.searchParams.get('jav'), '1');
        await link.click();
        await page.waitForURL(url => url.pathname === '/watch-later' && url.searchParams.get('tag') === 'VR');
        await settle(page, '[data-catalog-frame]');
        await page.getByRole('button', { name: '我的收藏', exact: true }).click();
        await dialog.getByRole('tab', { name: '保存的筛选', exact: true }).click();
        await dialog.getByRole('button', { name: '删除筛选VR 待看', exact: true }).click();
        await link.waitFor({ state: 'detached' });
        await dialog.getByRole('tab', { name: '全部', exact: true }).click();
        await dialog.getByRole('button', { name: /^取消收藏/ }).click();
        await dialog.getByRole('heading', { name: '还没有收藏', exact: true }).waitFor();
        const close = dialog.getByRole('button', { name: '关闭我的收藏', exact: true });
        assert.equal(await close.locator('use').getAttribute('href'), '#i-x');
        assert.deepEqual(await close.evaluate(node => {
          const box = node.getBoundingClientRect(); return [box.width, box.height];
        }), [24, 24]);
        await close.click();
        await dialog.waitFor({ state: 'hidden' });
        for (const [label, path] of [['已标记', '/flagged'], ['稍后看', '/watch-later']]) {
          await settle(page, '[data-catalog-frame]');
          await page.getByRole('button', { name: '我的收藏', exact: true }).click();
          await dialog.getByRole('link', { name: label, exact: true }).click();
          await page.waitForURL(url => url.pathname === path);
          await dialog.waitFor({ state: 'hidden' });
        }
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
