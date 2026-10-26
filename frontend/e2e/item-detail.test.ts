/* 作品详情岛（`item-detail`）在真浏览器里的行为：从目录卡进出、深链、后退回到详情与刷新后关掉回哪一页、
 * 播放列表换条后关掉回列表页、舞台在媒体框里挂 Video.js、
 * 脱盘说明、共演收起、标题折叠、评分与标签的撤销、反馈键态、四种队列、播放列表排序与移出、保存 Mix、接着看、
 * 实体页入口与手机布局。
 *
 * 桩数据见 `item-fixture.ts`；读写接口全部由桩接住，演示库数据库一次都不写。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { layout, launch, VIEWPORTS } from './harness.ts';
import { CATALOG, EDITIONS, ITEM, NAMES, PLAYLIST, RELATED, openItemPage } from './item-fixture.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const MOBILE = VIEWPORTS.find((viewport) => viewport.mobile)!;
const DETAIL_READY = '#stage[open] [data-item-side]';
/** 主网格卡点标题打开分卷；竖屏带中的同条目卡点标题进入沉浸。 */
const PART_CARD = `#grid [data-media-card][data-variant="grid"][data-id="${ITEM.part}"] [data-media-text] > button[data-media-title][data-open]`;
/** 桩里的片源没有正片，播放器拿到空响应会报一条 VIDEOJS 错误；量的不是它。 */
const withoutPlayer = (problems: string[]) => problems.filter((line) => !line.includes('VIDEOJS'));
const pathIs = (page: Page, path: string) => page.waitForFunction(
  (wanted) => (wanted.includes('?') ? location.pathname + location.search : location.pathname) === wanted, path, { timeout: 10_000 });
/** 回执栈在 body 下，舞台是模态 dialog，外面整页惰性，鼠标点不到撤销键；这里量的是撤销本身。 */
const undo = (page: Page) => page.locator('#toasts').getByRole('button', { name: '撤销' }).last().dispatchEvent('click');
const current = (page: Page) => page.locator('#stage [data-queue-item][aria-current="true"]').getAttribute('data-queue-item');
const ids = (page: Page, selector: string, attribute: string) => page.locator(selector).evaluateAll(
  (nodes, name) => nodes.map((node) => Number(node.getAttribute(name))), attribute);


/** 换条前记下舞台节点并盯住骨架：原地换条时浮窗还是同一个节点，中途不回骨架，焦点留在浮窗里。 */
const watchStage = (page: Page) => page.evaluate(() => {
  const watch = { stage: document.getElementById('stage'), skeleton: false, heights: [] as number[] };
  const sample = () => {
    const media = document.querySelector('#stage [data-stage-media]');
    if (media) watch.heights.push(media.getBoundingClientRect().height);
    if (watch.heights.length < 120) requestAnimationFrame(sample);
  }; sample();
  new MutationObserver(() => { if (document.querySelector('#stage [data-skeleton="detail"]')) watch.skeleton = true })
    .observe(document.body, { childList: true, subtree: true });
  Object.assign(window, { stageWatch: watch });
});
const stageKept = (page: Page) => page.evaluate(() => {
  const watch = (window as unknown as { stageWatch: { stage: Element | null; skeleton: boolean; heights: number[] } }).stageWatch;
  if (Math.max(...watch.heights) - Math.min(...watch.heights) > 1)
    throw new Error(`作品切换画框高度不稳定：${JSON.stringify(watch.heights)}`);
  return { same: document.getElementById('stage') === watch.stage, skeleton: watch.skeleton,
    focused: !!watch.stage?.contains(document.activeElement) };
});

