import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, requiredEnv } from './harness.ts';
import { mockJavDetail } from './jav-detail-fixture.ts';

describe('JAV 评论展开的详情绘制', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of [{ name: '桌面', width: 1730, height: 1032, scale: 2 },
    { name: '手机', width: 390, height: 844, scale: 1 }]) {
    it(`${viewport.name} 正常动画下评论区域独立等待`, { timeout: 60000 }, async () => {
      const context = await browser.newContext({ viewport, deviceScaleFactor: viewport.scale, reducedMotion: 'no-preference' });
      let release = () => {};
      try {
        const page = await context.newPage();
        const errors: string[] = [];
        page.on('pageerror', error => errors.push(error.message));
        await mockJavDetail(page);
        let requests = 0;
        const response = new Promise<void>(resolve => { release = resolve });
        await page.route(/\/api\/jav\/reviews\?/, async route => {
          requests++;
          await response;
          await route.fulfill({ json: { ok: true, page: 1, more: false,
            items: Array.from({ length: 10 }, (_, index) => ({ id: String(index), username: '示例评论者',
              content: '正常动画下的评论正文与资源来源。'.repeat(40), score: 4, created_at: '2026-10-13' })) } });
        });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001');
        const title = page.locator('[data-jav-info] h1');
        await title.waitFor();
        await page.waitForFunction(() => !document.querySelector('#stage[data-stage-reveal]'));
        await page.locator('#stage').evaluate(node => Promise.all(node.getAnimations().map(animation => animation.finished)));
        const original = await title.elementHandle();
        const side = page.locator('[data-jav-info]');
        assert.equal(requests, 0, '评论按展开请求');
        await page.locator('[data-jav-reviews] summary').click();
        await page.locator('[data-jav-reviews] [data-skeleton="reviews"]').waitFor();
        assert.equal(await side.locator('[data-skeleton]').count(), 1, '等待态只属于评论区');
        const shimmer = await page.locator('[data-skeleton="reviews"] .skeleton-sheen').evaluateAll(nodes => nodes.map(node => {
          const host = getComputedStyle(node), sweep = getComputedStyle(node, '::after');
          return { position: host.position, overflow: host.overflow, sweep: sweep.position,
            height: node.getBoundingClientRect().height, sweepHeight: parseFloat(sweep.height), radius: parseFloat(host.borderRadius) };
        }));
        assert.equal(shimmer.length, 15);
        for (const bar of shimmer) {
          assert.equal(bar.position, 'relative', '微光定位属于评论占位条');
          assert.equal(bar.overflow, 'hidden', '微光在占位条内裁切');
          assert.equal(bar.sweep, 'absolute');
          assert.ok(bar.radius > 0 && Math.abs(bar.height - bar.sweepHeight) < 1, JSON.stringify(bar));
        }
        assert.equal(await page.locator('[data-jav-review-skeleton]').count(), 3);
        assert.ok((await page.locator('[data-skeleton="reviews"]').boundingBox())!.height >= 240);
        assert.ok(await original!.evaluate(node => node.isConnected), '等待期间标题 DOM 保留');
        assert.ok(await page.locator('[data-jav-actions]').isVisible(), '等待期间操作区保留');
        await page.waitForFunction(() => {
          const placeholder = document.querySelector('[data-skeleton="reviews"]')!;
          const body = placeholder.closest('.ui-fcollapse')!;
          return body.getBoundingClientRect().height >= placeholder.getBoundingClientRect().height;
        });
        await page.screenshot({ path: `../build/comments-${viewport.name}-loading.png` });
        release();
        await page.locator('[data-jav-review]').first().waitFor();
        await page.locator('[data-jav-reviews] [data-skeleton]').waitFor({ state: 'hidden' });
        assert.ok(await original!.evaluate(node => node.isConnected), '标题 DOM 保留');
        await page.screenshot({ path: `../build/comments-${viewport.name}-expanded.png` });
        await page.locator('[data-jav-reviews] summary').click();
        await page.locator('[data-jav-reviews] details:not([open])').waitFor();
        await page.locator('[data-jav-reviews] summary').click();
        await page.locator('[data-jav-review]').first().waitFor();
        assert.equal(await side.locator('[data-skeleton="reviews"]').count(), 0, '缓存评论立即显示');
        assert.equal(requests, 1, '缓存展开保留已取得评论');
        assert.ok(await original!.evaluate(node => node.isConnected), '反复开合保留标题');
        assert.deepEqual(errors, []);
      } finally { release(); await context.close() }
    });
  }
});
