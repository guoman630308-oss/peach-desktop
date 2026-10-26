/* 合成短片上的播放器皮肤几何与状态。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import { join } from 'node:path';
import type { Browser } from 'playwright-core';
import { launch, requiredEnv, visit, VIEWPORTS } from './harness.ts';

describe('播放器皮肤', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });

  it('首次加载模块期间保留海报，控制条从完整皮肤开始显示', { timeout: 60_000 }, async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve });
    const opened = await visit(browser, `/assets/${requiredEnv('PEACH_E2E_ITEM')}`, VIEWPORTS[0]!, async page => {
      await page.route('**/vendor/videojs/10.1.0/video-player.js', async route => { await gate; await route.continue() });
    });
    try {
      const video = opened.page.locator('#stage video.video-js');
      await video.waitFor();
      assert.equal(await video.evaluate((node: HTMLVideoElement) => node.controls), false, '模块加载期间不显示浏览器控制条');
      release();
      await opened.page.locator('#stage .vjs-peach-right-controls').waitFor();
      assert.equal(await opened.page.locator('#stage .vjs-control-bar').evaluate(bar => getComputedStyle(bar).visibility), 'visible');
      assert.equal(await opened.page.locator('#stage video.vjs-tech').evaluate((node: HTMLVideoElement) => node.controls), false);
      assert.deepEqual(opened.problems, []);
    } finally { release(); await opened.close() }
  });

  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：控制组贴右、进度圆点唯一、全屏保留控件`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, `/assets/${requiredEnv('PEACH_E2E_ITEM')}`, viewport, async page => {
        await page.addInitScript(theme => localStorage.setItem('peach.settings.v1',
          JSON.stringify({ detailAutoplay: false, theme })), viewport.mobile ? 'dark' : 'light');
      });
      const { page, problems } = opened;
      try {
        await page.locator('#stage .vjs-peach-bezel').waitFor({ state: 'attached' });
        await page.screenshot({ path: join(process.cwd(), `../build/player-skin-${viewport.name}.png`) });
        const geometry = await page.locator('#stage .video-js').evaluate(root => {
          const bar = root.querySelector('.vjs-control-bar')!.getBoundingClientRect();
          const group = root.querySelector('.vjs-peach-right-controls')!.getBoundingClientRect();
          const slider = root.querySelector('.vjs-progress-holder')!;
          const thumb = slider.querySelector('media-slider-thumb')!;
          return { rightGap: bar.right - group.right, thumbOpacity: getComputedStyle(thumb).opacity,
            progressDot: getComputedStyle(slider.querySelector('.vjs-play-progress')!, '::before').content };
        });
        assert.ok(Math.abs(geometry.rightGap) < 1, `右侧控制组离右边缘 ${geometry.rightGap}px`);
        assert.equal(geometry.thumbOpacity, '1', '进度圆点常驻');
        assert.equal(geometry.progressDot, 'none', '进度仅由一枚滑块显示圆点');
        if (!viewport.mobile) {
          await page.locator('#stage .vjs-volume-panel').hover();
          const volume = (await page.locator('#stage .vjs-volume-panel').boundingBox())!;
          assert.equal(volume.width, 111, '音量胶囊展开为 111px');
          const right = (await page.locator('#stage .vjs-peach-right-controls').boundingBox())!;
          const bar = (await page.locator('#stage .vjs-control-bar').boundingBox())!;
          assert.ok(Math.abs(right.x + right.width - bar.x - bar.width) < 1);
        }
        // 演示库的进度预览使用合成图片，不依赖后台抽帧完成时间。
        await page.route('**/poster?**', route => route.fulfill({ contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270"><rect width="480" height="270" fill="#888"/></svg>' }));
        await page.waitForFunction(() => document.querySelector<HTMLVideoElement>('#stage video.vjs-tech')!.readyState >= 2);
        const progress = (await page.locator('#stage .vjs-progress-control').boundingBox())!;
        await page.mouse.click(progress.x + progress.width / 2, progress.y + 3);
        await page.waitForFunction(() => document.querySelector<HTMLVideoElement>('#stage video.vjs-tech')!.currentTime > .5);
        const dot = await page.locator('#stage .vjs-progress-thumb').evaluate(thumb => {
          const box = thumb.getBoundingClientRect();
          return { width: box.width, height: box.height, color: getComputedStyle(thumb).backgroundColor,
            fill: getComputedStyle(thumb.parentElement!.querySelector('.vjs-play-progress')!).backgroundColor };
        });
        assert.ok(Math.abs(dot.width - dot.height) < .1, '进度圆点保持正圆');
        assert.equal(dot.color, dot.fill, '进度圆点与已播段使用同一颜色');
        if (viewport.mobile) await page.locator('#stage [data-player-expand]').click();
        await page.locator('#stage media-fullscreen-button').click();
        await page.waitForFunction(() => !!document.fullscreenElement);
        const fullscreen = await page.locator('#stage media-container').evaluate(container => {
          const frame = container.getBoundingClientRect();
          const play = container.querySelector('media-play-button')!.getBoundingClientRect();
          const full = container.querySelector('media-fullscreen-button')!.getBoundingClientRect();
          return { frame: [frame.width, frame.height], viewport: [innerWidth, innerHeight],
            controlsInside: play.y >= 0 && full.right <= innerWidth && play.bottom <= innerHeight };
        });
        assert.deepEqual(fullscreen.frame, fullscreen.viewport);
        assert.equal(fullscreen.controlsInside, true);
        await page.locator('#stage media-fullscreen-button').click();
        await page.waitForFunction(() => !document.fullscreenElement);
        assert.deepEqual(problems, []);
      } finally { await opened.close() }
    });
  }
});
