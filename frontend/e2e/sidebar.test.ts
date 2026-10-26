/* 侧栏岛（`frontend/src/react/sidebar/`，ADR-0031 第 11f 步）在真浏览器里的行为：拖动排序写回、五处语境的
 * 筛选分组与计数、切页时那块玻璃一路滑过去、手机宽度下抽屉的开合，以及覆盖式滚动条仍挂在滚动层上。
 *
 * 写接口一律落在桩里（`settings-fixture`、`follow-fixture`、`item-fixture`）。聚合接口 `/api/facets` 照演示库
 * 真取一份，只把内容标签换成按语境可区分的几枚：首页与实体页数字不同，才看得出侧栏是按语境取的。演示库没有
 * 人物实体，实体页的资料与作品照接口的形状在这里给（同 `entity-filter.test.ts`）。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { expectBody, launch, settle, visit, VIEWPORTS } from './harness.ts';
import { openFollowFeed } from './follow-fixture.ts';
import { ITEM, openItemPage } from './item-fixture.ts';
import { openHome, ORDER } from './settings-fixture.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const PHONE = VIEWPORTS.find((viewport) => viewport.mobile)!;
const HOME_TAGS = [['演示侧栏甲', 41], ['演示侧栏乙', 7]] as const;
const ENTITY_TAGS = [['演示侧栏甲', 23], ['演示侧栏乙', 5]] as const;
const withoutPlayer = (problems: string[]) => problems.filter((line) => !line.includes('VIDEOJS'));
const PERFORMER = '篠田ゆう';
const THUMB = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#456"/></svg>';

/** 女优资料页要的资料、作品、照片与图。 */
async function serveEntity(page: Page): Promise<void> {
  const entity = {
    id: 90_101, kind: 'performer', canonical_name: PERFORMER, aliases: [], display_aliases: [], user_aliases: [],
    asset_count: 6, tags: [], related_performers: [], links: [], metadata: {}, has_image: false, has_avatar: false,
    avatar_focus: null, representative_asset_id: null, entry_links: [], feed: null,
  };
  const items = Array.from({ length: 6 }, (_, at) => ({
    id: 91_000 + at, name: `SSIS-${100 + at} 演示作品 ${at + 1}`, code: `SSIS-${100 + at}`, is_jav: true, has_thumb: true,
    duration: 7200, studio: 'S1 NO.1 STYLE', tags: [],
  }));
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: entity }));
  await page.route((url) => url.pathname === '/api/items', (route) => route.fulfill({ json: {
    items: Number(new URL(route.request().url()).searchParams.get('offset') || 0) ? [] : items, total: items.length, has_more: false,
  } }));
  await page.route((url) => url.pathname === '/api/photos', (route) =>
    route.fulfill({ json: { total: 0, sample_total: 0, sets: [], items: [], has_more: false, seed: '' } }));
  await page.route((url) => url.pathname === '/api/feeds/discoveries', (route) =>
    route.fulfill({ json: { ok: true, more: false, items: [] } }));
  await page.route(/\/(?:thumb|poster|photo-thumb|cover)\?/, (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: THUMB }));
}

/** 把 `/api/facets` 的内容标签换成按语境给的那一份；交回收到的查询串，好看实体页是不是按实体口径取的。 */
async function scopeFacets(page: Page): Promise<string[]> {
  const seen: string[] = [];
  await page.route((url) => url.pathname === '/api/facets', async (route) => {
    const url = new URL(route.request().url());
    seen.push(url.search);
    const real = await (await route.fetch()).json() as Record<string, unknown>;
    const rows = url.searchParams.get('scope_kind') ? ENTITY_TAGS : HOME_TAGS;
    await route.fulfill({ json: { ...real, tags: rows.map(([k, n]) => ({ k, n, cat: 'general' })) } });
  });
  return seen;
}

/** 抽屉里某一组的 chip：值与计数徽标的数字，按画出来的先后。 */
const chips = (page: Page, group: string) =>
  page.locator(`#drawer [data-sidebar-group="${group}"] [data-sidebar-chip]`).evaluateAll((nodes) =>
    nodes.map((node) => [node.getAttribute('data-val') ?? node.getAttribute('data-follow-drawer-tag'),
      node.querySelector('[data-sidebar-count]')?.textContent ?? null]));

