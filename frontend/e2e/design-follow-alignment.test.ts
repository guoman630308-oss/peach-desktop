import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';

import { openFollowFeed } from './follow-fixture.ts';
import { launch, VIEWPORTS } from './harness.ts';

describe('关注与馆藏共用卡片交互', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });

  for (const viewport of VIEWPORTS) it(`${viewport.name}：关注标题在等待与内容态采用相同几何`, async () => {
    const opened = await openFollowFeed(browser, '/follow', viewport);
    try {
      opened.stub.hold = () => true;
      await opened.page.reload({ waitUntil: 'domcontentloaded' });
      const title = opened.page.locator('[data-follow-title]').last();
      await title.waitFor();
      const read = () => title.evaluate(node => {
        const style = getComputedStyle(node), box = node.getBoundingClientRect();
        return { size: style.fontSize, line: style.lineHeight, height: box.height, left: box.left };
      });
      const pending = await read();
      await opened.stub.release();
      await opened.page.locator('[data-follow-list] > [data-follow-item]').first().waitFor();
      assert.deepEqual(await read(), pending);
      const boxes = await opened.page.locator('[data-follow-list] > [data-follow-item]').evaluateAll(nodes =>
        nodes.slice(0, 6).map(node => {
          const card = node.getBoundingClientRect();
          const pic = node.querySelector('[data-media-pic]')!.getBoundingClientRect();
          const labels = node.querySelector('[data-follow-labels]')!.getBoundingClientRect();
          return { height: card.height, left: pic.left, labels: labels.left + 4 };
        }));
      assert.ok(boxes.every(box => Math.abs(box.labels - box.left) <= 1), JSON.stringify(boxes));
      assert.ok(Math.max(...boxes.map(box => box.height)) - Math.min(...boxes.map(box => box.height)) <= 1, JSON.stringify(boxes));
    } finally { await opened.stub.release(); await opened.close() }
  });

  it('创作者头像悬停显示描边，键盘焦点可见', async () => {
    const opened = await openFollowFeed(browser, '/follow', VIEWPORTS[0]!, { bindAuthors: true });
    try {
      const avatar = opened.page.locator('a[data-follow-avatar]').first();
      await avatar.hover();
      assert.notEqual(await avatar.evaluate(node => getComputedStyle(node).boxShadow), 'none');
      await avatar.focus();
      assert.notEqual(await avatar.evaluate(node => getComputedStyle(node).outlineStyle), 'none');
    } finally { await opened.close() }
  });

  for (const viewport of VIEWPORTS) it(`${viewport.name}：详情动作与接着看采用共享几何`, async () => {
    const opened = await openFollowFeed(browser, '/posts/1002', viewport,
      { ready: '#stage[open] [data-follow-detail-side]', bindAuthors: true });
    try {
      const page = opened.page;
      const creator = page.locator('[data-follow-detail-identity] a[data-id-cell]');
      await creator.hover();
      const face = creator.locator('[data-id-face]');
      assert.deepEqual(await face.evaluate(node => {
        const style = getComputedStyle(node);
        return [style.width, style.height];
      }), ['46px', '46px']);
      assert.notEqual(await face.evaluate(node => getComputedStyle(node).boxShadow), 'none');
      await creator.focus();
      assert.notEqual(await creator.evaluate(node => getComputedStyle(node).outlineStyle), 'none');
      await page.locator('[data-follow-related] [data-follow-item]').first().waitFor({ timeout: 15_000 });
      const geometry = await page.locator('[data-follow-detail-actions]').evaluate(node => {
        const styles = [...node.querySelectorAll('button, a')].map(button => {
          const style = getComputedStyle(button);
          return [style.width, style.height, style.borderRadius];
        });
        return styles;
      });
      assert.ok(geometry.length > 3);
      assert.ok(geometry.every(style => JSON.stringify(style) === JSON.stringify(geometry[0])), JSON.stringify(geometry));
      const row = page.locator('[data-follow-related] [data-related-row]');
      const scroll = await row.evaluate(node => {
        node.scrollLeft = 100;
        return { width: node.clientWidth, content: node.scrollWidth, left: node.scrollLeft };
      });
      assert.ok(scroll.content > scroll.width && scroll.left > 0, JSON.stringify(scroll));
      assert.equal(await page.locator('[data-follow-related] [data-follow-item]').evaluateAll(nodes =>
        nodes.some(node => node.getAttribute('data-follow-item') === '1002')), false);
      const next = page.locator('[data-follow-related] [data-follow-item]').first();
      const id = await next.getAttribute('data-follow-item');
      const singleRequests: string[] = [];
      page.on('request', request => {
        const url = new URL(request.url());
        if (url.pathname === '/api/follow' && url.searchParams.has('item')) singleRequests.push(url.searchParams.get('item')!);
      });
      await next.locator('[data-follow-open]').click();
      await page.waitForFunction(id => location.pathname === `/posts/${id}`, id);
      await page.locator('#stage [data-follow-detail-name]').filter({ hasText: `演示更新 ${id}` }).waitFor();
      assert.deepEqual(singleRequests, [], '接着看已有条目缓存却再次单条取数');
      await page.locator('#closeStage').click();
      await page.waitForFunction(() => location.pathname === '/follow');
    } finally { await opened.close() }
  });
});
