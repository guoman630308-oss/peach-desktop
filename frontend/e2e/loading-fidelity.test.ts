import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, requiredEnv, VIEWPORTS } from './harness.ts';

describe('页面等待态的结构', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 新作封面抓取显示进度并在完成后换成图片`, { timeout: 60_000 }, async () => {
      const context = await browser.newContext({ viewport });
      try {
        const page = await context.newPage();
        let completed = false;
        await page.route('**/api/entity/shapes', route => route.fulfill({ json: { ok: true, entities: [], home: { feed: true } } }));
        await page.route(/\/api\/feeds\/discoveries\?/, route => route.fulfill({ json: { ok: true, items: [
          { id: 1, code: 'EXM-001', title: '示例新作', has_cover: completed, cover_fetching: !completed },
        ] } }));
        await page.route(/\/cover\?code=EXM-001/, route => route.fulfill({ contentType: 'image/svg+xml', body:
          '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="480"><rect width="320" height="480" fill="#ddd"/></svg>' }));
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/', { waitUntil: 'load' });
        const pending = page.locator('#feedNew [data-feed-cover-state="fetching"]');
        await pending.waitFor();
        assert.equal(await pending.innerText(), '抓取中');
        const color = await pending.evaluate(node => getComputedStyle(node.closest('.pic')!).backgroundColor);
        assert.notEqual(color, 'rgb(0, 0, 0)', '抓取中封面仍是黑底');
        completed = true;
        await page.locator('#feedNew img.cover').waitFor({ timeout: 10_000 });
        assert.equal(await pending.count(), 0);
      } finally { await context.close() }
    });

    it(`${viewport.name} 首页筛选控件立即显示，浮层保留最终材质`, { timeout: 60_000 }, async () => {
      const context = await browser.newContext({ viewport });
      let release = () => {};
      const held = new Promise<void>(resolve => { release = resolve });
      try {
        await context.addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false })));
        const page = await context.newPage();
        await page.route(/\/api\/(?:items|facets)\?/, async route => { await held; await route.continue() });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/', { waitUntil: 'load' });
        const frame = page.locator('[data-catalog-frame]');
        await frame.waitFor();
        assert.ok(await frame.locator('[data-entity-layout]').count(), '版式键没有立即显示');
        assert.ok(await frame.locator('[data-entity-sort]').count(), '排序键没有立即显示');
        const styles = await frame.evaluate(node => ({ filter: getComputedStyle(node).backdropFilter,
          background: getComputedStyle(node).backgroundColor }));
        assert.notEqual(styles.filter, 'none', '筛选框等待时失去了玻璃材质');
        assert.notEqual(styles.background, 'rgba(0, 0, 0, 0)');
      } finally { release(); await context.close() }
    });

    for (const jav of [false, true]) it(`${viewport.name} ${jav ? 'JAV' : '普通'}艺人等待态遵循作品语境`, { timeout: 60_000 }, async () => {
      const context = await browser.newContext({ viewport });
      let release = () => {};
      const held = new Promise<void>(resolve => { release = resolve });
      try {
        const page = await context.newPage();
        await page.route('**/performers/90001', async route => {
          const response = await route.fetch({ url: requiredEnv('PEACH_E2E_ORIGIN') + '/' });
          await route.fulfill({ response });
        });
        await page.route('**/api/entity/shapes', route => route.fulfill({ json: { ok: true, entities: [
          { id: 90001, kind: 'performer', names: ['演示艺人'], parts: ['costars', 'facts', ...(jav ? ['jav'] : [])] },
        ] } }));
        await page.route(/\/api\/entity\?/, async route => { await held; await route.fulfill({ json: { error: '未取得' } }) });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/performers/90001', { waitUntil: 'load' });
        const root = page.locator('[data-skeleton="entity/performer"]');
        await root.waitFor();
        assert.ok(await root.locator('[data-entity-foot]').count(), '数字地址漏了相关艺人');
        assert.ok(await root.locator('[data-entity-facts]').count(), '数字地址漏了资料表');
        assert.ok(await root.locator('[data-entity-state]').count(), '观看状态被画成了占位');
        assert.ok(await root.locator('[data-entity-sort]').count(), '排序控件被画成了占位');
        assert.equal(await root.locator('[data-entity-layout]').count() > 0, jav, '卡片版式开关误用了艺人类型');
        const ratio = await root.locator('[style*="--skeleton-card-ratio"]').first().evaluate(node =>
          Number((node as HTMLElement).style.getPropertyValue('--skeleton-card-ratio')));
        assert.equal(ratio, jav ? .75 : 16 / 9, '等待态封面比例与作品语境不一致');
      } finally { release(); await context.close() }
    });
  }
});