const navKeys = (page: Page) =>
  page.locator('#drawer [data-sidebar-nav] [data-nav]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-nav')));

/** 点开一组（初始只有正在生效的组展开，收着的组 `inert`，点不到里面的 chip）。 */
async function openGroup(page: Page, group: string): Promise<void> {
  const toggle = page.locator(`#drawer [data-sidebar-group="${group}"] > [data-sidebar-toggle]`);
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await page.locator(`#drawer [data-sidebar-group="${group}"] [data-sidebar-collapse]:not([inert])`).waitFor({ timeout: 5_000 });
}

describe('侧栏岛', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  it('拖动一项改导航顺序：当场重排、写进设置，刷新之后照新顺序', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page, server } = opened;
    try {
      assert.deepEqual(await navKeys(page), ORDER, '侧栏没有按设置里的顺序画');
      const from = (await page.locator('#drawer [data-nav="tags"]').boundingBox())!;
      const to = (await page.locator('#drawer [data-nav="follow"]').boundingBox())!;
      await page.mouse.move(from.x + 40, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + 44, from.y + from.height / 2 - 6, { steps: 4 });
      await page.mouse.move(to.x + 40, to.y + 6, { steps: 8 });
      await page.mouse.move(to.x + 42, to.y + 7, { steps: 2 });
      assert.equal(await page.locator('#drawer [data-nav="tags"]').getAttribute('data-dragging'), '', '被拖的那一项没有标出来');
      assert.equal(await page.locator('#drawer [data-nav="follow"]').getAttribute('data-drop'), 'before', '落点没有画在目标上沿');
      await page.mouse.up();
      const moved = ['', 'tags', 'follow', 'jav', 'performers', 'studios', 'flagged', 'manage'];
      await page.waitForFunction((want) => JSON.stringify([...document.querySelectorAll('#drawer [data-sidebar-nav] [data-nav]')]
        .map((node) => node.getAttribute('data-nav'))) === want, JSON.stringify(moved), { timeout: 5_000 });
      const posts = server.posts.filter((post) => post.path === '/api/settings' && 'sidebarOrder' in post.body);
      assert.deepEqual(posts.at(-1)?.body.sidebarOrder, moved, '新顺序没有写进设置');
      assert.equal(await page.locator('#drawer [data-dragging], #drawer [data-drop]').count(), 0, '松手后拖动标记没有清掉');

      await page.reload({ waitUntil: 'load' });
      await page.locator('#drawer [data-sidebar-nav] [data-nav]').first().waitFor({ timeout: 15_000 });
      await settle(page);
      assert.deepEqual(await navKeys(page), moved, '刷新之后侧栏回到了旧顺序');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('首页与实体页各按自己的口径列内容标签与计数；点一枚 chip 就按下并进地址', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    try {
      const seen = await scopeFacets(page);
      await page.reload({ waitUntil: 'load' });
      await page.locator('#drawer [data-sidebar-group="内容标签"]').waitFor({ state: 'attached', timeout: 15_000 });
      await settle(page);
      assert.deepEqual(await chips(page, '内容标签'), HOME_TAGS.map(([k, n]) => [k, String(n)]), '首页的内容标签不是全库那一份');
      assert.ok((await chips(page, '来源')).some(([value, count]) => value === 'local' && Number(count) > 0), '来源那一组没有本机的计数');

      await openGroup(page, '内容标签');
      const chip = page.locator(`#drawer [data-sidebar-chip][data-key="tag"][data-val="${HOME_TAGS[0][0]}"]`);
      await chip.click();
      await page.waitForFunction((tag) => new URL(location.href).searchParams.get('tag') === tag, HOME_TAGS[0][0], { timeout: 5_000 });
      await page.locator(`#drawer [data-sidebar-chip][data-val="${HOME_TAGS[0][0]}"][aria-pressed="true"]`).waitFor({ timeout: 5_000 });
      assert.ok(seen.some((query) => new URLSearchParams(query).get('tag') === HOME_TAGS[0][0]), '按下 chip 之后聚合没有按这条筛选重取');

      await serveEntity(page);
      await page.goto(new URL(`/performers/${encodeURIComponent(PERFORMER)}`, page.url()).toString(), { waitUntil: 'load' });
      await page.locator('#drawer [data-sidebar-group="内容标签"]').waitFor({ state: 'attached', timeout: 15_000 });
      await settle(page);
      assert.ok(seen.some((query) => {
        const params = new URLSearchParams(query);
        return params.get('scope_kind') === 'performer' && params.get('scope_name') === PERFORMER;
      }), `实体页没有按实体口径取聚合：${seen.join(' | ')}`);
      assert.deepEqual(await chips(page, '内容标签'), ENTITY_TAGS.map(([k, n]) => [k, String(n)]), '实体页列的不是这个实体的计数');
      assert.deepEqual(opened.problems, []);
    } finally {
      await page.unrouteAll({ behavior: 'ignoreErrors' });
      await opened.close();
    }
  });

  it('关注页按这一版条目数标签，关注详情只列这一条的标签、每枚计 1', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, '/follow', DESKTOP);
    const { page } = opened;
    try {
      await page.locator('#drawer [data-sidebar-group="内容标签"] [data-follow-drawer-tag]').first().waitFor({ state: 'attached', timeout: 10_000 });
      const listed = await chips(page, '内容标签');
      const shown = await page.locator('[data-follow-list] > [data-follow-item]').count();
      assert.ok(listed.length > 1, '关注页的标签组是空的');
      assert.ok(listed.every(([, count]) => Number(count) >= 1), `关注页有标签没带计数：${JSON.stringify(listed)}`);
      assert.equal(listed.reduce((sum, [, count]) => sum + Number(count), 0), shown * 2,
        '每条更新两枚标签，计数之和应是条目数的两倍');

      await page.locator('[data-follow-item="1002"] [data-follow-open]').click();
      await page.locator('#stage[open] [data-follow-detail-side]').waitFor({ timeout: 10_000 });
      await page.waitForFunction(() => document.querySelectorAll('#drawer [data-follow-drawer-tag]').length === 2, undefined, { timeout: 5_000 });
      assert.deepEqual((await chips(page, '内容标签')).sort(), [['3d', '1'], ['outdoor', '1']], '关注详情列的不是这一条的标签');
      await page.locator('#closeStage').click();
      await page.waitForFunction((count) => document.querySelectorAll('#drawer [data-follow-drawer-tag]').length === count,
        listed.length, { timeout: 5_000 });
      assert.deepEqual(await chips(page, '内容标签'), listed, '关掉详情之后关注页的标签计数变了');

      /* 抽屉里的关注标签是单选：点一枚就只按这一枚筛，抽屉随之收起，让出来的是筛过的列表。 */
      await openGroup(page, '内容标签');
      const [tag] = listed[0]!;
      await page.locator(`#drawer [data-follow-drawer-tag="${tag}"]`).click();
      await page.waitForFunction((want) => new URL(location.href).searchParams.get('tag') === want
        && !document.querySelector('#drawer')?.classList.contains('open'), tag, { timeout: 5_000 });
      await page.locator('#filterBtn').click();
      await page.locator(`#drawer.open [data-follow-drawer-tag="${tag}"][aria-pressed="true"]`).waitFor({ timeout: 5_000 });
      const pressed = await page.locator('#drawer [data-follow-drawer-tag][aria-pressed="true"]').count();
      assert.equal(pressed, 1, '关注标签按下了不止一枚');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('关注页侧栏有来源、时长、内容标签三组；来源与时长进地址、按它向服务端取数，抽屉不收；刷新照地址按下', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, '/follow', DESKTOP);
    const { page } = opened;
    const lists: URLSearchParams[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/follow' && !url.searchParams.has('item')) lists.push(url.searchParams);
    });
    const groups = () => page.locator('#drawer [data-sidebar-group]').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-sidebar-group')));
    try {
      await page.locator('#drawer [data-sidebar-group="来源"] [data-sidebar-chip]').first().waitFor({ state: 'attached', timeout: 10_000 });
      assert.deepEqual(await groups(), ['来源', '时长', '内容标签'], '关注页侧栏的分组不对');
      assert.deepEqual((await chips(page, '来源')).map(([value]) => value), ['kemono', 'rule34xxx', 'fanbox'],
        '来源组不是全库那几个站');

      await openGroup(page, '来源');
      await page.locator('#drawer [data-sidebar-chip][data-key="provider"][data-val="rule34xxx"]').click();
      await page.waitForFunction(() => new URL(location.href).searchParams.get('provider') === 'rule34xxx', undefined, { timeout: 5_000 });
      await page.locator('#drawer.open [data-sidebar-chip][data-val="rule34xxx"][aria-pressed="true"]').waitFor({ timeout: 5_000 });
      await page.waitForFunction(() => document.querySelectorAll('#drawer [data-sidebar-group="来源"] [data-sidebar-chip]').length === 3,
        undefined, { timeout: 5_000 });
      assert.ok(lists.some((params) => params.get('provider') === 'rule34xxx'), '按下来源之后没有按它向服务端取数');

      await openGroup(page, '时长');
      await page.locator('#durMin').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => new URL(location.href).searchParams.get('dur_min') === '300', undefined, { timeout: 5_000 });
      await page.waitForFunction(() => document.querySelector('#drawer')?.classList.contains('open'), undefined, { timeout: 5_000 });
      assert.ok(lists.some((params) => params.get('dur_min') === '300' && params.get('provider') === 'rule34xxx'),
        '拉时长之后没有带着两条筛选向服务端取数');
      assert.ok(!new URL(page.url()).searchParams.has('dur_max'), '右端没动却写进了上限');

      await page.reload({ waitUntil: 'load' });
      await page.locator('#drawer [data-sidebar-chip][data-val="rule34xxx"][aria-pressed="true"]').waitFor({ state: 'attached', timeout: 10_000 });
      assert.equal(await page.locator('#durMin').inputValue(), '5', '刷新之后时长左端没有照地址回到 5 分钟');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('作品详情开着时侧栏只剩导航，关掉之后列表那几组原样回来', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/', DESKTOP, { ready: `#grid [data-media-card][data-id="${ITEM.plain}"]` });
    const { page } = opened;
    try {
      await page.locator('#drawer [data-sidebar-group]').first().waitFor({ state: 'attached', timeout: 10_000 });
      const groups = () => page.locator('#drawer [data-sidebar-group]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-sidebar-group')));
      const before = await groups();
      const sources = await chips(page, '来源');
      assert.ok(before.length > 1, `首页的分组不全：${before.join('、')}`);
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"] [data-media-title]`).first().click();
      await page.locator('#stage[open] [data-item-side]').waitFor({ timeout: 10_000 });
      assert.deepEqual(await groups(), [], '详情开着时侧栏还挂着列表的筛选分组');
      assert.ok((await navKeys(page)).includes(''), '详情开着时导航那一列不在了');
      await page.locator('#closeStage').click();
      await page.waitForFunction((want) => JSON.stringify([...document.querySelectorAll('#drawer [data-sidebar-group]')]
        .map((node) => node.getAttribute('data-sidebar-group'))) === want, JSON.stringify(before), { timeout: 10_000 });
      assert.deepEqual(await chips(page, '来源'), sources, '关掉详情之后来源那一组的计数变了');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('切页时当前项玻璃保留节点，完整播放动画并停在选中项', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    try {
      await expectBody(page, '首页', [page.locator('#drawer.open [data-sidebar-nav] [aria-pressed="true"]')]);
      await page.locator('#drawer [data-sidebar-group]').first().waitFor({ state: 'attached', timeout: 15_000 });
      await settle(page);
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      for (const key of ['performers', 'tags', '']) {
        const run = await page.evaluate(async (target) => {
          const glide = document.querySelector<HTMLElement>('#drawer > [data-sidebar-glide]')!;
          (glide as unknown as { __mark: boolean }).__mark = true;
          const seen = new Set<Animation>();
          let cancelled = 0;
          const watch = () => glide.getAnimations().forEach((animation) => {
            if (seen.has(animation)) return;
            seen.add(animation);
            animation.addEventListener('cancel', () => { cancelled += 1 });
          });
          document.querySelector<HTMLElement>(`#drawer [data-sidebar-nav] [data-nav="${target}"]`)!.click();
          watch();
          const started = seen.size;
          await Promise.all([...seen].map((animation) => animation.finished));
          const now = document.querySelector('#drawer > [data-sidebar-glide]') as (HTMLElement & { __mark?: boolean }) | null;
          const pressed = document.querySelector<HTMLElement>('#drawer [data-sidebar-nav] [aria-pressed="true"]')!;
          return {
            same: !!now?.__mark, started, cancelled, to: now!.getBoundingClientRect().top,
            pressedKey: pressed.dataset.nav, pressedTop: pressed.getBoundingClientRect().top,
          };
        }, key);
        assert.equal(run.pressedKey, key, `点了 ${key || '首页'} 之后按下的不是它`);
        assert.ok(run.same, '切页之后那块玻璃换了节点，位移会从头起跑');
        assert.ok(run.started > 0, `切到 ${key || '首页'} 时玻璃没有起动画`);
        assert.equal(run.cancelled, 0, `切到 ${key || '首页'} 的途中玻璃的动画被掐断了`);
        assert.ok(Math.abs(run.to - run.pressedTop) <= 1, `玻璃停在 ${run.to}，当前项在 ${run.pressedTop}`);
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('手机宽度下抽屉收着、点开盖在页面上，点遮罩或点导航都收回去；滚动层仍挂着覆盖式滚动条', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', PHONE);
    const { page } = opened;
    try {
      await page.locator('#drawer [data-sidebar-nav] [data-nav]').first().waitFor({ state: 'attached', timeout: 15_000 });
      await settle(page);
      const state = () => page.evaluate(() => {
        const drawer = document.querySelector<HTMLElement>('#drawer')!;
        return {
          open: drawer.classList.contains('open'), inert: drawer.inert,
          expanded: document.querySelector('#filterBtn')!.getAttribute('aria-expanded'),
          scrim: getComputedStyle(document.querySelector('#scrim')!).display,
          left: Math.round(drawer.getBoundingClientRect().left),
        };
      });
      const closed = await state();
      assert.equal(closed.open, false, '手机上一进来抽屉就开着');
      assert.equal(closed.inert, true, '收着的抽屉没有 inert，Tab 会走进屏幕外面');
      assert.equal(closed.scrim, 'none', '抽屉收着时遮罩还在渲染树里');
      assert.ok(closed.left < 0, '收着的抽屉没有移出屏幕');

      await page.locator('#filterBtn').tap();
      await page.waitForFunction(() => document.querySelector('#drawer')?.classList.contains('open'));
      const shown = await state();
      assert.deepEqual([shown.inert, shown.expanded, shown.scrim, shown.left], [false, 'true', 'block', 12], '点开之后抽屉没有盖进来');

      assert.equal(await page.evaluate(() => document.elementFromPoint(370, 420)?.id), 'scrim', '抽屉右边那一截点到的不是遮罩');
      await page.mouse.click(370, 420);
      await page.waitForFunction(() => !document.querySelector('#drawer')?.classList.contains('open'));
      assert.deepEqual((await state()).scrim, 'none', '点遮罩收起之后遮罩没退出渲染树');

      await page.locator('#filterBtn').tap();
      await page.waitForFunction(() => document.querySelector('#drawer')?.classList.contains('open'));
      await page.locator('#drawer [data-sidebar-nav] [data-nav="tags"]').tap();
      await page.waitForFunction(() => location.pathname === '/tags' && !document.querySelector('#drawer')?.classList.contains('open'),
        undefined, { timeout: 5_000 });

      const scroller = await page.evaluate(() => {
        const scroll = document.querySelector<HTMLElement>('#drawerScroll')!;
        const drawer = document.querySelector<HTMLElement>('#drawer')!;
        return {
          parent: scroll.parentElement?.id, overflow: getComputedStyle(scroll).overflowY,
          drawerOverflow: getComputedStyle(drawer).overflowY,
          nav: !!scroll.querySelector(':scope > [data-sidebar-nav]'),
          tracks: drawer.querySelectorAll(':scope > .ovtrack').length,
        };
      });
      assert.deepEqual(scroller, { parent: 'drawer', overflow: 'auto', drawerOverflow: 'hidden', nav: true, tracks: 2 },
        '滚的不是抽屉里那一层，或覆盖式滚动条没有接在它上面');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });
});
