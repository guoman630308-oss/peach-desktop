/* 沉浸模式用例共用的夹具：从演示库里挑一条横屏、一条竖屏，把随机抽样钉成这几条，静音打开。
 *
 * `/api/items?sort=rand` 每次都是一次新的随机抽样，钉住之后谁先谁后由起播 id 与给的顺序定。 */
import assert from 'node:assert/strict';

import type { Page } from 'playwright-core';

export type Clip = { id: number; width: number; height: number; duration: number; cost?: string; [field: string]: unknown };

/** 演示库里能直接放的一条横屏与一条竖屏（整条卡片，原样交给抽样）。 */
export async function pickClips(page: Page): Promise<{ wide: Clip; tall: Clip; all: Clip[] }> {
  const items: Clip[] = await page.evaluate(async () =>
    (await (await fetch('/api/items?limit=60&offset=0&thumb=')).json()).items);
  const all = items.filter((item) => item.duration && item.cost !== 'metered');
  const wide = all.find((item) => item.width > item.height);
  const tall = all.find((item) => item.height > item.width);
  assert.ok(wide && tall, '演示库里横屏、竖屏各要有一条');
  return { wide, tall, all };
}

/** 随机抽样只回这几条，按给的顺序。 */
export const pinQueue = (page: Page, clips: Clip[]) =>
  page.route(/\/api\/items\?.*sort=rand/, (route) => route.fulfill({ json: { items: clips } }));

/** 这台机器旁边有人：新插进来的每条 video 一律静音。 */
export const muteVideos = (page: Page) => page.addInitScript(() => {
  new MutationObserver(() => document.querySelectorAll('video').forEach((video) => { video.muted = true; }))
    .observe(document, { childList: true, subtree: true });
});

/** 只剩一格、加载提示收起、这一格的 video 有画面可出。 */
export const settledClip = (page: Page) => page.waitForFunction(() =>
  document.querySelectorAll('[data-immerse-track] [data-immerse-slide]').length === 1
  && Boolean(document.querySelector<HTMLElement>('[data-immerse-loader]')?.hidden)
  && (document.querySelector<HTMLVideoElement>('[data-immerse-track] [data-immerse-slide] video')?.readyState ?? 0) >= 2,
undefined, { timeout: 20_000 });

/** 深链进沉浸模式，等第一条出画。 */
export async function openImmerse(page: Page, id: number): Promise<void> {
  await page.goto(new URL(`/immerse?id=${id}`, page.url()).href, { waitUntil: 'domcontentloaded' });
  await settledClip(page);
}

/** 当前出画那一格的 video。 */
export const currentVideo = '[data-immerse-track] [data-immerse-slide] video.vjs-tech';
