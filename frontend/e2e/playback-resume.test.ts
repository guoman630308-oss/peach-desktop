/* 使用演示库的两秒合成视频，条目与写接口全部由桩接住。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { DETAIL, openFollowFeed } from './follow-fixture.ts';
import { launch, VIEWPORTS } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';

const VIDEO = '#stage video.vjs-tech';

async function metadata(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const video = document.querySelector<HTMLVideoElement>('#stage video');
    return video && video.readyState >= 1 && !video.seeking;
  }, undefined, { timeout: 15_000 });
}

describe('视频退出续播', () => {
  let browser: Browser;
  let clip: string;

  before(async () => {
    browser = await launch();
    const origin = process.env.PEACH_E2E_ORIGIN!;
    const result = await (await fetch(new URL('/api/items?medium=video&limit=1', origin))).json();
    clip = new URL(`/stream?id=${result.items[0].id}`, origin).toString();
  });
  after(async () => { await browser.close() });

  for (const viewport of VIEWPORTS) for (const kind of ['item', 'follow'] as const) {
    it(`${viewport.name} ${kind}：关闭、重开和刷新恢复秒数，时间深链优先`, { timeout: 90_000 }, async () => {
      const path = kind === 'item' ? `/assets/${ITEM.plain}` : `/posts/${DETAIL.collection}`;
      const opened = kind === 'item' ? await openItemPage(browser, path, viewport)
        : await openFollowFeed(browser, path, viewport, { ready: '#stage [data-follow-detail-side]' });
      try {
        const page = opened.page;
        await page.route((url) => ['/stream', '/follow-stream'].includes(url.pathname),
          async (route) => { await route.fulfill({ response: await route.fetch({ url: clip }) }) });
        await page.route((url) => ['/api/activity', '/api/follow/activity', '/api/follow/play'].includes(url.pathname),
          (route) => route.fulfill({ json: { ok: true } }));
        const key = kind === 'item' ? `item:${ITEM.plain}` : `follow:${DETAIL.collection}:main`;
        await page.evaluate((key) => localStorage.setItem(`peach.playback-position.v1:${key}`, '0'), key);
        await page.reload({ waitUntil: 'load' });
        await metadata(page);
        await page.locator(VIDEO).evaluate((video: HTMLVideoElement) => new Promise<void>((resolve) => {
          video.addEventListener('seeked', () => resolve(), { once: true });
          video.currentTime = .7;
        }));
        await metadata(page);
        const saved = () => page.evaluate((key) => localStorage.getItem(`peach.playback-position.v1:${key}`), key);
        const diagnostic = () => page.evaluate(() => ({ storage: Object.entries(localStorage),
          time: document.querySelector<HTMLVideoElement>('#stage video')?.currentTime,
          ready: document.querySelector<HTMLVideoElement>('#stage video')?.readyState }));
        assert.ok(Math.abs(Number(await saved()) - .7) < .1, `定位后未保存：${JSON.stringify(await diagnostic())}`);
        await page.locator('#closeStage').click();
        await page.locator('#stage[open]').waitFor({ state: 'detached' });
        assert.ok(Math.abs(Number(await saved()) - .7) < .1, `关闭后位置异常：${await saved()}`);
        await page.goto(new URL(path, page.url()).toString(), { waitUntil: 'load' });
        await metadata(page);
        const current = () => page.locator(VIDEO).evaluate((video: HTMLVideoElement) => video.currentTime);
        assert.ok(Math.abs(await current() - .7) < .1, `重新进入秒数 ${await current()}，存储 ${await saved()}`);
        await page.reload({ waitUntil: 'load' });
        await metadata(page);
        assert.ok(Math.abs(await current() - .7) < .1, '刷新没有恢复秒数');
        await page.goto(new URL(`${path}?t=1`, page.url()).toString(), { waitUntil: 'load' });
        await metadata(page);
        assert.ok(await current() >= .9, '指定时间深链没有覆盖保存位置');
        assert.deepEqual(opened.problems.filter((line) => !line.includes('VIDEOJS')), []);
      } finally {
        if (await opened.page.locator('#stage[open]').count()) await opened.page.locator('#closeStage').click();
        await opened.page.unrouteAll({ behavior: 'wait' });
        await opened.close();
      }
    });
  }
});
