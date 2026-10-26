/* 关注详情岛（`follow-detail`）在真浏览器里的行为：从列表进出、后退回详情再关掉保住筛选、深链、舞台在媒体框里挂
 * Video.js、多图轮播、合集与多媒体两种队列、写操作与撤销、隐藏与恢复、标签回列表、手机。
 * 灯箱收到整组图那一条在 `photo-lightbox.test.ts`。
 *
 * 桩数据见 `follow-fixture.ts`（`DETAIL` 那几种形态只经单条取数交回）；关注来源的真实抓取一次都不发。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { DETAIL, openFollowFeed } from './follow-fixture.ts';
import { layout, launch, VIEWPORTS } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const MOBILE = VIEWPORTS.find((viewport) => viewport.mobile)!;
const DETAIL_READY = '#stage[open] [data-follow-detail-side]';
/** 桩里的视频没有正片，播放器拿到空响应会报一条 VIDEOJS 错误；量的不是它。 */
const withoutPlayer = (problems: string[]) => problems.filter((line) => !line.includes('VIDEOJS'));
const pathIs = (page: Page, path: string) => page.waitForFunction(
  (wanted) => location.pathname === wanted, path, { timeout: 10_000 });

/** 记下这一页之后发出的关注列表请求与单条取数，分开数。 */
function watchFollowRequests(page: Page) {
  const seen = { list: 0, item: [] as string[] };
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname !== '/api/follow') return;
    if (url.searchParams.has('item')) seen.item.push(url.searchParams.get('item')!);
    else seen.list += 1;
  });
  return seen;
}

/** 换条前记下舞台节点并盯住骨架：原地换条时浮窗还是同一个节点，中途不回骨架，焦点留在浮窗里。
 *  顺带盯新播放器：Peach 控件（提示圆是其中一枚）装上之前，Video.js 原装的控制条与大播放键不该露出来。 */
const watchStage = (page: Page) => page.evaluate(() => {
  const watch = { stage: document.getElementById('stage'), skeleton: false, rawControls: false };
  const shown = (node: Element | null) => !!node && getComputedStyle(node).display !== 'none' && getComputedStyle(node).visibility !== 'hidden';
  new MutationObserver(() => {
    if (document.querySelector('#stage [data-skeleton="detail"]')) watch.skeleton = true;
    for (const player of document.querySelectorAll('#stage div.video-js:not(:has(.vjs-peach-bezel))')) {
      if (shown(player.querySelector('.vjs-control-bar')) || shown(player.querySelector('.vjs-big-play-button'))) watch.rawControls = true;
    }
  }).observe(document.body, { childList: true, subtree: true });
  Object.assign(window, { stageWatch: watch });
});
const stageKept = (page: Page) => page.evaluate(() => {
  const watch = (window as unknown as { stageWatch: { stage: Element | null; skeleton: boolean } }).stageWatch;
  return { same: document.getElementById('stage') === watch.stage, skeleton: watch.skeleton,
    focused: !!watch.stage?.contains(document.activeElement) };
});

const openDetail = (browser: Browser, id: number, viewport = DESKTOP) =>
  openFollowFeed(browser, `/posts/${id}`, viewport, { ready: DETAIL_READY });

