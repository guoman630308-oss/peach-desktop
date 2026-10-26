import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { followSource, openFollowManage } from './design-fixture.ts';
import { launch, layout, settle, VIEWPORTS } from './harness.ts';

describe('创作者别名表', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：两张表显示身份，全选和取消更新当前候选`, async () => {
    const opened = await openFollowManage(browser, viewport);
    try {
      const { page } = opened;
      const canonical = '同名创作者・かな・作者🙂';
      const alias = 'platform_artist_' + 'long_name_'.repeat(12);
      await page.route('**/api/follow?summary=1', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({
          sources: [followSource(1, canonical, 'Kemono', canonical)],
          counts: {}, suggestions: [],
          author_aliases: [{ canonical_key: 'creator', canonical_name: canonical,
            aliases: [{ key: 'platform', name: alias }] }],
          alias_suggestions: [
            { canonical, alias, evidence: '来源确认' },
            { canonical: '作者 B', alias: '别名 B', evidence: '来源确认' },
          ],
        }),
      }));
      const target = new URL('/subscriptions?tab=add', page.url()).href;
      await page.goto(target, { waitUntil: 'load' });
      const pending = page.getByRole('grid', { name: '待合并创作者别名', exact: true });
      const saved = page.getByRole('grid', { name: '已保存创作者别名', exact: true });
      await pending.waitFor();
      await saved.waitFor();
      await settle(page);
      assert.equal(await pending.getByRole('row').count(), 3);
      assert.equal(await saved.getByRole('row').count(), 2);
      assert.ok((await saved.textContent())?.includes(alias));
      const all = pending.getByRole('checkbox', { name: '全选待合并别名', exact: true });
      const one = pending.getByRole('checkbox', { name: '选择别名 别名 B，归入 作者 B', exact: true });
      const allLabel = pending.locator('label:has(input[aria-label="全选待合并别名"])');
      const oneLabel = pending.locator('label:has(input[aria-label="选择别名 别名 B，归入 作者 B"])');
      await allLabel.click();
      assert.equal(await one.isChecked(), true);
      assert.equal(await all.evaluate(element => (element as HTMLInputElement).indeterminate), false);
      await oneLabel.click();
      assert.equal(await all.evaluate(element => (element as HTMLInputElement).indeterminate), true);
      await allLabel.click();
      await all.focus();
      await all.press('Space');
      assert.equal(await one.isChecked(), false);
      assert.equal(await page.getByRole('button', { name: '合并所选（0）', exact: true }).count(), 0);
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1, JSON.stringify(geometry));
      assert.deepEqual(geometry.offenders, []);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
});
