import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, VIEWPORTS } from './harness.ts';
import { COVER_ONLY_ITEM, openItemPage, SAMPLE_ITEM } from './item-fixture.ts';

describe('馆藏 JAV 官方预览', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：只有封面时视频方向键控制进度，图片方向键切换媒体`, { timeout: 60000 }, async () => {
    const opened = await openItemPage(browser, '/', viewport, { ready: '[data-media-grid]', settings: { seekSeconds: 1 } });
    const { page } = opened;
    try {
      await page.route('**/stream?*', route => route.continue({ url: new URL(`/stream?id=${process.env.PEACH_E2E_ITEM}`, page.url()).href }));
      await page.goto(new URL(`/assets/${COVER_ONLY_ITEM.id}`, page.url()).href);
      const frame = page.locator('[data-item-gallery]');
      const video = frame.locator('video.vjs-tech');
      await page.waitForFunction(() => (document.querySelector<HTMLVideoElement>('[data-item-gallery] video')?.readyState || 0) >= 3);
      assert.equal(await frame.locator('[data-follow-image-dots] button').count(), 2);
      await video.evaluate(node => { const video = node as HTMLVideoElement; video.muted = true; video.pause(); video.currentTime = 0.5 });
      await page.locator('#closeStage').focus();
      await page.keyboard.press('ArrowRight');
      assert.ok(await video.evaluate(node => Math.abs((node as HTMLVideoElement).currentTime - 1.5) < 0.1));
      assert.ok(await frame.locator('.video-js').isVisible());
      await page.keyboard.press('ArrowLeft');
      assert.ok(await video.evaluate(node => Math.abs((node as HTMLVideoElement).currentTime - 0.5) < 0.1));
      assert.ok(await frame.locator('.video-js').isVisible());
      await frame.locator('[data-player-theater]').dispatchEvent('click');
      await page.keyboard.press('ArrowRight');
      assert.ok(await video.evaluate(node => Math.abs((node as HTMLVideoElement).currentTime - 1.5) < 0.1));
      assert.ok(await frame.locator('.video-js').isVisible());
      await frame.getByRole('button', { name: '查看图片', exact: true }).click();
      await frame.locator('[data-catalog-picture]').waitFor();
      await page.locator('#closeStage').focus();
      await page.keyboard.press('ArrowRight');
      assert.ok(await frame.locator('.video-js').isVisible());
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
  for (const viewport of [...VIEWPORTS, { name: 'wide', width: 1920, height: 1080, mobile: false }]) it(`${viewport.name}：图片和正片共用圆点切换`, { timeout: 60000 }, async () => {
    const opened = await openItemPage(browser, '/', viewport, { ready: '[data-media-grid]' });
    const { page } = opened;
    const samples: string[] = [];
    try {
      await page.route('**/stream?*', route => route.continue({ url: new URL(`/stream?id=${process.env.PEACH_E2E_ITEM}`, page.url()).href }));
      await page.route(url => ['/sample-image', '/sample-thumb'].includes(url.pathname), route => {
        samples.push(route.request().url());
        return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="#777"/></svg>' });
      });
      await page.goto(new URL(`/assets/${SAMPLE_ITEM.id}`, page.url()).href);
      const frame = page.locator('[data-item-gallery]');
      await frame.waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.querySelector<HTMLVideoElement>('[data-item-gallery] video')?.readyState === 4);
      await frame.locator('[data-media-thumbnail] img').first().waitFor({ state: 'visible' });
      assert.ok(samples.some(url => new URL(url).pathname === '/sample-thumb'), '小图使用样图缩略图入口');
      const stats = frame.getByRole('button', { name: '播放统计', exact: true });
      await stats.waitFor({ state: 'visible' });
      const statsBox = await stats.boundingBox(), toggleBox = await frame.getByRole('button', { name: '查看图片', exact: true }).boundingBox();
      assert.ok(statsBox && toggleBox && toggleBox.x >= statsBox.x + statsBox.width, '图片入口与播放统计并列');
      const dots = frame.locator('[data-follow-image-dots]');
      assert.equal(await dots.locator('button').count(), 4);
      const videoBox = await frame.locator('.video-js').boundingBox(), dotsBox = await dots.boundingBox();
      assert.ok(videoBox && dotsBox && dotsBox.y >= videoBox.y + videoBox.height, '馆藏 indicator 位于播放器下方');
      const player = frame.locator('.video-js');
      const normalFrame = await frame.boundingBox(), dialog = await page.locator('#stage').boundingBox();
      await player.evaluate(node => { (node as HTMLElement).dataset.theaterProbe = 'kept' });
      await frame.locator('[data-player-theater]').dispatchEvent('click');
      await page.locator('#stage[data-theater]').waitFor();
      assert.ok(await frame.locator('[data-carousel-below]').isHidden());
      const theaterBox = await player.boundingBox(), theaterFrame = await frame.boundingBox();
      assert.ok(theaterBox && videoBox && theaterFrame && theaterBox.height > videoBox.height + 20);
      if (!viewport.mobile) assert.ok(theaterBox && videoBox && theaterBox.width > videoBox.width + 20);
      assert.ok(theaterBox && theaterFrame && Math.abs(theaterBox.height - theaterFrame.height) < 2);
      const theaterDialog = await page.locator('#stage').boundingBox();
      assert.ok(theaterFrame && normalFrame && Math.abs(theaterFrame.height - normalFrame.height) < 2, '影院保留媒体区预留高度');
      assert.ok(dialog && theaterDialog && Math.abs(theaterDialog.width - dialog.width) < 2 && Math.abs(theaterDialog.height - dialog.height) < 2, '影院保留详情框体尺寸');
      assert.equal(await player.getAttribute('data-theater-probe'), 'kept');
      await page.screenshot({ path: `../build/item-theater-${viewport.name}.png` });
      await frame.locator('[data-player-theater]').dispatchEvent('click');
      assert.ok(await frame.locator('[data-carousel-below]').isVisible());
      const restoredBox = await player.boundingBox();
      assert.ok(restoredBox && videoBox && Math.abs(restoredBox.width - videoBox.width) < 2 && Math.abs(restoredBox.height - videoBox.height) < 2);
      await frame.locator('video.vjs-tech').evaluate(node => { const video = node as HTMLVideoElement; video.muted = true; void video.play() });
      await page.waitForFunction(() => (document.querySelector<HTMLVideoElement>('[data-item-gallery] video')?.currentTime || 0) > 0);
      await frame.getByRole('button', { name: '预览 2', exact: true }).click();
      await frame.locator('[data-catalog-picture]').waitFor({ state: 'visible' });
      assert.match(await frame.locator('[data-catalog-picture]').getAttribute('src') || '', /n=3$/);
      assert.ok(await frame.locator('video.vjs-tech').evaluate(node => (node as HTMLVideoElement).paused), '查看图片暂停正片');
      assert.ok(await frame.locator('.video-js').isHidden());
      await page.keyboard.press('Tab');
      await frame.getByRole('button', { name: '预览 1', exact: true }).focus();
      assert.equal(await frame.locator('[data-media-thumbnails]').count(), 1);
      await frame.locator('[data-media-thumbnail="1"]').click();
      await page.waitForFunction(() => document.querySelector('[data-catalog-picture]')?.getAttribute('src')?.endsWith('n=1'));
      const stripBox = await dots.boundingBox(), imageBox = await frame.locator('[data-catalog-picture]').boundingBox();
      assert.ok(stripBox && imageBox && stripBox.y >= imageBox.y + imageBox.height);
      assert.equal(await page.locator('[data-media-preview-tip]').count(), 0);
      await frame.getByRole('button', { name: '预览 1', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('[data-catalog-picture]')?.getAttribute('src')?.endsWith('n=1'));
      await frame.getByRole('button', { name: '播放正片', exact: true }).click();
      await frame.locator('.video-js').waitFor({ state: 'visible' });
      assert.ok(await frame.locator('video.vjs-tech').evaluate(node => (node as HTMLVideoElement).currentTime > 0), '切回正片保留位置');
      await settle(page);
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1);
      assert.deepEqual(opened.problems, []);
      await page.screenshot({ path: `../build/item-gallery-${viewport.name}.png` });
    } finally { await opened.close() }
  });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：未登记样图时加载来源图片并保持正片实例和位置`, { timeout: 60000 }, async () => {
    const opened = await openItemPage(browser, '/', viewport, { ready: '[data-media-grid]' });
    const { page } = opened;
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve });
    let details = 0, images = 0;
    try {
      await page.route('**/stream?*', route => route.continue({ url: new URL(`/stream?id=${process.env.PEACH_E2E_ITEM}`, page.url()).href }));
      await page.route('**/api/jav/detail?*', async route => {
        details++;
        await pending;
        await route.fulfill({ json: { code: COVER_ONLY_ITEM.code, state: 'ready', preview_count: 11 } });
      });
      await page.route('**/api/jav/image?*', route => {
        images++;
        const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="#777"/></svg>';
        return route.fulfill({ json: { ok: true, image: `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}` } });
      });
      await page.goto(new URL(`/assets/${COVER_ONLY_ITEM.id}`, page.url()).href);
      const frame = page.locator('[data-item-gallery]'), player = frame.locator('.video-js');
      await frame.getByRole('status', { name: '正在读取样图' }).waitFor();
      await page.waitForFunction(() => (document.querySelector<HTMLVideoElement>('[data-item-gallery] video')?.readyState || 0) >= 3);
      await player.evaluate(node => { (node as HTMLElement).dataset.galleryProbe = 'kept' });
      await frame.locator('video.vjs-tech').evaluate(node => { const video = node as HTMLVideoElement; video.pause(); video.currentTime = 0.5 });
      const normalFrame = await frame.boundingBox();
      release();
      await frame.getByRole('button', { name: '预览 11', exact: true }).waitFor();
      assert.equal(await frame.locator('[data-follow-image-dots] button').count(), 13);
      assert.equal(await player.getAttribute('data-gallery-probe'), 'kept');
      const loadedFrame = await frame.boundingBox();
      assert.ok(normalFrame && loadedFrame && Math.abs(loadedFrame.height - normalFrame.height) < 2);
      assert.equal(await frame.locator('[data-item-samples-state]').count(), 0);
      await frame.getByRole('button', { name: '预览 1', exact: true }).click();
      await frame.locator('[data-jav-picture]').waitFor();
      assert.equal(await frame.locator('[data-jav-picture]').getAttribute('alt'), `${COVER_ONLY_ITEM.code} 预览 1`);
      assert.ok(await player.isHidden());
      await frame.getByRole('button', { name: '播放正片', exact: true }).click();
      assert.equal(await player.getAttribute('data-gallery-probe'), 'kept');
      assert.ok(await frame.locator('video.vjs-tech').evaluate(node => Math.abs((node as HTMLVideoElement).currentTime - 0.5) < 0.1));
      const imageReads = images;
      await frame.getByRole('button', { name: '预览 1', exact: true }).click();
      await frame.locator('[data-jav-picture]').waitFor();
      assert.equal(images, imageReads, '重复查看样图复用查询缓存');
      assert.equal(details, 1);
      assert.deepEqual(opened.problems, []);
      await page.screenshot({ path: `../build/item-source-gallery-${viewport.name}.png` });
    } finally { release(); await opened.close() }
  });
  it('来源样图加载失败时只在媒体区提供重试', { timeout: 60000 }, async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready: '[data-media-grid]' });
    const { page } = opened;
    let attempts = 0;
    try {
      await page.route('**/api/jav/detail?*', route => {
        attempts++;
        if (attempts > 1) assert.equal(new URL(route.request().url()).searchParams.get('refresh'), '1');
        return route.fulfill({ json: attempts === 1
          ? { code: COVER_ONLY_ITEM.code, state: 'partial', preview_count: 0, message: '来源暂不可用' }
          : { code: COVER_ONLY_ITEM.code, state: 'ready', preview_count: 1 } });
      });
      await page.route('**/api/jav/image?*', route => route.fulfill({ json: { ok: false, image: '', message: '图片未取得' } }));
      await page.goto(new URL(`/assets/${COVER_ONLY_ITEM.id}`, page.url()).href);
      const frame = page.locator('[data-item-gallery]');
      const retry = frame.getByRole('button', { name: '重试样图', exact: true });
      await retry.waitFor();
      assert.equal(await retry.getAttribute('title'), '来源暂不可用');
      const sideContent = page.locator('[data-stage-side-content]:not(.ui-skeleton-lines)');
      await sideContent.waitFor({ state: 'visible' });
      assert.ok(await sideContent.isVisible());
      assert.ok(await frame.locator('.video-js').isVisible());
      await retry.click();
      await frame.getByRole('button', { name: '预览 1', exact: true }).waitFor();
      assert.equal(attempts, 2);
    } finally { await opened.close() }
  });
});
