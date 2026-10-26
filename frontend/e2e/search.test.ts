/* 顶栏搜索（`search` 岛，ADR-0031）：下拉栏里有什么、键盘怎么走、提交之后去哪儿。
 *
 * 补全（`/api/suggest`）与搜索记录（`/api/search-history`）在这里各给一份桩，好数请求、好看写进去的
 * 是什么；作品与详情沿用 `item-fixture.ts`。下拉栏的排版（两栏、页签、封面格）由 `design-people.test.ts` 量。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, VIEWPORTS } from './harness.ts';
import { openItemPage, type ItemVisit } from './item-fixture.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const MENU = '#searchMenu';
const OPTION = `${MENU} [data-search-option]`;

/** 「七海」命中一位女优和一枚标签；别的词一条都没有。 */
const SUGGEST = {
  q: '七海',
  groups: [
    { kind: 'performer', label: '女优', total: 1, items: [
      { value: '七海ひな', n: 12, matched: '', entity_id: 90_001, has_image: false, rep: null, agency: '', works: [] },
    ] },
    { kind: 'tag', label: '标签', total: 1, items: [{ value: '七海の夏', n: 3, matched: '' }] },
  ],
};

interface Search extends ItemVisit {
  suggest: URL[];
  /** 桩里存着的搜索记录，最近的在前。 */
  history: string[];
  posts: { query?: string; operation?: string }[];
}

const fold = (value: string) => value.normalize('NFKC').trim().toLocaleLowerCase();

async function openSearch(browser: Browser, { limit = 10, history = [] as string[], viewport = DESKTOP } = {}): Promise<Search> {
  const opened = await openItemPage(browser, '/', viewport, {
    settings: { searchHistoryLimit: limit }, ready: '#grid [data-media-card]',
  });
  const { page } = opened;
  const suggest: URL[] = [];
  const stored = [...history];
  const posts: Search['posts'] = [];
  await page.route(/\/api\/suggest\?/, (route) => {
    const url = new URL(route.request().url());
    suggest.push(url);
    const q = url.searchParams.get('q') || '';
    return route.fulfill({ json: q === SUGGEST.q ? SUGGEST : { q, groups: [] } });
  });
  await page.route(/\/api\/search-history/, (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      const body = request.postDataJSON() as Search['posts'][number];
      posts.push(body);
      const query = body.query || '';
      const kept = stored.filter((value) => fold(value) !== fold(query));
      stored.splice(0, stored.length, ...(body.operation === 'remove' ? kept : [query, ...kept]));
      return route.fulfill({ json: { ok: true } });
    }
    const want = Number(new URL(request.url()).searchParams.get('limit')) || 10;
    return route.fulfill({ json: { items: stored.slice(0, want) } });
  });
  await page.locator(`${MENU} > .peach-react`).waitFor({ state: 'attached', timeout: 15_000 });
  return { ...opened, suggest, history: stored, posts };
}

const values = (page: Page) => page.locator(OPTION).evaluateAll((rows) =>
  rows.map((row) => row.getAttribute('data-search-value')));
const active = (page: Page) => page.locator(OPTION).evaluateAll((rows) =>
  rows.flatMap((row) => (row.hasAttribute('data-active') ? [row.getAttribute('data-search-value')] : [])));
const query = (page: Page) => new URL(page.url()).searchParams.get('q');

async function typeQuery(opened: Search, text = SUGGEST.q) {
  const { page } = opened;
  await page.locator('#q').click();
  await page.keyboard.type(text);
  await page.locator(`${OPTION}[data-search-value="七海の夏"]`).waitFor({ timeout: 5_000 });
}

