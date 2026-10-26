/* 设计决定：人物与厂牌资料页。读 `getComputedStyle` 断言用户定过的外观；共用的桩与浏览器生命周期在 `design-fixture.ts`。 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { expectBody, layout, requiredEnv, settle, visit, VIEWPORTS } from './harness.ts';
import {
  DESKTOP, MOBILE, tokenColor, PIXEL, TASTE_FACE, WIDE, openCatalog, openCatalogFixture, openFollowManage,
  openPerformer, openPhotoWall, photoWallFaces, PROFILED, openProfiledPerformer, popmenuShadow, SUGGEST,
  stubSuggest, installDesignBrowser,
} from './design-fixture.ts';

describe('设计决定：人物与厂牌资料页', () => {
  let browser: Browser;
  installDesignBrowser((next) => { browser = next; });

  for (const [viewport, split] of [[WIDE, true], [MOBILE, false]] as const) {
    it(`搜索下拉按种类分页签；宽屏分两栏、人名带头像与近作、视频排成封面格，窄屏一栏（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      try {
        const page = opened.page;
        const kinds = await stubSuggest(page);
        await expectBody(page, '/', [page.locator('#grid [data-media-card][data-id]').first()]);
        await settle(page);
        if (viewport.mobile) await page.locator('#searchBtn').click();
        await page.locator('#q').fill(SUGGEST.q);
        const menu = page.locator('#searchMenu');
        await menu.locator('[data-search-option="person"]').first().waitFor({ state: 'visible', timeout: 5_000 });
        const shown = await page.evaluate(() => {
          const menuBox = document.querySelector('#searchMenu')!.getBoundingClientRect();
          const results = document.querySelector<HTMLElement>('#searchMenu [data-search-results]')!;
          const columns = [...results.querySelectorAll(':scope > [data-search-col]')];
          const person = document.querySelector('#searchMenu [data-search-option="person"]')!;
          /* 命中的别名与作品数是这一行的注脚：字号小一档、字色退一档，作品数靠右。 */
          const footnote = (selector: string) => {
            const note = document.querySelector<HTMLElement>(`#searchMenu ${selector}`)!;
            const row = note.closest('[data-search-option]')!;
            const name = note.previousElementSibling!;
            const [noteStyle, nameStyle] = [getComputedStyle(note), getComputedStyle(name)];
            return {
              smaller: parseFloat(noteStyle.fontSize) < parseFloat(nameStyle.fontSize),
              quieter: noteStyle.color !== nameStyle.color,
              trailing: Math.round(row.getBoundingClientRect().right - note.getBoundingClientRect().right),
            };
          };
          return {
            matched: footnote('[data-search-matched]'),
            count: footnote('[data-search-n]'),
            radius: getComputedStyle(person).borderRadius === getComputedStyle(document.documentElement).getPropertyValue('--control-radius').trim(),
            tabs: [...document.querySelectorAll('#searchMenu [role="tab"]')].map((tab) => tab.textContent!.trim()),
            tracks: getComputedStyle(results).gridTemplateColumns.split(' ').filter((track) => track !== 'none').length,
            divider: getComputedStyle(results, '::before').content,
            dividerHeight: parseFloat(getComputedStyle(results, '::before').height),
            resultsHeight: results.getBoundingClientRect().height,
            right: columns.at(-1)!.querySelector('[data-search-group]')?.getAttribute('data-kind'),
            videoGrid: getComputedStyle(document.querySelector('#searchMenu [data-search-group][data-kind="asset"] [data-search-items]')!).display,
            peeks: getComputedStyle(person.querySelector('[data-search-peeks]')!).display,
            peekCount: person.querySelectorAll('[data-open-work]').length,
            sub: person.querySelector('[data-search-sub]')!.textContent,
            face: person.querySelector('[data-search-face] img')?.getAttribute('src'),
            inView: menuBox.left >= 0 && menuBox.right <= innerWidth,
            edges: [Math.round(menuBox.left), Math.round(innerWidth - menuBox.right)],
          };
        });
        assert.deepEqual(shown.tabs, ['全部', '女优2', '厂牌1', '视频12'], '页签不是「全部」加各类命中数');
        assert.equal(shown.tracks, split ? 2 : 0, split ? '宽屏下拉没有分两栏' : '窄屏下拉不该分栏');
        assert.equal(shown.divider, split ? '""' : 'none', split ? '宽屏两栏之间没有分隔线' : '窄屏一栏不该有分隔线');
        if (split) {
          assert.ok(Math.abs(shown.dividerHeight - shown.resultsHeight) <= 1,
            `分隔线高 ${shown.dividerHeight}px，两栏高 ${shown.resultsHeight}px`);
        }
        assert.equal(shown.right, 'asset', '视频不在最后一栏');
        assert.equal(shown.videoGrid, split ? 'grid' : 'block', split ? '宽屏视频没排成封面格' : '窄屏视频该一行一部');
        assert.equal(shown.peeks, split ? 'flex' : 'none', split ? '宽屏人名一行没摆近作' : '窄屏人名一行不摆近作');
        assert.equal(shown.peekCount, 4);
        assert.equal(shown.sub, '128 个视频 · Capsule Agency');
        assert.equal(shown.face, '/entity-image?kind=performer&id=90301&thumb=1');
        for (const [name, note] of [['别名', shown.matched], ['作品数', shown.count]] as const) {
          assert.ok(note.smaller && note.quieter, `${name}没有退成注脚：${JSON.stringify(note)}`);
        }
        assert.equal(shown.count.trailing, 10, '作品数没有靠右贴着行的内边距');
        assert.ok(shown.radius, '补全行的圆角不是 --control-radius');
        assert.ok(shown.inView, '下拉栏越出了视口');
        // 窄屏下拉栏盖过返回键那一列，和顶栏两侧一样各留 8，不缩进到搜索框底下。
        if (viewport.mobile) assert.deepEqual(shown.edges, [8, 8], '窄屏下拉栏两侧留白不是 8');
        const page_ = await layout(page);
        assert.ok(page_.scrollWidth <= page_.viewportWidth, `下拉把页面撑出了横向滚动：${page_.offenders.join('，')}`);

        await menu.getByRole('tab', { name: /^女优/ }).click();
        await page.waitForFunction(() => document.querySelectorAll('#searchMenu [data-search-group][data-kind]').length === 1);
        assert.equal(await menu.isVisible(), true, '点页签把下拉栏收掉了');
        assert.ok(kinds.includes('performer'), '选了一类没有按这一类去拉满');
        assert.equal(await menu.getByRole('tab', { name: /^女优/ }).getAttribute('aria-selected'), 'true');

        // 人名那一行点开的是资料页，不是按名字再搜一遍。
        await page.keyboard.press('ArrowDown');
        await page.keyboard.press('Enter');
        await page.waitForURL(/\/performers\//, { timeout: 5_000 });
        await page.waitForURL('**/performers/90301');
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  it('只看视频时封面格按列均分铺满整栏，每格有宽度上限', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', WIDE);
    try {
      const page = opened.page;
      await stubSuggest(page);
      await expectBody(page, '/', [page.locator('article[data-media-card][data-id]').first()]);
      await settle(page);
      await page.locator('#q').fill(SUGGEST.q);
      const menu = page.locator('#searchMenu');
      await menu.locator('[data-search-option="person"]').first().waitFor({ state: 'visible', timeout: 5_000 });
      await menu.getByRole('tab', { name: /^视频/ }).click();
      await page.waitForFunction(() => document.querySelectorAll('#searchMenu [data-search-group][data-kind]').length === 1);
      const grid = await page.evaluate(() => {
        const items = document.querySelector('#searchMenu [data-search-group][data-kind="asset"] [data-search-items]')!;
        const box = items.getBoundingClientRect();
        const cards = [...items.querySelectorAll('[data-search-option="work"]')].map((card) => card.getBoundingClientRect());
        return {
          rows: new Set(cards.map((card) => Math.round(card.top))).size,
          widths: cards.map((card) => Math.round(card.width)),
          slack: Math.round(box.right - cards.at(-1)!.right),
        };
      });
      assert.equal(grid.rows, 1, '五部视频没排在同一行');
      assert.ok(grid.slack <= 1, `封面格右侧空出 ${grid.slack}px`);
      assert.ok(grid.widths.every((width) => width >= 96 && width <= 242), `封面宽度越出 96～242：${grid.widths.join('、')}`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('资料页照片墙：1280 宽小图 4 列、大图 2 列，手机 2 列；固定版式是方格，瀑布流按列填；格子是一块等图的空底', { timeout: 90_000 }, async () => {
    const opened = await openPhotoWall(browser);
    try {
      const { page } = opened;
      // 默认瀑布流、小图。
      assert.deepEqual(await photoWallFaces(page), { display: 'block', columns: 4, gap: '10px', square: true });
      const cell = await page.evaluate(() => {
        const node = document.querySelector<HTMLElement>('[data-local-wall] [data-photo-cell]')!;
        const style = getComputedStyle(node);
        const head = document.querySelector('[data-photo-group]')!;
        return {
          radius: style.borderRadius, cursor: style.cursor, sunk: style.backgroundColor,
          token: getComputedStyle(document.documentElement).getPropertyValue('--surface-radius').trim(),
          alt: getComputedStyle(node.querySelector('img')!).color,
          headTop: getComputedStyle(head).marginTop,
          rows: [head, head.querySelector('b')!].map((node) => getComputedStyle(node).lineHeight),
          code: getComputedStyle(head.querySelector('b')!).fontFamily,
          meta: getComputedStyle(head.querySelector('[data-photo-group-meta]')!).marginLeft !== '0px',
        };
      });
      assert.equal(cell.radius, cell.token, '照片格的圆角是 --surface-radius');
      assert.equal(cell.cursor, 'zoom-in');
      assert.equal(cell.alt, 'rgba(0, 0, 0, 0)', '等图时 alt 文件名不画出来');
      assert.notEqual(cell.sunk, 'rgba(0, 0, 0, 0)', '格子自带一块沉底色');
      assert.equal(cell.headTop, '28px', '每段上方留出段距');
      assert.deepEqual(cell.rows, ['20px', '20px'], '段头一行 20px，番号不按自己的字号撑高');
      assert.match(cell.code, /Cascadia Mono/, '段头番号是等宽字');
      assert.ok(cell.meta, '来源与张数靠右');
      assert.equal(await page.locator('#index [data-entity-more]').evaluate((node) => {
        const style = getComputedStyle(node);
        return `${style.height} ${style.borderRadius} ${style.fontSize} ${style.fontWeight}`;
      }), '36px 10px 14px 500', '载入更多是 36px 高、10px 圆角的 secondary 按钮');
      await page.locator('[data-entity-layout][aria-label="图片布局"] label:has(input[value="fixed"])').click();
      await page.waitForFunction(() => document.querySelector('[data-local-wall]')?.getAttribute('data-layout') === 'fixed');
      assert.deepEqual(await photoWallFaces(page), { display: 'grid', columns: 4, gap: '10px', square: true });
      // 顶栏那枚大小键在照片档里换的是照片的大小档，一次请求都不发。
      await page.locator('#density').click();
      await page.waitForFunction(() => document.querySelector('[data-local-wall]')?.getAttribute('data-size') === 'big');
      assert.deepEqual(await photoWallFaces(page), { display: 'grid', columns: 2, gap: '10px', square: true });
      assert.equal(await page.locator('#density').getAttribute('title'), '当前：大图');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
    const phone = await openPhotoWall(browser, MOBILE);
    try {
      const { page } = phone;
      assert.deepEqual(await photoWallFaces(page), { display: 'block', columns: 2, gap: '10px', square: true });
      await page.locator('[data-entity-layout][aria-label="图片布局"] label:has(input[value="fixed"])').click();
      await page.waitForFunction(() => document.querySelector('[data-local-wall]')?.getAttribute('data-layout') === 'fixed');
      assert.deepEqual(await photoWallFaces(page), { display: 'grid', columns: 2, gap: '8px', square: true });
      assert.deepEqual(phone.problems, []);
    } finally {
      await phone.close();
    }
  });

  it('空搜索的头像与封面分栏，搜索记录位于推荐之后，分隔线覆盖两栏', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', WIDE);
    try {
      const page = opened.page;
      const history = Array.from({ length: 10 }, (_, at) => `搜索记录 ${at + 1}`);
      await page.route(/\/api\/search-history\?/, (route) => route.fulfill({ json: { items: history } }));
      await page.reload({ waitUntil: 'load' });
      await expectBody(page, '/', [page.locator('article[data-media-card][data-id]').first()]);
      await settle(page);
      await page.locator('#q').click();
      await page.locator('#searchMenu [data-search-recommendations] [data-search-results][data-split]').waitFor({ state: 'visible', timeout: 5_000 });
      const split = await page.evaluate(() => {
        const results = document.querySelector('#searchMenu [data-search-results]')!;
        const [left, right] = [...results.querySelectorAll(':scope > [data-search-col]')].map((col) => col.getBoundingClientRect());
        const box = results.getBoundingClientRect();
        const line = getComputedStyle(results, '::before');
        const history = document.querySelector('#searchMenu [data-search-root] > [data-search-group]')!;
        return {
          box: box.height, leftWidth: left.width, rightWidth: right.width, line: parseFloat(line.height),
          historyAfter: history.getBoundingClientRect().top >= box.bottom,
          historyCount: history.querySelectorAll('[data-search-option]').length,
          offset: parseFloat(line.left) + parseFloat(line.width) / 2 - ((left.right + right.left) / 2 - box.left),
        };
      });
      assert.ok(split.rightWidth > split.leftWidth, '封面栏应比头像栏宽');
      assert.equal(split.historyAfter, true, '搜索记录应在推荐之后');
      assert.equal(split.historyCount, 10);
      assert.ok(Math.abs(split.line - split.box) <= 1, `分隔线高 ${split.line}px，两栏高 ${split.box}px`);
      assert.ok(Math.abs(split.offset) <= 1, `分隔线偏离两栏之间的缝 ${split.offset}px`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('资料表的标签只列前四个，余下的收进「+N」，浮层进顶层列全部标签', { timeout: 60_000 }, async () => {
    const opened = await openProfiledPerformer(browser, DESKTOP);
    try {
      const page = opened.page;
      const cell = await page.locator('[data-entity-facts] dd[data-fact="tags"]').evaluate((dd) => ({
        shown: [...dd.querySelectorAll(':scope > [data-fact-tag]')].map((tag) => tag.textContent!.trim()),
        more: dd.querySelector(':scope > [data-hero-more="fact"]')?.textContent?.trim() ?? '',
      }));
      assert.deepEqual(cell.shown, PROFILED.profile.tags.slice(0, 4), '标签那一格不是前四个');
      assert.equal(cell.more, '+5', '「+N」数的不是剩下那几个标签');
      const pop = page.locator('#entityTagPop');
      assert.equal(await pop.isVisible(), false);
      await page.locator('[data-hero-more="fact"]').hover();
      await pop.waitFor({ state: 'visible', timeout: 5_000 });
      const shown = await pop.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return {
          topLayer: element.matches(':popover-open'),
          inView: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
          title: element.querySelector('[data-hero-pop-head]')?.textContent?.trim(),
          tags: [...element.querySelectorAll('[data-fact-tag]')].map((tag) => tag.textContent!.trim()),
        };
      });
      assert.equal(shown.topLayer, true, '标签浮层没进顶层，会被资料卡的 overflow:hidden 裁掉');
      assert.ok(shown.inView, '标签浮层越出了视口');
      assert.equal(shown.title, '9 个标签');
      assert.deepEqual(shown.tags, PROFILED.profile.tags);
      await page.mouse.move(1, 1);
      await pop.waitFor({ state: 'hidden', timeout: 5_000 });
      // 点一下钉住：指针离开也不收，Escape 才收。
      await page.locator('[data-hero-more="fact"]').click();
      await page.mouse.move(1, 1);
      await page.waitForTimeout(300);
      assert.equal(await pop.isVisible(), true, '点按钉住的标签浮层指针一走就收了');
      await page.keyboard.press('Escape');
      assert.equal(await pop.isVisible(), false, 'Escape 收不起标签浮层');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('换头像的加号量在圆框上：整行比圆框高时也贴着圆框右下角', { timeout: 60_000 }, async () => {
    const opened = await openProfiledPerformer(browser, { name: 'wide', width: 1440, height: 900, mobile: false });
    try {
      const page = opened.page;
      const button = page.locator('[data-entity-portrait-wrap] [data-avatar-picker] button');
      await button.waitFor({ timeout: 15_000 });
      // 行有多高看她有多少资料、别名那一行折不折；这里直接把身份列撑高，量的是加号跟不跟圆框。
      await page.addStyleTag({ content: '[data-entity-identity]{padding-block:40px}' });
      const box = await page.evaluate(() => {
        const rect = (element: Element) => element.getBoundingClientRect();
        const wrap = rect(document.querySelector('[data-entity-portrait-wrap]')!);
        const circle = rect(document.querySelector('[data-entity-portrait]')!);
        const plus = rect(document.querySelector('[data-entity-portrait-wrap] [data-avatar-picker] button')!);
        return { wrap: wrap.height, circle: { right: circle.right, bottom: circle.bottom, height: circle.height },
          plus: { right: plus.right, bottom: plus.bottom } };
      });
      assert.ok(box.wrap > box.circle.height + 8, `这一行没有比圆框高，用例量不出偏移（行 ${box.wrap}，圆框 ${box.circle.height}）`);
      // Tailwind 的 `right-1 bottom-1`：按钮离圆框外框右、下各 4px。
      assert.ok(Math.abs(box.circle.bottom - box.plus.bottom - 4) < 1, `加号没有贴着圆框底边：${JSON.stringify(box)}`);
      assert.ok(Math.abs(box.circle.right - box.plus.right - 4) < 1, `加号没有贴着圆框右边：${JSON.stringify(box)}`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('女优页头深色主题：资料表与别名浮层跟着主题取墨色和浮层底', { timeout: 60_000 }, async () => {
    const opened = await openProfiledPerformer(browser, DESKTOP, 'dark');
    try {
      const page = opened.page;
      const ink = await tokenColor(page, '#main', '--ink');
      const muted = await tokenColor(page, '#main', '--muted');
      const ground = await tokenColor(page, '#main', '--ground');
      const facts = await page.locator('[data-entity-facts]').evaluate((dl) => ({
        dd: getComputedStyle(dl.querySelector('dd')!).color, dt: getComputedStyle(dl.querySelector('dt')!).color,
      }));
      assert.equal(facts.dd, ink, '深色下资料表的值不是墨色');
      assert.equal(facts.dt, muted, '深色下资料表的项名不是次级字色');
      await page.locator('[data-hero-more="alias"]').hover();
      const pop = page.locator('#entityAliasPop');
      await pop.waitFor({ state: 'visible', timeout: 5_000 });
      const { face, shadow } = await pop.evaluate((element) => {
        const style = getComputedStyle(element);
        return { face: style.backgroundColor, shadow: style.boxShadow };
      });
      assert.notEqual(face, 'rgba(0, 0, 0, 0)');
      assert.notEqual(face, 'rgb(255, 255, 255)', '深色下别名浮层还是白底');
      assert.equal(shadow, await popmenuShadow(page), '深色下别名浮层的落影和 .popmenu 不是同一副');
      assert.notEqual(ground, 'rgb(255, 255, 255)', '主题没有切到深色');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('详情出演区每一组的标题、头像和名字共用一条左边缘', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const page = opened.page;
      /* 演示库的作品没有女优与厂牌，身份数据按 `/api/item` 的 `entity_refs` 形状换进去；
         名字比头像宽的那种最容易看出错位，所以女优名取四个字。 */
      await page.route(/\/api\/item\?/, async (route) => {
        const payload = await (await route.fetch()).json();
        Object.assign(payload, {
          is_jav: true, performers: ['凉森玲梦'], studio: 'Prestige', creator: '',
          entity_refs: {
            creator: [], series: [],
            performer: [{ id: 90_101, name: '凉森玲梦', has_image: true }],
            studio: [{ id: 90_102, name: 'Prestige', has_image: false, has_logo: true }],
          },
        });
        await route.fulfill({ json: payload });
      });
      const square = { contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#888"/></svg>' };
      await page.route(/\/entity-image\?/, (route) => route.fulfill(square));
      await page.route(/\/logo\?/, (route) => route.fulfill(square));
      await page.goto(new URL(`/assets/${requiredEnv('PEACH_E2E_ITEM')}`, page.url()).href, { waitUntil: 'load' });
      await page.locator('[data-item-identity] [data-id-group="studio"] [data-id-name]').waitFor({ timeout: 15_000 });
      const edges = await page.evaluate(() => Object.fromEntries(
        ['performer', 'studio'].map((kind) => {
          const group = document.querySelector(`[data-item-identity] [data-id-group="${kind}"]`)!;
          const name = document.createRange();
          name.selectNodeContents(group.querySelector('[data-id-name]')!);
          return [kind, {
            label: group.querySelector('[data-id-label]')!.getBoundingClientRect().left,
            face: group.querySelector('[data-id-face]')!.getBoundingClientRect().left,
            name: name.getBoundingClientRect().left,
          }];
        })));
      for (const [kind, edge] of Object.entries(edges)) {
        assert.ok(Math.abs(edge.face - edge.label) < 0.5, `${kind} 组头像左缘 ${edge.face} 不在标题左缘 ${edge.label}`);
        assert.ok(Math.abs(edge.name - edge.label) < 0.5, `${kind} 组名字左缘 ${edge.name} 不在标题左缘 ${edge.label}`);
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('人物页同台艺人的头像悬停和首页顶栏女优头像同一副：抬整格填充、不描圈', { timeout: 60_000 }, async () => {
    const opened = await openPerformer(browser, DESKTOP);
    try {
      const page = opened.page;
      const person = page.locator('[data-entity-foot] [data-related-performer]').first();
      const face = () => person.evaluate((element) => {
        const style = getComputedStyle(element), ring = getComputedStyle(element.querySelector('[data-hero-ring]')!);
        return { fill: style.backgroundColor, ink: style.color, radius: style.borderRadius, padding: style.padding,
          width: style.width, ring: ring.boxShadow, size: ring.width };
      });
      await page.mouse.move(0, 0);
      const rest = await face();
      await person.hover();
      const hovered = await face();
      // 首页那一格（board.css「首页顶部两排」）：76px 宽、6/4px 内边距、12px 圆角，悬停铺
      // primary-hover、字换主文字色，48px 圆头像不另描圈。
      const home = {
        fill: await tokenColor(page, '#main', '--color-background-primary-hover'),
        ink: await tokenColor(page, '#main', '--color-text-primary'),
      };
      assert.equal(rest.fill, 'rgba(0, 0, 0, 0)', '没悬停就垫了底');
      assert.deepEqual({ radius: hovered.radius, padding: hovered.padding, width: hovered.width, size: hovered.size },
        { radius: '12px', padding: '6px 4px', width: '76px', size: '48px' }, '同台艺人那一格和首页头像格不是同一副几何');
      assert.equal(hovered.fill, home.fill, '悬停没有铺首页那一档填充');
      assert.equal(hovered.ink.replace(/\s/g, ''), home.ink.replace(/\s/g, ''), '悬停没有换成主文字色');
      assert.equal(hovered.ring, 'none', '悬停还在给圆头像描圈');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('资料卡大位与同台艺人的头像按人脸框放大：图保持原比例、盖满圆框，不被预检的 max-width 夹住', { timeout: 60_000 }, async () => {
    const name = '七沢みあ';
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const page = opened.page;
      const focus = { box: TASTE_FACE };
      await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
        id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [],
        user_aliases: [], asset_count: 0, tags: [], links: [], metadata: {}, entry_links: [],
        related_performers: [{ id: 90_002, k: '共演者', n: 1, rep: null, has_image: true, has_avatar: false,
          avatar_focus: focus }],
        has_image: true, has_avatar: false, avatar_focus: focus, representative_asset_id: null,
      } }));
      await page.route(/\/entity-image\?/, (route) => route.fulfill({
        contentType: 'image/svg+xml',
        body: `<svg xmlns="http://www.w3.org/2000/svg" width="${TASTE_FACE.imgW}" height="${TASTE_FACE.imgH}">`
          + `<rect width="${TASTE_FACE.imgW}" height="${TASTE_FACE.imgH}" fill="#888"/></svg>`,
      }));
      await page.goto(new URL(`/performers/${encodeURIComponent(name)}`, page.url()).href, { waitUntil: 'load' });
      for (const [where, selector] of [['大位', '[data-entity-portrait] > img[data-facebox]'],
        ['同台艺人', '[data-hero-ring] > img[data-facebox]']] as const) {
        const img = page.locator(selector).first();
        await img.waitFor({ state: 'attached', timeout: 15_000 });
        // 放大是图加载后 `avatarFrame` 写进内联 style 的；等到那一步落地再量。
        await page.waitForFunction((element) => element instanceof HTMLImageElement
          && element.complete && element.naturalWidth > 0 && element.style.width !== '',
        await img.elementHandle(), { timeout: 15_000 });
        const frame = await img.evaluate((element) => {
          const ring = element.parentElement!.getBoundingClientRect();
          const box = element.getBoundingClientRect();
          return {
            maxWidth: getComputedStyle(element).maxWidth, maxHeight: getComputedStyle(element).maxHeight,
            ring: ring.width, width: box.width, aspect: box.width / box.height,
            gaps: [box.left - ring.left, ring.right - box.right, box.top - ring.top, ring.bottom - box.bottom],
            initial: getComputedStyle(element.parentElement!.querySelector('span')!).display,
          };
        });
        assert.deepEqual([frame.maxWidth, frame.maxHeight], ['none', 'none'], `${where}的图被预检的 max-width 夹住`);
        assert.ok(frame.width > frame.ring, `${where}没有按人脸框放大：图宽 ${frame.width}px，圆框 ${frame.ring}px`);
        assert.ok(Math.abs(frame.aspect - TASTE_FACE.imgW / TASTE_FACE.imgH) < .02, `${where}的图宽高比 ${frame.aspect}，被压扁了`);
        assert.ok(frame.gaps.every((gap) => gap <= .5), `${where}的图没盖满圆框：${frame.gaps.join('、')}`);
        if (where === '大位') assert.equal(frame.initial, 'none', '大位有图时首字母没有让位');
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`资料卡外链的圆盘不垫底、社媒字形铺满圆盘；手机上那一排不换行、横滑，滚动层顶到卡沿（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openProfiledPerformer(browser, viewport);
      try {
        const row = await opened.page.locator('[data-entity-links]').evaluate((element) => {
          const style = getComputedStyle(element);
          const card = element.closest('[data-entity-card]')!.getBoundingClientRect();
          const box = element.getBoundingClientRect();
          const brand = element.querySelector('[data-link-icon="brand"]')!;
          const glyph = brand.querySelector('svg')!.getBoundingClientRect(), plate = brand.getBoundingClientRect();
          return {
            wrap: style.flexWrap, overflowX: style.overflowX, justify: style.justifyContent,
            edges: [box.left - card.left, card.right - box.right],
            discs: [...element.querySelectorAll('[data-link-icon]')].map((disc) => getComputedStyle(disc).backgroundColor),
            glyph: [Math.round(glyph.width), Math.round(glyph.height)], plate: [Math.round(plate.width), Math.round(plate.height)],
          };
        });
        // 垫一层底会让圆盘比周围暗一档，看着像这条链接被禁用了。
        assert.ok(row.discs.every((fill) => fill === 'rgba(0, 0, 0, 0)'), `外链圆盘垫了底色：${row.discs.join('、')}`);
        assert.deepEqual(row.glyph, row.plate, '社媒字形没有铺满圆盘，场色的角露出底');
        if (viewport.mobile) {
          // 普通 `center` 在溢出时把前半排推到滚动起点之前，那几条滑不到。
          assert.deepEqual([row.wrap, row.overflowX, row.justify], ['nowrap', 'auto', 'safe center'], '窄屏外链不是一行横滑');
          assert.ok(row.edges.every((gap) => Math.abs(gap) < 0.5), `窄屏外链的滚动层没有顶到卡沿：${row.edges.join('、')}`);
        } else {
          assert.equal(row.wrap, 'wrap', '宽屏外链该换行，不横滑');
        }
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });

    it(`名字菜单：当前统称抬一档底色并打勾，其余行的勾位空着；手机上开关画 32px、命中区 44px，菜单行 44px（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openProfiledPerformer(browser, viewport);
      try {
        const page = opened.page;
        const toggle = page.locator('[data-namepick-toggle]');
        const hit = await toggle.evaluate((element) => {
          const box = element.getBoundingClientRect(), after = getComputedStyle(element, '::after');
          return { drawn: [Math.round(box.width), Math.round(box.height)], hit: [after.width, after.height] };
        });
        assert.deepEqual(hit.drawn, [32, 32], '名字旁那枚开关画出来不是 32px');
        if (viewport.mobile) assert.deepEqual(hit.hit, ['44px', '44px'], '手机上开关的命中区不到 44px');
        await toggle.click();
        await page.locator('[data-namepick-menu]').waitFor({ state: 'visible', timeout: 5_000 });
        const rows = await page.locator('[data-namepick-menu] [role="menuitemradio"]').evaluateAll((items) => items.map((item) => ({
          checked: item.getAttribute('aria-checked') === 'true', fill: getComputedStyle(item).backgroundColor,
          tick: getComputedStyle(item.querySelector('svg')!).visibility, height: item.getBoundingClientRect().height,
          menu: getComputedStyle(item.closest('[data-namepick-menu]')!).backgroundColor,
        })));
        assert.deepEqual(rows.map((row) => row.checked), [true, false], '菜单里当前统称不是第一行，或不止一行被选中');
        const [current, other] = rows;
        assert.notEqual(current!.fill, 'rgba(0, 0, 0, 0)', '当前统称那一行没有抬底');
        assert.notEqual(current!.fill, current!.menu, '当前统称那一行的底色和菜单面同色，看不出来');
        assert.deepEqual([current!.tick, other!.tick], ['visible', 'hidden'], '勾没有只给当前统称，或未选中那行不留勾位');
        assert.equal(other!.fill, 'rgba(0, 0, 0, 0)', '未选中那一行也抬了底');
        const least = viewport.mobile ? 44 : 36;
        const alias = await page.locator('[data-namepick-alias]').evaluate((item) => item.getBoundingClientRect().height);
        assert.ok([...rows.map((row) => row.height), alias].every((height) => height >= least - 0.5), `菜单行矮于 ${least}px`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  /* 尺寸按身份列有哪几行定：外链与看片那一行都在是 160px，缺一行是 120px。取行高百分比的话
     圆框宽、列宽、别名折行、行高绕成一个圈，折行多出的那截压进卡底内边距。
     有资料的女优默认只有外链那一行，两行都在的那位要补上看片标识与订阅开关；七沢みあ只有看片那一行。 */
  for (const viewport of VIEWPORTS) {
    it(`分类图标与视频数量同排，分类位于身份行最左侧（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openProfiledPerformer(browser, viewport, 'light', { identity_labels: ['女优','西方'] });
      try {
        const geometry = await opened.page.locator('[data-entity-alias="meta"]').evaluate((element) => {
          const classification = element.querySelector('[data-identity-classification]')!;
          const count = classification.nextElementSibling!;
          const label = classification.getBoundingClientRect(), video = count.getBoundingClientRect();
          const glyph = classification.querySelector('svg')!.getBoundingClientRect();
          return { first: element.firstElementChild === classification, top: [label.top, video.top],
            edges: [label.right, video.left], glyph: [glyph.width, glyph.height] };
        });
        assert.equal(geometry.first, true);
        assert.ok(Math.abs(geometry.top[0]! - geometry.top[1]!) < 1, '分类与视频数量另起了行');
        assert.ok(geometry.edges[0]! <= geometry.edges[1]!, '分类没有排在视频数量左侧');
        assert.deepEqual(geometry.glyph, [16, 16]);
        const measured = await layout(opened.page);
        assert.ok(measured.scrollWidth <= measured.viewportWidth + 1);
        assert.deepEqual(measured.offenders, []);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }
  const openBothRows = (browser: Browser, viewport: typeof DESKTOP) => openProfiledPerformer(browser, viewport, 'light', {
    entry_links: [
      { site: 'minnano-av', label: 'みんなのAV', ordinal: '', slot: 'pill', mark: 'brand-minnano',
        url: 'https://www.minnano-av.com/actress12345.html' },
      { site: 'javdb', label: 'JavDB', ordinal: '', slot: 'mark', mark: 'mark-javdb', url: 'https://javdb.com/actors/NPD3' },
    ],
    feed: { following: false },
  });
  for (const [label, open, size] of [
    ['外链与看片那一行都在', openBothRows, 160],
    ['只有看片那一行', openPerformer, 120],
    ['只有外链那一行', openProfiledPerformer, 120],
  ] as const) {
    it(`资料卡大位按身份列的行数定尺寸：${label}是 ${size}px 正圆，身份列不压进卡底内边距`, { timeout: 60_000 }, async () => {
      const opened = await open(browser, DESKTOP);
      try {
        const box = await opened.page.locator('[data-entity-portrait]').evaluate((element) => {
          const portrait = element.getBoundingClientRect();
          const profile = element.closest('[data-entity-profile]')!.getBoundingClientRect();
          const identity = element.closest('[data-entity-profile]')!.querySelector('[data-entity-identity]')!.getBoundingClientRect();
          return { width: portrait.width, height: portrait.height, room: profile.bottom - identity.bottom };
        });
        assert.ok(Math.abs(box.height - size) < 1, `头像 ${box.height}px，应是 ${size}px`);
        assert.ok(Math.abs(box.width - box.height) < 1, '头像不是正圆');
        assert.ok(box.room >= 19.5, `身份列离卡底只剩 ${box.room}px，压进了 20px 的内边距`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  it('看片那一行：两枚标识隔 32px、按 22px 字高对齐；MISSAV 按它站上的排字，AV 用它的粉；第二枚带序号', { timeout: 60_000 }, async () => {
    const name = '七沢みあ';
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const page = opened.page;
      await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
        id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [],
        user_aliases: [], asset_count: 0, tags: [], related_performers: [], links: [], metadata: {},
        has_image: false, has_avatar: false, avatar_focus: null, representative_asset_id: null,
        entry_links: [
          { site: 'javdb', label: 'JavDB', ordinal: '', slot: 'mark', mark: 'mark-javdb', url: 'https://javdb.com/actors/NPD3' },
          { site: 'missav', label: 'MISSAV', ordinal: '2', slot: 'mark', mark: '', url: 'https://missav.ws/actresses/x' },
        ],
      } }));
      await page.goto(new URL(`/performers/${encodeURIComponent(name)}`, page.url()).href, { waitUntil: 'load' });
      await page.locator('[data-missav-mark]').waitFor({ timeout: 15_000 });
      const marks = await page.locator('[data-entry-marks]').evaluate((row) => {
        const wordmark = row.querySelector('[data-missav-mark]')!;
        const [miss, av] = [...wordmark.children].map((part) => getComputedStyle(part).color);
        return {
          gap: getComputedStyle(row).columnGap,
          javdb: row.querySelector('[data-entry-mark] svg')!.getBoundingClientRect().height,
          family: getComputedStyle(wordmark).fontFamily, weight: getComputedStyle(wordmark).fontWeight,
          miss, av, ink: getComputedStyle(wordmark.closest('a')!).color,
          ordinal: row.querySelector('[data-entry-ordinal]')?.textContent ?? '',
          hrefs: [...row.querySelectorAll('a[data-entry-mark]')].map((a) => a.getAttribute('href')),
        };
      });
      assert.equal(marks.gap, '32px', '两枚标识的间距不是 32px');
      assert.equal(Math.round(marks.javdb), 22, 'JavDB 标识的字高不是 22px');
      assert.match(marks.family, /^Halant/, 'MISSAV 没有用 Halant 排字');
      assert.equal(marks.weight, '500');
      assert.equal(marks.miss, marks.ink, 'MISS 那半没有跟页面墨色');
      assert.equal(marks.av, 'rgb(254, 98, 142)', 'AV 那半不是 MISSAV 的粉');
      assert.equal(marks.ordinal, '2', '第二枚没带服务端编好的序号');
      assert.deepEqual(marks.hrefs, ['https://javdb.com/actors/NPD3', 'https://missav.ws/actresses/x'], '入口地址不是服务端下发的那个');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('厂牌页资料卡：标识铺满方框；指回自家站的官网写「官方网站」，带站点圆标、字距 .02em', { timeout: 60_000 }, async () => {
    const name = 'S1 NO.1 STYLE';
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const page = opened.page;
      await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
        id: 90_601, kind: 'studio', canonical_name: name, aliases: [], display_aliases: [], user_aliases: [],
        asset_count: 3, tags: [], related_performers: [], labels: [], metadata: {}, entry_links: [],
        has_image: false, has_avatar: false, has_logo: true, avatar_focus: null, representative_asset_id: null,
        links: [{ link_id: 90_602, link_kind: 'official', clickable: true, label: 'S1', url: 'https://www.s1s1s1.com/' }],
      } }));
      // 标识比方框大，走铺满那条；比框小的走原尺寸居中，是另一条判据。
      await page.route(/\/logo\?/, (route) => route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600"><rect width="600" height="600" fill="#888"/></svg>',
      }));
      await page.route('**/link-mark**', (route) => route.fulfill({
        status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
      }));
      await page.goto(new URL(`/studios/${encodeURIComponent(name)}`, page.url()).href, { waitUntil: 'load' });
      const logo = page.locator('[data-entity-portrait] > img');
      await logo.waitFor({ state: 'attached', timeout: 15_000 });
      await page.waitForFunction((element) => element instanceof HTMLImageElement && element.complete && element.naturalWidth > 0,
        await logo.elementHandle(), { timeout: 15_000 });
      const face = await logo.evaluate((element) => {
        const frame = element.parentElement!, box = element.getBoundingClientRect(), outer = frame.getBoundingClientRect();
        return { shape: frame.getAttribute('data-entity-portrait'), radius: getComputedStyle(frame).borderTopLeftRadius,
          fit: getComputedStyle(element).objectFit,
          gaps: [box.left - outer.left, outer.right - box.right, box.top - outer.top, outer.bottom - box.bottom] };
      });
      assert.equal(face.shape, 'square');
      assert.notEqual(face.radius, '50%', '厂牌的标识框画成了圆');
      // 标识走「图比框小就别放大」那组：contain 不裁字标，比框大的按框收，所以照样铺满。
      assert.equal(face.fit, 'contain', '厂牌标识被裁成了铺满');
      assert.ok(face.gaps.every((gap) => Math.abs(gap) < 0.5), `标识没盖满方框：${face.gaps.join('、')}`);
      const site = await page.locator('[data-entity-links] a[data-link="url"]').evaluate((a) => ({
        text: a.querySelector('[data-link-label]')?.textContent ?? '',
        spacing: parseFloat(getComputedStyle(a).letterSpacing) / parseFloat(getComputedStyle(a).fontSize),
        mark: a.querySelector('[data-link-icon] img')?.getAttribute('src') ?? '',
        referrer: a.querySelector('[data-link-icon] img')?.getAttribute('referrerpolicy') ?? '',
      }));
      assert.equal(site.text, '官方网站');
      assert.ok(Math.abs(site.spacing - 0.02) < 0.001, `官网那一格的字距是 ${site.spacing}em`);
      assert.deepEqual([site.mark, site.referrer], ['/link-mark?id=90602', 'no-referrer'], '官网没带本机合成的站点圆标');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('人物页骨架第一帧就带着这一位有的新作行与同台艺人，数据到了一次换齐、高度不变、封面不再等第二遍', { timeout: 60_000 }, async () => {
    const name = '七沢みあ';
    const opened = await visit(browser, '/', DESKTOP);
    try {
      await opened.page.route(/\/api\/entity\/shapes/, (route) => route.fulfill({ json: {
        ok: true, entities: [{ id: 90_001, kind: 'performer', names: [name], parts: ['feed', 'costars'] }] } }));
      // 骨架插进页面的那一刻就记下它带着哪几块：之后才补进去的，就是在骨架里跳了一下。
      await opened.page.addInitScript(() => {
        const first = { feed: null as boolean | null, foot: null as boolean | null, sheen: '' };
        (window as unknown as { skeletonFirst: typeof first }).skeletonFirst = first;
        new MutationObserver(() => {
          const skeleton = document.querySelector('[data-skeleton="entity/performer"]');
          if (!skeleton || first.feed !== null) return;
          first.feed = !!skeleton.querySelector('.feednew');
          first.foot = !!skeleton.querySelector('.entityfoot');
          // 骨架卡的微光和骨架同一帧就在：晚一步的话，那一步里露出来的是封面格的黑底。
          const pic = skeleton.querySelector('.feednewskeleton .pic.imgwait');
          first.sheen = pic ? getComputedStyle(pic, '::after').opacity : '';
        }).observe(document, { childList: true, subtree: true });
        // 真实库上启动脚本发出名单请求后还要连续跑四五百毫秒，名单的响应就在这段时间里到、
        // 排在队里。这里在发请求的同一个任务末尾占住主线程 700ms，把那一段复现出来。
        const fetch = window.fetch;
        let blocked = false;
        window.fetch = (...args) => {
          if (!blocked && String(args[0]).includes('/api/entity/shapes')) {
            blocked = true;
            queueMicrotask(() => { const end = performance.now() + 700; while (performance.now() < end); });
          }
          return fetch(...args);
        };
      });
      let release = () => {};
      const held = new Promise<void>((resolve) => { release = resolve; });
      await opened.page.route(/\/api\/entity\?/, async (route) => {
        await held;
        await route.fulfill({ json: {
          id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [],
          user_aliases: [], asset_count: 0, tags: [],
          related_performers: [{ id: 90_002, k: '共演者', n: 1, rep: null, has_image: false,
            has_avatar: false, avatar_focus: null }],
          links: [], metadata: {}, has_image: false, has_avatar: false, avatar_focus: null,
          representative_asset_id: null, entry_links: [], feed: { following: true },
        } });
      });
      await opened.page.route(/\/api\/feeds\/discoveries\?/, (route) => route.fulfill({ json: {
        ok: true, more: false, items: [{
          id: 1, code: 'ABC-001', title: '标题', link: 'https://javdb.com/v/x', cover_url: null,
          has_cover: true, cover_frame: null, poster_box: null, release_date: '2026-09-01',
          studio: '厂牌', performers: name, source_name: '', read: false, ignored: false,
          scrape_error: null }] } }));
      // 封面比数据晚到一截：整页要等它，而不是先换上真卡、再在封面格里微光一遍。
      await opened.page.route(/\/cover\?code=ABC-001/, async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 400));
        await route.fulfill({ contentType: 'image/png', body: Buffer.from(PIXEL, 'base64') });
      });
      await opened.page.goto(new URL(`/performers/${encodeURIComponent(name)}`,
        opened.page.url()).href, { waitUntil: 'load' });
      const skeleton = opened.page.locator('[data-skeleton="entity/performer"]');
      await skeleton.locator('.feednew .feednewskeleton').first().waitFor({ timeout: 15_000 });
      const before = await skeleton.evaluate((element) => {
        const row = element.querySelector('.feednew')!;
        const foot = element.querySelector('.entityhero > .entityfoot');
        // 同一个 `.pic.imgwait` 放在新作那一行外面，它的微光就是全站等待态那一种。
        const probe = document.createElement('div');
        probe.className = 'pic imgwait';
        document.querySelector('#main')!.append(probe);
        const plain = getComputedStyle(probe, '::after').backgroundImage;
        probe.remove();
        return { height: row.getBoundingClientRect().height,
          footHeight: foot?.getBoundingClientRect().height ?? 0,
          between: !!(row.previousElementSibling?.matches('[data-filter-frame]') || row.previousElementSibling?.querySelector('[data-filter-frame]'))
            && !!row.nextElementSibling?.matches('.entitysection'),
          sheen: getComputedStyle(row.querySelector('.pic.imgwait')!, '::after').backgroundImage, plain,
          first: (window as unknown as { skeletonFirst: { feed: boolean; foot: boolean; sheen: string } }).skeletonFirst };
      });
      assert.ok(before.between, '骨架里的新作那一行不在筛选框和作品之间');
      const { sheen, ...parts } = before.first;
      assert.deepEqual(parts, { feed: true, foot: true }, '骨架先画了一版，新作行或同台艺人是后来才补进去的');
      assert.equal(sheen, '1', '新作骨架卡的微光晚于骨架出现，中间露出封面格的黑底');
      assert.ok(before.footHeight > 0, '骨架的资料卡底没有同台艺人那一条');
      assert.equal(before.sheen, before.plain, '新作骨架的微光另起了一种颜色');
      // 画好的页面上那一行一出现就得是真卡：再露一回它自己的骨架，就是同一行等了两遍。
      await opened.page.evaluate(() => {
        const seen = { second: false };
        (window as unknown as { feedSeen: typeof seen }).feedSeen = seen;
        new MutationObserver(() => {
          if (document.querySelector('[data-feed-new] .feednewskeleton')) seen.second = true;
        }).observe(document.querySelector('#index')!, { childList: true, subtree: true });
      });
      release();
      const row = opened.page.locator('[data-feed-new]');
      await row.locator('[data-feed-id]').waitFor({ timeout: 15_000 });
      const after = await row.evaluate((element) => ({
        height: element.getBoundingClientRect().height, busy: element.getAttribute('aria-busy'),
        waiting: element.querySelectorAll('[data-feed-id] .pic.imgwait').length,
        footHeight: document.querySelector('[data-entity-card] > [data-entity-foot]')?.getBoundingClientRect().height ?? 0,
        second: (window as unknown as { feedSeen: { second: boolean } }).feedSeen.second }));
      assert.equal(after.height, before.height, '占位行和到货的那一行不一样高，下面的作品网格会跳');
      assert.equal(after.footHeight, before.footHeight, '同台艺人那一条占位和真的不一样高，资料卡会伸缩');
      assert.equal(after.busy, null);
      assert.equal(after.second, false, '整页画出来之后新作那一行又单独骨架了一轮');
      assert.equal(after.waiting, 0, '骨架退场后封面格里又微光了一遍');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('卡片悬停面不顶到邻卡，三处卡片网格同一副列距', { timeout: 60_000 }, async () => {
    const opened = await openCatalog(browser);
    try {
      const card = opened.page.locator('#grid [data-media-grid] > [data-media-card]').first();
      await card.hover();
      const geometry = await card.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const neighbor = [...element.parentElement!.children].find((other) => other !== element
          && Math.abs(other.getBoundingClientRect().top - box.top) < 1)!;
        const spread = Number(/0px 0px 0px (\d+(?:\.\d+)?)px/.exec(getComputedStyle(element).boxShadow)?.[1]);
        // 壳画的骨架网格和关注页岛的视频列表不在首页这叠卡里：各挂一个同形的空壳读列距。
        const columnGap = (build: (probe: HTMLElement) => HTMLElement) => {
          const probe = document.createElement('div');
          const host = build(probe);
          document.querySelector('#main')!.append(host);
          const gap = parseFloat(getComputedStyle(probe).columnGap);
          host.remove();
          return gap;
        };
        return {
          spread, clearance: neighbor.getBoundingClientRect().left - (box.right + spread),
          home: parseFloat(getComputedStyle(element.parentElement!).columnGap),
          skeleton: columnGap((probe) => { probe.className = 'grid'; return probe }),
          follow: columnGap((probe) => {
            const island = document.createElement('div');
            island.className = 'peach-react';
            probe.setAttribute('data-follow-list', '');
            island.append(probe);
            return island;
          }),
        };
      });
      assert.ok(geometry.spread > 0, '卡片悬停面没有往盒外铺');
      assert.ok(geometry.clearance >= geometry.spread,
        `悬停面离邻卡只剩 ${geometry.clearance}px，比它自己往外铺的 ${geometry.spread}px 还窄`);
      assert.equal(geometry.skeleton, geometry.home, '壳画的骨架网格的列距和首页不一样，真卡换上来时整排挪位');
      assert.equal(geometry.follow, geometry.home, '关注页视频列表的列距和首页不一样');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('跳过渲染的元信息区不裁掉贴着边的头像悬停描边和焦点环', { timeout: 60_000 }, async () => {
    /* 演示库的作品都未归属，头像是不可聚焦的 `<span>`；首张卡归给一位女优，头像才是按钮。 */
    const opened = await openCatalogFixture(browser, (payload) => {
      const item = payload.items[0];
      if (!item) throw new Error('演示目录没有可替换的卡片');
      item.creator = '';
      item.performers = ['演示演员'];
      item.performer_total = 1;
      item.performer_entities = [{ id: 90_000, name: '演示演员', has_image: false }];
    });
    try {
      /* 视口外跳过渲染连带 paint containment，元信息区里画出 padding box 的像素一律裁掉；
         几何照算，所以这里比的是描边外沿与 padding box，不是与内容盒。头像贴着内容盒的
         左缘和上缘。这条不读 `overflow-clip-margin`：Safari 不认它，裁切边只能靠盒子本身。 */
      const { page } = opened;
      const avatar = page.locator('#grid [data-media-grid] > [data-media-card] [data-media-meta] > button[data-media-avatar]').first();
      const edges = () => avatar.evaluate((element) => {
        const meta = element.closest('[data-media-meta]')!;
        const style = getComputedStyle(element);
        const shadow = Number(/0px 0px 0px (\d+(?:\.\d+)?)px/.exec(style.boxShadow)?.[1] ?? 0);
        const outline = style.outlineStyle === 'none' ? 0
          : parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset);
        const ring = Math.max(shadow, outline);
        const box = element.getBoundingClientRect();
        const clip = meta.getBoundingClientRect();
        const left = clip.left + meta.clientLeft, top = clip.top + meta.clientTop;
        return {
          contained: getComputedStyle(meta).contentVisibility === 'auto',
          focused: element.matches(':focus-visible'), ring,
          // 描边外沿到裁切边还剩多少，四边取最小的那一边。
          room: Math.min(box.left - ring - left, box.top - ring - top,
            left + meta.clientWidth - (box.right + ring), top + meta.clientHeight - (box.bottom + ring)),
        };
      });
      await avatar.hover();
      const hovered = await edges();
      assert.ok(hovered.contained, '首页卡片的元信息区不再跳过渲染：这条用例守的裁切前提变了，改用例');
      assert.ok(hovered.ring > 0, '头像悬停没有描边');
      assert.ok(hovered.room >= -.5, `头像悬停描边越过了元信息区的裁切边 ${-hovered.room}px，那一截会被裁掉`);
      await page.mouse.move(0, 0);
      await page.keyboard.press('Tab');
      await avatar.focus();
      const focused = await edges();
      assert.ok(focused.focused && focused.ring > 0, '键盘聚焦的头像没有焦点环');
      assert.ok(focused.room >= -.5, `头像焦点环越过了元信息区的裁切边 ${-focused.room}px，那一截会被裁掉`);
    } finally {
      await opened.close();
    }
  });

  it('头像圆框里的图按人脸框写进的内联尺寸不被岛里的预检夹回框宽', { timeout: 60_000 }, async () => {
    /* 演示库没有实体图，往首张卡的头像框里塞一张按人脸框放大的图：宽超过框宽时图就该那么宽，
       由圆框的 overflow 裁；被夹回框宽的话脸偏到左边，右侧露出底下的首字母。 */
    const opened = await openCatalogFixture(browser, (payload) => {
      const item = payload.items[0];
      if (!item) throw new Error('演示目录没有可替换的卡片');
      item.creator = '';
      item.performers = ['演示演员'];
      item.performer_total = 1;
      item.performer_entities = [{ id: 90_000, name: '演示演员', has_image: false }];
    });
    try {
      const avatar = opened.page.locator('#grid [data-media-grid] > [data-media-card] [data-media-meta] > button[data-media-avatar]').first();
      const framed = await avatar.evaluate((element) => {
        const img = document.createElement('img');
        img.setAttribute('style', 'position:absolute;inset:-25% auto auto 0;width:150%;height:195%');
        element.appendChild(img);
        const frame = element.getBoundingClientRect();
        const box = img.getBoundingClientRect();
        const style = getComputedStyle(img);
        return { maxWidth: style.maxWidth, maxHeight: style.maxHeight,
          width: box.width / frame.width, height: box.height / frame.height };
      });
      assert.equal(framed.maxWidth, 'none', '头像图还带着预检的 max-width');
      assert.equal(framed.maxHeight, 'none', '头像图还带着 max-height');
      assert.ok(Math.abs(framed.width - 1.5) <= .02, `按人脸框放大到 1.5 倍框宽的图被夹成了 ${framed.width} 倍`);
      assert.ok(Math.abs(framed.height - 1.95) <= .02, `按人脸框放大到 1.95 倍框高的图被夹成了 ${framed.height} 倍`);
    } finally {
      await opened.close();
    }
  });

  it('亮暗按钮保持尺寸与悬停面色，卡片和回执保持接触阴影', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/stats', DESKTOP);
    try {
      // 选中的那张页签按基线收掉接触阴影，读旁边没选中的一张。
      const card = opened.page.locator('#main [class~="shadow-card"]:not([data-selected])').first();
      await card.waitFor({ timeout: 15_000 });
      await settle(opened.page);
      const checkSecondaryButton = async () => {
        await opened.page.mouse.move(0, 0);
        const dimensions = await opened.page.evaluate(() => {
          const holder = document.createElement('div');
          holder.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:10000';
          const button = document.createElement('button');
          button.className = 'geist-button';
          button.dataset.buttonProbe = '';
          button.textContent = '操作';
          holder.append(button);
          document.body.append(holder);
          const style = getComputedStyle(button);
          return [style.boxSizing, style.height, style.paddingTop, style.paddingRight,
            style.borderTopWidth, style.display];
        });
        assert.deepEqual(dimensions, ['border-box', '36px', '8px', '12px', '1px', 'inline-flex']);
        const button = opened.page.locator('[data-button-probe]');
        try {
          const idle = await button.evaluate(element => getComputedStyle(element).backgroundColor);
          assert.equal(idle, await tokenColor(opened.page, 'body', '--color-background-primary-default'));
          const border = await button.evaluate(element => getComputedStyle(element).borderTopColor);
          await button.hover();
          await settle(opened.page);
          const hovered = await button.evaluate(element => getComputedStyle(element).backgroundColor);
          assert.equal(hovered, await tokenColor(opened.page, 'body', '--control-hover'));
          assert.equal(await button.evaluate(element => getComputedStyle(element).borderTopColor), border);
        } finally {
          await button.evaluate(element => element.parentElement!.remove());
        }
      };
      /* Tailwind 把阴影 token 的字面值抄进工具类，`.dark` 里改 `--shadow-*` 够不着它；
         旧样式表里写死的浅色阴影同样不跟主题走。读三类来源各一处的计算值。 */
      const alphas = () => opened.page.evaluate(() => {
        const strongest = (shadow: string) => Math.max(0, ...[...shadow.matchAll(
          /rgba\(0, 0, 0, ([\d.]+)\)|rgb\(0, 0, 0\)/g)].map((match) => (match[1] ? Number(match[1]) : 1)));
        const probe = (html: string, parent: Element = document.body) => {
          const holder = document.createElement('div');
          holder.innerHTML = html;
          const element = holder.firstElementChild!;
          // 挂在 React 岛外面：岛里的重置会把旧样式表的按钮阴影清掉。
          parent.append(element);
          const shadow = getComputedStyle(element).boxShadow;
          element.remove();
          return strongest(shadow);
        };
        return {
          card: strongest(getComputedStyle(document.querySelector('#main [class~="shadow-card"]:not([data-selected])')!).boxShadow),
          button: probe('<button class="geist-button" type="button">键</button>'),
          // Toast 的面只在 #toasts 里成立：阴影写在 Sonner 那一条的属性选择器上。
          toast: probe('<li data-sonner-toast data-styled="true">回执</li>', document.getElementById('toasts')!),
        };
      });
      await opened.page.evaluate(() => {
        document.documentElement.dataset.theme = 'light';
        document.documentElement.classList.remove('dark');
      });
      const light = await alphas();
      await checkSecondaryButton();
      // `web/app.js` 的 `applyTheme('dark')` 就是这两句；这里只借它换一次配色。
      await opened.page.evaluate(() => {
        document.documentElement.dataset.theme = 'dark';
        document.documentElement.classList.add('dark');
      });
      const dark = await alphas();
      await checkSecondaryButton();
      for (const key of Object.keys(light) as Array<keyof typeof light>) {
        assert.ok(light[key] > 0 && light[key] < .2, `浅色下 ${key} 的阴影 ${light[key]} 不在浅色那一档`);
        assert.ok(dark[key] > light[key] && dark[key] <= .2,
          `暗色 ${key} 阴影应保留接触感而不形成重边：${dark[key]}`);
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`JAV 订阅卡片包含分页与贴边底栏（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openFollowManage(browser, viewport);
      try {
        const page = opened.page;
        await page.route('**/api/feeds', route => route.fulfill({ json: {
          unread: 81, sources: Array.from({ length: 25 }, (_, i) => ({
            id: i + 1, kind: 'javdb-actor', kind_label: 'JAV 订阅', name: `测试订阅 ${i + 1}`,
            url: `https://example.com/actors/${i + 1}`, entity_id: null, entity_name: null,
            has_image: false, enabled: true, interval_minutes: 360, last_fetched_at: null,
            last_error: null, last_new_count: 0, seen: 0,
          })),
        } }));
        await page.reload({ waitUntil: 'load' });
        await page.getByRole('tab', { name: 'JAV 订阅源', exact: true }).click();
        const card = page.getByRole('region', { name: 'JAV 订阅列表', exact: true });
        await card.getByText('1–20 / 25 个订阅源', { exact: true }).waitFor();
        assert.equal(await card.locator('[role="row"][data-key]').count(), 20);
        await card.getByRole('button', { name: '下一页', exact: true }).click();
        await card.getByText('21–25 / 25 个订阅源', { exact: true }).waitFor();
        assert.equal(await card.locator('[role="row"][data-key]').count(), 5);
        for (const theme of ['light', 'dark']) {
          await page.evaluate(value => {
            document.documentElement.dataset.theme = value;
            document.documentElement.classList.toggle('dark', value === 'dark');
          }, theme);
          const surface = await card.evaluate(element => {
            const footer = element.querySelector('footer')!;
            const box = element.getBoundingClientRect();
            const bottom = footer.getBoundingClientRect();
            return { background: getComputedStyle(element).backgroundColor,
              footerBackground: getComputedStyle(footer).backgroundColor,
              left: bottom.left - box.left, right: box.right - bottom.right,
              bottom: box.bottom - bottom.bottom };
          });
          assert.notEqual(surface.background, 'rgba(0, 0, 0, 0)');
          assert.notEqual(surface.background, surface.footerBackground);
          for (const edge of ['left', 'right', 'bottom'] as const) assert.ok(Math.abs(surface[edge]) <= 1);
          const dimensions = await layout(page);
          assert.ok(dimensions.scrollWidth <= dimensions.viewportWidth + 1, JSON.stringify(dimensions));
        }
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
    it(`关注分类使用 Pills，添加关注结果带描边（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openFollowManage(browser, viewport);
      try {
        const page = opened.page;
        const tabs = page.getByRole('tablist', { name: '关注管理区域' });
        for (const theme of ['light', 'dark']) {
          await page.evaluate((value) => {
            document.documentElement.dataset.theme = value;
            document.documentElement.classList.toggle('dark', value === 'dark');
          }, theme);
          await tabs.getByRole('tab', { name: 'JAV 订阅源', exact: true }).click();
          const face = await tabs.evaluate((element) => {
            const selected = element.querySelector('[aria-selected=true]')!;
            return { track: getComputedStyle(element).backgroundColor,
              selected: getComputedStyle(selected).backgroundColor,
              shadow: getComputedStyle(selected).boxShadow };
          });
          assert.equal(face.track, 'rgba(0, 0, 0, 0)', 'Pills 容器应透明');
          assert.notEqual(face.selected, face.track, '当前分类应有独立底色');
          assert.equal(face.shadow, 'none', 'Pills 不使用浮起滑块');
          await tabs.getByRole('tab', { name: 'JAV 订阅源', exact: true }).focus();
          await page.keyboard.press('Home');
          await page.keyboard.press('Enter');
          assert.equal(await tabs.getByRole('tab', { name: '关注列表', exact: true }).getAttribute('aria-selected'), 'true');
          const dimensions = await layout(page);
          assert.ok(dimensions.scrollWidth <= dimensions.viewportWidth + 1, JSON.stringify(dimensions));
        }
        await page.route('**/api/follow/resolve', (route) => route.fulfill({
          status: 200, contentType: 'application/json', body: JSON.stringify({
            status: 'done', results: [{ line: '主题测试来源', candidates: [{
              provider: 'kemono', provider_label: 'Kemono', label: '主题测试候选', url: 'https://example.com/creator',
            }] }],
          }),
        }));
        await tabs.getByRole('tab', { name: '添加关注', exact: true }).click();
        await page.getByRole('textbox', { name: '来源链接、名字或 id' }).fill('主题测试来源');
        await page.getByRole('button', { name: '查找', exact: true }).click();
        const result = page.getByText('主题测试来源', { exact: true }).locator('..');
        await result.waitFor();
        for (const theme of ['light', 'dark']) {
          await page.evaluate((value) => {
            document.documentElement.dataset.theme = value;
            document.documentElement.classList.toggle('dark', value === 'dark');
          }, theme);
          const surface = await result.evaluate((element) => {
            const s = getComputedStyle(element);
            return { shadow: s.boxShadow, width: s.borderTopWidth, border: s.borderTopColor, bg: s.backgroundColor };
          });
          assert.equal(surface.shadow, 'none');
          assert.equal(surface.width, '1px');
          assert.notEqual(surface.border, surface.bg, '结果分组必须有可辨认边界');
        }
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
    it(`Peach 配置的来源凭据按要求分组并带描边（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openFollowManage(browser, viewport);
      try {
        const page = opened.page;
        await page.getByRole('button', { name: /^来源与订阅设置/ }).click();
        await page.getByRole('tab', { name: '来源与订阅', exact: true, selected: true }).waitFor();
        await page.getByRole('region', { name: '可配置凭据', exact: true }).waitFor();
        await settle(page);
        for (const theme of ['light', 'dark']) {
          await page.evaluate(value => {
            document.documentElement.dataset.theme = value;
            document.documentElement.classList.toggle('dark', value === 'dark');
          }, theme);
          for (const name of ['可配置凭据', '不需要凭据', '暂不支持']) {
            const group = page.getByRole('region', { name, exact: true });
            const rows = await group.evaluate(element => {
              const style = getComputedStyle(element.lastElementChild!);
              return { shadow: style.boxShadow, width: style.borderTopWidth,
                border: style.borderTopColor, background: style.backgroundColor };
            });
            assert.equal(rows.shadow, 'none');
            assert.equal(rows.width, '1px');
            assert.notEqual(rows.border, rows.background);
          }
          assert.match(await page.getByRole('region', { name: '不需要凭据', exact: true }).innerText(), /Kemono/);
          assert.match(await page.getByRole('region', { name: '暂不支持', exact: true }).innerText(), /OnlyFans/);
          const dimensions = await layout(page);
          assert.ok(dimensions.scrollWidth <= dimensions.viewportWidth + 1, JSON.stringify(dimensions));
        }
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }
});
