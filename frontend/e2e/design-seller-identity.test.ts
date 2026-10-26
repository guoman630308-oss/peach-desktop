import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, VIEWPORTS } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';

describe('卖家署名与头像', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：卡片和详情共用代表作头像，卖家与厂牌按空间排列`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, '/', viewport, {
        catalogIds: [ITEM.seller], ready: '#grid [data-media-card]',
      });
      try {
        const page = opened.page;
        const avatar = page.locator('#grid [data-media-avatar] img').first();
        assert.equal(await avatar.count(), 1, '卡片需独立于顶部代表作表取得卖家头像');
        assert.equal(await avatar.getAttribute('src'), '/avatar?id=18');
        assert.equal(await avatar.getAttribute('title'), '代表作画面，非本人');
        await page.locator('#grid [data-media-title]').first().click();
        await page.locator('#stage [data-item-side]').waitFor();
        const result = await page.evaluate(() => {
          const seller = document.querySelector('#stage [data-id-group="creator"]')!;
          const studio = document.querySelector('#stage [data-id-group="studio"]')!;
          const image = seller?.querySelector('img');
          return { label: seller?.querySelector('h5')?.textContent, src: image?.getAttribute('src'),
            note: image?.getAttribute('title'), sameRow: Math.abs(seller.getBoundingClientRect().top - studio.getBoundingClientRect().top) < 1,
            performers: document.querySelectorAll('#stage [data-id-cell="performer"]').length };
        });
        assert.deepEqual(result, { label: '卖家', src: '/avatar?id=18', note: '代表作画面，非本人', sameRow: true, performers: 0 });
        const bounds = await layout(page);
        assert.deepEqual(bounds.offenders, []);
        assert.ok(bounds.scrollWidth <= bounds.viewportWidth + 1);
        assert.deepEqual(opened.stub.writes, []);
        assert.deepEqual(opened.problems.filter(line => !line.includes('VIDEOJS')), []);
        const evidence = resolve('../build/fc2-seller-identity');
        await mkdir(evidence, { recursive: true });
        await page.screenshot({ path: resolve(evidence, `${viewport.name}.png`) });
      } finally { await opened.close() }
    });
  }
  it('创作者已安装的实体头像使用本人图，不显示卖家角色或代表作提示', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, VIEWPORTS[0]!);
    try {
      const page = opened.page;
      const item = await page.evaluate(async id => (await fetch(`/api/item?id=${id}`)).json(), ITEM.plain);
      await page.route((url) => url.pathname === '/api/item', route => route.fulfill({ json: {
        ...item, creator: '演示作者',
        performers: [], tags: [], entity_refs: { creator: [{ id: 81, name: '演示作者', has_image: true,
          image_version: 'portrait-v1', rep: 18, has_avatar: true, avatar_stand_in: true }] },
      } }));
      await page.reload();
      await page.locator('#stage [data-id-cell="creator"] img').waitFor();
      await settle(page);
      const group = page.locator('#stage [data-id-group="creator"]');
      assert.equal(await group.locator('h5').textContent(), '创作者');
      const img = group.locator('img');
      assert.equal(await img.getAttribute('src'), '/entity-image?kind=creator&id=81&v=portrait-v1');
      assert.equal(await img.getAttribute('title'), null);
      assert.deepEqual(opened.stub.writes, []);
    } finally { await opened.close() }
  });
});
