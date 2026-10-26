/* 沉浸模式的取流与舞台形状、地址栏与流会话、手机上的手势。
 *
 * 队列固定成演示库里的一条横屏加一条竖屏：`/api/items` 的随机抽样换成这两条，谁先谁后由
 * 起播 id 定。第一条用例里两条的 `/stream` 由用例在采样结束后放行，确保采到等待状态。
 * 手机那一条用 CDP 发真触摸：浏览器自己合成的 click 也会跟着来，700ms 忽略窗要在这里才验得到。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, visit, VIEWPORTS } from './harness.ts';
import { currentVideo, muteVideos, openImmerse, pickClips, pinQueue, settledClip } from './immerse-fixture.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const MOBILE = VIEWPORTS.find((viewport) => viewport.mobile)!;
const HOLD_MS = 1500;

type Item = { id: number; width: number; height: number };
type Sample = { wide: boolean; shown: boolean; slides: string[] };

/** 当前的舞台：形状、框与动作列、作者标题看不看得见，以及每一格的位移。 */
const sample = (page: Page) => page.evaluate((): Sample => ({
  wide: document.querySelector('[data-immerse-stage]')!.hasAttribute('data-wide'),
  shown: ['[data-immerse-stage]', '[data-immerse-actions]', '[data-immerse-ui]'].every((selector) =>
    getComputedStyle(document.querySelector(selector)!).visibility === 'visible'),
  slides: [...document.querySelectorAll<HTMLElement>('[data-immerse-track] [data-immerse-slide]')].map((slide) => slide.style.transform),
}));

/** 每 60ms 采一次，直到 `until` 成立或到点。 */
async function watch(page: Page, until: () => Promise<boolean>, ms: number) {
  const moments: Sample[] = [];
  const deadline = Date.now() + ms;
  while (Date.now() < deadline && !(await until())) {
    moments.push(await sample(page));
    await new Promise((resolve) => setTimeout(resolve, 60));
  }
  return moments;
}

/** 只剩一格、加载提示收起、这一格的 video 有画面可出。 */
const settled = (page: Page) => page.waitForFunction(() =>
  document.querySelectorAll('[data-immerse-track] [data-immerse-slide]').length === 1
  && Boolean(document.querySelector<HTMLElement>('[data-immerse-loader]')?.hidden)
  && (document.querySelector<HTMLVideoElement>('[data-immerse-track] [data-immerse-slide] video')?.readyState ?? 0) >= 2,
undefined, { timeout: 20_000 });

