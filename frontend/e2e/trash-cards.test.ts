import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, requiredEnv, VIEWPORTS } from './harness.ts';

describe('回收站卡片', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 混排卡片${viewport.mobile ? '按内容定高' : '等高'}、展示归入原因并可还原视频`, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
      try {
        const page = await context.newPage();
        await page.addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false })));
        const reasons = ['manual', 'junk', 'duplicate', 'missing', null];
        const items = Array.from({ length: 10 }, (_, i) => ({
          id: 92000 + i, name: i === 1 ? '长标题'.repeat(40) : `示例资源 ${i}`,
          medium: ['video', 'image', 'other'][i % 3], location: 'local', duration: i % 3 === 0 ? 163 : null,
          size: 10000, disposal: 'trash', disposal_reason: reasons[i % 5],
          is_jav: i === 0, code: i === 0 ? 'DEMO-001' : null, release_date: '2026-01-01',
          creator: '示例创作者', tags: i % 2 === 0 ? ['示例标签', '长标签'.repeat(20)] : [],
        }));
        const restored: number[] = [];
        let attempts = 0;
        await page.route('**/photo-thumb?*', route => route.fulfill({ contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="gray"/></svg>' }));
        await page.route('**/api/items?*', route => route.fulfill({ json: {
          items: items.filter(item => !restored.includes(item.id)), total: items.length - restored.length, has_more: false,
        } }));
        await page.route('**/api/batch', async route => {
          const body = route.request().postDataJSON();
          if (body.operation === 'restore') {
            attempts += 1;
            if (attempts === 1) {
              await route.fulfill({ status: 503, json: { error: '演示还原失败' } });
              return;
            }
            restored.push(...body.ids);
          } else {
            assert.equal(body.operation, 'dispose');
            assert.equal(body.disposal_reason, 'manual');
            restored.splice(0);
          }
          await route.fulfill({ json: { ok: true, changed: body.ids.length } });
        });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/trash', { waitUntil: 'load' });
        const cards = page.locator('#grid [data-media-card]');
        await cards.first().waitFor();
        assert.equal(await cards.count(), 10);
        const heights: number[] = [];
        const frames: number[] = [];
        for (let i = 0; i < 10; i++) {
          await cards.nth(i).scrollIntoViewIfNeeded();
          heights.push(await cards.nth(i).evaluate(card => card.getBoundingClientRect().height));
          frames.push(await cards.nth(i).evaluate(card => card.getBoundingClientRect().height
            - card.querySelector('[data-media-meta]')!.getBoundingClientRect().height));
          assert.equal(await cards.nth(i).locator('[data-media-resource-action="restore"]').count(), 1);
        }
        if (viewport.mobile) {
          assert.ok(Math.max(...heights) - Math.min(...heights) > 1, JSON.stringify(heights));
          assert.ok(Math.max(...frames) - Math.min(...frames) < 1, JSON.stringify(frames));
        } else {
          assert.ok(Math.max(...heights) - Math.min(...heights) < 1, JSON.stringify(heights));
        }
        assert.deepEqual(await cards.locator('[data-media-disposal-reason]').allTextContents(),
          ['手动', '垃圾', '重复', '文件缺失', '原因未记录', '手动', '垃圾', '重复', '文件缺失', '原因未记录']);
        assert.equal(await cards.locator('[data-flag="dispose"]').count(), 0);
        const textInsets: number[] = [];
        for (const i of [3, 4]) {
          await cards.nth(i).scrollIntoViewIfNeeded();
          textInsets.push(await cards.nth(i).locator('[data-media-title]').evaluate(title => {
            const range = document.createRange();
            range.selectNodeContents(title.firstChild!);
            return range.getBoundingClientRect().top - title.getBoundingClientRect().top;
          }));
        }
        assert.ok(Math.abs(textInsets[0]! - textInsets[1]!) < 1,
          `视频与资源的首行标题未对齐：${JSON.stringify(textInsets)}`);
        const video = cards.first();
        await video.scrollIntoViewIfNeeded();
        const boxes = await video.evaluate(card => {
          const duration = card.querySelector('[data-media-duration]')!.getBoundingClientRect();
          const restore = card.querySelector('[data-media-resource-action]')!.getBoundingClientRect();
          return { duration: duration.toJSON(), restore: restore.toJSON(), overflow: document.documentElement.scrollWidth > innerWidth };
        });
        assert.ok(boxes.duration.bottom <= boxes.restore.top || boxes.duration.left >= boxes.restore.right);
        assert.equal(boxes.overflow, false);
        await video.locator('[data-media-resource-action="restore"]').click();
        await video.locator('[data-media-operation-error]').waitFor();
        assert.equal(await video.locator('[data-media-operation-error]').textContent(), '演示还原失败');
        assert.equal(await cards.count(), 10);
        await video.locator('[data-media-resource-action="restore"]').click();
        await page.locator('#grid [data-media-card][data-id="92000"]').waitFor({ state: 'detached' });
        assert.deepEqual(restored, [92000]);
        await page.getByRole('button', { name: '撤销', exact: true }).last().click();
        const returned = page.locator('#grid [data-media-card][data-id="92000"]');
        await returned.waitFor();
        assert.equal(await returned.locator('[data-media-disposal-reason]').textContent(), '手动');
      } finally { await context.close() }
    });
  }
});
