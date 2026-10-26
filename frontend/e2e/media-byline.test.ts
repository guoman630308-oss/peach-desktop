import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { layout, settle, VIEWPORTS } from './harness.ts';
import { installDesignBrowser, openCatalogFixture } from './design-fixture.ts';

describe('作品卡片署名信息行', () => {
  let browser: Browser;
  installDesignBrowser(next => { browser = next });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 姓名靠左，与日期大小自然排列`, { timeout: 60_000 }, async () => {
      const opened = await openCatalogFixture(browser, payload => {
        Object.assign(payload.items[0], { is_jav: true, code: 'MIAA-155', name: '演示作品',
          performers: ['篠田优'], performer_total: 1, release_date: '2019-09-13', size: 6549825126 });
      });
      try {
        await opened.page.setViewportSize({ width: viewport.width, height: viewport.height });
        await settle(opened.page);
        const card = opened.page.locator('#grid [data-media-card]').first();
        for (const [field, glyph] of [['who', 'user-round'], ['release', 'calendar'], ['size', 'hard-drive']]) {
          assert.equal(await card.locator(`[data-media-${field}] svg use`).getAttribute('href'), `#i-${glyph}`);
          assert.equal(await card.locator(`[data-media-${field}] svg`).evaluate(node => node.getBoundingClientRect().width), 14);
        }
        const geometry = await card.evaluate(element => {
          const who = element.querySelector('[data-media-who]')!;
          const byline = element.querySelector('[data-media-byline]')!;
          const date = element.querySelector('[data-media-release]')!;
          const style = getComputedStyle(who);
          const a = who.getBoundingClientRect(), b = date.getBoundingClientRect();
          return { align: style.textAlign, width: a.width, available: byline.getBoundingClientRect().width,
            together: Math.abs(a.top - b.top) < 2, height: byline.clientHeight, content: byline.scrollHeight };
        });
        assert.equal(geometry.align, 'left');
        assert(geometry.width < geometry.available / 2);
        if (!viewport.mobile) assert.equal(geometry.together, true);
        assert(geometry.content <= geometry.height + 1, JSON.stringify(geometry));
        const size = await layout(opened.page);
        assert.equal(size.viewportWidth, viewport.width);
        assert(size.scrollWidth <= size.viewportWidth + 1, JSON.stringify(size));
        assert.deepEqual(size.offenders, []);
        await opened.page.screenshot({ path: resolve('../build', `byline-${viewport.name}.png`) });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