describe('顶栏搜索', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 收起输入法保留搜索建议，焦点移出搜索区或点击页面关闭`, { timeout: 60_000 }, async () => {
      const opened = await openSearch(browser, { viewport, history: ['最近搜索'] });
      try {
        const { page } = opened;
        if (viewport.mobile) await page.locator('#searchBtn').click();
        await typeQuery(opened);
        await page.locator('#q').evaluate(field => field.blur());
        await page.waitForTimeout(220);
        assert.equal(await page.locator(MENU).isVisible(), true, '输入法收起后建议仍可选择');
        assert.equal(await page.locator('#q').inputValue(), SUGGEST.q);
        await page.locator('#grid [data-media-title]').first().focus();
        await page.waitForTimeout(220);
        assert.equal(await page.locator(MENU).isVisible(), false, '键盘焦点移出搜索区关闭建议');
        await page.locator('#q').focus();
        await page.locator(MENU).waitFor({ state: 'visible' });
        await page.locator('#q').evaluate(field => field.blur());
        await page.waitForTimeout(220);
        await page.locator('#grid [data-media-title]').first().dispatchEvent('pointerdown');
        await page.locator(MENU).waitFor({ state: 'hidden' });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });

    it(`${viewport.name} 空搜索显示作品封面、作者头像和新作订阅入口`, { timeout: 60_000 }, async () => {
      const opened = await openSearch(browser, { viewport, history: ['最近搜索'] });
      try {
        const { page } = opened;
        await page.route(/\/api\/jav\/discoveries\?/, route => route.fulfill({ json: { items: [
          { id: 11, asset_id: 11, code: 'ABW-101', title: 'JAV 作品', is_jav: true, has_cover: true,
            performer_refs: [{ id: 90001, name: '七海ひな', has_image: true }] },
        ] } }));
        await page.route(/\/api\/follow\?/, route => route.fulfill({ json: {
          sources: [{ id: 1, author_key: 'name:作者', label: '作者', ref: '作者', provider: 'pixiv', official_avatar_url: '/poster?id=11' }],
          groups: [{ primary: { id: 901, source_id: 1, title: '关注作品', thumb_url: '/poster?id=11', media_kind: 'video', status: 'saved' },
            variants: [{ id: 902, source_id: 1, title: '图片作品', thumb_url: '/poster?id=12', media_kind: 'image', status: 'seen',
              media_items: [{ index: 2, media_kind: 'image', thumb_url: '/poster?id=13' }] }], duplicates: [] }],
        } }));
        await page.route(/\/api\/follow\/authors\?/, route => route.fulfill({ json: {
          items: [{ k: '作者', key: 'name:作者', entity_id: 90002 }],
        } }));
        await page.route(/\/api\/feeds\/discoveries\?/, route => route.fulfill({ json: { items: [
          { id: 91, code: 'ABW-102', title: '订阅新作', has_cover: true },
        ] } }));
        if (viewport.mobile) await page.locator('#searchBtn').click();
        await page.locator('#q').click();
        await page.locator(`${MENU} [data-search-recommendations]`).waitFor();
        assert.equal(await page.locator(`${MENU} [data-kind="author"] [data-search-face] img`).count(), 1);
        for (const kind of ['asset', 'jav', 'post', 'photo', 'release']) {
          const cover = page.locator(`${MENU} [data-kind="${kind}"] [data-search-pic] img`).first();
          assert.ok(await cover.count(), `${kind} 没有图片`);
          const box = await cover.boundingBox();
          assert.ok(box && box.width >= 80, `${kind} 没有以封面展示`);
        }
        const overflow = await page.locator(MENU).evaluate(el => ({
          width: el.clientWidth, scrollWidth: el.scrollWidth, right: el.getBoundingClientRect().right,
          viewport: innerWidth,
        }));
        assert.ok(overflow.scrollWidth <= overflow.width + 1);
        assert.ok(overflow.right <= overflow.viewport + 1);
        assert.equal(await page.locator(`${MENU} [data-kind="photo"] h3 a`).getAttribute('href'), '/follow?media=images');
        assert.ok((await page.locator(`${MENU} [data-kind="photo"] img`).getAttribute('src'))?.includes('id=13'));
        await page.screenshot({ path: `../build/agent-verification/browser/search-recommendations-${viewport.name}.png` });
        const subscription = page.locator(`${MENU} [data-kind="performer"] h3 a`);
        await subscription.focus();
        await page.waitForTimeout(180);
        assert.equal(await page.locator(MENU).isVisible(), true, '菜单内键盘焦点关闭了推荐');
        await subscription.press('Enter');
        await page.waitForURL('**/subscriptions?tab=feeds');
        assert.ok(!new URL(page.url()).searchParams.has('q'));
      } finally { await opened.close(); }
    });
  }

  for (const viewport of VIEWPORTS) {
    it(`单类视频预览可滚动，完整结果入口可由键盘提交（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openSearch(browser, { viewport });
      try {
        const { page } = opened;
        await page.route(/\/api\/suggest\?/, (route) => {
          const url = new URL(route.request().url());
          const limit = Math.min(53, Number(url.searchParams.get('limit')) || 5);
          const items = Array.from({ length: limit }, (_, i) => ({
            value: `ABF-${i + 1}`, id: i + 1, code: `ABF-${i + 1}`, title: '演示作品',
            card: { id: 11, code: `ABF-${i + 1}`, is_jav: true, has_cover: true },
          }));
          return route.fulfill({ json: { q: 'abf', groups: [{ kind: 'asset', label: '视频', total: 53, items }] } });
        });
        if (viewport.mobile) await page.locator('#searchBtn').click();
        await page.locator('#q').fill('abf');
        await page.locator(`${MENU} [data-search-option="work"]`).first().waitFor({ state: 'visible' });
        assert.equal(await page.locator(`${MENU} [data-search-option="work"]`).count(), 20);
        const geometry = await page.locator(MENU).evaluate((el) => ({
          overflow: el.scrollHeight > el.clientHeight, bottom: el.getBoundingClientRect().bottom,
          viewport: innerHeight, pageScroll: scrollY,
        }));
        assert.ok(geometry.overflow, '搜索面板没有可滚动内容');
        assert.ok(geometry.bottom <= geometry.viewport, '搜索面板超出视口');
        await page.locator(MENU).hover();
        await page.mouse.wheel(0, 400);
        await page.waitForFunction(() => document.querySelector('#searchMenu')!.scrollTop > 0);
        assert.equal(await page.evaluate(() => scrollY), geometry.pageScroll, '滚动搜索面板带动了背景页面');
        for (let i = 0; i < 21; i++) await page.keyboard.press('ArrowDown');
        const more = page.locator(`${MENU} [data-search-option="more"]`);
        assert.equal(await more.getAttribute('data-active'), '');
        assert.equal(await more.textContent(), '查看全部 53 个视频');
        await page.keyboard.press('Enter');
        await page.waitForFunction(() => new URLSearchParams(location.search).get('q') === 'abf');
        await page.locator(MENU).waitFor({ state: 'hidden' });
      } finally {
        await opened.close();
      }
    });
  }

  it('连敲几个字只问一次补全；下拉栏照后端给的组次序摆', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser);
    try {
      const { page, suggest } = opened;
      await typeQuery(opened);
      assert.deepEqual(suggest.map((url) => url.searchParams.get('q')), [SUGGEST.q], '连敲两个字发了不止一次补全');
      assert.deepEqual(await page.locator(`${MENU} [data-search-group] > h3`).allTextContents(), ['女优', '标签']);
      assert.deepEqual(await values(page), ['七海ひな', '七海の夏']);
      assert.equal(query(opened.page), null, '只是敲字，不该提交');
    } finally {
      await opened.close();
    }
  });

  it('软键盘的回车键标成「搜索」', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser);
    try {
      assert.equal(await opened.page.locator('#q').getAttribute('enterkeyhint'), 'search');
    } finally {
      await opened.close();
    }
  });

  it('上下键环绕着选；回车按选中的那一项搜，记进搜索记录，下拉栏收起', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser);
    try {
      const { page, posts } = opened;
      await typeQuery(opened);
      assert.deepEqual(await active(page), [], '刚画出来就有选中');
      for (const [key, want] of [['ArrowDown', '七海ひな'], ['ArrowDown', '七海の夏'], ['ArrowDown', '七海ひな'],
        ['ArrowUp', '七海の夏']] as const) {
        await page.keyboard.press(key);
        assert.deepEqual(await active(page), [want], `${key} 之后选中的不对`);
      }
      assert.equal(await page.locator('#q').inputValue(), SUGGEST.q, '上下键不该改输入框里的字');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => new URLSearchParams(location.search).get('q') === '七海の夏');
      await page.locator(MENU).waitFor({ state: 'hidden', timeout: 5_000 });
      assert.equal(await page.locator('#q').inputValue(), '七海の夏');
      assert.deepEqual(posts, [{ query: '七海の夏' }]);
      assert.notEqual(await page.evaluate(() => document.activeElement?.id), 'q', '提交之后焦点还留在输入框里');
    } finally {
      await opened.close();
    }
  });

  it('回车选中一位女优：打开她的资料页，不绕一趟搜索', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser);
    try {
      const { page, posts } = opened;
      await typeQuery(opened);
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('Enter');
      await page.waitForURL('**/performers/90001');
      assert.equal(query(page), null);
      assert.deepEqual(posts, [{ query: '七海ひな' }], '记进搜索记录的应当是这个名字');
    } finally {
      await opened.close();
    }
  });

  it('Escape 先只关下拉栏、字留着；再按一次才清空输入框', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser);
    try {
      const { page } = opened;
      await typeQuery(opened);
      await page.keyboard.press('Escape');
      await page.locator(MENU).waitFor({ state: 'hidden', timeout: 5_000 });
      assert.equal(await page.locator('#q').inputValue(), SUGGEST.q, '第一下 Escape 把字也清了');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'q', '第一下 Escape 把焦点带走了');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#q').inputValue(), '', '第二下 Escape 没有清空');
      assert.equal(query(page), null, 'Escape 不该提交');
    } finally {
      await opened.close();
    }
  });

  it('组字期间的回车与方向键是给输入法的：不提交、不挪选中、不关下拉栏', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser);
    try {
      const { page, posts } = opened;
      await typeQuery(opened);
      await page.locator('#q').evaluate((input) => {
        input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '' }));
        for (const key of ['ArrowDown', 'Enter']) {
          input.dispatchEvent(new KeyboardEvent('keydown', { key, isComposing: true, bubbles: true, cancelable: true }));
        }
      });
      await page.waitForTimeout(300);
      assert.equal(query(page), null, '组字期间的回车提交了搜索');
      assert.deepEqual(posts, []);
      assert.deepEqual(await active(page), [], '组字期间的方向键挪了选中');
      assert.equal(await page.locator(MENU).isVisible(), true, '组字期间的回车把下拉栏收了');
      await page.locator('#q').evaluate((input) => {
        input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '' }));
      });
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => new URLSearchParams(location.search).get('q') === '七海');
      assert.deepEqual(posts, [{ query: '七海' }]);
    } finally {
      await opened.close();
    }
  });

  it('搜索记录照设置的条数摆；删一条只摘掉这一条，下拉栏和焦点都不走；新搜的词排到最前', { timeout: 60_000 }, async () => {
    const opened = await openSearch(browser, { limit: 3, history: ['甲片', '乙片', '丙片', '丁片'] });
    try {
      const { page, posts, history } = opened;
      const recent = () => page.locator(`${MENU} [data-search-group]`, { hasText: '搜索记录' })
        .locator('[data-search-option]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-search-value')));
      await page.locator('#q').click();
      await page.locator(`${OPTION}[data-search-value="丙片"]`).waitFor({ timeout: 5_000 });
      assert.deepEqual(await recent(), ['甲片', '乙片', '丙片'], '搜索记录没有照设置只摆三条');
      await page.locator(`${MENU} [data-remove-history="乙片"]`).click();
      await page.waitForFunction(() => !document.querySelector('#searchMenu [data-search-value="乙片"]'));
      assert.deepEqual(await recent(), ['甲片', '丙片']);
      assert.deepEqual(posts, [{ operation: 'remove', query: '乙片' }]);
      await page.waitForTimeout(300);
      assert.equal(await page.locator(MENU).isVisible(), true, '删一条记录把下拉栏收了');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'q', '删一条记录把焦点抢走了');
      await page.keyboard.type('戊片');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => new URLSearchParams(location.search).get('q') === '戊片');
      assert.deepEqual(posts.at(-1), { query: '戊片' });
      assert.deepEqual(history.slice(0, 3), ['戊片', '甲片', '丙片']);
      await page.locator('#q').evaluate((input: HTMLInputElement) => { input.value = ''; });
      await page.locator('#q').click();
      await page.locator(`${OPTION}[data-search-value="戊片"]`).waitFor({ timeout: 5_000 });
      assert.deepEqual(await recent(), ['戊片', '甲片', '丙片']);
    } finally {
      await opened.close();
    }
  });
});
