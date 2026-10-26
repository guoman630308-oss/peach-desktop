/* 实体资料页资料卡下面那一整块：交集条与两排的玻璃浮层（`entity-filter` 岛，ADR-0031）。
 *
 * 演示库没有人物实体：资料、作品与照片都照接口的形状在这里给，页面自己的路由、渲染和动作照常跑。
 * 验的是行为：骨架换成真浮层时不跳、按下去的反馈不等请求、视图之间切换时哪些东西该收起来、
 * 吸顶与窄屏横滚。外观对照另有截图，不在这里比像素。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, settle, visit, VIEWPORTS, type Visit } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const PHONE = VIEWPORTS.find((viewport) => viewport.mobile)!;
const NAME = '篠田ゆう';
const PATH = `/performers/${encodeURIComponent(NAME)}`;
const THUMB = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#456"/></svg>';
const TAGS = ['巨乳', '美乳', '中出', '单体作品', '高画质', '痴女', '口交', '姐姐', '苗条', '制服', '潮吹', '美腿'];

const ENTITY = {
  id: 90_101, kind: 'performer', canonical_name: NAME, aliases: [], display_aliases: [], user_aliases: [],
  asset_count: 24, tags: TAGS.map((k, at) => ({ k, n: 40 - at * 2 })), related_performers: [], links: [],
  metadata: {}, has_image: false, has_avatar: false, avatar_focus: null, representative_asset_id: null,
  entry_links: [], feed: null,
};
const costar = (id: number, k: string) => ({ id, k, n: 3, rep: null, has_image: false, has_avatar: false, avatar_focus: null });
const AGENCY = {
  ...ENTITY, id: 90_701, kind: 'agency', canonical_name: 'New Actor eXperience', member_count: 3, feed: undefined,
  tags: ENTITY.tags.slice(0, 6),
  related_performers: [costar(90_710, NAME), costar(90_711, '河北彩伽'), costar(90_712, '三上悠亜')],
};
type Entity = typeof ENTITY | typeof AGENCY;
const items = (jav: boolean) => Array.from({ length: 24 }, (_, at) => ({
  id: 91_000 + at, name: jav ? `SSIS-${100 + at} 演示作品 ${at + 1}` : `演示作品 ${at + 1}`, code: jav ? `SSIS-${100 + at}` : '',
  is_jav: jav, has_thumb: true, duration: 7200, studio: 'S1 NO.1 STYLE', tags: [],
}));
const PHOTOS = { total: 18, sample_total: 0, sets: [], has_more: false, seed: '',
  items: Array.from({ length: 18 }, (_, at) => ({ id: 92_000 + at, name: `${String(at + 1).padStart(3, '0')}.jpg` })) };
const NO_PHOTOS = { total: 0, sample_total: 0, sets: [], items: [], has_more: false, seed: '' };

/** 资料、作品、照片与图都在这里给；事务所旗下不是 JAV 语境，只有女优有照片。
 *  `/api/items` 可以挂住：`hold()` 之后的那一趟等 `release()` 才回。 */
async function serve(page: Page, entity: Entity = ENTITY) {
  let gate: Promise<void> | null = null;
  let open = () => {};
  const itemQueries: string[] = [];
  const list = items(entity.kind !== 'agency');
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: entity }));
  await page.route((url) => url.pathname === '/api/items', async (route) => {
    const url = new URL(route.request().url());
    itemQueries.push(url.search);
    if (gate) await gate;
    const offset = Number(url.searchParams.get('offset') || 0);
    await route.fulfill({ json: { items: offset ? [] : list, total: list.length, has_more: false } });
  });
  await page.route((url) => url.pathname === '/api/photos', (route) =>
    route.fulfill({ json: entity.kind === 'performer' ? PHOTOS : NO_PHOTOS }));
  await page.route((url) => url.pathname === '/api/feeds/discoveries', (route) =>
    route.fulfill({ json: { ok: true, more: false, items: [] } }));
  await page.route(/\/(?:thumb|poster|photo-thumb|cover)\?/, (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: THUMB }));
  return {
    itemQueries,
    hold() { gate = new Promise<void>((resolve) => { open = resolve; }); },
    release() { open(); gate = null; },
  };
}