describe('作品详情岛', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  for (const count of [1, 2, 3]) {
    it(`${count} 张版本卡按内容分配空间，推荐卡与版本卡等宽对齐`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, '/', DESKTOP, { ready: '#grid [data-media-card]' });
      try {
        const page = opened.page;
        await page.route(url => url.pathname === '/api/editions', route => route.fulfill({ json: {
          ...EDITIONS, items: Array.from({ length: count }, (_, index) => ({ ...EDITIONS.items[index % 2], id: 31 + index })),
        } }));
        await page.goto(new URL(`/assets/${ITEM.edition}?editions=${ITEM.edition}`, page.url()).toString());
        await page.locator('#stage [data-queue-row]').first().waitFor();
        await page.locator('#stage [data-item-related] [data-media-card]').first().waitFor();
        const measured = await page.locator('[data-detail-continuation]').evaluate(node => {
          const queue = node.querySelector('[data-mix-queue]')!.getBoundingClientRect();
          const cards = [...node.querySelectorAll('[data-queue-row]')].map(card => card.getBoundingClientRect());
          const related = node.querySelector('[data-item-related] [data-media-card]')!.getBoundingClientRect();
          return { width: queue.width, full: node.getBoundingClientRect().width, cards: cards.map(card => ({ width: card.width, y: card.y })), related: { width: related.width, y: related.y } };
        });
        const width = measured.cards[0]!.width;
        if (count < 3) assert.ok(Math.abs(measured.width - (count * width + (count - 1) * 10 + 28)) < 1, JSON.stringify(measured));
        else assert.ok(Math.abs(measured.width - measured.full / 2) < 1, JSON.stringify(measured));
        assert.ok(measured.cards.every(card => Math.abs(card.width - measured.related.width) < 1 && Math.abs(card.y - measured.related.y) < 1), JSON.stringify(measured));
        if (count === 2) await page.screenshot({ path: '../build/detail-two-versions.png' });
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally { await opened.close() }
    });
  }

  for (const destination of ['/tasks', '/performers']) {
    it(`分卷等待响应时后退到 ${destination}：舞台撤回，晚到的响应不能改变地址或历史深度`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, '/', DESKTOP, { ready: PART_CARD, catalogIds: [...CATALOG, ITEM.part] });
      let release = () => {};
      try {
        const page = opened.page;
        await page.evaluate(async (path) => {
          const moduleUrl = '/dist/peach-app.js';
          const { peachHistory } = await import(moduleUrl);
          peachHistory.push(path);
        }, destination);
        await pathIs(page, destination);
        await page.evaluate(async () => { const moduleUrl = '/dist/peach-app.js'; (await import(moduleUrl)).peachHistory.push('/') });
        await page.locator(PART_CARD).waitFor();
        const depth = await page.evaluate(() => history.length);
        let arrived = () => {};
        const requested = new Promise<void>(resolve => { arrived = resolve });
        const gate = new Promise<void>(resolve => { release = resolve });
        let finished = () => {};
        const responded = new Promise<void>(resolve => { finished = resolve });
        await page.route((url) => url.pathname === '/api/parts', async route => {
          arrived();
          await gate;
          try { await route.fallback() }
          catch (error) { assert.match(route.request().failure()?.errorText || '', /ERR_ABORTED/, String(error)) }
          finally { finished() }
        });
        await page.locator(PART_CARD).click();
        await requested;
        await page.locator('#stage[open]').waitFor();
        assert.equal(await page.evaluate(() => location.pathname), '/');
        assert.equal(await page.evaluate(() => history.length), depth, '队列未取齐时没有临时历史条目');
        await page.goBack();
        await pathIs(page, destination);
        await page.waitForFunction(() => !document.querySelector('#stage[open]'));
        const seq = await page.evaluate(async () => { const moduleUrl = '/dist/peach-app.js'; return (await import(moduleUrl)).peachHistory.navigation.seq });
        release();
        await responded;
        await page.waitForTimeout(500);
        assert.equal(await page.locator('#stage[open]').count(), 0);
        assert.equal(await page.evaluate(() => location.pathname), destination);
        assert.equal(await page.evaluate(() => history.length), depth);
        assert.equal(await page.evaluate(async () => { const moduleUrl = '/dist/peach-app.js'; return (await import(moduleUrl)).peachHistory.navigation.seq }), seq);
        assert.deepEqual(opened.stub.writes, []);
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally { release(); await opened.close() }
    });
  }

  it('点分卷的同一任务里重放源页 POP：队列意图不取数、不铺舞台、不写条目', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/', DESKTOP, { ready: PART_CARD, catalogIds: [...CATALOG, ITEM.part] });
    try {
      const page = opened.page;
      const depth = await page.evaluate(() => history.length);
      await page.locator(PART_CARD).evaluate(node => {
        (node as HTMLElement).click();
        window.dispatchEvent(new PopStateEvent('popstate', { state: history.state }));
      });
      await page.waitForTimeout(500);
      assert.equal(opened.stub.reads.get('/api/parts') || 0, 0);
      assert.equal(await page.locator('#stage[open]').count(), 0);
      assert.deepEqual(await page.evaluate(() => [location.pathname, history.length]), ['/', depth]);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally { await opened.close() }
  });

  it('首次舞台宿主接上时重放源页 POP：包已到货的旧队列请求也不能开舞台', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/', DESKTOP, { ready: PART_CARD, catalogIds: [...CATALOG, ITEM.part] });
    try {
      const page = opened.page;
      assert.equal(await page.locator('[data-stage-host]').count(), 0);
      const depth = await page.evaluate(() => history.length);
      await page.evaluate(() => {
        const append = document.body.append.bind(document.body);
        document.body.append = (...nodes: (Node | string)[]) => {
          append(...nodes);
          if (nodes.some(node => node instanceof HTMLElement && node.hasAttribute('data-stage-host'))) {
            document.body.append = append;
            document.body.dataset.queueStagePop = '1';
            window.dispatchEvent(new PopStateEvent('popstate', { state: history.state }));
          }
        };
      });
      await page.locator(PART_CARD).click();
      await page.locator('body[data-queue-stage-pop]').waitFor({ state: 'attached' });
      await page.waitForTimeout(500);
      assert.equal(opened.stub.reads.get('/api/parts') || 0, 0);
      assert.equal(await page.locator('#stage[open]').count(), 0);
      assert.deepEqual(await page.evaluate(() => [location.pathname, history.length]), ['/', depth]);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally { await opened.close() }
  });

  it('目录点卡进详情：地址换成这一条；关掉回列表不重取', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/', DESKTOP, { ready: `#grid [data-media-card][data-id="${ITEM.plain}"]` });
    try {
      const page = opened.page;
      await page.emulateMedia({reducedMotion:'no-preference'});
      await page.route(/\/api\/item\?/, async route => {
        await new Promise(resolve => setTimeout(resolve, 400));
        await route.fallback();
      });
      await page.evaluate(() => {
        const heights: number[] = [];
        Object.assign(window, { coldCatalogHeights: heights });
        const sample = () => {
          const dialog = document.querySelector<HTMLDialogElement>('#stage[open]');
          if (dialog) heights.push(dialog.clientHeight);
          if (heights.length < 180) requestAnimationFrame(sample);
        }; requestAnimationFrame(sample);
      });
      const listed = opened.stub.reads.get('/api/items') || 0;
      const backgroundPlayState=()=>page.locator('.top .search').first().evaluate(
        node=>getComputedStyle(node).animationPlayState);
      const initialPlayState=await backgroundPlayState();
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"] [data-media-title]`).first().click();
      await pathIs(page, `/assets/${ITEM.plain}`);
      await page.locator(DETAIL_READY).waitFor();
      const heights = await page.evaluate(() => (window as unknown as { coldCatalogHeights: number[] }).coldCatalogHeights);
      assert.ok(heights.length > 5 && Math.max(...heights) - Math.min(...heights) < 1, JSON.stringify(heights));
      assert.ok((await backgroundPlayState()).split(',').every(state=>state.trim()==='paused'));
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'closeStage', '内容到了焦点不在关闭键上');
      await page.locator('#closeStage').click();
      await pathIs(page, '/');
      await page.locator('#stage[open]').waitFor({ state: 'detached', timeout: 10_000 }).catch(() => {});
      assert.equal(await page.locator('#stage[open]').count(), 0);
      assert.equal(await backgroundPlayState(),initialPlayState,'关闭详情恢复背景动画状态');
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"]`).first().waitFor();
      assert.equal(opened.stub.reads.get('/api/items') || 0, listed, '关掉详情回列表重取了一遍');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('深链进详情：单条取这一条；关掉之后照地址补画列表', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      assert.equal(opened.stub.reads.get('/api/item'), 1);
      await page.locator('#closeStage').click();
      await pathIs(page, '/');
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"]`).first().waitFor({ timeout: 15_000 });
      assert.ok((opened.stub.reads.get('/api/items') || 0) >= 1, '深链关掉之后没有补取列表');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('后退回到详情再关掉：回到这一条打开时压着的那一页，地址与画面一致', { timeout: 90_000 }, async () => {
    const opened = await openItemPage(browser, '/trash', DESKTOP, { ready: `#grid [data-media-card][data-id="${ITEM.plain}"]` });
    try {
      const page = opened.page;
      const openCard = async (id: number) => {
        await page.locator(`#grid [data-media-card][data-id="${id}"] [data-media-title]`).first().click();
        await pathIs(page, `/assets/${id}`);
        await page.locator(DETAIL_READY).waitFor();
      };
      await openCard(ITEM.plain);
      await page.locator('#closeStage').click();
      await pathIs(page, '/trash');
      await page.locator('[data-nav=""]:visible').first().click();
      await pathIs(page, '/');
      await page.locator(`#grid [data-media-card][data-id="${ITEM.cast}"]`).first().waitFor({ timeout: 15_000 });
      await openCard(ITEM.cast);
      for (const path of ['/', '/trash', `/assets/${ITEM.plain}`]) {
        await page.goBack();
        await pathIs(page, path);
      }
      await page.locator(DETAIL_READY).waitFor();
      await page.locator('#closeStage').click();
      await pathIs(page, '/trash');
      await page.locator('#stage[open]').waitFor({ state: 'detached', timeout: 10_000 }).catch(() => {});
      assert.equal(await page.locator('#stage[open]').count(), 0);
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"]`).first().waitFor();
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('刷新落在详情上不读条目记的来处：下面补画目录网格，关掉回首页', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/trash', DESKTOP, { ready: `#grid [data-media-card][data-id="${ITEM.plain}"]` });
    try {
      const page = opened.page;
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"] [data-media-title]`).first().click();
      await pathIs(page, `/assets/${ITEM.plain}`);
      await page.locator(DETAIL_READY).waitFor();
      assert.deepEqual(await page.evaluate(() => history.state?.usr?.backgroundLocation), { pathname: '/trash', search: '' });
      await page.reload({ waitUntil: 'load' });
      await page.locator(DETAIL_READY).waitFor({ timeout: 15_000 });
      await page.locator('#closeStage').click();
      await pathIs(page, '/');
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"]`).first().waitFor({ timeout: 15_000 });
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('播放列表里换条再关掉回到列表页并重读；后退回到其中一条再关掉也一样', { timeout: 90_000 }, async () => {
    const opened = await openItemPage(browser, '/', DESKTOP, { ready: `#grid [data-media-card][data-id="${ITEM.plain}"]` });
    try {
      const page = opened.page;
      let listed = 0;
      // 桩里的列表行没有续播位置，卡片不给打开键；这里补上停在第一条。
      await page.route((url) => url.pathname === '/api/playlists', (route) => {
        listed += 1;
        return route.fulfill({ json: { items: [{
          id: PLAYLIST.id, name: PLAYLIST.name, item_count: PLAYLIST.items.length,
          current_asset_id: PLAYLIST.items[0], preview_asset_id: PLAYLIST.items[0],
        }] } });
      });
      await page.goto(new URL('/playlists', page.url()).toString(), { waitUntil: 'load' });
      const card = page.locator(`[data-playlist-card="${PLAYLIST.id}"]`);
      const step = async (id: number) => {
        await page.locator(`#stage [data-queue-item="${id}"]`).click();
        await pathIs(page, `/assets/${id}?playlist=${PLAYLIST.id}`);
        await page.locator(`#stage [data-queue-item="${id}"][aria-current="true"]`).waitFor();
      };
      const enter = async () => {
        await card.locator('[data-mix-open]').click();
        await pathIs(page, `/assets/${PLAYLIST.items[0]}?playlist=${PLAYLIST.id}`);
        await page.locator(DETAIL_READY).waitFor();
        await step(PLAYLIST.items[1]!);
        await step(PLAYLIST.items[2]!);
      };
      const closeToList = async () => {
        const before = listed;
        await page.locator('#closeStage').click();
        await pathIs(page, '/playlists');
        await page.locator('#stage[open]').waitFor({ state: 'detached', timeout: 10_000 }).catch(() => {});
        await card.waitFor();
        assert.equal(await page.locator('#stage[open]').count(), 0);
        for (const deadline = Date.now() + 10_000; listed <= before && Date.now() < deadline;) await page.waitForTimeout(50);
        assert.ok(listed > before, '关掉之后列表页没有重读');
      };
      await card.waitFor({ timeout: 15_000 });
      await enter();
      await closeToList();
      await enter();
      await page.goBack();
      await pathIs(page, `/assets/${PLAYLIST.items[1]}?playlist=${PLAYLIST.id}`);
      await page.locator(`#stage [data-queue-item="${PLAYLIST.items[1]}"][aria-current="true"]`).waitFor();
      await page.locator(DETAIL_READY).waitFor();
      await closeToList();
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('舞台在媒体框里挂上 Video.js，氛围光画布与统计角标跟它进同一块媒体区', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#stage [data-item-media="video"] > .video-js video.vjs-tech').waitFor({ state: 'attached' });
      const shape = await page.evaluate(() => {
        const media = document.querySelector('#stage [data-item-media="video"]')!;
        return { id: media.querySelector(':scope > .video-js')!.id, canvas: !!media.querySelector(':scope > [data-ambient-canvas]'),
          stats: !!media.querySelector('#playerStatsBtn'), videos: media.querySelectorAll('video').length };
      });
      assert.deepEqual(shape, { id: 'vid', canvas: true, stats: true, videos: 1 });
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('来源不可读取：说明块代替播放器，刷新状态后显示读取结果', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.offline}`, DESKTOP);
    try {
      const page = opened.page;
      const gate = page.locator('#stage [data-item-gate="offline"]');
      assert.match(await gate.innerText(), /媒体目录无法访问/);
      assert.equal(await page.locator('#stage .vjs-tech').count(), 0, '脱盘的条目挂了播放器');
      await page.locator('#offlineRetry').click();
      await page.locator('#offlineRetry', { hasText: '仍无法读取 · 刷新' }).waitFor();
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`${viewport.mobile ? '手机' : '桌面'}来源状态：权限原因可见，未取得状态不判脱盘`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, `/assets/${ITEM.offline}`, viewport);
      try {
        const page = opened.page;
        let online: boolean | null = false;
        await page.route((url) => url.pathname === '/api/sources', (route) => route.fulfill({ json: {
          sources: [{ location: 'local', online: true }, { location: '115', online,
            state: online === false ? 'permission_denied' : 'timeout',
            message: online === false ? 'CloudDrive · 115：没有权限读取' : 'CloudDrive · 115：探测未返回' }],
        } }));
        await page.reload({ waitUntil: 'load' });
        await page.locator(DETAIL_READY).waitFor();
        assert.match(await page.locator('#stage [data-item-gate="offline"]').innerText(), /没有权限读取/);
        online = null;
        await page.reload({ waitUntil: 'load' });
        await page.locator(DETAIL_READY).waitFor();
        assert.equal(await page.locator('#stage [data-item-gate="offline"]').count(), 0);
        assert.equal(await page.locator('body.offline-source').count(), 0);
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally {
        await opened.close();
      }
    });
  }

  for (const viewport of [DESKTOP, MOBILE]) {
  it(`${viewport.name}：出演超过 8 位先收起，点「还有 N 位」全部展开`, { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.cast}`, viewport);
    try {
      const page = opened.page;
      const shown = () => page.locator('#stage [data-item-identity] [data-entity-kind="performer"]:visible').count();
      assert.equal(await shown(), 8);
      assert.equal((await page.locator('#castMore').innerText()).trim(), `还有 ${NAMES.length - 8} 位`);
      const button = page.getByRole('button', { name: `还有 ${NAMES.length - 8} 位` });
      const appearance = await button.evaluate((node) => {
        const style = getComputedStyle(node);
        return { border: style.borderTopStyle, width: parseFloat(style.borderTopWidth),
          background: style.backgroundColor, icon: !!node.querySelector('svg'), height: node.getBoundingClientRect().height };
      });
      assert.equal(appearance.border, 'solid');
      assert.ok(appearance.width >= 1);
      assert.notEqual(appearance.background, 'rgba(0, 0, 0, 0)');
      assert.equal(appearance.icon, true);
      assert.ok(appearance.height >= 32);
      const centers = await button.evaluate((node) => {
        const own = node.getBoundingClientRect();
        const siblings = [...node.parentElement!.querySelectorAll<HTMLElement>('[data-id-cell]:not([hidden])')]
          .map((cell) => cell.getBoundingClientRect()).filter((cell) => cell.top < own.bottom && cell.bottom > own.top);
        return { margin: getComputedStyle(node).marginTop,
          delta: siblings.map((cell) => own.top + own.height / 2 - cell.top - cell.height / 2) };
      });
      assert.equal(centers.margin, '0px', '展开按钮应在出演名单行内垂直居中');
      assert.ok(centers.delta.every((delta) => Math.abs(delta) < 1), JSON.stringify(centers));
      await button.focus();
      await page.keyboard.press('Enter');
      assert.equal(await shown(), NAMES.length);
      assert.equal(await page.locator('#castMore').count(), 0);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });
  }

  it('标题折成两行，真溢出才给展开键；展开键与标题文字都能展开，再点收回', { timeout: 60_000 }, async () => {
    const short = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      assert.equal(await short.page.locator('#stage [data-title-fold]').isHidden(), true);
    } finally {
      await short.close();
    }
    const opened = await openItemPage(browser, `/assets/${ITEM.long}`, DESKTOP);
    try {
      const page = opened.page;
      const title = page.locator('#stage [data-detail-title]');
      const fold = page.locator('#stage [data-title-fold]');
      const lines = () => title.evaluate((node) => Math.round(node.clientHeight / parseFloat(getComputedStyle(node).lineHeight)));
      assert.equal(await fold.isVisible(), true);
      assert.equal(await lines(), 2);
      await fold.click();
      assert.equal(await fold.getAttribute('aria-label'), '收起标题');
      assert.ok(await lines() > 2, '展开之后整段标题都在');
      await title.click();
      assert.equal(await fold.getAttribute('aria-label'), '展开标题');
      assert.equal(await lines(), 2);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('评分：点当前那一颗是撤销评分；回执的撤销写回原分', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      const value = page.locator('#detailRating [data-rating-value]');
      assert.equal((await value.innerText()).trim(), '3 星');
      await page.locator('#detailRating [data-rate="3"]').click();
      await page.locator('#detailRating [data-rating-value]', { hasText: '未评分' }).waitFor();
      assert.equal(await page.locator('#detailRating [data-on="true"]').count(), 0);
      await undo(page);
      await page.locator('#detailRating [data-rating-value]', { hasText: '3 星' }).waitFor();
      assert.deepEqual(opened.stub.writes.filter((write) => write.url === '/api/feedback').map((write) => write.body), [
        { id: ITEM.plain, kind: 'rate', value: 0 },
        { id: ITEM.plain, kind: 'rate', value: 60 },
      ]);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('标签：删一枚再撤销回来；从选择器新建一枚', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      const tag = (name: string) => page.locator(`#detailTags [data-tag="${name}"]`);
      await page.locator('#detailTags [data-remove-tag="剧情"]').click();
      await tag('剧情').waitFor({ state: 'detached' });
      await undo(page);
      await tag('剧情').waitFor();
      await page.locator('#tagPlus').click();
      await page.locator('#tagPicker:not([hidden])').waitFor();
      await page.locator('#tagPickSearch').fill('新建的标签');
      await page.locator('#tagPicker [data-pick="新建的标签"]', { hasText: '新建“新建的标签”' }).waitFor();
      await page.locator('#tagPickSearch').press('Enter');
      await tag('新建的标签').waitFor();
      assert.equal(await page.locator('#tagPicker').isHidden(), true, '加完标签选择器没有收起');
      assert.deepEqual(opened.stub.writes.filter((write) => write.url === '/api/item-tag').map((write) => write.body), [
        { id: ITEM.plain, operation: 'remove', tag: '剧情' },
        { id: ITEM.plain, operation: 'add', tag: '剧情' },
        { id: ITEM.plain, operation: 'add', tag: '新建的标签' },
      ]);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`${viewport.name}详情动作保持单行，提示显示在浮窗顶层`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, viewport);
      try {
        const page = opened.page;
        const actions = page.locator('#stage [data-item-feedback] button');
        const rects = await actions.evaluateAll(nodes => nodes.map(node => {
          const r = node.getBoundingClientRect(); return { top: r.top, width: r.width };
        }));
        assert.equal(rects.length, 8);
        assert.equal(await page.locator('#stage [data-fb="cloud-download"]').count(), 0);
        assert.ok(Math.max(...rects.map(r => r.top)) - Math.min(...rects.map(r => r.top)) <= 1);
        assert.ok(rects.every(r => r.width >= 30));
        await actions.first().scrollIntoViewIfNeeded();
        await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await actions.first().focus();
        const tip = page.locator('#board-control-tooltip');
        await tip.waitFor({state:'visible'});
        assert.equal(await tip.evaluate(node => node.matches(':popover-open')), true);
        assert.equal(await tip.evaluate(node => node.closest('dialog')?.id), 'stage');
        const r = await tip.boundingBox();
        assert.ok(r && r.x >= 0 && r.y >= 0 && r.x + r.width <= viewport.width && r.y + r.height <= viewport.height);
        await actions.first().evaluate(node => (node as HTMLElement).blur());
        assert.equal(await tip.isHidden(), true);
      } finally { await opened.close() }
    });
  }

  it('稍后看与反馈键：写完键态跟着服务端的回话变', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#stageLater').click();
      await page.locator('#stageLater[aria-pressed="true"]').waitFor();
      await page.locator('#stage [data-kind="dislike"]').click();
      await page.locator('#stage [data-kind="dislike"][aria-pressed="true"]').waitFor();
      await page.locator('#stage [data-kind="seen"]').click();
      await page.locator('#stage [data-kind="seen"][aria-pressed="true"]').waitFor();
      assert.equal(await page.locator('#stage [data-kind="dislike"]').getAttribute('aria-pressed'), 'false', '看过与不合口味同时亮着');
      assert.deepEqual(opened.stub.writes.map((write) => [write.url, write.body.kind ?? null]), [
        ['/api/watch-later', null], ['/api/feedback', 'dislike'], ['/api/feedback', 'seen'],
      ]);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`${viewport.name}完整文件与修复分卷共用一张卡，版本队列只续播同版下一卷`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, '/', viewport, { catalogIds: [ITEM.edition], ready: '#grid [data-media-card]' });
      try {
        const page = opened.page;
        const full = await page.evaluate(async id => (await fetch(`/api/item?id=${id}`)).json(), ITEM.edition);
        const repair = await page.evaluate(async () => (await fetch('/api/item?id=32')).json());
        const edition_group = { key: 'PCH-031', seed_id: 31, count: 2, editions: ['1080p', '4K 修复'] };
        const part_group = { key: 'PCH-031:32', seed_id: 32, count: 2 };
        const files = [
          { ...full, edition_group, edition_label: '1080p', version_id: 31 },
          { ...repair, edition_group, part_group, edition_label: '4K 修复', version_id: 32, part_label: '1' },
          { ...repair, id: 33, edition_group, part_group, edition_label: '4K 修复', version_id: 32, part_label: '2' },
        ];
        await page.route(url => url.pathname === '/api/item', route => {
          const file = files.find(row => row.id === Number(new URL(route.request().url()).searchParams.get('id')));
          return file ? route.fulfill({ json: file }) : route.fallback();
        });
        await page.route(url => url.pathname === '/api/editions', route => route.fulfill({ json: { title: 'PCH-031', count: 2, items: files } }));
        await page.route(url => url.pathname === '/api/items', route => route.fulfill({ json: {
          items: [files[2], files[0], files[1]].map(row => ({ ...row, tags: row.tags.map((tag: { k: string }) => tag.k) })),
          total: 3, work_total: 1, has_more: false,
        } }));
        await page.reload({ waitUntil: 'load' });
        const card = page.locator('#grid [data-media-card][data-variant="grid"]');
        await card.first().waitFor();
        assert.equal(await card.count(), 1);
        assert.deepEqual(await card.locator('[data-media-group]').allTextContents(), ['2 个版本']);
        await card.locator('[data-media-title]').click();
        await pathIs(page, '/assets/33?editions=31');
        await page.locator('#stage [data-queue-item="33"][aria-current="true"]').waitFor();
        assert.equal(await page.locator('#stage [data-queue-item]').count(), 3);
        assert.equal(await page.locator('#stage').getByText('2 个版本', { exact: true }).count(), 1);
        await page.locator('#stage [data-queue-item="32"]').click();
        await page.locator('#stage [data-queue-item="32"][aria-current="true"]').waitFor();
        await page.locator('#stage video.vjs-tech').dispatchEvent('ended');
        await pathIs(page, '/assets/33?editions=31');
        await page.locator('#stage [data-queue-item="33"][aria-current="true"]').waitFor();
        await page.locator('#stage video.vjs-tech').dispatchEvent('ended');
        assert.equal(new URL(page.url()).pathname + new URL(page.url()).search, '/assets/33?editions=31');
        const bounds = await layout(page);
        assert.ok(bounds.scrollWidth <= bounds.viewportWidth + 1, JSON.stringify(bounds));
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally {
        await opened.close();
      }
    });
  }

  for (const [kind, path, next, wanted] of [
    ['Mix', `/assets/${ITEM.plain}?mix=${ITEM.plain}`, 14, `/assets/14?mix=${ITEM.plain}`],
    ['分卷', `/assets/22?parts=${ITEM.part}`, 23, `/assets/23?parts=${ITEM.part}`],
    ['版本', `/assets/${ITEM.edition}?editions=${ITEM.edition}`, 32, `/assets/32?editions=${ITEM.edition}`],
    ['播放列表', `/assets/11?playlist=${PLAYLIST.id}`, 15, `/assets/15?playlist=${PLAYLIST.id}`],
  ] as const) {
    it(`${kind}队列：深链停在给的那一条，点另一条原地换过去且不重取队列`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, path, DESKTOP);
      try {
        const page = opened.page;
        assert.equal(await current(page), new URL(path, page.url()).pathname.split('/').at(-1));
        const reads = new Map(opened.stub.reads);
        await watchStage(page);
        await page.locator(`#stage [data-queue-item="${next}"]`).click();
        await pathIs(page, wanted);
        await page.locator(`#stage [data-queue-item="${next}"][aria-current="true"]`).waitFor();
        await page.locator(DETAIL_READY).waitFor();
        assert.deepEqual(await stageKept(page), { same: true, skeleton: false, focused: true }, `${kind}队列换一条重开了浮窗`);
        if (kind === '分卷') assert.match(await page.locator('#stage [data-detail-title]').innerText(), /第 3 卷/);
        for (const url of ['/api/parts', '/api/editions', '/api/related']) {
          assert.equal(opened.stub.reads.get(url) || 0, reads.get(url) || 0, `同一个${kind}队列里换一条又取了 ${url}`);
        }
        if (kind === '播放列表') {
          assert.deepEqual(opened.stub.writes.filter((write) => write.body.action === 'progress').map((write) => write.body.asset_id),
            [11, next], '播放列表没有记下续播位置');
        }
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally {
        await opened.close();
      }
    });
  }

  it('播放列表：拖动换顺序、移出一条，两者都能撤销', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/11?playlist=${PLAYLIST.id}`, DESKTOP);
    try {
      const page = opened.page;
      const order = () => ids(page, '#stage [data-queue-row]', 'data-queue-row');
      const wait = (wanted: number[]) => page.waitForFunction((want) => JSON.stringify([...document
        .querySelectorAll('#stage [data-queue-row]')].map((row) => Number(row.getAttribute('data-queue-row')))) === want,
      JSON.stringify(wanted), { timeout: 10_000 });
      assert.deepEqual(await order(), PLAYLIST.items);
      await page.locator('#stage [data-queue-row="16"]').dragTo(page.locator('#stage [data-queue-row="14"]'),
        { targetPosition: { x: 4, y: 2 } });
      await wait([11, 16, 14, 15]);
      await undo(page);
      await wait(PLAYLIST.items);
      await page.locator('#stage [data-queue-remove="15"]').click();
      await page.locator('[data-modal-confirm]').click();
      await wait([11, 14, 16]);
      await undo(page);
      await wait(PLAYLIST.items);
      assert.deepEqual(opened.stub.writes.filter((write) => write.body.action !== 'progress')
        .map((write) => [write.body.action, write.body.asset_ids ?? write.body.asset_id]), [
        ['reorder', [11, 16, 14, 15]], ['reorder', PLAYLIST.items],
        ['remove', 15], ['add', [15]], ['reorder', PLAYLIST.items],
      ]);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('保存 Mix：确认之后转去新建的那份播放列表', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}?mix=${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#stage [data-save-mix]').click();
      await page.locator('[name="name"]').fill('我的 Mix');
      await page.locator('[data-modal-confirm]').click();
      await page.waitForFunction(() => location.pathname.startsWith('/assets/') && new URLSearchParams(location.search).get('playlist') === '8', undefined, { timeout: 10_000 });
      await page.locator('#stage [data-queue-row]').first().waitFor();
      assert.deepEqual(await ids(page, '#stage [data-queue-row]', 'data-queue-row'), [ITEM.plain, ...RELATED]);
      const created = opened.stub.writes.find((write) => write.body.action === 'create')!;
      assert.deepEqual(created.body, {
        action: 'create', name: '我的 Mix', asset_ids: [ITEM.plain, ...RELATED], source_kind: 'mix',
        source_seed_asset_id: ITEM.plain,
      });
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('接着看：一排卡按相关作品的顺序；点一张原地换到那一条', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#nrow [data-media-card]').first().waitFor();
      assert.deepEqual(await ids(page, '#nrow [data-media-card]', 'data-id'), RELATED);
      const before = await page.locator('#stage [data-detail-title]').innerText();
      await watchStage(page);
      await page.locator(`#nrow [data-media-card][data-id="${RELATED[0]}"] [data-media-title]`).click();
      await pathIs(page, `/assets/${RELATED[0]}`);
      await page.waitForFunction((title) => {
        const now = document.querySelector('#stage [data-detail-title]');
        return !!now && (now as HTMLElement).innerText !== title;
      }, before, { timeout: 10_000 });
      await page.locator(DETAIL_READY).waitFor();
      assert.deepEqual(await stageKept(page), { same: true, skeleton: false, focused: true }, '接着看换一条重开了浮窗');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('点女优进她的实体页，舞台收起', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.route(url => url.pathname === '/api/entity', route => {
        assert.equal(new URL(route.request().url()).searchParams.get('id'), '1');
        return route.fulfill({ json: {
          id: 1, kind: 'performer', canonical_name: NAMES[0], aliases: [], display_aliases: [], user_aliases: [],
          asset_count: 1, tags: [], related_performers: [], links: [], metadata: {},
          has_image: false, has_avatar: false, representative_asset_id: null, entry_links: [],
        } });
      });
      await page.locator(`#stage [data-item-identity] [data-entity-kind="performer"][data-entity-name="${NAMES[0]}"]`).click();
      await pathIs(page, '/performers/1');
      await page.locator('#index [data-entity-title] h2').filter({ hasText: NAMES[0] }).waitFor();
      await page.waitForFunction(() => !document.querySelector('#stage[open]'), undefined, { timeout: 10_000 });
      assert.equal(await page.evaluate(() => document.body.classList.contains('entity-open')), true);
    } finally {
      await opened.close();
    }
  });

  it('取不到这一条：播放器格照 16:9 撑开，重试提示居中，不塌成一条窄条', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.route((url) => url.pathname === '/api/item', (route) => route.fulfill({ status: 500, json: { error: 'database is locked' } }));
      await page.goto(new URL('/assets/987654', page.url()).toString(), { waitUntil: 'load' });
      await page.locator('#stage [data-item-placeholder] [data-media-error]').waitFor({ timeout: 15_000 });
      const box = await page.evaluate(() => {
        const frame = document.querySelector('#stage [data-item-placeholder]')!.getBoundingClientRect();
        const note = document.querySelector('#stage [data-item-placeholder] [data-media-error]')!.getBoundingClientRect();
        return { ratio: frame.width / frame.height, offset: Math.abs((note.top + note.bottom) / 2 - (frame.top + frame.bottom) / 2) };
      });
      assert.ok(Math.abs(box.ratio - 16 / 9) < 0.02, `播放器格不是 16:9：${JSON.stringify(box)}`);
      assert.ok(box.offset < 2, `提示没有竖直居中：${JSON.stringify(box)}`);
    } finally {
      await opened.close();
    }
  });

  it('长标签单行省略，高度和别的标签一样', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, MOBILE);
    try {
      const page = opened.page;
      const heights = await page.evaluate(() => {
        const tag = document.querySelector('#detailTags [data-tag]') as HTMLElement;
        const before = tag.getBoundingClientRect().height;
        tag.textContent = 'customer-feedback-from-enterprise-onboarding-customer-feedback-from-enterprise-onboarding';
        return { before, after: tag.getBoundingClientRect().height, wide: tag.scrollWidth > tag.clientWidth };
      });
      assert.equal(heights.after, heights.before, `长标签折行了：${JSON.stringify(heights)}`);
      assert.equal(heights.wide, true, '长标签应当被省略');
      assert.deepEqual((await layout(page)).offenders, [], '长标签撑出视口');
    } finally {
      await opened.close();
    }
  });

  it('手机 390：详情不横向溢出，侧栏排在播放区下面', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, MOBILE);
    try {
      const page = opened.page;
      assert.deepEqual((await layout(page)).offenders, [], '详情里有元素越出视口右边');
      const boxes = await page.evaluate(() => ({
        media: document.querySelector('#stage [data-item-media]')!.getBoundingClientRect().bottom,
        side: document.querySelector('#stage [data-item-side]')!.getBoundingClientRect().top,
      }));
      assert.ok(boxes.side >= boxes.media - 1, `侧栏不在播放区下面：${JSON.stringify(boxes)}`);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });
});
