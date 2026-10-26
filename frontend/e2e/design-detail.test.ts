/* 设计决定：关注详情、作品详情、播放器与侧栏。读 `getComputedStyle` 断言用户定过的外观；共用的桩与浏览器生命周期在 `design-fixture.ts`。 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { DETAIL, openFollowFeed } from './follow-fixture.ts';
import { settle, visit, VIEWPORTS, type Viewport, type Visit } from './harness.ts';
import { muteVideos, openImmerse, pickClips, pinQueue, settledClip } from './immerse-fixture.ts';
import { ITEM, openItemPage } from './item-fixture.ts';
import { DESKTOP, MOBILE, tokenColor, installDesignBrowser } from './design-fixture.ts';

describe('设计决定：关注详情、作品详情、播放器与侧栏', () => {
  let browser: Browser;
  installDesignBrowser((next) => { browser = next; });

  it('关注详情翻页圆钮固定两侧 11px；桌面 48px、手机 44px，导航条位于画面下方', { timeout: 90_000 }, async () => {
    const read = (page: Page) => page.evaluate(() => {
      const frame = document.querySelector<HTMLElement>('#stage [data-follow-detail-media="image"]')!;
      const next = frame.querySelector('[data-follow-image-arrow="next"]')!, style = getComputedStyle(next);
      const outer = frame.getBoundingClientRect(), box = next.getBoundingClientRect();
      const indicator = frame.querySelector('[data-media-indicator]')!.getBoundingClientRect();
      return {
        size: [box.width, box.height], background: style.backgroundColor, blur: style.backdropFilter,
        inset: outer.right - box.right, indicatorBottom: outer.bottom - indicator.bottom,
        framed: frame.hasAttribute('data-framed'),
        ratio: getComputedStyle(frame.querySelector('[data-follow-detail-poster]')!).aspectRatio,
      };
    });
    const settlePoster = async (page: Page) => {
      await page.waitForFunction(() => {
        const image = document.querySelector<HTMLImageElement>('#stage [data-follow-detail-poster]');
        return image?.complete && image.naturalWidth > 0;
      });
      // 图像解码与布局就绪后读取几何。
      await page.evaluate(() => new Promise<void>(resolve => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }));
    };
    const wide = await openFollowFeed(browser, `/posts/${DETAIL.gallery}`, DESKTOP,
      { ready: '#stage [data-follow-image-arrow="next"]' });
    try {
      await settlePoster(wide.page);
      const shown = await read(wide.page);
      assert.deepEqual(shown.size, [48, 48]);
      assert.equal(shown.background, 'rgba(0, 0, 0, 0.6)');
      assert.equal(shown.blur, 'blur(16px)');
      assert.ok(Math.abs(shown.inset - 11) <= 1, `箭头内距：${JSON.stringify(shown)}`);
      assert.ok(Math.abs(shown.indicatorBottom - 8) <= .5, `导航条底部留白 ${shown.indicatorBottom}px`);
      // 画框比例跟整组图走：换图时详情不忽高忽低。
      assert.equal(shown.framed, true);
      assert.match(shown.ratio, /^0\.75\b/);
      assert.deepEqual(wide.problems, []);
    } finally {
      await wide.close();
    }
    const narrow = await openFollowFeed(browser, `/posts/${DETAIL.gallery}`, MOBILE,
      { ready: '#stage [data-follow-image-arrow="next"]' });
    try {
      await settlePoster(narrow.page);
      const shown = await read(narrow.page);
      assert.deepEqual(shown.size, [44, 44]);
      assert.ok(Math.abs(shown.inset - 11) <= 1, `窄屏箭头内距：${JSON.stringify(shown)}`);
      assert.ok(Math.abs(shown.indicatorBottom - 8) <= .5, `窄屏导航条底部留白 ${shown.indicatorBottom}px`);
      assert.deepEqual(narrow.problems, []);
    } finally {
      await narrow.close();
    }
  });

  it('关注详情：没有预览的那一格占 16:9；标签按来源记下的类型取 r34 色板，未知类型取中性灰', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, `/posts/${DETAIL.bare}`, DESKTOP,
      { ready: '#stage [data-follow-detail-tags]' });
    try {
      const page = opened.page;
      const shown = await page.evaluate(() => ({
        placeholder: getComputedStyle(document.querySelector('#stage [data-follow-detail-placeholder]')!).aspectRatio,
        tags: Object.fromEntries([...document.querySelectorAll<HTMLElement>('#stage [data-follow-detail-tags] [data-follow-tag]')].map((tag) =>
          [tag.dataset.followTag, getComputedStyle(tag).getPropertyValue('--tag-color').trim()])),
      }));
      assert.equal(shown.placeholder, '16 / 9');
      const muted = await tokenColor(page, '#stage [data-follow-detail]', '--muted');
      const unknown = await page.evaluate(() => {
        const tag = document.querySelector<HTMLElement>('#stage [data-follow-detail-tags] [data-follow-tag="odd"]')!;
        const probe = document.createElement('div');
        probe.style.backgroundColor = 'var(--tag-color)';
        tag.append(probe);
        const value = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return value;
      });
      assert.deepEqual({ ...shown.tags, odd: unknown }, {
        ow: '#d675d6', tracer: '#68c76f', kou: '#e36c6c', solo: '#55a7ff', animated: '#f5a24a', odd: muted,
      });
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('关注详情标题：来源外链标与标题第一行同心，标题折行时仍贴第一行', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, `/posts/${DETAIL.gallery}`, DESKTOP,
      { ready: '#stage [data-follow-origin]' });
    try {
      const page = opened.page;
      const measure = () => page.evaluate(() => {
        const name = document.querySelector('#stage [data-follow-detail-name]')!;
        const mark = document.querySelector('#stage [data-follow-origin] svg')!.getBoundingClientRect();
        return { line: name.getBoundingClientRect().top + parseFloat(getComputedStyle(name).lineHeight) / 2,
          mark: (mark.top + mark.bottom) / 2 };
      });
      const single = await measure();
      assert.ok(Math.abs(single.mark - single.line) <= 0.5, `外链标与标题第一行不同心：${JSON.stringify(single)}`);
      await page.evaluate(() => { document.querySelector('#stage [data-follow-detail-name]')!.textContent = '很长的标题'.repeat(12) });
      const wrapped = await measure();
      assert.ok(Math.abs(wrapped.mark - wrapped.line) <= 0.5, `标题折行后外链标离开了第一行：${JSON.stringify(wrapped)}`);
    } finally {
      await opened.close();
    }
  });

  /* 作品详情（`item-detail` 岛）。桩数据见 `item-fixture.ts`：普通那一条评了 3 星、两位出演、
     一个厂牌一个系列，都带实体 id。桩里的片源没有正片，播放器那一条 VIDEOJS 错误不算。 */
  const withoutPlayer = (problems: string[]) => problems.filter((line) => !line.includes('VIDEOJS'));

  it('作品详情标题与评分：来源徽标站在标题第一行开头；整排星一种琥珀、评没评只看填不填，悬停预演到指针那一颗', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      const title = await page.evaluate(() => {
        const text = document.querySelector('#stage [data-detail-title]')!;
        const badge = text.querySelector('[class~="srcbig"]')!;
        const style = getComputedStyle(badge);
        const glyph = badge.querySelector('svg, img')!.getBoundingClientRect();
        const line = text.getBoundingClientRect().top + parseFloat(getComputedStyle(text).lineHeight) / 2;
        return {
          clamp: getComputedStyle(text).webkitLineClamp,
          first: text.firstElementChild === badge,
          badge: { display: style.display, width: style.width, height: style.height, gap: style.marginRight },
          centered: Math.abs((glyph.top + glyph.bottom) / 2 - line) <= 0.5,
          tools: document.querySelector('#stage [data-title-tools]')!.getBoundingClientRect().top
            >= text.getBoundingClientRect().bottom - 0.5,
        };
      });
      assert.deepEqual(title, {
        clamp: '2', first: true, badge: { display: 'inline-grid', width: '17px', height: '28px', gap: '8px' },
        centered: true, tools: true,
      }, '徽标是行内块、与第一行同心、随文字一起被两行折叠裁住；那排键自成一行排在标题下面');

      const stars = page.locator('#stage [data-rate]');
      const read = () => stars.evaluateAll((nodes) => nodes.map((node) => ({
        color: getComputedStyle(node).color, filled: getComputedStyle(node.querySelector('svg')!).fill !== 'none',
      })));
      const amber = await tokenColor(page, '#stage [data-item-detail]', '--rating');
      await page.mouse.move(0, 0);
      const rest = await read();
      assert.deepEqual(rest.map((star) => star.color), Array(5).fill(amber), '换色那一档在浅色主题下凑不出两级都成立的灰');
      assert.deepEqual(rest.map((star) => star.filled), [true, true, true, false, false]);
      await stars.nth(1).hover();
      const preview = await read();
      assert.deepEqual(preview.map((star) => star.color), Array(5).fill(amber));
      assert.deepEqual(preview.map((star) => star.filled), [true, true, false, false, false]);
      const edges = await page.evaluate(() => {
        const box = (selector: string) => document.querySelector(`#stage ${selector}`)!.getBoundingClientRect();
        return { stars: box('[data-rating-stars]').left, title: box('[data-detail-title]').left };
      });
      assert.equal(edges.stars, edges.title - 4, '26px 命中区里的星形靠右约 4px 起笔，这一排往左让回来');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('作品详情身份区：各组按内容宽并排换行，名字行框容得下下伸部；系列是整行宽的图标链接，不是标签胶囊', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      const shown = await page.evaluate(() => {
        const stage = document.querySelector('#stage')!;
        const primary = stage.querySelector('[data-identity-primary]')!;
        const width = primary.getBoundingClientRect().width;
        const name = getComputedStyle(stage.querySelector('[data-id-group="performer"] [data-id-name]')!);
        const series = stage.querySelector('[data-series-link]')!;
        const link = getComputedStyle(series);
        return {
          primary: [getComputedStyle(primary).display, getComputedStyle(primary).flexWrap],
          narrower: [...primary.querySelectorAll('[data-id-group]')].every((group) => group.getBoundingClientRect().width < width),
          name: { ratio: parseFloat(name.lineHeight) / parseFloat(name.fontSize), overflow: name.textOverflow, align: name.textAlign },
          cursor: getComputedStyle(stage.querySelector('[data-id-group="performer"] [data-id-cell]')!).cursor,
          face: getComputedStyle(stage.querySelector('[data-id-group="performer"] [data-id-face]')!).backgroundColor,
          series: {
            display: link.display, wrap: link.overflowWrap, border: link.borderTopWidth,
            full: Math.abs(series.getBoundingClientRect().width - series.parentElement!.getBoundingClientRect().width) < 0.5,
          },
        };
      });
      assert.deepEqual(shown.primary, ['flex', 'wrap']);
      assert.equal(shown.narrower, true, '组按内容宽，共演作品不会一组占满一行');
      assert.ok(shown.name.ratio >= 1.5, `名字行高 ${shown.name.ratio} 倍字号，拉丁字母的下伸部会被省略号那层 overflow 裁掉`);
      assert.deepEqual([shown.name.overflow, shown.name.align], ['ellipsis', 'left']);
      assert.equal(shown.cursor, 'pointer');
      assert.notEqual(shown.face, 'rgba(0, 0, 0, 0)', '没图的头像画首字盘');
      assert.deepEqual(shown.series, { display: 'flex', wrap: 'anywhere', border: '0px', full: true });
      await page.locator('#stage [data-series-link]').hover();
      const hovered = await page.locator('#stage [data-series-link]').evaluate((node) =>
        [getComputedStyle(node).color, getComputedStyle(node).textDecorationLine]);
      assert.deepEqual(hovered, [await tokenColor(page, '#stage [data-item-detail]', '--tungsten'), 'none']);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of VIEWPORTS) {
    it(`作品详情头像按有尺寸的圆框取景并显示（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, viewport);
      try {
        const page = opened.page;
        const base = await page.evaluate(() => fetch('/api/item?id=14').then((response) => response.json()));
        const payload = { ...base, performers: ['取景艺人', '无图艺人'],
          entity_refs: { ...base.entity_refs, performer: [
            { id: 3, name: '取景艺人', has_image: true, image_version: 'face-version',
              avatar_focus: { box: { cx: 0.58, cy: 0.49, faceW: 344, imgW: 1000, imgH: 1000 } } },
            { id: 4, name: '无图艺人', has_image: false },
          ] } };
        await page.route((url) => url.pathname === '/api/item', (route) => route.fulfill({ json: payload }));
        await page.route('**/entity-image**', (route) => route.fulfill({ contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000"><rect width="1000" height="1000" fill="#ddd"/></svg>' }));
        await page.goto(new URL('/assets/14', page.url()).href, { waitUntil: 'load' });
        const face = page.locator('#stage [data-entity-name="取景艺人"] [data-id-face]');
        await face.scrollIntoViewIfNeeded();
        await page.waitForFunction(() => {
          const image = document.querySelector<HTMLImageElement>('#stage [data-id-cell="performer"] img');
          return image?.complete && image.naturalWidth > 0;
        });
        const geometry = await face.evaluate((ring) => {
          const image = ring.querySelector('img')!, frame = ring.getBoundingClientRect(), pixels = image.getBoundingClientRect();
          return { visible: getComputedStyle(image).visibility, framed: image.hasAttribute('data-face-framed'),
            direct: image.parentElement === ring, zoomed: pixels.width > frame.width,
            covers: pixels.left <= frame.left + 0.5 && pixels.right >= frame.right - 0.5
              && pixels.top <= frame.top + 0.5 && pixels.bottom >= frame.bottom - 0.5 };
        });
        assert.deepEqual(geometry, { visible: 'visible', framed: true, direct: true, zoomed: true, covers: true });
        assert.equal(await page.locator('#stage [data-entity-name="无图艺人"] img').count(), 0);
        assert.deepEqual(withoutPlayer(opened.problems), []);
      } finally { await opened.close(); }
    });
  }

  it('作品详情身份区：没实体 id 的格子不给手形，厂牌标识铺满方框', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      // 先经桩取到原样的那一条；`route.fetch()` 会绕过桩直接打到服务器。
      const base = await page.evaluate(() => fetch('/api/item?id=14').then((response) => response.json()));
      const payload = {
        ...base, performers: ['无名氏'], performer_total: 1, performer_entities: [{ id: 0, has_image: false }], has_studio_logo: true,
        entity_refs: { ...base.entity_refs,
          performer: [{ id: 0, name: '无名氏', has_image: false, avatar_focus: null }],
          studio: [{ id: 50, name: 'Peach Studio', has_image: false, has_logo: true }] },
      };
      await page.route((url) => url.pathname === '/api/item', (route) => route.fulfill({ json: payload }));
      await page.goto(new URL('/assets/14', page.url()).href, { waitUntil: 'load' });
      await page.locator('#stage [data-id-cell="studio"] img').waitFor({ timeout: 15_000 });
      await settle(page);
      const shown = await page.evaluate(() => {
        const cell = (kind: string) => document.querySelector(`#stage [data-id-cell="${kind}"]`)!;
        const face = cell('studio').querySelector('[data-id-face]')!.getBoundingClientRect();
        const img = cell('studio').querySelector('img')!;
        const box = img.getBoundingClientRect();
        return {
          performer: [cell('performer').tagName, getComputedStyle(cell('performer')).cursor],
          studio: getComputedStyle(cell('studio')).cursor,
          fit: getComputedStyle(img).objectFit,
          filled: [box.left - face.left, box.top - face.top, box.width - face.width, box.height - face.height]
            .every((delta) => Math.abs(delta) < 0.5),
        };
      });
      assert.deepEqual(shown, { performer: ['SPAN', 'default'], studio: 'pointer', fit: 'cover', filled: true },
        '标识文件自带边距，页面再补 inset 或换成 contain 就多围出一圈框');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('作品详情标签与反馈键：标签和卡片同一张脸，筛选那半边悬停抬填充；反馈条每一枚悬停都换色', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      const face = await page.evaluate(() => {
        const tag = document.querySelector('#stage [data-detail-tag]')!;
        const probe = document.createElement('div');
        probe.style.cssText = 'border:1px solid var(--line);border-radius:var(--tag-radius);background:var(--tag-fill);color:var(--ink-2)';
        tag.parentElement!.append(probe);
        const want = getComputedStyle(probe);
        const expected = { radius: want.borderTopLeftRadius, line: want.borderTopColor, fill: want.backgroundColor, ink: want.color };
        probe.remove();
        const style = getComputedStyle(tag);
        return { expected, actual: { radius: style.borderTopLeftRadius, line: style.borderTopColor,
          fill: style.backgroundColor, ink: getComputedStyle(tag.querySelector('[data-tag]')!).color } };
      });
      assert.deepEqual(face.actual, face.expected, '圆角、线、填充与字色都走卡片那颗 `.tg` 的同一组 token');
      const filter = page.locator('#stage [data-detail-tag] [data-tag]').first();
      await filter.hover();
      assert.equal(await filter.evaluate((node) => getComputedStyle(node).backgroundColor),
        await tokenColor(page, '#stage [data-item-detail]', '--hover'));

      const buttons = ['[data-fb="like"]', '[data-fb="reason"]', '[data-stage-action="dislike"]', '[data-stage-action="seen"]', '[data-stage-action="later"]', '[data-fb="playlist"]',
        '[data-fb="quality"]', '[data-fb="dispose"]'];
      const unchanged: string[] = [];
      for (const selector of buttons) {
        const button = page.locator(`#stage [data-stage-actions] ${selector}`);
        const color = () => button.evaluate((node) => {
          node.getAnimations({ subtree: true }).forEach((animation) => animation.finish());
          return getComputedStyle(node).color;
        });
        await page.mouse.move(0, 0);
        const rest = await color();
        await button.hover();
        if (await color() === rest) unchanged.push(selector);
      }
      assert.deepEqual(unchanged, [], '漏写配色的那一枚全程停在 --muted，看着像不能点');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('作品详情喜爱理由框与「接着看」：输入框聚焦画 BoardUI 那枚 2px 内环；接着看和侧栏同底、顶上一条细线', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#stage [data-media-card]').first().waitFor({ timeout: 15_000 });
      await page.locator('#preferenceToggle').click();
      const field = page.locator('#stage [data-item-preference] textarea');
      await field.focus();
      const ring = await field.evaluate((node) => {
        node.getAnimations().forEach((animation) => animation.finish());
        return getComputedStyle(node).boxShadow;
      });
      const active = await tokenColor(page, '#stage [data-item-detail]', '--color-border-button-active');
      assert.equal(ring, `${active} 0px 0px 0px 2px inset`);

      const related = await page.evaluate(() => {
        const block = document.querySelector('#stage [data-item-related]')!;
        const style = getComputedStyle(block);
        const row = getComputedStyle(block.querySelector('[data-related-row]')!);
        return {
          fill: style.backgroundColor, side: getComputedStyle(document.querySelector('#stage [data-item-side]')!).backgroundColor,
          line: [style.borderTopWidth, style.borderTopStyle, style.borderTopColor],
          heading: getComputedStyle(block.querySelector('h3')!).fontWeight,
          row: [row.display, row.overflowX, row.scrollbarWidth],
        };
      });
      assert.equal(related.fill, related.side, '同一格详情的两块，底色同源，不然整幅宽度上留一道色差');
      assert.deepEqual(related.line, ['1px', 'solid', await tokenColor(page, '#stage', '--line-soft')]);
      assert.equal(related.heading, '600');
      assert.deepEqual(related.row, ['flex', 'auto', 'none'], '那一排横滚，不露系统滚动条');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('作品详情队列与说明块：版次徽章和标题同一行、不斜体；脱盘说明块铺满播放器格', { timeout: 60_000 }, async () => {
    const editions = await openItemPage(browser, `/assets/${ITEM.edition}?editions=${ITEM.edition}`, DESKTOP,
      { ready: '#stage [data-queue-edition]' });
    try {
      const shown = await editions.page.evaluate(() => {
        const badge = document.querySelector('#stage [data-queue-edition]')!;
        const head = badge.parentElement!;
        const name = head.querySelector('b')!.getBoundingClientRect();
        const box = badge.getBoundingClientRect();
        return {
          head: [getComputedStyle(head).display, getComputedStyle(head).flexDirection],
          badge: [getComputedStyle(badge).fontStyle, getComputedStyle(badge).flexShrink, getComputedStyle(badge).maxWidth],
          sameRow: box.top < name.bottom && box.bottom > name.top,
          heading: getComputedStyle(document.querySelector('#stage [data-mix-queue-head] h2')!).fontWeight,
        };
      });
      assert.deepEqual(shown, { head: ['flex', 'row'], badge: ['normal', '1', '50%'], sameRow: true, heading: '600' },
        '`<i>` 默认斜体，徽章不是强调语气；长版次字样最多占一半宽，番号不被挤没');
      /* 队列头的按钮是关闭一类的操作，走控件圆角，不是圆形标签。 */
      const head = await editions.page.evaluate(() => {
        const button = document.querySelector('#stage [data-mix-queue-head] button')!;
        return [getComputedStyle(button).borderTopLeftRadius, getComputedStyle(button).getPropertyValue('--control-radius').trim()];
      });
      assert.equal(head[0], head[1]);
      assert.deepEqual(withoutPlayer(editions.problems), []);
    } finally {
      await editions.close();
    }
    const offline = await openItemPage(browser, `/assets/${ITEM.offline}`, DESKTOP, { ready: '#stage #offlineGate' });
    try {
      const fit = await offline.page.evaluate(() => {
        const gate = document.querySelector('#stage #offlineGate')!;
        const cell = gate.parentElement!;
        const a = gate.getBoundingClientRect(), b = cell.getBoundingClientRect();
        return {
          filled: [a.left - b.left, a.top - b.top, a.width - b.width, a.height - b.height].every((delta) => Math.abs(delta) < 0.5),
          radius: [getComputedStyle(gate).borderTopLeftRadius, getComputedStyle(cell).borderTopLeftRadius],
        };
      });
      assert.equal(fit.filled, true, '说明块铺满播放器格，不上下留黑');
      assert.equal(fit.radius[0], fit.radius[1], '圆角跟着格走');
      assert.notEqual(fit.radius[0], '0px');
      assert.deepEqual(offline.problems, []);
    } finally {
      await offline.close();
    }
  });

  it('作品详情标签选择器用全站下拉面板那一对开合动效，减少动态效果时当场开合', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      const picker = page.locator('#stage [data-tag-picker]');
      /* 关闭动效只有 150ms，点完再读计算样式会在负载高时读到已收起；改记选择器上真正开播过的动效。 */
      await page.evaluate(() => {
        const played: string[] = [];
        (window as unknown as { pickerMotion: string[] }).pickerMotion = played;
        document.addEventListener('animationstart', (event) => {
          if ((event.target as Element).matches('[data-tag-picker]')) played.push(event.animationName);
        }, true);
      });
      const played = () => page.evaluate(() => (window as unknown as { pickerMotion: string[] }).pickerMotion.splice(0));
      const cycle = async () => {
        await page.locator('#tagPlus').click();
        await page.locator('#tagPickSearch').waitFor();
        await page.locator('#stage [data-rating-value]').click();
        await picker.waitFor({ state: 'hidden' });
        return played();
      };
      assert.deepEqual(await cycle(), [], '减少动态效果时当场开合，不播动效');
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      assert.deepEqual(await cycle(), ['board-menu-in', 'board-menu-out']);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  /* 舞台与播放器的外观：数值照 YouTube 桌面版 delhi-modern 的播放器与 ytd-miniplayer 实测定下。
     桩里的片源放不出来，Video.js 停在错误态，控件照样挂着，读的是计算值。 */
  const PLAYER_BAR = '#stage .video-js .vjs-control-bar';
  const PLAYER_BLACK = 'rgba(0, 0, 0, 0.6)';
  const styleOf = (page: Page, selector: string, properties: string[], pseudo = '') => page.evaluate(
    ([target, names, element]) => {
      const node = document.querySelector(target);
      if (!node) return null;
      const style = getComputedStyle(node, element || null);
      return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name)]));
    }, [selector, properties, pseudo] as const);
  const tokenOf = (page: Page, selector: string, token: string) => page.evaluate(([target, name]) =>
    getComputedStyle(document.querySelector(target)!).getPropertyValue(name).trim(), [selector, token] as const);

  it('详情浮窗：遮罩同一档 --scrim、不带模糊，进出场同设置弹层；关闭键是压在画面上的 40px 黑圆；媒体格只圆左上角，剧场模式圆上面两角、排成单列', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP, { settings: { theme: 'light' } });
    try {
      const page = opened.page;
      const scrim = await tokenColor(page, 'body', '--scrim');
      assert.equal(scrim, 'rgba(0, 0, 0, 0.7)');
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      assert.deepEqual(await styleOf(page, '#stage', ['background-color', 'backdrop-filter', 'animation-name'], '::backdrop'),
        { 'background-color': scrim, 'backdrop-filter': 'none', 'animation-name': 'settings-backdrop-in' });
      assert.equal((await styleOf(page, '#stage', ['animation-name']))!['animation-name'], 'board-dialog-in');
      /* 亮色主题下白底白晕看不出悬停，用户以 YouTube 为参照：播放器上的键在亮色下也是黑底。 */
      assert.deepEqual(await styleOf(page, '#closeStage', ['width', 'height', 'border-top-left-radius', 'background-color', 'color']),
        { width: '40px', height: '40px', 'border-top-left-radius': '50%', 'background-color': PLAYER_BLACK, color: 'rgb(255, 255, 255)' });

      const corners = () => styleOf(page, '#stage [data-stage-media]',
        ['border-top-left-radius', 'border-top-right-radius', 'border-bottom-left-radius']);
      const wide = (await corners())!;
      assert.notEqual(wide['border-top-left-radius'], '0px', '媒体格左上角没圆');
      assert.deepEqual([wide['border-top-right-radius'], wide['border-bottom-left-radius']], ['0px', '0px'],
        '媒体格右边贴着侧栏、下面接着「接着看」，只圆左上角');
      /* `overflow-y:auto` 会把 overflow-x 算成 auto，内容宽出 1px 就冒横向滚动条；侧栏撑满所在那一行，不露半截底色。 */
      assert.deepEqual(await styleOf(page, '#stage [data-stage-side-content]', ['overflow-x', 'overflow-y']),
        { 'overflow-x': 'hidden', 'overflow-y': 'auto' });
      assert.equal((await styleOf(page, '#stage [data-stage-side]', ['align-self']))!['align-self'], 'stretch');
      await page.locator('#stage [data-player-theater]').dispatchEvent('click');
      await page.locator('#stage[data-theater]').waitFor();
      const theater = (await corners())!;
      assert.deepEqual([theater['border-top-right-radius'], theater['border-bottom-left-radius']],
        [wide['border-top-left-radius'], '0px'], '剧场模式下媒体格圆上面两角');
      const columns = (await styleOf(page, '#stage [data-stage-grid]', ['grid-template-columns']))!['grid-template-columns']!;
      assert.equal(columns.split(' ').length, 1, `剧场模式没排成单列：${columns}`);
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('手机上详情浮窗离屏幕边 8px，滚的是里面那一层，视频格吸在它顶上', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, MOBILE);
    try {
      const page = opened.page;
      await page.locator(PLAYER_BAR).waitFor({ state: 'attached' });
      const box = (await page.locator('#stage').boundingBox())!;
      assert.deepEqual([box.x, box.y, box.width], [8, 8, MOBILE.width - 16]);
      assert.equal((await styleOf(page, '#stage > [data-stage-scroll]', ['overflow-y']))!['overflow-y'], 'auto');
      assert.deepEqual(await styleOf(page, '#stage [data-stage-media]', ['position', 'top']), { position: 'sticky', top: '0px' });
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('手机上界面按钮与标签长按不选字、点按不等双击判定；标题、番号这类馆藏数据照常能选中复制', { timeout: 90_000 }, async () => {
    /* 每个样本取 [touch-action, user-select]；没找到记成 null。 */
    const touchOf = (page: Page, selectors: string[]) => page.evaluate((list) => list.map((selector) => {
      const node = document.querySelector(selector);
      return node ? [getComputedStyle(node).touchAction, getComputedStyle(node).userSelect] : null;
    }), selectors);
    const controls = ['#closeStage', '#likeBtn', '#stage [data-stage-action]', '#stage [data-title-fold]', '#tagPlus',
      '#stage [data-detail-tag] [data-tag]'];
    const stage = await openItemPage(browser, `/assets/${ITEM.plain}`, MOBILE);
    try {
      await stage.page.locator('#stage [data-detail-tag] [data-tag]').first().waitFor({ state: 'attached' });
      assert.deepEqual(await touchOf(stage.page, controls), controls.map(() => ['manipulation', 'none']),
        `控件顺序：${controls.join('、')}`);
      const [title] = await touchOf(stage.page, ['#stage [data-detail-title]']);
      assert.notEqual(title?.[1], 'none', '详情标题是内容，不能跟着控件一起关掉选字');
      assert.deepEqual(withoutPlayer(stage.problems), []);
    } finally {
      await stage.close();
    }
    const home = await openItemPage(browser, '/', MOBILE, { ready: '#grid [data-media-card] [data-media-title]' });
    try {
      const [card] = await touchOf(home.page, ['#grid [data-media-card] [data-media-title]']);
      assert.equal(card?.[0], 'manipulation', '作品卡标题是按钮，点按不该等双击判定');
      assert.notEqual(card?.[1], 'none', '作品卡标题装着番号，长按要能选中复制');
      assert.deepEqual(home.problems, []);
    } finally {
      await home.close();
    }
  });

  it('播放器控件：40px 黑圆播放键、同一档黑的右侧胶囊与提示、钨丝色进度、页面字体；统计键与加载速度角标压在左上；报错是一张盖在统计上面的卡', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator(PLAYER_BAR).waitFor({ state: 'attached' });
      assert.deepEqual(await styleOf(page, `${PLAYER_BAR} > .vjs-play-control`, ['width', 'height', 'border-top-left-radius', 'background-color']),
        { width: '40px', height: '40px', 'border-top-left-radius': '50%', 'background-color': PLAYER_BLACK });
      assert.equal((await styleOf(page, '#stage .vjs-peach-right-controls', ['background-color']))!['background-color'], PLAYER_BLACK);
      /* 错误态下控件条收起、量不到盒子，读它离播放器四边的计算值。 */
      assert.deepEqual(await styleOf(page, PLAYER_BAR, ['left', 'right', 'bottom', 'height']),
        { left: '12px', right: '12px', bottom: '8px', height: '59px' });
      /* 彩条 6px，抓取区 18px，多出来的 12px 全在条上方：往下扩会盖住按钮那一排的顶边。 */
      assert.deepEqual(await styleOf(page, '#stage .vjs-progress-control', ['height', 'top']), { height: '18px', top: '-12px' });
      assert.equal((await styleOf(page, '#stage .vjs-progress-holder > media-slider-track', ['height']))!.height, '6px', '彩条本身不变粗');
      assert.equal((await styleOf(page, '#stage .vjs-play-progress', ['background-color']))!['background-color'],
        await tokenColor(page, '#stage', '--tungsten'));
      assert.equal((await styleOf(page, '#stage .video-js', ['font-family']))!['font-family'],
        (await styleOf(page, 'body', ['font-family']))!['font-family'], '播放器里的字用页面字体，不是 Video.js 的 Arial');
      assert.deepEqual(await styleOf(page, '#stage [data-player-theater] > .vjs-peach-tooltip',
        ['background-color', 'color', 'padding-top', 'padding-left', 'backdrop-filter', 'white-space']),
      { 'background-color': PLAYER_BLACK, color: 'rgb(255, 255, 255)', 'padding-top': '5px', 'padding-left': '9px',
        'backdrop-filter': 'blur(16px)', 'white-space': 'nowrap' });
      assert.deepEqual(await styleOf(page, '#stage #playerStatsBtn', ['left', 'top', 'width', 'height', 'border-top-left-radius', 'background-color']),
        { left: '11px', top: '11px', width: '40px', height: '40px', 'border-top-left-radius': '50%', 'background-color': PLAYER_BLACK });
      /* 音量胶囊和右边那枚同一排，毛玻璃同一档：只有一边磨砂，展开后它就比邻居更透。 */
      assert.equal((await styleOf(page, `${PLAYER_BAR} > .vjs-volume-panel`, ['backdrop-filter']))!['backdrop-filter'], 'blur(16px)');
      assert.deepEqual(await styleOf(page, '#stage #playerNet', ['left', 'top', 'height', 'white-space']),
        { left: '58px', top: '11px', height: '40px', 'white-space': 'nowrap' }, '速率断成两行会顶破 40px 的胶囊');
      /* sprite 里的仪表盘是描边图形，容器不声明就按 SVG 默认填成黑块，压在黑底上等于没有图标。
         演示库的片子放不出来、角标没有速率可写，量的是临时塞进去的一枚 svg。 */
      const gauge = await page.evaluate(() => {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        document.querySelector('#stage #playerNet')!.append(svg);
        const style = getComputedStyle(svg);
        const shown = { fill: style.fill, stroke: style.stroke };
        svg.remove();
        return shown;
      });
      assert.deepEqual(gauge, { fill: 'none', stroke: 'rgb(255, 255, 255)' });
      await page.locator('#stage .video-js.vjs-error').waitFor({ timeout: 10_000 });
      const error = (await styleOf(page, '#stage .vjs-error-display .vjs-modal-dialog-content', ['background-color', 'z-index']))!;
      const stats = (await styleOf(page, '#stage #playerStats', ['z-index']))!;
      assert.equal(error['background-color'], 'rgba(2, 4, 8, 0.86)');
      assert.ok(Number(error['z-index']) > Number(stats['z-index']), `报错卡压在统计面板底下：${error['z-index']} / ${stats['z-index']}`);
      const card = (await page.locator('#stage .vjs-error-display .vjs-modal-dialog-content').boundingBox())!;
      const frame = (await page.locator('#stage .video-js').boundingBox())!;
      assert.ok(Math.abs(card.x + card.width / 2 - (frame.x + frame.width / 2)) < 1
        && Math.abs(card.y + card.height / 2 - (frame.y + frame.height / 2)) < 1, '报错卡居中在画面里，躲开左上角的速率角标');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('播放器的设置面板与右键菜单：同一档黑、浮层圆角、48px 的行；右键菜单悬停抬一层白', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#stage #playerStatsBtn').waitFor();
      const menu = '#stage .vjs-peach-settings-menu';
      /* 关闭态不能是 display:none：它没有可过渡的中间态，面板只会瞬间消失。 */
      assert.deepEqual(await styleOf(page, menu, ['display', 'opacity', 'visibility']),
        { display: 'block', opacity: '0', visibility: 'hidden' });
      await page.locator('#stage .vjs-peach-settings-toggle').dispatchEvent('click');
      const floating = await tokenOf(page, menu, '--floating-radius');
      const settings = (await styleOf(page, menu, ['background-color', 'border-top-left-radius', 'width', 'backdrop-filter']))!;
      assert.equal(settings['background-color'], PLAYER_BLACK);
      assert.equal(settings['backdrop-filter'], 'blur(16px)', '设置面板的毛玻璃没生效');
      assert.equal(settings['border-top-left-radius'], floating);
      assert.ok(parseFloat(settings.width!) <= 274, `设置面板宽 ${settings.width}`);
      assert.equal((await styleOf(page, `${menu} .vjs-peach-menu-row`, ['min-height']))!['min-height'], '48px');
      await page.locator('#stage .vjs-peach-settings-toggle').dispatchEvent('click');

      await page.locator('#stage .video-js').click({ button: 'right' });
      await page.locator('#playerMenu:popover-open').waitFor();
      assert.deepEqual(await styleOf(page, '#playerMenu', ['padding-top', 'background-color', 'border-top-left-radius', 'backdrop-filter']),
        { 'padding-top': '8px', 'background-color': PLAYER_BLACK, 'border-top-left-radius': await tokenOf(page, '#playerMenu', '--floating-radius'),
          'backdrop-filter': 'blur(16px)' });
      const row = (await styleOf(page, '#playerMenu [data-player-menu]', ['min-height', 'grid-template-columns']))!;
      assert.equal(row['min-height'], '48px');
      const tracks = row['grid-template-columns']!.split(' ');
      assert.deepEqual([tracks[0], tracks.at(-1)], ['56px', '32px'], `右键菜单行的列：${row['grid-template-columns']}`);
      await page.locator('#playerMenu [data-player-menu]').first().hover();
      assert.equal((await styleOf(page, '#playerMenu [data-player-menu]', ['background-color']))!['background-color'],
        'rgba(255, 255, 255, 0.1)');
      await page.keyboard.press('Escape');
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('统计图 20px 高，缓冲那条按健康度换色；小窗照 ytd-miniplayer：固定在右下角 16px、宽 400px、层级在弹层之下，快退快进键 36px', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#stage #playerStatsBtn').click();
      const plot = '#stage [data-player-stats-plot="buffer"]';
      assert.equal((await styleOf(page, plot, ['height']))!.height, '20px');
      const colors = await page.evaluate((selector) => ['low', 'mid'].map((state) => {
        const bar = document.createElement('i');
        bar.dataset.bar = state;
        document.querySelector(selector)!.append(bar);
        const color = getComputedStyle(bar).backgroundColor;
        bar.remove();
        return color;
      }), plot);
      assert.deepEqual(colors, ['rgb(225, 105, 98)', 'rgb(239, 181, 95)']);

      await page.locator('#closeStage').focus();
      await page.keyboard.press('i');
      await page.locator('#miniplayer:not([hidden]) .video-js').waitFor({ timeout: 10_000 });
      assert.deepEqual(await styleOf(page, '#miniplayer', ['position', 'z-index']), { position: 'fixed', 'z-index': '900' });
      const box = (await page.locator('#miniplayer').boundingBox())!;
      assert.deepEqual([box.width, box.x + box.width, box.y + box.height], [400, DESKTOP.width - 16, DESKTOP.height - 16]);
      assert.deepEqual(await styleOf(page, '#miniplayerBack', ['width', 'height']), { width: '36px', height: '36px' });
      assert.deepEqual(withoutPlayer(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  /** 一组元素的外框，取到小数点后一位。 */
  const rectsOf = (page: Page, selectors: Record<string, string>) => page.evaluate((entries) => Object.fromEntries(
    Object.entries(entries).map(([name, selector]) => {
      const box = document.querySelector(selector)!.getBoundingClientRect();
      const round = (value: number) => Math.round(value * 10) / 10;
      return [name, { left: round(box.left), top: round(box.top), right: round(box.right), bottom: round(box.bottom), width: round(box.width), height: round(box.height) }];
    })), selectors);
  const IMMERSE = {
    root: '[data-immerse]', stage: '[data-immerse-stage]', actions: '[data-immerse-actions]', ui: '[data-immerse-ui]',
    bar: '[data-immerse-bar]', close: '[data-immerse-close]',
  };

  it('沉浸模式桌面照 Shorts 三段：9:16 舞台居中、动作列贴舞台右侧 12px、作者标题在左下；横片换成 16:9，动作列收进框里；浅色主题下仍是深色层', { timeout: 90_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const { page } = opened;
      const { wide, tall } = await pickClips(page);
      await pinQueue(page, [tall, wide]);
      await muteVideos(page);
      await openImmerse(page, tall.id);
      const portrait = await rectsOf(page, IMMERSE);
      assert.deepEqual(portrait.stage, { left: 415, top: 0, right: 865, bottom: 800, width: 450, height: 800 });
      assert.deepEqual([portrait.actions.left, portrait.actions.width, portrait.actions.bottom], [877, 72, 792]);
      assert.deepEqual([portrait.ui.left, portrait.ui.bottom], [20, 780]);
      assert.deepEqual(portrait.bar, { left: 0, top: 780, right: 1280, bottom: 800, width: 1280, height: 20 });
      assert.deepEqual([portrait.close.top, portrait.close.right, portrait.close.width], [15, 1265, 42]);
      const faces = await page.evaluate(() => {
        const read = (selector: string, names: string[]) => {
          const style = getComputedStyle(document.querySelector(selector)!);
          return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name)]));
        };
        return {
          root: read('[data-immerse]', ['background-color']),
          track: read('[data-immerse-track]', ['border-radius', 'background-color']),
          floating: (() => {
            const probe = document.createElement('div');
            probe.style.borderRadius = 'var(--floating-radius)';
            document.querySelector('[data-immerse]')!.append(probe);
            const value = getComputedStyle(probe).borderRadius;
            probe.remove();
            return value;
          })(),
          circle: read('[data-immerse-circle]', ['width', 'height', 'border-radius', 'background-color', 'color']),
          glyph: read('[data-immerse-circle] svg', ['width', 'height', 'fill', 'stroke']),
          author: read('[data-immerse-author]>a', ['color', 'font-weight']),
          title: read('[data-immerse-title]', ['font-size', 'font-weight', 'text-overflow', 'white-space', 'pointer-events']),
          caption: read('[data-immerse-ui]', ['pointer-events']),
          progress: read('[data-immerse-bar] i', ['height']),
        };
      });
      const { floating, ...face } = faces;
      assert.notEqual(floating, '0px');
      assert.deepEqual(face, {
        root: { 'background-color': 'rgb(15, 15, 15)' },
        track: { 'border-radius': floating, 'background-color': 'rgb(0, 0, 0)' },
        circle: { width: '48px', height: '48px', 'border-radius': '50%', 'background-color': 'rgba(255, 255, 255, 0.1)', color: 'rgb(241, 241, 241)' },
        glyph: { width: '24px', height: '24px', fill: 'none', stroke: 'rgb(241, 241, 241)' },
        author: { color: 'rgb(241, 241, 241)', 'font-weight': '600' },
        title: { 'font-size': '20px', 'font-weight': '600', 'text-overflow': 'ellipsis', 'white-space': 'nowrap', 'pointer-events': 'auto' },
        caption: { 'pointer-events': 'none' },
        progress: { height: '4px' },
      });

      // 进度：4px 的线贴着 20px 热区的底，走钨丝蓝；指针靠近才变粗。
      assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('[data-immerse-bar] i')!).backgroundColor),
        await tokenColor(page, '[data-immerse]', '--tungsten'));
      await page.hover('[data-immerse-bar]');
      await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-immerse-bar] i')!).height === '9px');

      // 动作键以底色反馈，悬停与按住保持尺寸；选中使用浅底深字。
      await page.route('**/api/feedback', (route) => route.fulfill({ json: { feedback: 'seen', o_count: 0 } }));
      const seen = '[data-immerse-actions] button[aria-label="标为看过"]';
      const resting = await page.locator(seen).boundingBox();
      const background = await page.locator(seen).evaluate(node => getComputedStyle(node).backgroundColor);
      await page.hover(seen);
      await page.waitForFunction(([selector, color]) => getComputedStyle(document.querySelector(selector!)!).backgroundColor !== color, [seen, background]);
      assert.deepEqual(await page.locator(seen).boundingBox(), resting);
      assert.equal(await page.locator(seen).evaluate(node => getComputedStyle(node).scale), 'none');
      await page.mouse.down();
      assert.deepEqual(await page.locator(seen).boundingBox(), resting);
      assert.equal(await page.locator(seen).evaluate(node => getComputedStyle(node).scale), 'none');
      await page.mouse.up();
      await page.locator(`${seen}[aria-pressed="true"]`).waitFor({ timeout: 5_000 });
      await page.mouse.move(0, 0);
      assert.deepEqual(await page.evaluate((selector) => {
        const style = getComputedStyle(document.querySelector(selector)!);
        return [style.backgroundColor, style.color];
      }, seen), ['rgb(241, 241, 241)', 'rgb(15, 15, 15)']);

      await page.keyboard.press('ArrowDown');
      await page.waitForFunction((id) => location.search === `?id=${id}`, wide.id);
      await settledClip(page);
      const landscape = await rectsOf(page, IMMERSE);
      assert.deepEqual(landscape.stage, { left: 230.4, top: 169.6, right: 1049.6, bottom: 630.4, width: 819.2, height: 460.8 });
      assert.deepEqual([landscape.actions.right, landscape.actions.bottom], [1037.6, 612.4]);
      assert.equal(landscape.ui.width, 404.8);

      await page.evaluate(() => { document.documentElement.dataset.theme = 'light' });
      assert.deepEqual(await page.evaluate(() => [
        getComputedStyle(document.querySelector('[data-immerse]')!).backgroundColor,
        getComputedStyle(document.querySelector('[data-immerse-author]>a')!).color,
      ]), ['rgb(15, 15, 15)', 'rgb(241, 241, 241)']);
      assert.deepEqual(opened.problems.filter((line) => !line.includes('VIDEOJS')), []);
    } finally {
      await opened.close();
    }
  });

  it('沉浸模式手机铺满视口：舞台不留圆角，动作列贴右、作者标题贴左下，作者那行的时长与序号收起', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', MOBILE);
    try {
      const { page } = opened;
      const { wide, tall } = await pickClips(page);
      await pinQueue(page, [tall, wide]);
      await muteVideos(page);
      await openImmerse(page, tall.id);
      const rects = await rectsOf(page, { ...IMMERSE, avatar: '[data-immerse-avatar]' });
      assert.deepEqual(rects.stage, { left: 0, top: 0, right: 390, bottom: 844, width: 390, height: 844 });
      assert.deepEqual([rects.actions.right, rects.actions.bottom, rects.actions.width], [382, 752, 56]);
      assert.deepEqual([rects.ui.left, rects.ui.right, rects.ui.bottom], [14, 308, 816]);
      assert.deepEqual([rects.avatar.width, rects.avatar.height], [36, 36]);
      assert.deepEqual([rects.close.top, rects.close.right], [10, 380]);
      assert.deepEqual(await page.evaluate(() => ({
        radius: getComputedStyle(document.querySelector('[data-immerse-track]')!).borderRadius,
        meta: getComputedStyle(document.querySelector('[data-immerse-author]>span')!).display,
        title: getComputedStyle(document.querySelector('[data-immerse-title]')!).fontSize,
      })), { radius: '0px', meta: 'none', title: '14px' });
      assert.deepEqual(opened.problems.filter((line) => !line.includes('VIDEOJS')), []);
    } finally {
      await opened.close();
    }
  });

  /* ── 侧栏岛（`frontend/src/react/sidebar/sidebar.css`） ── */

  /** 打开首页等侧栏的分组画出来。聚合照演示库真取，只保证时长那一组在（演示库的短片可能凑不出总时长）。 */
  async function openSidebar(viewport: Viewport): Promise<Visit> {
    const opened = await visit(browser, '/', viewport);
    await opened.page.route((url) => url.pathname === '/api/facets', async (route) => {
      const real = await (await route.fetch()).json() as { stats?: Record<string, unknown> };
      await route.fulfill({ json: { ...real, stats: { ...real.stats, duration: real.stats?.duration || 540 } } });
    });
    await opened.page.reload({ waitUntil: 'load' });
    await opened.page.locator('#drawer [data-sidebar-group="时长"]').waitFor({ state: 'attached', timeout: 15_000 });
    await settle(opened.page);
    return { ...opened, close: async () => {
      await opened.page.unrouteAll({ behavior: 'ignoreErrors' });
      await opened.close();
    } };
  }

  it('侧栏当前项自己不铺底，由挂在抽屉上的那块玻璃标出；悬停时玻璃跟过去，离开这一列回到当前项', { timeout: 60_000 }, async () => {
    const opened = await openSidebar(DESKTOP);
    try {
      const { page } = opened;
      const read = (key: string) => page.evaluate((target) => {
        const glide = document.querySelector<HTMLElement>('[data-sidebar-glide]')!;
        const button = document.querySelector<HTMLElement>(`#drawer [data-sidebar-nav] [data-nav="${target}"]`)!;
        const box = (node: Element) => {
          const rect = node.getBoundingClientRect();
          return [rect.left, rect.top, rect.width, rect.height].map(Math.round);
        };
        const style = getComputedStyle(button);
        return {
          glide: box(glide), button: box(button), parent: glide.parentElement?.id,
          layer: getComputedStyle(glide).zIndex, radius: getComputedStyle(glide).borderRadius,
          fill: [style.backgroundColor, style.backgroundImage, style.boxShadow], color: style.color,
        };
      }, key);
      const current = await read('');
      assert.equal(current.parent, 'drawer', '玻璃没有挂在抽屉上，纵滚时会被滚动层切掉');
      assert.deepEqual([current.layer, current.radius], ['-1', '10px'], '玻璃要压在字底下、圆角照按钮的 10px');
      assert.deepEqual(current.glide, current.button, '玻璃没有垫在当前项下面');
      assert.deepEqual(current.fill, ['rgba(0, 0, 0, 0)', 'none', 'none'], '当前项自己铺了底，静止时就是两层底叠着');
      assert.equal(current.color, await tokenColor(page, '#drawer', '--glass-text'), '当前项的字色不是玻璃上的字色');
      const idle = await read('studios');
      assert.equal(idle.color, await tokenColor(page, '#drawer', '--color-text-secondary'), '未选中项的字色不是次要文字色');

      await page.locator('#drawer [data-sidebar-nav] [data-nav="studios"]').hover();
      const hovered = await read('studios');
      assert.deepEqual(hovered.glide, hovered.button, '悬停时玻璃没有跟到指着的那一项');
      assert.deepEqual(hovered.fill, ['rgba(0, 0, 0, 0)', 'none', 'none'], '悬停的那一项自己铺了底');
      await page.mouse.move(900, 420);
      const back = await read('');
      assert.deepEqual(back.glide, back.button, '指针离开这一列之后玻璃没有回到当前项');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('侧栏标题行：切换器 32px 圆标识、收起键 20px 高，与导航首项相隔 12px；分组箭头展开时转 90°；收起后只剩 60px 宽的图标列', { timeout: 60_000 }, async () => {
    const opened = await openSidebar(DESKTOP);
    try {
      const { page } = opened;
      const head = await page.evaluate(() => {
        const drawer = document.querySelector('#drawer')!;
        const rect = (selector: string) => drawer.querySelector(selector)!.getBoundingClientRect();
        const mark = drawer.querySelector('#brandHome .mark')!;
        const rotation = (group: Element) => {
          const matrix = new DOMMatrix(getComputedStyle(group.querySelector(':scope > [data-sidebar-toggle] svg')!).transform);
          return Math.round(Math.atan2(matrix.b, matrix.a) * 180 / Math.PI);
        };
        const groups = [...drawer.querySelectorAll<HTMLDetailsElement>('[data-sidebar-group]')];
        return {
          gap: Math.round(rect('[data-sidebar-nav]').top - rect('[data-sidebar-head]').bottom),
          mark: [Math.round(rect('#brandHome .mark').width), Math.round(rect('#brandHome .mark').height), getComputedStyle(mark).borderRadius],
          toggle: Math.round(rect('#filterBtn').height),
          open: groups.filter((group) => group.open).map(rotation),
          closed: groups.filter((group) => !group.open).map(rotation),
        };
      });
      assert.equal(head.gap, 12, '切换器与导航首项挨得太近，两块底色读起来像压在一起');
      assert.deepEqual(head.mark, [32, 32, '50%'], '切换器的标识不是 32px 的圆');
      assert.equal(head.toggle, 20, '收起键不是 20px 高');
      assert.ok(head.open.length && head.closed.length, `首页的分组没有一开一合可比：${JSON.stringify(head)}`);
      assert.ok(head.open.every((angle) => angle === 90), `展开的分组箭头没有转到 90°：${head.open}`);
      assert.ok(head.closed.every((angle) => angle === 0), `收着的分组箭头没有回到 0°：${head.closed}`);

      await page.locator('#filterBtn').click();
      await page.waitForFunction(() => Math.round(document.querySelector('#drawer')!.getBoundingClientRect().width) === 60,
        undefined, { timeout: 5_000 });
      const rail = await page.evaluate(() => {
        const drawer = document.querySelector('#drawer')!;
        const toggle = drawer.querySelector('#filterBtn')!.getBoundingClientRect();
        return {
          toggle: [Math.round(toggle.width), Math.round(toggle.height)],
          groups: [...new Set([...drawer.querySelectorAll('[data-sidebar-group]')].map((group) => getComputedStyle(group).display))],
          labels: [...new Set([...drawer.querySelectorAll('[data-sidebar-nav] button span')].map((span) => getComputedStyle(span).maxWidth))],
        };
      });
      assert.deepEqual(rail, { toggle: [36, 20], groups: ['none'], labels: ['0px'] }, '收起的窄栏不是一列图标');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('时长拉条：已选段是 accent 渐变，两端读数常显、推到端点也不越出轨道，刚动过的那枚压在上面', { timeout: 60_000 }, async () => {
    const opened = await openSidebar(DESKTOP);
    try {
      const { page } = opened;
      await page.locator('#drawer [data-sidebar-group="时长"] > [data-sidebar-toggle]').click();
      await page.locator('#drawer [data-sidebar-group="时长"] [data-sidebar-collapse]:not([inert])').waitFor({ timeout: 5_000 });
      const read = () => page.evaluate(() => {
        const range = document.querySelector('#durationRange')!;
        const bounds = range.getBoundingClientRect();
        return {
          fill: getComputedStyle(range.querySelector('[data-sidebar-range-fill]')!).backgroundImage,
          tips: [...range.querySelectorAll<HTMLElement>('[data-sidebar-range-tip]')].map((tip) => {
            const box = tip.getBoundingClientRect();
            return {
              end: tip.dataset.rangeEnd, text: tip.textContent, layer: getComputedStyle(tip).zIndex,
              visible: getComputedStyle(tip).visibility === 'visible' && box.width > 0,
              inside: box.left >= bounds.left - 1 && box.right <= bounds.right + 1,
            };
          }),
        };
      });
      const resting = await read();
      const accent = await tokenColor(page, '#drawer', '--color-accent-400');
      assert.ok(resting.fill.startsWith('linear-gradient(90deg') && resting.fill.includes(accent),
        `已选段不是从 accent-400 起的横向渐变：${resting.fill}`);
      assert.deepEqual(resting.tips, [
        { end: 'min', text: '0 分钟', layer: '1', visible: true, inside: true },
        { end: 'max', text: '不限', layer: '2', visible: true, inside: true },
      ], '两端读数要常显、贴着轨道两端不越界，没动过时右端那枚在上');

      await page.locator('#durMin').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[data-sidebar-range-tip][data-range-end="min"]')?.hasAttribute('data-range-active'),
        undefined, { timeout: 5_000 });
      const moved = await read();
      assert.deepEqual(moved.tips.map((tip) => [tip.end, tip.layer, tip.inside]), [['min', '2', true], ['max', '1', true]],
        '刚动过的那一端没有压到上面');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('手机侧栏：抽屉开着时遮罩外壳隐形、暗色画在 ::before 上，层级低于抽屉；收起时遮罩退出渲染树', { timeout: 60_000 }, async () => {
    const opened = await openSidebar(MOBILE);
    try {
      const { page } = opened;
      const scrim = () => page.evaluate(() => {
        const node = document.querySelector('#scrim')!, drawer = document.querySelector('#drawer')!;
        const before = getComputedStyle(node, '::before');
        return {
          display: getComputedStyle(node).display, visibility: getComputedStyle(node).visibility,
          dim: [before.visibility, before.backgroundColor],
          layers: [Number(getComputedStyle(node).zIndex), Number(getComputedStyle(drawer).zIndex)],
          fill: getComputedStyle(drawer).getPropertyValue('--glass-fill'),
        };
      });
      assert.equal((await scrim()).display, 'none', '抽屉收着时遮罩还在渲染树里，iOS 的 Safari 会拿它给状态栏取色');
      await page.locator('#filterBtn').tap();
      await page.waitForFunction(() => document.querySelector('#drawer')?.classList.contains('open'));
      const shown = await scrim();
      assert.deepEqual([shown.display, shown.visibility], ['block', 'hidden'], '遮罩外壳没有隐形，iOS 的 Safari 会拿它给状态栏取色');
      assert.equal(shown.dim[0], 'visible', '遮罩的暗色没有画在 ::before 上');
      assert.notEqual(shown.dim[1], 'rgba(0, 0, 0, 0)', '遮罩的 ::before 是透明的，压不暗身后的页面');
      assert.ok(shown.layers[0] < shown.layers[1], `遮罩压在抽屉上面，抽屉里就点不动了：${shown.layers}`);
      assert.ok(shown.fill.replace(/\s/g, '').includes('86%'), `窄屏抽屉的填充没有加厚到 86%：${shown.fill}`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });
});
