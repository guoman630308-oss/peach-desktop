import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('JAV 入库作品卡', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });
  for (const viewport of VIEWPORTS) {
    it(`资源链接与操作完整可见（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, '/jav', viewport);
      const page = opened.page;
      try {
        await settle(page);
        await page.route('**/api/wants', route => route.fulfill({ json: { items: [{
          id: 9001, code: 'DEMO-001', title: '演示作品'.repeat(30), origin: 'code', phase: 'searching',
          link: null, release_date: '2025-01-01', studio: '演示厂牌', performers: '演示人物',
          fresh: false, search_count: 0, cover: null, scrape_error: null,
        }], counts: { searching: 1 }, scraping: false } }));
        let resourceRequests = 0;
        await page.route('**/api/wants/magnets?*', route => {
          resourceRequests += 1;
          return route.fulfill({ json: {
          state: 'ready', error: '', checked_at: null, warnings: [], source_url: 'https://example.test/work', items: [{
            id: 'magnet:abc', protocol: 'magnet', info_hash: 'abc', uri: 'magnet:?xt=urn:btih:abc',
            name: 'DEMO-001'.repeat(24), size: '4 GB', files: '2 個文件', date: '2026-10-01',
            attributes: ['字幕', '高清'], source: 'JavDB', source_url: 'https://example.test/resources',
            origins: ['https://example.test/resources'],
          }, {
            id: 'ed2k:abc', protocol: 'ed2k', info_hash: '', uri: 'ed2k://|file|demo|1|abc|/', name: '评论补充文件',
            size: '27,311,519,717 B', files: '', date: '2026-10-02', attributes: [], source: 'JavDB 评论',
            source_url: 'https://example.test/comment', origins: ['https://example.test/comment'],
          }, ...Array.from({ length: 6 }, (_, index) => ({
            id: `magnet:extra-${index}`, protocol: 'magnet', info_hash: `extra-${index}`, uri: `magnet:?xt=urn:btih:extra-${index}`,
            name: `DEMO-001 资源 ${index}`, size: '2 GB', files: '1 個文件', date: '2025-01-01',
            attributes: [], source: 'JavDB', source_url: 'https://example.test/resources', origins: [],
          }))],
        } }); });
        await page.route('**/api/downloads?*', route => route.fulfill({ json: { available: true,
          providers: [{ key: '115', label: '115', configured: true, target: '/下载' }], tasks: [] } }));
        await page.getByRole('tab', { name: '想要', exact: true }).click();
        await page.locator('[data-want-magnets]').scrollIntoViewIfNeeded();
        await page.getByRole('heading', { name: '资源链接 · 8' }).waitFor();
        await settle(page);
        const card = page.locator('[data-want-id="9001"]');
        assert.equal(await page.locator('#stats').getByRole('button', { name: '查找资源', exact: true }).count(), 2, '通用入口与作品入口分别保留一处');
        assert.equal(await page.getByRole('combobox', { name: '下载到', exact: true }).count(), 0, '下载目标在资源下载面板中选择');
        const source = card.getByRole('link', { name: '来源页', exact: true });
        assert.deepEqual(await source.evaluate(node => [...node.parentElement!.querySelectorAll('button, a')].map(action => action.textContent)), ['查找资源', '移除', '来源页']);
        const style = await card.evaluate(el => ({ radius: getComputedStyle(el).borderRadius,
          border: getComputedStyle(el).borderTopWidth }));
        assert.notEqual(style.radius, '0px');
        assert.equal(style.border, '1px');
        assert.equal(await source.evaluate(el => el.getBoundingClientRect().height), 32);
        assert.equal(await source.evaluate(el => getComputedStyle(el).borderTopWidth), '1px');
        assert.equal(await source.getAttribute('href'), 'https://example.test/work');
        assert.equal(await card.locator('[data-resource-protocol]:visible').count(), 3);
        assert.equal((await card.textContent())!.includes('個'), false);
        const ed2k = card.locator('[data-resource-protocol="ed2k"]');
        assert.ok((await ed2k.textContent())!.includes('ED2K'));
        assert.ok((await ed2k.textContent())!.includes('25.4 GB'));
        assert.equal(await ed2k.locator('span').first().locator('svg').count(), 0);
        const magnetStyle = await card.locator('[data-resource-protocol="magnet"]').first().locator('span').first().evaluate(el => getComputedStyle(el).backgroundColor);
        const ed2kStyle = await card.locator('[data-resource-protocol="ed2k"] span').first().evaluate(el => getComputedStyle(el).backgroundColor);
        assert.notEqual(magnetStyle, ed2kStyle);
        const add = card.getByRole('button', { name: '添加下载', exact: true }).first();
        const copy = card.getByRole('button', { name: '复制链接', exact: true }).first();
        assert.notEqual(await add.evaluate(el => getComputedStyle(el).backgroundColor), await copy.evaluate(el => getComputedStyle(el).backgroundColor));
        assert.equal(await add.locator('img').count(), 1, '下载渠道使用对应的网盘图标');
        if (viewport.name === 'desktop') {
          const centers = await add.evaluate(el => {
            const row = el.closest('[data-resource-protocol]')!.getBoundingClientRect();
            const button = el.getBoundingClientRect();
            return { row: row.top + row.height / 2, button: button.top + button.height / 2 };
          });
          assert.ok(Math.abs(centers.row - centers.button) <= 1, JSON.stringify(centers));
        }
        const geometry = await layout(page);
        assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1, JSON.stringify(geometry));
        assert.deepEqual(geometry.offenders, []);
        await card.screenshot({ path: `../build/want-resources-${viewport.name}.png` });
        await card.locator('summary').click();
        await page.waitForFunction(() => document.querySelectorAll('[data-resource-protocol]').length === 8 &&
          !document.querySelector('details [inert=""]'));
        assert.equal(await card.locator('[data-resource-protocol]:visible').count(), 8);
        const phase = page.locator('[data-want-phase="searching"]');
        await phase.locator('label:has(input[value="available"])').click();
        assert.equal(await phase.locator('[data-want-id]').count(), 1);
        await phase.locator('label:has(input[value="missing"])').click();
        assert.equal(await phase.locator('[data-want-id]').count(), 0);
        await phase.locator('label:has(input[value="available"])').click();
        await phase.getByRole('heading', { name: '资源链接 · 8' }).waitFor();
        assert.equal(resourceRequests, 1, '切换磁链筛选重复查询已有资源');
        await phase.screenshot({ path: `../build/want-filter-${viewport.name}.png` });
        await page.getByRole('tab', { name: '馆藏', exact: true }).click();
        await page.getByRole('tab', { name: '想要', exact: true }).click();
        await page.getByRole('heading', { name: '资源链接 · 8' }).waitFor();
        assert.equal(resourceRequests, 1, '返回页面重复查询资源');
        await page.reload();
        await page.getByRole('tab', { name: '想要', exact: true }).click();
        await page.getByRole('heading', { name: '资源链接 · 8' }).waitFor();
        assert.equal(resourceRequests, 1, '刷新页面未复用持久缓存');
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }
});