describe('关注详情岛', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  it('列表点卡进详情：地址换成这一条，不再单条取数；关掉回列表不重取', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, '/follow', DESKTOP);
    try {
      const page = opened.page;
      const seen = watchFollowRequests(page);
      await page.locator('[data-follow-item="1002"] [data-follow-open]').click();
      await pathIs(page, '/posts/1002');
      await page.locator(DETAIL_READY).waitFor();
      assert.equal((await page.locator('#stage [data-follow-detail-name]').innerText()).trim(), '演示更新 1002');
      assert.deepEqual(seen.item, [], '列表缓存里有的这一条又单条取了一次');
      await page.locator('[data-follow-related] [data-follow-item]').first().waitFor();
      const listedBeforeClose = seen.list;
      await page.locator('#closeStage').click();
      await pathIs(page, '/follow');
      await page.locator('[data-follow-list] > [data-follow-item]').first().waitFor();
      assert.equal(seen.list, listedBeforeClose, '关掉详情回列表重取了一遍');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('筛过一档再进详情：后退回到详情再关掉，回到同一档筛选', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, '/follow?status=new', DESKTOP);
    try {
      const page = opened.page;
      const filtered = () => page.waitForFunction(
        () => location.pathname === '/follow' && location.search === '?status=new', undefined, { timeout: 10_000 });
      await page.locator('[data-follow-item="1002"] [data-follow-open]').click();
      await pathIs(page, '/posts/1002');
      await page.locator(DETAIL_READY).waitFor();
      await page.locator('#closeStage').click();
      await filtered();
      await page.goBack();
      await pathIs(page, '/posts/1002');
      await page.locator(DETAIL_READY).waitFor();
      await page.locator('#closeStage').click();
      await filtered();
      await page.locator('[data-follow-list] > [data-follow-item]').first().waitFor();
      assert.equal(await page.locator('#stage[open]').count(), 0);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('深链进详情只单条取这一条，不挂列表岛；内容到了焦点在关闭键上；刷新时只有浮窗里的骨架', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.collection);
    try {
      const page = opened.page;
      assert.equal(await page.locator('[data-follow-feed]').count(), 0, '深链进详情时挂了列表岛');
      assert.equal((await page.locator('#stage [data-follow-detail-name]').innerText()).trim(), '合集主条目 5101');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'closeStage');
      assert.deepEqual(withoutPlayer(opened.problems), []);

      // 刷新这一页：等的那一下只有浮窗里的骨架，页面里不先铺一份不在浮窗里的。
      await page.addInitScript(() => {
        const seen = { outside: false };
        Object.assign(window, { detailSkeletonSeen: seen });
        new MutationObserver(() => {
          for (const one of document.querySelectorAll('[data-skeleton="detail"]')) if (!one.closest('#stage')) seen.outside = true;
        }).observe(document, { childList: true, subtree: true });
      });
      let releaseItem = () => {};
      const itemWait = new Promise<void>((resolve) => { releaseItem = resolve; });
      await page.route((url) => url.pathname === '/api/follow' && url.searchParams.has('item'), async (route) => {
        await itemWait;
        await route.fallback();
      });
      try {
        await page.reload({ waitUntil: 'load' });
        const media = page.locator('#stage [data-skeleton="detail"] [data-stage-media]');
        await media.waitFor();
        const box = await media.boundingBox();
        assert.ok(box && box.height > 120, `详情等待画面高度不足：${JSON.stringify(box)}`);
        assert.equal(await page.locator('#stage [data-skeleton="detail"] [data-stage-side-content]')
          .evaluate((node) => getComputedStyle(node).rowGap), '12px');
      } finally {
        releaseItem();
      }
      await page.locator(DETAIL_READY).waitFor({ timeout: 15_000 });
      assert.equal(await page.evaluate(() => (window as unknown as { detailSkeletonSeen: { outside: boolean } })
        .detailSkeletonSeen.outside), false, '刷新时浮窗外先画了一份详情骨架');
    } finally {
      await opened.close();
    }
  });

  it('视频：舞台在媒体框里挂上 Video.js，氛围光画布与统计角标插进同一个媒体框；换一条与后退都原地换、只剩一个播放器', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.collection);
    try {
      const page = opened.page;
      const frame = page.locator('#stage [data-follow-detail-media="video"]');
      // 控制条在用户一段时间不动后会隐去（Video.js 的 userActive），只等它挂上，不等可见。
      await frame.locator('.video-js .vjs-control-bar').waitFor({ state: 'attached' });
      assert.equal(await frame.locator('[data-ambient-canvas]').count(), 1);
      assert.equal(await frame.locator('.video-js video.vjs-tech').count(), 1, '媒体框里的活动 video 不止一个');
      await watchStage(page);
      const seen = watchFollowRequests(page);
      await page.locator('[data-follow-queue-item="5102"]').click();
      await pathIs(page, '/posts/5102');
      await page.locator('#stage [data-follow-queue-item="5102"][aria-current="true"]').waitFor();
      await page.locator('#stage .video-js .vjs-control-bar').waitFor({ state: 'attached' });
      assert.equal(await page.locator('#stage .video-js').count(), 1, '换一条之后旧播放器没拆');
      assert.equal(await page.locator('#stage [data-ambient-canvas]').count(), 1);
      assert.deepEqual(await stageKept(page), { same: true, skeleton: false, focused: true }, '换一条重开了浮窗');
      await page.goBack();
      await pathIs(page, '/posts/5101');
      await page.locator('#stage [data-follow-queue-item="5101"][aria-current="true"]').waitFor();
      await page.locator('#stage .video-js .vjs-control-bar').waitFor({ state: 'attached' });
      assert.equal(await page.locator('#stage .video-js').count(), 1, '后退之后旧播放器没拆');
      assert.deepEqual(await stageKept(page), { same: true, skeleton: false, focused: true }, '后退到上一条重开了浮窗');
      assert.deepEqual(seen.item, [], '组里的另一条和取过的那一条又单条取了一次，点下去要等一个来回才换');
      assert.equal(await page.evaluate(() => (window as unknown as { stageWatch: { rawControls: boolean } }).stageWatch.rawControls),
        false, 'Peach 控件装上之前露出了 Video.js 原装的控制条或大播放键');
      await page.locator('#closeStage').click();
      await pathIs(page, '/follow');
      await page.locator('[data-follow-list] > [data-follow-item]').first().waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('.video-js').count(), 0, '关掉详情后播放器还挂着');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('多图轮播：圆点报当前那张，箭头与圆点换图不换地址', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.gallery);
    try {
      const page = opened.page;
      const current = () => page.locator('#stage [data-follow-image-dots] button').evaluateAll((dots) =>
        dots.map((dot) => dot.getAttribute('aria-current')));
      const poster = page.locator('#stage [data-follow-detail-poster]');
      assert.deepEqual(await current(), ['true', 'false', 'false']);
      assert.equal(await poster.getAttribute('src'), '/follow-stream?id=5001&media=0');
      await page.locator('#stage [data-follow-image-arrow="next"]').click();
      await page.waitForFunction(() => document.querySelector('#stage [data-follow-detail-poster]')
        ?.getAttribute('src') === '/follow-stream?id=5001&media=1');
      assert.deepEqual(await current(), ['false', 'true', 'false']);
      await page.locator('#stage [data-follow-image-arrow="prev"]').click();
      await page.locator('#stage [data-follow-image-dots] button').nth(2).click();
      await page.waitForFunction(() => document.querySelector('#stage [data-follow-detail-poster]')
        ?.getAttribute('src') === '/follow-stream?id=5001&media=2');
      assert.deepEqual(await current(), ['false', 'false', 'true']);
      assert.equal(new URL(page.url()).pathname, '/posts/5001');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('合集队列：只列可播视频、新到旧，当前那条按下；另一站那条的出处是站点图标', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.collection);
    try {
      const page = opened.page;
      const queue = page.locator('#stage [data-follow-queue][data-queue-kind="collection"]');
      const rows = await queue.locator('[data-follow-queue-item]').evaluateAll((buttons) => buttons.map((button) => ({
        id: Number((button as HTMLElement).dataset.followQueueItem), current: button.getAttribute('aria-current'),
        icon: button.querySelector('[data-follow-queue-meta] img')?.getAttribute('alt') ?? null,
      })));
      assert.deepEqual(rows, [
        { id: 5101, current: 'true', icon: null },
        { id: 5102, current: 'false', icon: null },
        { id: 5103, current: 'false', icon: 'Rule34.xxx' },
      ]);
      assert.match(await queue.locator('[data-mix-queue-head]').innerText(), /视频合集[\s\S]*3 个视频/);
      assert.equal(await queue.locator('[data-queue-close]').isVisible(), false, '并排布局的队列头不该有关闭键');
      /* 桌面上媒体在左、详情在右，底部队列横向排开。 */
      const grid = await page.evaluate(() => {
        const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
        const media = box('#stage [data-stage-media]'), side = box('#stage [data-follow-detail-side]');
        const queue = box('#stage [data-follow-queue]');
        return { sideRight: side.left >= media.right - 1, queueBelow: queue.top >= Math.max(media.bottom, side.bottom) - 1,
          flow: getComputedStyle(document.querySelector('#stage [data-follow-queue] [data-mix-list]')!).gridAutoFlow };
      });
      assert.deepEqual(grid, { sideRight: true, queueBelow: true, flow: 'column' });
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('多媒体队列：一帖里的图与视频各占一行，点图换成图片不换地址', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.media);
    try {
      const page = opened.page;
      const queue = page.locator('#stage [data-follow-queue][data-queue-kind="media"]');
      const kinds = await queue.locator('[data-follow-media-item]').evaluateAll((buttons) =>
        buttons.map((button) => [(button as HTMLElement).dataset.mediaKind, button.getAttribute('aria-current')]));
      assert.deepEqual(kinds, [['video', 'true'], ['image', 'false'], ['video', 'false']]);
      await queue.locator('[data-follow-media-item="1"]').click();
      await page.locator('#stage [data-follow-detail-media="image"] [data-follow-detail-poster]').waitFor();
      assert.equal(await queue.locator('[data-follow-media-item="1"]').getAttribute('aria-current'), 'true');
      assert.equal(await page.locator('#stage .video-js').count(), 0, '换成图片后播放器没拆');
      assert.equal(new URL(page.url()).pathname, '/posts/5201');
      // 当前是图，操作条里多一枚「隐藏这张图」。
      assert.equal(await page.locator('#stage [data-follow-media-hide="1"]').count(), 1);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('保存与状态：写进桩、键态跟着变；标记已看的回执能撤销，撤销后键态回原样', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.gallery);
    try {
      const page = opened.page;
      const seen = page.locator('#stage [data-follow-detail-status="seen"]');
      assert.equal(await seen.getAttribute('aria-pressed'), 'false');
      await seen.click();
      await page.locator('#stage [data-follow-detail-status="seen"][aria-pressed="true"]').waitFor();
      assert.equal(await page.locator('#stage [data-follow-detail-status="new"]').count(), 1, '已看之后没有「恢复未看」');
      // 回执栈在 body 下，舞台是模态 dialog，外面整页惰性，鼠标点不到撤销键；这里量的是撤销本身。
      await page.locator('#toasts').getByRole('button', { name: '撤销' }).dispatchEvent('click');
      await page.locator('#stage [data-follow-detail-status="seen"][aria-pressed="false"]').waitFor();
      await page.locator('#stage [data-follow-detail-save]').click();
      await page.locator('#stage [data-follow-detail-save][aria-label="已保存"]').waitFor();
      assert.deepEqual(opened.stub.writes, [
        { url: '/api/follow/status', body: { item: 5001, to: 'seen' } },
        { url: '/api/follow/status', body: { item: 5001, to: 'new' } },
        { url: '/api/follow/save', body: { item: 5001 } },
      ]);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of VIEWPORTS) it(`${viewport.name}：二十一张图的圆点可滚动并跟随当前图片，地址保持当前帖子`, { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.largeGallery, viewport);
    try {
      const page = opened.page;
      const poster = page.locator('#stage [data-follow-detail-poster]');
      const dots = page.locator('#stage [data-follow-image-dots]');
      const thumbnails = page.locator('#stage [data-media-thumbnails]');
      assert.equal(await thumbnails.locator('button').count(), 21);
      await thumbnails.locator('img').first().waitFor({ state: 'visible' });
      const thumbnailBox = await thumbnails.boundingBox(), posterBox = await poster.boundingBox();
      assert.ok(thumbnailBox && posterBox && thumbnailBox.y >= posterBox.y + posterBox.height, '小图位于主图下方');
      assert.equal(await dots.locator('button').count(), 21);
      assert.ok(await dots.evaluate(node => node.scrollWidth > node.clientWidth), '圆点超出时在条内滚动');
      assert.ok(await dots.evaluate(node => {
        const frame = node.closest('[data-media-indicator]');
        if (!frame) return false;
        const style = getComputedStyle(frame);
        return style.borderTopWidth === '0px' && style.backgroundColor === 'rgba(0, 0, 0, 0.5)'
          && style.borderRadius === getComputedStyle(document.documentElement).getPropertyValue('--pill-radius').trim();
      }), 'indicator 使用关注详情的半透明胶囊底衬');
      assert.equal(await dots.getAttribute('data-overflow-left'), 'false');
      assert.equal(await dots.getAttribute('data-overflow-right'), 'true');
      assert.ok(await dots.evaluate(node => getComputedStyle(node).maskImage.includes('linear-gradient')), '溢出端复用横向渐隐');
      await dots.hover();
      await page.mouse.wheel(0, 100);
      await page.waitForFunction(() => {
        const strip = document.querySelector('[data-follow-image-dots]');
        return (strip?.scrollLeft || 0) > 1 && strip?.getAttribute('data-overflow-left') === 'true';
      });
      assert.equal(await dots.getAttribute('data-overflow-left'), 'true');
      for (const [key, index] of [['ArrowRight', 1], ['ArrowLeft', 0], ['ArrowLeft', 20]] as const) {
        await page.keyboard.press(key);
        await page.waitForFunction((wanted) => document.querySelector('#stage [data-follow-detail-poster]')
          ?.getAttribute('src') === wanted, `/follow-stream?id=5002&media=${index}`);
        assert.equal(await poster.getAttribute('src'), `/follow-stream?id=5002&media=${index}`);
        const selected = dots.locator('[aria-current="true"]');
        assert.equal(await selected.getAttribute('data-follow-image-item'), String(index));
        assert.ok(await selected.evaluate(node => {
          const item = node.getBoundingClientRect(), strip = node.parentElement!.getBoundingClientRect();
          return item.left >= strip.left && item.right <= strip.right;
        }), '当前圆点完整可见');
        assert.ok(await thumbnails.locator('[aria-current="true"]').evaluate(node => {
          const item = node.getBoundingClientRect(), strip = node.parentElement!.getBoundingClientRect();
          return item.left >= strip.left && item.right <= strip.right;
        }), '当前小图完整可见');
        if (index === 20) assert.equal(await dots.getAttribute('data-overflow-right'), 'false');
      }
      await page.keyboard.press('ArrowRight');
      await dots.locator('[data-follow-image-item="0"][aria-current="true"]').waitFor();
      assert.ok(await dots.evaluate(node => node.scrollLeft < 1), '循环回第一张时圆点回到起点');
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1);
      assert.equal(new URL(page.url()).pathname, '/posts/5002');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('隐藏这张图退到恢复带，从恢复带点回来又回到轮播', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.hidden);
    try {
      const page = opened.page;
      const restores = () => page.locator('#stage [data-follow-media-restore]').evaluateAll((buttons) =>
        buttons.map((button) => Number((button as HTMLElement).dataset.followMediaRestore)));
      const dots = () => page.locator('#stage [data-follow-image-dots] button').count();
      assert.deepEqual(await restores(), [3, 4]);
      assert.equal(await dots(), 3);
      await page.locator('#stage [data-follow-media-hide="0"]').click();
      await page.waitForFunction(() => document.querySelectorAll('#stage [data-follow-media-restore]').length === 3);
      assert.deepEqual(await restores(), [0, 3, 4]);
      assert.equal(await dots(), 2);
      assert.match(await page.locator('#stage [data-follow-hidden-label]').innerText(), /已隐藏 3 张/);
      await page.locator('#stage [data-follow-media-restore="0"]').click();
      await page.waitForFunction(() => document.querySelectorAll('#stage [data-follow-media-restore]').length === 2);
      assert.equal(await page.locator('#stage [data-follow-detail-poster]').getAttribute('src'),
        '/follow-stream?id=5301&media=0', '恢复的那张没有回到舞台上');
      assert.deepEqual(opened.stub.writes, [
        { url: '/api/follow/media/hide', body: { item: 5301, media: 0, hidden: true } },
        { url: '/api/follow/media/hide', body: { item: 5301, media: 0, hidden: false } },
      ]);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('点详情里的标签回到带上这枚标签的列表', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.gallery);
    try {
      const page = opened.page;
      const tag = page.locator('#stage [data-follow-tag]').first();
      const name = (await tag.getAttribute('data-follow-tag'))!;
      await tag.click();
      await page.waitForFunction((wanted) => location.pathname === '/follow'
        && new URLSearchParams(location.search).get('tag') === wanted, name, { timeout: 10_000 });
      await page.locator('[data-follow-list] > [data-follow-item]').first().waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('#stage[open]').count(), 0);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('手机 390：详情不横向溢出，操作条按内容定宽，合集队列排在侧栏下面', { timeout: 60_000 }, async () => {
    const opened = await openDetail(browser, DETAIL.collection, MOBILE);
    try {
      const page = opened.page;
      assert.deepEqual((await layout(page)).offenders, [], '详情里有元素越出视口右边');
      const boxes = await page.evaluate(() => {
        const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
        const actions = box('#stage [data-follow-detail-actions]');
        const buttons = [...document.querySelectorAll('#stage [data-follow-detail-actions] > *')]
          .map((node) => node.getBoundingClientRect());
        return { actions: actions.width, used: buttons.at(-1)!.right - buttons[0]!.left,
          side: box('#stage [data-follow-detail-side]').bottom, queue: box('#stage [data-follow-queue]').top };
      });
      assert.ok(Math.abs(boxes.actions - boxes.used) <= 1, `操作条被撑宽了：${JSON.stringify(boxes)}`);
      assert.ok(boxes.queue >= boxes.side - 1, `合集队列不在侧栏下面：${JSON.stringify(boxes)}`);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`没有预览的关注详情保留完整画框（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openDetail(browser, DETAIL.bare, viewport);
      try {
        const { page } = opened;
        const placeholder = page.locator('#stage [data-follow-detail-placeholder]');
        await placeholder.waitFor();
        const box = await placeholder.evaluate(node => {
          const frame = node.closest('[data-stage-media]')!.getBoundingClientRect();
          const rect = node.getBoundingClientRect();
          return { width: rect.width, height: rect.height, frameWidth: frame.width, frameHeight: frame.height };
        });
        assert.ok(Math.abs(box.width - box.frameWidth) < 1, `空态占满媒体宽度：${JSON.stringify(box)}`);
        if (viewport.mobile) assert.ok(Math.abs(box.width / box.height - 16 / 9) < .02, `空态按画框比例留高：${JSON.stringify(box)}`);
        else assert.ok(box.height >= 180, `桌面空态完整显示图标与说明：${JSON.stringify(box)}`);
        assert.ok(box.frameHeight >= box.height - 1, '媒体框容纳完整空态');
        assert.equal(await page.locator('#closeStage').isVisible(), true);
        assert.deepEqual((await layout(page)).offenders, []);
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally { await opened.close(); }
    });

    it(`长摘要收在八行内、不被侧栏压扁，长串标题与媒体报错不撑宽侧栏（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openDetail(browser, DETAIL.long, viewport);
      try {
        const page = opened.page;
        const measure = () => page.evaluate(() => {
          const side = document.querySelector('#stage [data-stage-side-content]')!;
          const summary = document.querySelector<HTMLElement>('#stage [data-follow-detail-summary]')!;
          const lineHeight = parseFloat(getComputedStyle(summary).lineHeight);
          const right = side.getBoundingClientRect().right;
          const inside = ['[data-follow-detail-name]', '[data-follow-detail-summary]', '[data-follow-media-issue]']
            .map((selector) => document.querySelector(`#stage ${selector}`)!.getBoundingClientRect().right)
            .every((edge) => edge <= right + 1);
          return { lines: summary.clientHeight / lineHeight, inside, scroll: side.scrollWidth, width: side.clientWidth };
        });
        const folded = await measure();
        assert.ok(folded.lines >= 7.5 && folded.lines <= 8.5, `收起的摘要不是八行：${JSON.stringify(folded)}`);
        assert.ok(folded.inside && folded.scroll <= folded.width, `侧栏被撑宽：${JSON.stringify(folded)}`);
        const toggle = page.locator('#stage [data-follow-summary-toggle]');
        assert.equal((await toggle.innerText()).trim(), '展开');
        await toggle.click();
        const open = await measure();
        assert.ok(open.lines > 20, `展开后摘要没有全部露出：${JSON.stringify(open)}`);
        assert.ok(open.inside && open.scroll <= open.width, `展开后侧栏被撑宽：${JSON.stringify(open)}`);
        assert.deepEqual((await layout(page)).offenders, [], '详情里有元素越出视口右边');
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally {
        await opened.close();
      }
    });
  }
});