describe('沉浸模式', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  it('片源先问 stream-plan；首条出画前不露舞台，切片预加载时保持旧形状，出画那一刻才换', async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    const releases: (() => void)[] = [];
    try {
      const items: Item[] = await page.evaluate(async () =>
        (await (await fetch('/api/items?limit=60&offset=0&thumb=')).json()).items);
      const wide = items.find((item) => item.width > item.height);
      const tall = items.find((item) => item.height > item.width);
      assert.ok(wide && tall, '演示库里横屏、竖屏各要有一条');
      await page.route(/\/api\/items\?.*sort=rand/, (route) => route.fulfill({ json: { items: [wide, tall]
        .map((item) => items.find((full) => full.id === item.id)) } }));
      const planned: number[] = [];
      page.on('request', (request) => {
        const url = new URL(request.url());
        if (url.pathname === '/api/stream-plan') planned.push(Number(url.searchParams.get('id')));
      });
      const holds = new Map<number, { wait: Promise<void>; release: () => void }>();
      for (const item of [wide, tall]) {
        let release = () => {};
        const wait = new Promise<void>((resolve) => { release = resolve; });
        holds.set(item.id, { wait, release });
        releases.push(release);
      }
      await page.route(new RegExp(`/stream\\?id=(${wide.id}|${tall.id})&`), async (route) => {
        await holds.get(Number(new URL(route.request().url()).searchParams.get('id')))!.wait;
        await route.continue().catch(() => {});
      });
      // 这台机器旁边有人：新插进来的每条 video 一律静音。
      await page.addInitScript(() => {
        new MutationObserver(() => document.querySelectorAll('video').forEach((video) => { video.muted = true; }))
          .observe(document, { childList: true, subtree: true });
      });
      await page.goto(new URL(`/immerse?id=${wide.id}`, page.url()).href, { waitUntil: 'domcontentloaded' });
      await page.locator('[data-immerse-track] [data-immerse-slide] video.vjs-tech').waitFor({ state: 'attached' });

      // 首条出画前：框、动作列、作者标题一样都不露，出画时一次摆成横屏。
      const loaderHidden = () => page.evaluate(() => document.querySelector<HTMLElement>('[data-immerse-loader]')!.hidden);
      const loading = await watch(page, loaderHidden, HOLD_MS);
      assert.ok(loading.length > 3, `首条加载窗口里只采到 ${loading.length} 次`);
      for (const moment of loading) assert.equal(moment.shown, false, JSON.stringify(moment));
      holds.get(wide.id)!.release();
      await settled(page);
      assert.deepEqual(await sample(page), { wide: true, shown: true, slides: [''] });
      assert.deepEqual(planned, [wide.id]);

      await page.dispatchEvent('[data-immerse]', 'wheel', { deltaY: 100 });
      await page.waitForFunction(() => document.querySelectorAll('[data-immerse-track] [data-immerse-slide]').length === 2);
      const preloading = await watch(page, async () => false, HOLD_MS - 300);
      assert.ok(preloading.length > 3, `预加载窗口里只采到 ${preloading.length} 次`);
      for (const moment of preloading) {
        assert.deepEqual(moment, { wide: true, shown: true, slides: ['', 'translateY(100%)'] });
      }
      holds.get(tall.id)!.release();
      await settled(page);
      assert.deepEqual(await sample(page), { wide: false, shown: true, slides: [''] });
      assert.deepEqual(planned, [wide.id, tall.id]);
      assert.deepEqual(opened.problems, [], JSON.stringify(opened.problems));
    } finally {
      releases.forEach((release) => release());
      await opened.close();
    }
  });

  it('深链与刷新落回同一条；换条用 replace 写地址、每条只取一次详情；关闭时按会话取消读取', async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    try {
      const { wide, tall } = await pickClips(page);
      await pinQueue(page, [tall, wide]);
      await muteVideos(page);
      const details = new Map<number, number>();
      const plans: { id: number; session: string }[] = [];
      const cancels: string[] = [];
      page.on('request', (request) => {
        const url = new URL(request.url());
        const id = Number(url.searchParams.get('id'));
        if (url.pathname === '/api/item') details.set(id, (details.get(id) ?? 0) + 1);
        if (url.pathname === '/api/stream-plan') plans.push({ id, session: url.searchParams.get('session') ?? '' });
        if (url.pathname === '/api/stream-cancel') cancels.push(url.searchParams.get('session') ?? '');
      });
      const landed = (id: number) => page.waitForFunction((want) => location.search === `?id=${want}`, id);

      await openImmerse(page, tall.id);
      assert.equal(plans[0]?.id, tall.id);
      const depth = await page.evaluate(() => history.length);
      await page.keyboard.press('ArrowDown');
      await landed(wide.id);
      await settledClip(page);
      await page.keyboard.press('ArrowUp');
      await landed(tall.id);
      await settledClip(page);
      assert.equal(await page.evaluate(() => history.length), depth, '每划一下都进历史的话，后退键就废了');
      assert.deepEqual(Object.fromEntries(details), { [tall.id]: 1, [wide.id]: 1 });
      // 换走的那一格按自己的会话取消。
      assert.ok(cancels.includes(plans[0]!.session), JSON.stringify({ plans, cancels }));

      // 划到后面那一条再刷新：落回的是它，不是抽样里排第一的那条。
      await page.keyboard.press('ArrowDown');
      await landed(wide.id);
      await settledClip(page);
      const before = plans.length;
      await page.reload({ waitUntil: 'domcontentloaded' });
      await settledClip(page);
      assert.equal(plans[before]?.id, wide.id);
      assert.equal(new URL(page.url()).searchParams.get('id'), String(wide.id));

      const live = plans.at(-1)!.session;
      await page.evaluate(() => { delete document.documentElement.dataset.peachStreamCancel });
      await page.click('[data-immerse-close]');
      await page.waitForFunction(() => document.documentElement.dataset.peachStreamCancel !== undefined);
      assert.ok(cancels.includes(live), JSON.stringify({ live, cancels }));
      assert.equal(await page.locator('[data-immerse]').isHidden(), true);
      assert.equal(await page.locator(currentVideo).count(), 0);
      assert.equal(new URL(page.url()).pathname, '/');
      assert.deepEqual(opened.problems, [], JSON.stringify(opened.problems));
    } finally {
      await opened.close();
    }
  });

  it('手机 390 宽：竖划换条，横划拖进度（拖动中进度条变粗），同一半区双击快进且不切播放', async () => {
    const opened = await visit(browser, '/', MOBILE);
    const { page } = opened;
    try {
      const { wide, tall } = await pickClips(page);
      await pinQueue(page, [tall, wide]);
      await muteVideos(page);
      await openImmerse(page, tall.id);
      const cdp = await page.context().newCDPSession(page);
      const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', x = 0, y = 0) =>
        cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
      async function drag(from: [number, number], to: [number, number], end = true) {
        await touch('touchStart', ...from);
        for (let step = 1; step <= 8; step += 1) {
          await touch('touchMove', from[0] + (to[0] - from[0]) * step / 8, from[1] + (to[1] - from[1]) * step / 8);
        }
        if (end) await touch('touchEnd');
      }
      const media = () => page.evaluate((selector) => {
        const video = document.querySelector<HTMLVideoElement>(selector)!;
        return { time: video.currentTime, duration: video.duration, paused: video.paused };
      }, currentVideo);

      await drag([195, 640], [195, 300]);
      await page.waitForFunction((want) => location.search === `?id=${want}`, wide.id);
      await settledClip(page);

      await page.evaluate((selector) => {
        const video = document.querySelector<HTMLVideoElement>(selector)!;
        video.pause();
        video.currentTime = 0;
      }, currentVideo);
      const start = await media();
      await drag([100, 400], [100 + MOBILE.width / 4, 404], false);
      assert.equal(await page.locator('[data-immerse-bar][data-scrubbing]').count(), 1, '拖动中进度条没有变粗');
      assert.ok((await media()).time < 0.5, '拖动中不该 seek');
      await touch('touchEnd');
      const scrubbed = await media();
      assert.ok(Math.abs(scrubbed.time - start.duration / 4) < 0.5, JSON.stringify({ start, scrubbed }));
      assert.equal(await page.locator('[data-immerse-bar][data-scrubbing]').count(), 0);

      const tap = async () => { await touch('touchStart', 300, 400); await touch('touchEnd') };
      await tap();
      await tap();
      await new Promise((resolve) => setTimeout(resolve, 800));
      const seeked = await media();
      assert.ok(Math.abs(seeked.time - Math.min(seeked.duration, scrubbed.time + 10)) < 0.5, JSON.stringify({ scrubbed, seeked }));
      assert.equal(seeked.paused, true, '双击与随后合成的 click 都不该切播放');
      assert.deepEqual(opened.problems, [], JSON.stringify(opened.problems));
    } finally {
      await opened.close();
    }
  });
});
