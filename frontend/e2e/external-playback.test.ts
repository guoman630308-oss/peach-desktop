import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, VIEWPORTS } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';

describe('外部播放器', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of [VIEWPORTS.find((one) => !one.mobile)!, VIEWPORTS.find((one) => one.mobile)!]) {
    it(`${viewport.mobile ? '手机' : '桌面'}按需生成可复制的网络地址`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, viewport, { ready: '#stage[open] [data-item-side]' });
      try {
        const page = opened.page;
        let requested = 0;
        await page.route('**/api/external-playback?*', route => {
          requested++;
          return route.fulfill({ json: { url: `/external-stream/${ITEM.plain}?ticket=${'a'.repeat(90)}`,
            playlist: `/external-playlist/${ITEM.plain}.m3u?ticket=${'a'.repeat(90)}` } });
        });
        await page.getByText('外部播放器 / VR', { exact: true }).click();
        assert.equal(requested, 0);
        await page.getByRole('button', { name: '生成播放链接', exact: true }).click();
        await page.getByRole('link', { name: '下载 M3U 播放列表' }).waitFor();
        assert.equal(requested, 1);
        assert.match(await page.getByRole('textbox', { name: '网络播放地址' }).inputValue(), /\/external-stream\/11\?ticket=/);
        assert.deepEqual((await layout(page)).offenders, []);
      } finally { await opened.close() }
    });
  }
});