async function openPerformer(browser: Browser, viewport = DESKTOP, search = '', entity: Entity = ENTITY) {
  const opened = await visit(browser, '/', viewport);
  const stubs = await serve(opened.page, entity);
  const path = entity.kind === 'agency' ? `/agencies/${encodeURIComponent(entity.canonical_name)}` : PATH;
  await opened.page.goto(new URL(path + search, opened.page.url()).href, { waitUntil: 'load' });
  await opened.page.locator('[data-entity-filter-glass] [data-entity-readout]', { hasText: /\S/ })
    .waitFor({ timeout: 15_000 });
  await settle(opened.page);
  assert.equal(await opened.page.locator('[data-entity-filter-glass]').getByRole('button', { name: '我的收藏', exact: true }).count(), 1);
  return { ...opened, stubs };
}

/** 滑动玻璃与它该垫着的那一枚键：两者的外框，玻璃收起时为 null。 */
const glideOver = (page: Page, glide: string, key: string) => page.evaluate(([glideSelector, keySelector]) => {
  const rect = (node: Element | null) => {
    if (!node || (node as HTMLElement).hidden) return null;
    const box = node.getBoundingClientRect();
    return { left: box.left, top: box.top, width: box.width, height: box.height };
  };
  return { glide: rect(document.querySelector(glideSelector)), key: rect(document.querySelector(keySelector)) };
}, [glide, key] as const);

function assertCovers(pair: Awaited<ReturnType<typeof glideOver>>, what: string) {
  assert.ok(pair.glide && pair.key, `${what}：玻璃或键不在屏幕上 ${JSON.stringify(pair)}`);
  for (const side of ['left', 'top', 'width', 'height'] as const) {
    assert.ok(Math.abs(pair.glide![side] - pair.key![side]) <= 1,
      `${what}：玻璃的 ${side} 是 ${pair.glide![side]}，键是 ${pair.key![side]}`);
  }
}

const unexpected = (opened: Visit) => opened.problems;

/** 分段开关里的一格：点得到的是整格 label，单选框本身是藏起来的。 */
const segment = (page: Page, group: string, value: string) =>
  page.locator(`[data-entity-layout][aria-label="${group}"] label:has(input[value="${value}"])`);

