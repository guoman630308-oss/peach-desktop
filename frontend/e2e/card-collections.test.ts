import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, VIEWPORTS } from './harness.ts';
import { openFollowFeed } from './follow-fixture.ts';
import { openItemPage } from './item-fixture.ts';

describe('卡片信息与收藏入口', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 标题与署名相邻，标签有间距，${viewport.mobile ? '卡片按内容定高' : '同排卡片等高'}`, async () => {
      const opened = await openFollowFeed(browser, '/follow', viewport);
      try {
        assert.equal(await opened.page.locator('[data-follow-filter-glass]').getByRole('button', { name: '我的收藏', exact: true }).count(), 1);
        const payload = await opened.page.evaluate(async () => (await fetch('/api/follow')).json());
        payload.groups[0].primary.title = '这是一个很长的作品标题'.repeat(8);
        payload.groups[1].primary.title = '短标题';
        await opened.page.route(url => url.pathname === '/api/follow' && !url.searchParams.has('item'), route => route.fulfill({ json: payload }));
        await opened.page.reload();
        await opened.page.locator('[data-follow-list] [data-follow-item]').first().waitFor();
        const inspect = () => opened.page.locator('[data-follow-list] [data-follow-item]').evaluateAll(cards => cards.slice(0, 6).map(card => {
          const box = (selector: string) => card.querySelector(selector)!.getBoundingClientRect();
          const title = box('[data-media-title]'), byline = box('[data-follow-byline]'), labels = box('[data-follow-labels]');
          return { gap: byline.top - title.bottom, labelGap: labels.top - byline.bottom, height: card.getBoundingClientRect().height,
            title: title.height, line: parseFloat(getComputedStyle(card.querySelector('[data-media-title]')!).lineHeight) };
        }));
        const rows = await inspect();
        assert(rows[0].title >= rows[0].line * 1.9 && rows[1].title < rows[1].line * 1.5, JSON.stringify(rows));
        assert(rows.every(row => row.gap >= 4 && row.gap <= 8 && row.labelGap >= 8 && row.labelGap <= 12), JSON.stringify(rows));
        if (viewport.mobile) {
          assert(Math.abs((rows[0].height - rows[1].height) - (rows[0].title - rows[1].title)) <= 1, JSON.stringify(rows));
        } else {
          assert(Math.max(...rows.map(row => row.height)) - Math.min(...rows.map(row => row.height)) <= 1, JSON.stringify(rows));
        }
        await opened.page.evaluate(() => { document.body.dataset.density = 'dense' });
        const dense = await inspect();
        assert(dense.every(row => row.title < row.line * (viewport.mobile ? 2.1 : 1.5)
          && row.labelGap >= 8 && row.labelGap <= 12), JSON.stringify(dense));
        if (viewport.mobile) {
          assert(dense[0].title >= dense[0].line * 1.9, JSON.stringify(dense));
          assert(Math.abs((dense[0].height - dense[1].height) - (dense[0].title - dense[1].title)) <= 1, JSON.stringify(dense));
        }
      } finally { await opened.close() }
    });
    it(`${viewport.name} 收藏入口是紧凑图标按钮，播放媒体没有侧箭头`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-catalog-frame]' });
      try {
        const geometry = await opened.page.locator('[data-shortcuts-trigger]').evaluate(node => {
          const button = node.getBoundingClientRect(), readout = document.querySelector('[data-catalog-readout]')!.getBoundingClientRect();
          return { width: button.width, height: button.height, text: node.textContent?.trim(),
            glide: !!node.parentElement?.querySelector('[data-view-glide]'),
            gap: button.left - readout.right, parent: node.closest('[data-entity-sorts]') !== null };
        });
        assert.equal(geometry.parent, false);
        assert(geometry.width >= 30 && geometry.width <= 40 && geometry.width === geometry.height
          && geometry.gap >= 0 && geometry.gap <= 16, JSON.stringify(geometry));
        assert.equal(geometry.text, '');
        assert.equal(geometry.glide, false);
        await opened.page.goto(new URL('/item/31', opened.page.url()).href);
        await opened.page.locator('[data-carousel-video-active]').waitFor();
        assert.equal(await opened.page.locator('[data-carousel-video-active] [data-follow-image-step]:visible').count(), 0);
        await opened.page.locator('[data-follow-image-item="-1"]').click();
        assert.equal(await opened.page.locator('[data-follow-image-step]:visible').count(), 2);
      } finally { await opened.close() }
    });
  }
});