describe('实体页筛选浮层', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  for (const viewport of [DESKTOP, PHONE]) {
    it(`${viewport.mobile ? '手机' : '桌面'}别名地址归一保留参数与片段，后退回到入口`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      try {
        const { page } = opened;
        await serve(page);
        const suffix = '?sort=release&dir=asc&tag=a%2Fb&offset=48#works';
        await page.goto(new URL('/performers/OldName' + suffix, page.url()).href, { waitUntil: 'load' });
        await page.waitForURL(url => url.pathname === '/performers/90101' && url.search + url.hash === suffix);
        await page.locator('[data-entity-filter-glass] [data-entity-readout]').waitFor();
        await page.goBack({ waitUntil: 'load' });
        assert.equal(new URL(page.url()).pathname, '/');
      } finally { await opened.close(); }
    });
  }

  it('骨架里那块浮层换成真浮层时不跳：大小、离资料卡的距离与作品网格的上沿都留在原位', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const { page } = opened;
      await serve(page);
      let release = () => {};
      const held = new Promise<void>((resolve) => { release = resolve; });
      await page.route(/\/api\/entity\?/, async (route) => { await held; await route.fallback(); });
      await page.goto(new URL(PATH, page.url()).href, { waitUntil: 'load' });
      await page.locator('#index [data-skeleton="entity/performer"] .board-filter-frame').waitFor({ timeout: 15_000 });
      const material = await page.locator('#index [data-skeleton] .board-filter-frame').evaluate(node => {
        const style = getComputedStyle(node);
        return { image: style.backgroundImage, backdrop: style.backdropFilter, background: style.backgroundColor };
      });
      assert.notEqual(material.backdrop, 'none');
      assert.notEqual(material.background, 'rgb(0, 0, 0)');
      /* 资料卡是另一座岛，骨架与真卡的高度差不归这一块管：浮层的位置按它离资料卡下沿的距离量。 */
      const measure = (hero: string, glass: string, grid: string) => page.evaluate(([heroSelector, glassSelector, gridSelector]) => {
        const heroBox = document.querySelector(heroSelector)!.getBoundingClientRect();
        const glassBox = document.querySelector(glassSelector)!.getBoundingClientRect();
        const gridBox = document.querySelector(gridSelector)!.getBoundingClientRect();
        return { gap: glassBox.top - heroBox.bottom, left: glassBox.left, width: glassBox.width,
          height: glassBox.height, grid: gridBox.top - glassBox.bottom };
      }, [hero, glass, grid] as const);
      const skeleton = await measure('#index [data-skeleton] .entityhero', '#index [data-skeleton] .board-filter-frame',
        '#index [data-skeleton] .entitysection');
      release();
      await page.locator('[data-entity-filter-glass] [data-entity-readout]', { hasText: '视频' }).waitFor({ timeout: 15_000 });
      await settle(page);
      const live = await measure('#index [data-entity-card]', '#index [data-entity-filter-glass]', '#index [data-entity-body]');
      for (const key of ['gap', 'left', 'width', 'height', 'grid'] as const) {
        assert.ok(Math.abs(skeleton[key] - live[key]) <= 1,
          `浮层的 ${key} 接管时跳了：骨架 ${skeleton[key]}，接管后 ${live[key]}`);
      }
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('进页时两块滑动玻璃就垫在当前视图与观看状态下面，换观看状态时玻璃当场滑过去、读数等这一趟', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser);
    try {
      const { page, stubs } = opened;
      assertCovers(await glideOver(page, '[data-view-glide="round"]', '[data-media-view][aria-pressed="true"]'), '视图键');
      assertCovers(await glideOver(page, '[data-view-glide=""]', '[data-entity-state][aria-pressed="true"]'), '观看状态');
      stubs.hold();
      await page.locator('[data-entity-state="fresh"]').click();
      const pressed = await page.evaluate(() => ({
        state: document.querySelector('[data-entity-state="fresh"]')?.getAttribute('aria-pressed'),
        busy: document.querySelector('[data-entity-filter-glass] [data-filter-row="bottom"]')?.getAttribute('aria-busy'),
        shimmer: !!document.querySelector('[data-entity-readout] [data-skeleton="count"]'),
      }));
      assert.deepEqual(pressed, { state: 'true', busy: 'true', shimmer: true }, '按下态要等请求回来才到位');
      assertCovers(await glideOver(page, '[data-view-glide=""]', '[data-entity-state="fresh"]'), '换过去的观看状态');
      stubs.release();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24' }).waitFor({ timeout: 15_000 });
      assert.match(page.url(), /[?&]state=fresh/);
      assert.match(stubs.itemQueries.at(-1)!, /state=fresh/);
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('换排序当场按下并画出方向箭头，再点一次翻方向，JAV 版式切换不发请求', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser);
    try {
      const { page, stubs } = opened;
      stubs.hold();
      await page.locator('[data-entity-sort="rating"]').click();
      assert.equal(await page.locator('[data-entity-sort="rating"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('[data-entity-sort="new"]').getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator('[data-entity-sort="rating"] svg').count(), 1, '选中的排序键没有方向箭头');
      stubs.release();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24' }).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('[data-entity-sort="rating"]').getAttribute('aria-label'), '按评分从低到高排序');
      await page.locator('[data-entity-sort="rating"]').click();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24' }).waitFor({ timeout: 15_000 });
      assert.match(page.url(), /[?&]dir=asc/);
      const before = stubs.itemQueries.length;
      const small = segment(page, 'JAV 卡片版式', 'small');
      assert.equal(await small.getAttribute('data-selected'), null);
      await small.click();
      await page.waitForFunction(() => document.querySelector(
        '[data-entity-layout][aria-label="JAV 卡片版式"] label:has(input[value="small"])')?.hasAttribute('data-selected'));
      await segment(page, 'JAV 卡片版式', 'big').click();
      assert.equal(stubs.itemQueries.length, before, '切版式重新取了一次作品');
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('点标签加进交集条，交集条上撤掉它、标签跟着弹起；地址栏同步', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser);
    try {
      const { page } = opened;
      assert.equal(await page.locator('[data-entity-combo] [data-combo-chip]').count(), 0);
      // 标签跟首页筛选条上那枚同一张脸：30px 高、`--tag-radius` 那一档 8px 圆角、一圈 1px 实线。
      const face = await page.locator('[data-entity-tag="中出"]').evaluate((el) => {
        const css = getComputedStyle(el);
        return [el.getBoundingClientRect().height, css.borderTopLeftRadius, css.borderTopWidth, css.borderTopStyle];
      });
      assert.deepEqual(face, [30, '8px', '1px', 'solid']);
      await page.locator('[data-entity-tag="中出"]').click();
      assert.equal(await page.locator('[data-entity-tag="中出"]').getAttribute('aria-pressed'), 'true');
      await page.locator('[data-entity-combo] [data-combo-chip]', { hasText: '中出' }).waitFor();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24 · 已选 1 个标签' }).waitFor({ timeout: 15_000 });
      assert.match(decodeURIComponent(page.url()), /[?&]tag=中出/);
      await page.getByRole('button', { name: '撤掉 中出' }).click();
      await page.locator('[data-entity-readout]', { hasText: /^视频 · 24$/ }).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('[data-entity-tag="中出"]').getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator('[data-entity-combo] [data-combo-chip]').count(), 0);
      assert.doesNotMatch(page.url(), /[?&]tag=/);
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('切到照片：视图键当场换过去，观看状态、标签与交集条收起，下排换成照片那一排；切回视频都回来', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser, DESKTOP, `?tag=${encodeURIComponent('中出')}`);
    try {
      const { page } = opened;
      await page.locator('[data-media-view="photos"]').click();
      await page.locator('[data-entity-readout]', { hasText: /^照片/ }).waitFor({ timeout: 15_000 });
      const photos = await page.evaluate(() => ({
        pressed: document.querySelector('[data-media-view="photos"]')?.getAttribute('aria-pressed'),
        states: (document.querySelector('[data-entity-states]') as HTMLElement | null)?.hidden,
        tags: document.querySelectorAll('[data-entity-tag]').length,
        combo: (document.querySelector('[data-entity-combo]') as HTMLElement | null)?.hidden,
        sorts: document.querySelectorAll('[data-entity-sort]').length,
        batch: document.querySelectorAll('[data-entity-batch]').length,
      }));
      assert.deepEqual(photos, { pressed: 'true', states: true, tags: 0, combo: true, sorts: 0, batch: 1 });
      assertCovers(await glideOver(page, '[data-view-glide="round"]', '[data-media-view="photos"]'), '照片键');
      assert.equal(await page.locator('[data-view-glide=""]').evaluate((node) => (node as HTMLElement).hidden), true,
        '观看状态收起了，那块玻璃还亮着');
      await segment(page, '图片布局', 'fixed').click();
      await page.waitForFunction(() => document.querySelector('[data-local-wall]')?.getAttribute('data-layout') === 'fixed');
      await segment(page, '图片布局', 'masonry').click();
      await page.locator('[data-media-view="videos"]').click();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24 · 已选 1 个标签' }).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('[data-entity-tag]').count(), TAGS.length);
      assert.equal(await page.locator('[data-entity-combo] [data-combo-chip]').count(), 1);
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('浮层吸在顶栏下沿，吸住时标 data-stuck，玻璃跟着键不走位', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser);
    try {
      const { page } = opened;
      await page.evaluate(() => window.scrollTo(0, 700));
      await page.waitForFunction(() => document.querySelector('[data-entity-filter-glass]')!.hasAttribute('data-stuck'),
        undefined, { timeout: 5_000 });
      const pinned = await page.evaluate(() => {
        const glass = document.querySelector('[data-entity-filter-glass]')!;
        return { top: glass.getBoundingClientRect().top, sticky: parseFloat(getComputedStyle(glass).top) };
      });
      assert.ok(Math.abs(pinned.top - pinned.sticky) <= 1, `浮层没有吸住：上沿 ${pinned.top}，吸顶位置 ${pinned.sticky}`);
      assertCovers(await glideOver(page, '[data-view-glide="round"]', '[data-media-view][aria-pressed="true"]'), '吸顶时的视图键');
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForFunction(() => !document.querySelector('[data-entity-filter-glass]')!.hasAttribute('data-stuck'),
        undefined, { timeout: 5_000 });
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('事务所进页落在旗下艺人名册，下排只有读数；切到视频出排序与标签，没有 JAV 版式', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser, DESKTOP, '', AGENCY);
    try {
      const { page } = opened;
      const roster = await page.evaluate(() => ({
        readout: document.querySelector('[data-entity-readout]')?.textContent,
        pressed: document.querySelector('[data-media-view][aria-pressed="true"]')?.getAttribute('data-media-view'),
        views: [...document.querySelectorAll('[data-media-view]')].map((node) => node.getAttribute('data-media-view')),
        sorts: document.querySelectorAll('[data-entity-sort]').length,
        tags: document.querySelectorAll('[data-entity-tag]').length,
        cards: document.querySelectorAll('#index [data-entity-body] [data-index-grid] > [data-index-cell]').length,
      }));
      assert.deepEqual(roster, { readout: '艺人 · 3', pressed: 'people', views: ['people', 'videos'], sorts: 0, tags: 0, cards: 3 });
      await page.locator('[data-media-view="videos"]').click();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24' }).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('[data-entity-tag]').count(), AGENCY.tags.length);
      assert.ok(await page.locator('[data-entity-sort]').count() > 0);
      assert.equal(await page.locator('[data-entity-layout][aria-label="JAV 卡片版式"]').count(), 0);
      assertCovers(await glideOver(page, '[data-view-glide="round"]', '[data-media-view="videos"]'), '视频键');
      await page.locator('[data-media-view="people"]').click();
      await page.locator('[data-entity-readout]', { hasText: '艺人 · 3' }).waitFor({ timeout: 15_000 });
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('手机宽度下观看状态与标签整段横滚、视图键钉住；往下滚浮层让出顶栏，往回滚落下来', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser, PHONE);
    try {
      const { page } = opened;
      const scroller = page.locator('[data-entity-scroll]');
      const overflow = await scroller.evaluate((node) => node.scrollWidth - node.clientWidth);
      assert.ok(overflow > 100, `窄屏下这一段没有横向溢出（${overflow}px），横滚的判据没生效`);
      const keyBefore = await page.locator('[data-media-view="videos"]').boundingBox();
      await scroller.evaluate((node) => { node.scrollLeft = 220; node.dispatchEvent(new Event('scroll')); });
      await page.waitForFunction(() => document.querySelector('[data-entity-scroll]')!.scrollLeft > 0);
      const keyAfter = await page.locator('[data-media-view="videos"]').boundingBox();
      assert.equal(keyAfter?.x, keyBefore?.x, '视图键跟着横滚走了');
      await page.waitForFunction(() => (document.querySelector('[data-view-glide=""]') as HTMLElement).hidden,
        undefined, { timeout: 5_000 });
      const glass = '[data-entity-filter-glass]';
      /* 方向判据按「上一个 y」算：壳在 scroll 事件里排一帧 rAF 才读 scrollY，两次滚动落进同一帧
         的话它只读到后一次，往下 300 再到 700 会被看成一步、700 回 500 会被看成从 300 往下滚。
         所以每滚一次都等到这个位置的 scroll 事件真的发出来，再过两帧：壳那一帧排在这一帧前面，
         两帧过去它一定已经读过这个位置，下一次滚动才发。 */
      const scrollSettled = (y: number) => page.evaluate((target) => new Promise<void>((done) => {
        addEventListener('scroll', function seen() {
          if (scrollY !== target) return;
          removeEventListener('scroll', seen);
          requestAnimationFrame(() => requestAnimationFrame(() => done()));
        });
        scrollTo(0, target);
      }), y);
      await scrollSettled(300);
      await scrollSettled(700);
      await page.waitForFunction((selector) => document.querySelector(selector)!.hasAttribute('data-filter-free'),
        glass, { timeout: 5_000 });
      const bottom = (selector: string) => page.evaluate((s) => document.querySelector(s)!.getBoundingClientRect().bottom, selector);
      assert.ok(await bottom(glass) <= 0, `让位后浮层还压在屏幕上沿：下沿 ${await bottom(glass)}`);
      await scrollSettled(500);
      await page.waitForFunction((selector) => !document.querySelector(selector)!.hasAttribute('data-filter-free'),
        glass, { timeout: 5_000 });
      const top = await page.evaluate((s) => {
        const node = document.querySelector(s)!;
        return { at: node.getBoundingClientRect().top, sticky: parseFloat(getComputedStyle(node).top) };
      }, glass);
      assert.ok(Math.abs(top.at - top.sticky) <= 1, `往回滚后浮层没有落回顶栏下沿：${JSON.stringify(top)}`);
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('深链带 media=photos 直接落在照片墙；回到视频后加一枚标签只重取一次，切照片再切回不重取', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser, DESKTOP, '?media=photos');
    try {
      const { page, stubs } = opened;
      await page.locator('[data-entity-readout]', { hasText: /^照片/ }).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator('[data-media-view="photos"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('[data-local-wall] [data-photo-index]').count(), PHOTOS.items.length);
      await page.locator('[data-media-view="videos"]').click();
      await page.locator('[data-entity-readout]', { hasText: /^视频 · 24$/ }).waitFor({ timeout: 15_000 });
      assert.doesNotMatch(page.url(), /[?&]media=/);
      const before = stubs.itemQueries.length;
      await page.locator('[data-entity-tag="中出"]').click();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24 · 已选 1 个标签' }).waitFor({ timeout: 15_000 });
      await settle(page);
      assert.equal(stubs.itemQueries.length - before, 1, `加一枚标签发了 ${stubs.itemQueries.length - before} 次作品请求`);
      await page.locator('[data-media-view="photos"]').click();
      await page.locator('[data-entity-readout]', { hasText: /^照片/ }).waitFor({ timeout: 15_000 });
      await page.locator('[data-media-view="videos"]').click();
      await page.locator('[data-entity-readout]', { hasText: '视频 · 24 · 已选 1 个标签' }).waitFor({ timeout: 15_000 });
      await settle(page);
      assert.equal(stubs.itemQueries.length - before, 1, '切照片再切回视频又取了一遍作品');
      assert.deepEqual(unexpected(opened), []);
    } finally {
      await opened.close();
    }
  });

  it('选了六枚长标签，读数只报个数，两种宽度下页面都不被撑宽', { timeout: 60_000 }, async () => {
    const long = Array.from({ length: 6 }, (_, at) => `とても長い日本語のタグ名その${at + 1}・途中で切れない`);
    for (const viewport of [DESKTOP, PHONE]) {
      const opened = await openPerformer(browser, viewport, `?tag=${encodeURIComponent(long.join(','))}`);
      try {
        const { page } = opened;
        await page.locator('[data-entity-readout]', { hasText: '视频 · 24 · 已选 6 个标签' }).waitFor({ timeout: 15_000 });
        const width = await page.evaluate(() => ({
          page: document.documentElement.scrollWidth, view: window.innerWidth,
          readout: document.querySelector('[data-entity-filter-glass] [data-entity-readout]')!.textContent,
        }));
        assert.ok(width.page <= width.view, `${viewport.name}：页面被撑到 ${width.page}px（视口 ${width.view}）`);
        assert.ok(!long.some((tag) => width.readout?.includes(tag)), `${viewport.name}：读数里又列了标签名`);
        assert.deepEqual(unexpected(opened), []);
      } finally {
        await opened.close();
      }
    }
  });
});
