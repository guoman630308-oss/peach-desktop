/* 设计决定：设置、口味、统计与筛选玻璃。读 `getComputedStyle` 断言用户定过的外观；共用的桩与浏览器生命周期在 `design-fixture.ts`。 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { openFollowFeed } from './follow-fixture.ts';
import { requiredEnv, settle, visit, VIEWPORTS } from './harness.ts';
import { openHome, openPanel, ORDER, PANEL, stubServer } from './settings-fixture.ts';
import {
  DESKTOP, MOBILE, tokenColor, settledRun, openActivity, TASTE_CATEGORIES, TASTE_FACE, openTaste, openIndexPage,
  farthestShadow, holdApi, openLightbox, installDesignBrowser,
} from './design-fixture.ts';

describe('设计决定：设置、口味、统计与筛选玻璃', () => {
  let browser: Browser;
  installDesignBrowser((next) => { browser = next; });

  it('使用空间按字节量级的真实容量画出已用那一段，每个卷一行、不越出卡片', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/stats', DESKTOP);
    try {
      const page = opened.page;
      const TB = 1024 ** 4;
      const volumes = [
        { kind: 'system', label: '系统盘', root: 'C:\\', online: true, total: 2 * TB, used: 1.8 * TB, free: .2 * TB },
        { kind: 'media', label: '资源盘', root: 'R:\\media', online: false, total: null, used: null, free: null },
        { kind: 'media', label: 'PikPak 网盘', root: 'A:\\', online: true, total: 10240 * TB, used: 0, free: 10240 * TB },
      ];
      await page.route(/\/api\/stats(?:\?|$)/, async (route) => {
        const json = await (await route.fetch()).json();
        await route.fulfill({ json: { ...json, storage_volumes: volumes, storage_summary: {
          volumes: 3, online: 2, measured: 2, free: 10240.2 * TB, used: 1.8 * TB, total: 10242 * TB,
        } } });
      });
      await page.reload({ waitUntil: 'load' });
      await page.getByRole('tab', { name: /使用空间/ }).click();
      const panel = page.locator('[role="tabpanel"]').first();
      await panel.locator('[role="progressbar"]').first().waitFor({ state: 'visible', timeout: 15_000 });
      await settle(page);
      const shown = await panel.evaluate((element) => {
        const card = element.firstElementChild!;
        const cardBox = card.getBoundingClientRect();
        const detail = card.lastElementChild!.getBoundingClientRect();
        return {
          filled: [...element.querySelectorAll('[role="progressbar"]')].map((bar) =>
            Math.round(bar.querySelectorAll('rect')[1]!.getBoundingClientRect().width / bar.getBoundingClientRect().width * 100)),
          rows: [...element.querySelectorAll('article')].map((row) => getComputedStyle(row).borderBottomWidth),
          overflow: detail.right - (cardBox.right - parseFloat(getComputedStyle(card).paddingRight)),
          text: element.textContent ?? '',
        };
      });
      assert.deepEqual(shown.filled, [90, 0], '进度条画出来的已用比例不对');
      assert.deepEqual(shown.rows, ['1px', '1px', '0px'], '卷与卷之间没有覆盖率那样的分隔线');
      assert.ok(shown.overflow <= .5, `使用空间的详情越出卡片内边距 ${shown.overflow}px`);
      assert.ok(shown.text.includes('10.00 PB'), '网盘容量没按 PB 写');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of VIEWPORTS) {
    it(`口味维度排名宽屏跟雷达那一栏等高、窄屏按条数给高度，图拉高时条不变粗（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openTaste(browser, viewport);
      try {
        const page = opened.page;
        const bars = page.locator('section[aria-label="口味维度排名"]');
        await bars.locator('.recharts-bar-rectangle').first().waitFor({ state: 'visible', timeout: 15_000 });
        await settle(page);
        const shown = await page.locator('section[aria-label="浏览器画像"]').evaluate((section) => {
          const radar = section.firstElementChild!.getBoundingClientRect();
          const ranked = section.querySelector('section[aria-label="口味维度排名"]')!.getBoundingClientRect();
          return {
            radar: radar.height,
            ranked: ranked.height,
            thickness: [...section.querySelectorAll('.recharts-bar-rectangle')]
              .map((bar) => bar.getBoundingClientRect().height),
          };
        });
        assert.equal(shown.thickness.length, TASTE_CATEGORIES.length);
        if (viewport.mobile) {
          assert.ok(shown.ranked >= 32 * TASTE_CATEGORIES.length,
            `窄屏排行条只有 ${shown.ranked}px，${TASTE_CATEGORIES.length} 条挤不下`);
        } else {
          assert.ok(Math.abs(shown.ranked - shown.radar) < 2,
            `排行条 ${shown.ranked}px，雷达那一栏 ${shown.radar}px，两栏不等高`);
        }
        // 26 是一格 32 里默认留出的粗细；图被拉高时多出来的高度拉开条距，不进条本身。
        assert.ok(shown.thickness.every((height) => height <= 26.5), `条粗 ${shown.thickness.join('、')}px`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  it('口味榜的女优与创作者头像按人脸框放大：图保持原比例、盖满圆框，不露出底下的首字母', { timeout: 60_000 }, async () => {
    const opened = await openTaste(browser, DESKTOP);
    try {
      const page = opened.page;
      await page.getByRole('tablist', { name: '口味证据来源' }).getByRole('tab', { name: 'Peach 内部' }).click();
      for (const name of ['女优', '创作者']) {
        await page.getByRole('tablist', { name: 'Peach 口味维度' }).getByRole('tab', { name }).click();
        const img = page.getByRole('tabpanel', { name }).locator('img[data-facebox]').first();
        await img.scrollIntoViewIfNeeded();
        // 放大是图加载后 `avatarFrame` 写进内联 style 的；等到那一步落地再量。
        await page.waitForFunction((element) => element instanceof HTMLImageElement
          && element.complete && element.naturalWidth > 0 && element.style.width !== '',
        await img.elementHandle(), { timeout: 15_000 });
        const frame = await img.evaluate((element) => {
          const ring = element.parentElement!.getBoundingClientRect();
          const box = element.getBoundingClientRect();
          return {
            maxWidth: getComputedStyle(element).maxWidth, ring: ring.width,
            width: box.width, aspect: box.width / box.height,
            gaps: [box.left - ring.left, ring.right - box.right, box.top - ring.top, ring.bottom - box.bottom],
          };
        });
        assert.equal(frame.maxWidth, 'none', `${name}头像的图被预检的 max-width 夹住`);
        assert.ok(frame.width > frame.ring, `${name}头像没有按人脸框放大：图宽 ${frame.width}px，圆框 ${frame.ring}px`);
        assert.ok(Math.abs(frame.aspect - TASTE_FACE.imgW / TASTE_FACE.imgH) < .02,
          `${name}头像的图宽高比 ${frame.aspect}，被压扁了`);
        assert.ok(frame.gaps.every((gap) => gap <= .5), `${name}头像的图没盖满圆框：${frame.gaps.join('、')}`);
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('被打断的那一轮只有结束原因，卡片正文下面不留空行', { timeout: 60_000 }, async () => {
    const opened = await openActivity(browser, [
      { ...settledRun(1, 'interrupted', '追更检查'), error: '服务重启，这一轮没有跑完' },
    ]);
    try {
      const body = await opened.page.locator('li[data-status="interrupted"] > div').first()
        .evaluate((element) => ({
          trailing: element.getBoundingClientRect().bottom - element.lastElementChild!.getBoundingClientRect().bottom,
          padding: parseFloat(getComputedStyle(element).paddingBottom),
        }));
      assert.ok(Math.abs(body.trailing - body.padding) <= .5,
        `正文最后一行下面空出 ${body.trailing}px，底边距只有 ${body.padding}px`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  /* 首页「未入库的新作」那一排会自己横着走，每一帧都发一次 scroll。手机上侧栏是抽屉，
     两枚弹层一打开就碰上它；演示库没有订阅，这一排由拦下的 `/api/feeds/discoveries`
     画出来（字段以 `src/peach/web_feeds.py` 为准）。夹具关了动效，自动滚动不起步，
     所以这里亲手滚它，发出的是同一种 scroll。 */
  it('390px 下侧栏两枚弹层不随别处那一排横滚收起，整页滚动才收', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', MOBILE);
    try {
      const items = Array.from({ length: 12 }, (_, index) => ({
        id: index + 1, code: `DEMO-${String(index + 1).padStart(3, '0')}`, title: `演示新作 ${index + 1}`,
        studio: '演示厂牌', release_date: '2026-09-01', has_cover: false, cover_url: '', link: '', read: false,
      }));
      await opened.page.route((url) => url.pathname === '/api/feeds/discoveries', (route) => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({ items }),
      }));
      await opened.page.reload({ waitUntil: 'load' });
      const row = opened.page.locator('#feedNew .feednewrow');
      await row.waitFor({ state: 'visible', timeout: 15_000 });
      await settle(opened.page);
      assert.ok(await row.evaluate((element) => element.scrollWidth > element.clientWidth + 120),
        '新作那一排在 390px 下没有可横滚的余量，这条用例量不到东西');
      await opened.page.locator('#filterBtn').tap();
      await opened.page.waitForFunction(() => document.querySelector('#drawer')?.classList.contains('open'));
      for (const [trigger, menuId] of [['#brandHome', 'boardLibraryMenu'], ['#boardGlowBtn', 'boardGlowMenu']] as const) {
        const menu = opened.page.locator(`#${menuId}`);
        await opened.page.locator(trigger).tap();
        await menu.waitFor({ state: 'visible', timeout: 5_000 });
        for (let step = 0; step < 4; step += 1) {
          await row.evaluate((element) => { element.scrollLeft += 30; });
          await opened.page.waitForTimeout(100);
        }
        assert.ok(await menu.isVisible(), `${trigger} 打开的弹层随新作那一排横滚收起了`);
        // 菜单装不下时本来就要在内部滚；捕获阶段的 scroll 连它自己的也收得到。
        await menu.evaluate((element) => element.dispatchEvent(new Event('scroll')));
        await opened.page.waitForTimeout(100);
        assert.ok(await menu.isVisible(), `${trigger} 打开的弹层随它自己的内部滚动收起了`);
        assert.equal(await opened.page.locator(trigger).getAttribute('aria-expanded'), 'true');
        await opened.page.evaluate(() => document.dispatchEvent(new Event('scroll')));
        await menu.waitFor({ state: 'hidden', timeout: 5_000 });
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('设置里的光晕配色和侧栏配色卡是同一组预设色块，键盘选一档两处一起换', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    try {
      await settle(opened.page);
      await opened.page.locator('#settingsBtn').click();
      const grid = opened.page.locator('#homeGlowControls [data-glow-grid]');
      await grid.waitFor({ state: 'visible', timeout: 10_000 });
      const chips = (root: string) => opened.page.locator(`${root} [data-glow-preset]`).evaluateAll((nodes) =>
        nodes.map((node) => ({
          key: (node as HTMLElement).dataset.glowPreset,
          label: node.getAttribute('aria-label'),
          pressed: node.getAttribute('aria-pressed'),
          ball: getComputedStyle(node.querySelector('[data-glow-ball]')!).backgroundImage,
        })));
      const inSettings = await chips('#homeGlowControls');
      assert.ok(inSettings.length >= 2, '设置里没有预设色块');
      assert.deepEqual(inSettings, await chips('#boardGlowMenu'), '设置里的预设色块和侧栏配色卡不是同一组');
      const size = await grid.locator('[data-glow-ball]').first().evaluate((node) => node.getBoundingClientRect().width);
      assert.equal(size, 28, '设置里的色块和侧栏那一枚不是同一副尺寸');
      /* 设置这一行有整块设置那么宽：列数跟着可用宽度走，每格就是一枚球，挨着排满再换行，
         间距与侧栏那条 `[data-glow-grid]` 同一个值；侧栏那张卡仍是六列。 */
      const gridStyle = (root: string) => opened.page.locator(`${root} [data-glow-grid]`).evaluate((node) => {
        const style = getComputedStyle(node);
        return { tracks: style.gridTemplateColumns, gap: style.columnGap, inline: style.paddingLeft };
      });
      const swatchLayout = () => grid.evaluate((node) => {
        const box = node.getBoundingClientRect();
        const chips = [...node.querySelectorAll('[data-glow-preset]')].map((chip) => chip.getBoundingClientRect());
        const firstTop = chips[0]!.top;
        return {
          count: chips.length,
          firstRow: chips.filter((chip) => chip.top === firstTop).length,
          rows: new Set(chips.map((chip) => chip.top)).size,
          steps: chips.slice(1).filter((chip) => chip.top === firstTop).map((chip, index) => chip.left - chips[index]!.left),
          startsAt: chips[0]!.left - box.left,
          overflow: node.scrollWidth > node.clientWidth || chips.some((chip) => chip.right > box.right + 0.5),
          viewportOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        };
      });
      const settingsGrid = await gridStyle('#homeGlowControls');
      const sidebarGrid = await gridStyle('#boardGlowMenu');
      assert.ok(settingsGrid.tracks.split(' ').every((track) => track === '28px'), '设置里的色块每格不是一枚球的宽度');
      assert.equal(settingsGrid.gap, sidebarGrid.gap, '设置里的色块间距和侧栏配色卡不同');
      assert.equal(settingsGrid.inline, '0px', '设置里的色块没有从这一行的内容左缘起');
      // 侧栏配色卡此刻收着，计算值停在声明式 `repeat(6, 1fr)`；展开时是六个解析后的宽度。
      assert.ok(sidebarGrid.tracks === 'repeat(6, 1fr)' || sidebarGrid.tracks.split(' ').length === 6,
        `侧栏配色卡不再是每行六枚：${sidebarGrid.tracks}`);
      const wide = await swatchLayout();
      assert.ok(wide.firstRow > 6, `桌面宽度下第一行只排了 ${wide.firstRow} 枚，右边的空间没用上`);
      assert.ok(wide.steps.every((step) => step === 34), `设置里的色块没有挨着排：步长 ${wide.steps.join('/')}`);
      assert.equal(wide.startsAt, 0, '设置里的第一枚色块没有从这一行的内容左缘起');
      assert.equal(wide.overflow, false, '桌面宽度下色块越出了这一行');
      assert.equal(await grid.getAttribute('role'), 'group');
      assert.ok(await grid.getAttribute('aria-label'), '预设色块那一组没有无障碍名称');

      const target = inSettings.find((chip) => chip.pressed === 'false')!;
      const chip = grid.locator(`[data-glow-preset="${target.key}"]`);
      await chip.focus();
      await opened.page.keyboard.press('Space');
      await opened.page.waitForFunction((key) => document.querySelector(
        `#homeGlowControls [data-glow-preset="${key}"]`)?.getAttribute('aria-pressed') === 'true', target.key);
      assert.equal(await grid.locator('[aria-pressed="true"]').count(), 1);
      assert.equal(await opened.page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.glowPreset
        && document.activeElement!.closest('#homeGlowControls [data-glow-grid]') ? (document.activeElement as HTMLElement).dataset.glowPreset : null),
        target.key, '选完之后焦点离开了刚选的那一枚色块');
      assert.equal(await opened.page.locator(`#boardGlowMenu [data-glow-preset="${target.key}"]`)
        .getAttribute('aria-pressed'), 'true', '侧栏配色卡没有跟着换到同一档');
      assert.equal(await opened.page.locator('#homeGlowControls [data-glow-preset-name]').textContent(), target.label);
      assert.equal(await opened.page.evaluate(() => JSON.parse(localStorage.getItem('peach.settings.v1')!).homeGlow.preset),
        target.key, '选中的那一档没有写进设置');

      await opened.page.setViewportSize({ width: MOBILE.width, height: MOBILE.height });
      await grid.waitFor({ state: 'visible', timeout: 10_000 });
      const narrow = await swatchLayout();
      assert.ok(narrow.firstRow < narrow.count && narrow.rows > 1, `${MOBILE.width}px 下色块没有换行`);
      assert.ok(narrow.steps.every((step) => step === 34), `${MOBILE.width}px 下色块没有挨着排`);
      assert.equal(narrow.startsAt, 0, `${MOBILE.width}px 下第一枚色块没有从内容左缘起`);
      assert.equal(narrow.overflow, false, `${MOBILE.width}px 下色块越出了这一行`);
      assert.equal(narrow.viewportOverflow, false, `${MOBILE.width}px 下页面出现横向溢出`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  /* ADR-0050：设置弹层只放一行一个值、改完就生效的控件；带「保存配置」的表单在配置页，
     要确认、要看进度的长任务在数据管理页，订阅源在关注管理页。 */
  /* 设置面板：盖住整页的弹层与作品详情浮窗同一档遮罩、同一组进出场；遮罩不带模糊，值只来自 `--scrim`。
     动效要真跑起来才读得到关键帧名，这一条自建不减动态的上下文。 */
  it('设置面板的遮罩取 --scrim、不带模糊、压在最顶层，卡片用弹层那一组关键帧进出', { timeout: 60_000 }, async () => {
    const context = await browser.newContext({ viewport: { width: DESKTOP.width, height: DESKTOP.height } });
    await context.addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false })));
    try {
      const page = await context.newPage();
      await stubServer(page);
      await page.goto(`${requiredEnv('PEACH_E2E_ORIGIN')}/`, { waitUntil: 'load' });
      await settle(page);
      await openPanel(page);
      const scrim = await tokenColor(page, 'body', '--scrim');
      const shown = await page.locator(PANEL).evaluate((panel) => {
        const style = getComputedStyle(panel);
        const card = getComputedStyle(panel.querySelector('[data-settings-card]')!);
        return {
          position: style.position, z: style.zIndex, inset: [style.top, style.right, style.bottom, style.left],
          layer: getComputedStyle(document.documentElement).getPropertyValue('--layer-dialog').trim(),
          face: style.backgroundColor, blur: [style.backdropFilter, card.backdropFilter],
          backdrop: [style.animationName, style.animationFillMode], card: [card.animationName, card.animationFillMode],
          padding: [style.paddingTop, style.paddingBottom],
        };
      });
      assert.equal(shown.position, 'fixed');
      assert.equal(shown.z, shown.layer, '设置遮罩不在 --layer-dialog 那一层');
      assert.deepEqual(shown.inset, ['0px', '0px', '0px', '0px'], '设置遮罩没有铺满视口');
      assert.equal(shown.face, scrim, '设置遮罩的颜色不是 --scrim');
      assert.deepEqual(shown.blur, ['none', 'none'], '设置遮罩或卡片带了模糊');
      assert.deepEqual(shown.backdrop, ['settings-backdrop-in', 'both']);
      // 进场填 `backwards`：终点帧留下的 filter 会另起一个 backdrop root，左栏玻璃就只采样得到卡片自己。
      assert.deepEqual(shown.card, ['board-dialog-in', 'backwards']);
      /* 安全区内边距让开刘海与 Home 指示条；桌面上没有安全区，两端都是 18px 的底。 */
      assert.deepEqual(shown.padding, ['18px', '18px']);

      const leaving = await page.locator(PANEL).evaluate((panel) => {
        (panel.querySelector('#settingsClose') as HTMLButtonElement).click();
        const card = getComputedStyle(panel.querySelector('[data-settings-card]')!);
        return { backdrop: getComputedStyle(panel).animationName, card: [card.animationName, card.animationFillMode] };
      });
      assert.equal(leaving.backdrop, 'settings-backdrop-out');
      // 退场要 `both`：终点（透明）不是元素的自然状态。
      assert.deepEqual(leaving.card, ['board-dialog-out', 'both']);
      await page.locator(PANEL).waitFor({ state: 'hidden', timeout: 5_000 });
    } finally {
      await context.close();
    }
  });

  it('设置面板的标题栏占满右栏、压在滚动区上面，滚过那段留白才投下左栏玻璃那道影子', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page } = opened;
    try {
      await openPanel(page);
      const shadow = await page.locator(`${PANEL} [data-settings-card]`).evaluate((card) => {
        const probe = document.createElement('div');
        probe.style.boxShadow = 'var(--glass-shadow)';
        card.append(probe);
        const value = getComputedStyle(probe).boxShadow;
        probe.remove();
        return value;
      });
      const read = () => page.locator(PANEL).evaluate((panel) => {
        const head = panel.querySelector('[data-settings-head]')!, scroll = panel.querySelector('[data-settings-scroll]')!;
        const top = head.getBoundingClientRect(), body = scroll.getBoundingClientRect(), style = getComputedStyle(scroll);
        return {
          head: [top.left, top.width, top.bottom], body: [body.left, body.width, body.top],
          z: getComputedStyle(head).zIndex, shadow: getComputedStyle(head).boxShadow, clip: getComputedStyle(head).clipPath,
          overflow: style.overflowY, overscroll: style.overscrollBehaviorY, gap: style.paddingTop,
        };
      });
      const resting = await read();
      assert.deepEqual(resting.head.slice(0, 2), resting.body.slice(0, 2), '标题栏和滚动区不是同一列同一宽');
      assert.ok(resting.head[2]! <= resting.body[2]! + 0.5, '标题栏没有压在滚动区上面');
      assert.equal(resting.z, '2');
      assert.equal(resting.overflow, 'auto');
      assert.equal(resting.overscroll, 'contain', '滚到底之后滚动会漏给背后的页面');
      assert.equal(resting.shadow, 'none', '还没滚动标题栏就投了影');
      // 影子只要往下那一半：往上会糊在标题自己头上、往左会糊到左栏上。
      assert.equal(resting.clip, 'inset(0px 0px -96px)');

      await page.locator(`${PANEL} [data-settings-scroll]`).evaluate((scroll) => { scroll.scrollTop = 4 });
      await page.waitForTimeout(100);
      assert.equal((await read()).shadow, 'none', `滚动量还没过 ${resting.gap} 的留白标题栏就投了影`);
      await page.locator(`${PANEL} [data-settings-scroll]`).evaluate((scroll) => { scroll.scrollTop = 80 });
      await page.waitForFunction((want) => getComputedStyle(
        document.querySelector('#settingsPanel [data-settings-head]')!).boxShadow === want, shadow);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('设置面板左栏当前项自己不铺底，那块玻璃跟着指针走、离开这一列回到当前项', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page } = opened;
    try {
      await openPanel(page);
      const nav = page.locator(`${PANEL} [data-settings-nav]`);
      const glide = () => nav.locator('[data-settings-glide]').evaluate((node) => Math.round(node.getBoundingClientRect().top));
      const top = (name: string) => nav.getByRole('tab', { name, exact: true })
        .evaluate((node) => Math.round(node.getBoundingClientRect().top));
      const selected = await nav.getByRole('tab', { name: '外观与导航', exact: true }).evaluate((node) => {
        const style = getComputedStyle(node), icon = getComputedStyle(node.querySelector('svg')!);
        return { face: style.backgroundColor, ring: style.boxShadow, icon: [icon.width, icon.height, icon.fill === style.color, icon.stroke] };
      });
      assert.equal(selected.face, 'rgba(0, 0, 0, 0)', '当前项自己铺了底，滑动的那块玻璃被盖住');
      assert.equal(selected.ring, 'none');
      // Remix 的线条是 `fill` 画出的轮廓，全站默认的 `stroke:currentColor;fill:none` 会让它整枚消失。
      assert.deepEqual(selected.icon, ['20px', '20px', true, 'none']);
      assert.equal(await glide(), await top('外观与导航'), '玻璃没有落在当前项上');

      await nav.getByRole('tab', { name: '浏览与播放', exact: true }).hover();
      const target = await top('浏览与播放');
      await page.waitForFunction(([want]) => Math.round(document.querySelector(
        '#settingsPanel [data-settings-glide]')!.getBoundingClientRect().top) === want, [target]);
      assert.equal(await nav.getByRole('tab', { name: '外观与导航', exact: true }).getAttribute('aria-selected'), 'true',
        '指针移上去就换了分区');
      await page.locator(`${PANEL} [data-settings-scroll]`).hover();
      const home = await top('外观与导航');
      await page.waitForFunction(([want]) => Math.round(document.querySelector(
        '#settingsPanel [data-settings-glide]')!.getBoundingClientRect().top) === want, [home]);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('设置面板的分组是 --ground 圆角面，开关、主题分段与关闭键各是一副尺寸，窄屏下开关不换行', { timeout: 60_000 }, async () => {
    for (const viewport of [DESKTOP, MOBILE]) {
      const opened = await openHome(browser, viewport);
      const { page } = opened;
      try {
        await openPanel(page);
        const ground = await tokenColor(page, PANEL, '--ground');
        const shown = await page.locator(PANEL).evaluate((panel) => {
          const box = (node: Element) => {
            const rect = node.getBoundingClientRect();
            return [Math.round(rect.width), Math.round(rect.height)];
          };
          const group = getComputedStyle(panel.querySelector('[data-setting-group]:not([hidden])')!);
          const toggles = [...panel.querySelectorAll('[data-setting-group]:not([hidden]) [data-toggle]')];
          const on = panel.querySelector('#uiSoundsSetting')!, row = on.closest('[data-setting-row]')!;
          const title = row.querySelector('b')!.getBoundingClientRect(), knob = on.getBoundingClientRect();
          const close = panel.querySelector('#settingsClose')!;
          return {
            group: [group.backgroundColor, group.borderTopLeftRadius],
            toggles: [...new Set(toggles.map((node) => box(node).join('×')))],
            toggleOnRow: knob.top < title.bottom && knob.left > title.right,
            theme: [...panel.querySelectorAll('#themeSetting label')].map((node) => box(node).join('×')),
            themeIcon: box(panel.querySelector('#themeSetting svg')!).join('×'),
            close: [box(close).join('×'), getComputedStyle(close).borderTopLeftRadius, getComputedStyle(close).paddingLeft],
          };
        });
        assert.deepEqual(shown.group, [ground, '16px'], `${viewport.name}：分组不是 --ground 的 16px 圆角面`);
        assert.deepEqual(shown.toggles, ['42×24'], `${viewport.name}：开关不是同一副 42×24`);
        // 开关只有 42px，跟标题同一行绰绰有余；跟着换行只是白占一行高度。
        assert.equal(shown.toggleOnRow, true, `${viewport.name}：开关没有留在标题那一行`);
        assert.deepEqual(shown.theme, ['32×32', '32×32', '32×32'], `${viewport.name}：主题三档不是 32px 的圆`);
        assert.equal(shown.themeIcon, '16×16');
        assert.deepEqual(shown.close, ['24×24', '50%', '0px'], `${viewport.name}：关闭键不是 24px 的圆钮`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    }
  });

  it('侧栏排序的添加行：触发器与「添加」同高 --control-h，「添加」是主按钮，候选用完时触发器给禁止光标', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    try {
      const server = await stubServer(page);
      await page.reload({ waitUntil: 'load' });
      await settle(page);
      await openPanel(page);
      const row = page.locator(`${PANEL} [data-sidebar-add-row]`);
      await row.scrollIntoViewIfNeeded();
      const shown = await row.evaluate((node) => {
        const probe = document.createElement('div');
        probe.style.height = 'var(--control-h)';
        node.append(probe);
        const height = getComputedStyle(probe).height;
        probe.remove();
        const primary = document.createElement('button');
        primary.className = 'geist-button primary';
        document.body.append(primary);
        const want = getComputedStyle(primary);
        const expected = [want.backgroundColor, want.backgroundImage, want.color];
        primary.remove();
        const trigger = node.querySelector('[data-sidebar-add-trigger]')!, add = node.querySelector('[data-sidebar-add]')!;
        const face = getComputedStyle(add);
        return {
          height, trigger: getComputedStyle(trigger).height, add: face.height,
          face: [face.backgroundColor, face.backgroundImage, face.color], expected,
          cursor: getComputedStyle(trigger).cursor,
        };
      });
      assert.equal(shown.trigger, shown.height, '添加行的触发器没有引用 --control-h');
      assert.equal(shown.add, shown.height, '「添加」没有引用 --control-h');
      assert.deepEqual(shown.face, shown.expected, '「添加」没有穿主按钮那一身');
      assert.equal(shown.cursor, 'pointer');

      /* 一项一项加回去，直到清单里没有可加的入口。 */
      const add = row.locator('[data-sidebar-add]'), trigger = row.locator('[data-sidebar-add-trigger]');
      for (let left = 30; left > 0 && await add.isEnabled(); left -= 1) await add.click();
      assert.equal(await trigger.isDisabled(), true, '全部入口都加回侧栏之后触发器还能点');
      assert.ok((server.settings.sidebarOrder as string[]).length > ORDER.length, '加回的入口没有写进数据库');
      assert.equal(await trigger.evaluate((node) => getComputedStyle(node).cursor), 'not-allowed',
        '没有可添加的入口时触发器还是普通光标');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('来源与订阅中禁用的自动检查开关降低轨道不透明度并使用禁止光标', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    try {
      const server = await stubServer(page);
      server.schedule.available = false;
      await page.goto(new URL('/settings/library', page.url()).href, { waitUntil: 'load' });
      await settle(page);
      const source = () => page.getByRole('tablist', { name: '配置分区' }).getByRole('tab', { name: '来源与订阅', exact: true });
      await source().click();
      const toggle = page.getByRole('switch', { name: '自动检查更新', exact: true });
      await toggle.and(page.locator(':disabled:checked')).waitFor({ state: 'attached', timeout: 10_000 });
      const face = (node: Element) => {
        const label = node.closest('label')!;
        const track = getComputedStyle(label.querySelector(':scope > span[aria-hidden]')!);
        return { opacity: Number(track.opacity), gradient: track.backgroundImage, cursor: getComputedStyle(label).cursor };
      };
      const blocked = await toggle.evaluate(face);
      assert.equal(blocked.opacity, 0.5);
      assert.equal(blocked.cursor, 'not-allowed');
      server.schedule.available = true;
      await page.reload({ waitUntil: 'load' });
      await settle(page);
      await source().click();
      await toggle.and(page.locator(':enabled:checked')).waitFor({ state: 'attached', timeout: 10_000 });
      const live = await toggle.evaluate(face);
      assert.equal(live.opacity, 1);
      assert.equal(live.cursor, 'pointer');
      assert.notEqual(live.gradient, 'none');
      assert.equal(blocked.gradient, live.gradient, '两种状态应比较同一个已开启的开关');
      assert.ok(blocked.opacity < live.opacity, '禁用轨道的视觉强度没有降低');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('光晕参数块自己让出分组右侧的留白、下边距与设置行同一档；「玻璃原色」那一档只留漂移速度', { timeout: 60_000 }, async () => {
    for (const viewport of [DESKTOP, MOBILE]) {
      const opened = await openHome(browser, viewport);
      const { page } = opened;
      try {
        await openPanel(page);
        const block = page.locator(`${PANEL} [data-glow-setting]`);
        await block.waitFor({ state: 'visible', timeout: 10_000 });
        const shown = await block.evaluate((node) => {
          const group = node.closest('[data-setting-group]')!.getBoundingClientRect(), rect = node.getBoundingClientRect();
          const row = node.parentElement!.querySelector('[data-setting-row]')!;
          return {
            right: Math.round(group.right - rect.right), left: rect.left >= group.left,
            overflow: node.scrollWidth > node.clientWidth,
            pad: getComputedStyle(node).paddingBottom, rowPad: getComputedStyle(row).paddingBottom,
          };
        });
        assert.ok(shown.right >= 16 && shown.left, `${viewport.name}：参数块压到了分组的圆角上（右侧只剩 ${shown.right}px）`);
        assert.equal(shown.overflow, false, `${viewport.name}：参数块被内容顶宽`);
        assert.equal(shown.pad, shown.rowPad, `${viewport.name}：参数块的下边距和上面每一行不是同一档`);

        await block.locator('[data-glow-preset="native"]').click();
        await block.locator('[data-glow-native-note]').waitFor({ state: 'visible', timeout: 5_000 });
        const fields = await block.locator('[data-glow-field]').evaluateAll((nodes) => nodes
          .filter((node) => node.getClientRects().length > 0).map((node) => (node as HTMLElement).dataset.glowField));
        assert.deepEqual(fields, ['speed'], `${viewport.name}：「玻璃原色」下还摆着管不着任何东西的拉条`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    }
  });

  it('统计页选中的读数卡：2px 描边压在脚注带上面、圆角跟卡走，接触阴影收掉', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/stats', DESKTOP);
    try {
      const page = opened.page;
      const tab = page.locator('#stats [role="tab"][data-selected]').first();
      await tab.waitFor({ timeout: 15_000 });
      await settle(page);
      const ring = await tokenColor(page, '.peach-react', '--color-border-focus-ring');
      const shown = await tab.evaluate((element) => {
        const card = getComputedStyle(element);
        const after = getComputedStyle(element, '::after');
        const footer = element.querySelector('small')!;
        const other = document.querySelector('#stats [role="tab"]:not([data-selected])')!;
        // `shadow-none` 是五层透明的 0px，不是字面的 none：看有没有一层带颜色。
        const shadowVisible = [...card.boxShadow.matchAll(/rgba?\([^)]*\)/g)]
          .some((match) => !/, 0\)$/.test(match[0]));
        return {
          shadowVisible, position: card.position, radius: card.borderRadius,
          after: {
            content: after.content, position: after.position, width: after.borderTopWidth,
            color: after.borderTopColor, radius: after.borderRadius, z: after.zIndex,
            inset: [after.top, after.right, after.bottom, after.left],
          },
          footerFace: getComputedStyle(footer).backgroundColor,
          otherAfter: getComputedStyle(other, '::after').content,
        };
      });
      assert.equal(shown.shadowVisible, false, '选中的卡还压着接触阴影');
      assert.equal(shown.position, 'relative');
      assert.equal(shown.after.content, '""', '选中的卡没有画覆盖层');
      assert.equal(shown.after.position, 'absolute');
      assert.deepEqual(shown.after.inset, ['0px', '0px', '0px', '0px'], '覆盖层没有铺满整张卡');
      assert.deepEqual([shown.after.width, shown.after.color], ['2px', ring], '描边不是 2px 焦点环色');
      assert.equal(shown.after.radius, shown.radius, '覆盖层圆角和卡不一致');
      assert.ok(Number(shown.after.z) >= 1, `覆盖层 z-index ${shown.after.z}，会被脚注带盖住`);
      assert.notEqual(shown.footerFace, 'rgba(0, 0, 0, 0)', '脚注带没有底色，这条用例守的就是环压在它上面');
      assert.equal(shown.otherAfter, 'none', '没选中的卡也画了覆盖层');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('索引页容器不横向裁剪：筛选玻璃左右两侧的影洒得出来，页面也不横向溢出', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/tags');
    try {
      const page = opened.page;
      const clip = await page.evaluate(() => {
        const index = document.querySelector('#index')!;
        return {
          overflow: getComputedStyle(index).overflowX,
          spill: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
      });
      assert.equal(clip.overflow, 'visible', '#index 横向裁剪，玻璃左右两侧的影被切成直边');
      assert.equal(clip.spill, 0, '不裁剪后页面被撑出横向滚动');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('标签页骨架里就铺着那块筛选玻璃，React 落地时玻璃与开头那组内容都留在原位', { timeout: 60_000 }, async () => {
    /* 骨架与真页面各量一遍：玻璃的位置与大小、开头那组内容的上沿。药丸、读数与首字要等数据，
       骨架里是占位；视图切换此刻就是最终那一档。 */
    const opened = await visit(browser, '/tags', DESKTOP);
    try {
      const page = opened.page;
      await page.route('**/api/index?**', (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ items: [{ k: '痴女', n: 4, cat: 'role' }, { k: '秘书OL', n: 2, cat: 'scene' },
          { k: 'BDSM', n: 3, cat: 'role' }], has_more: false, categories: { role: 2, scene: 1 } }),
      }));
      const release = await holdApi(page);
      await page.reload({ waitUntil: 'load' });
      await page.locator('#index [data-skeleton^="index/tags/"]').waitFor({ timeout: 15_000 });
      const measure = (glass: string, group: string, view: string) => page.evaluate(([glassSelector, groupSelector, viewSelector]) => {
        const rect = (selector: string) => {
          const box = document.querySelector(selector)?.getBoundingClientRect();
          return box ? { top: box.top, left: box.left, width: box.width, height: box.height } : null;
        };
        const [first, second] = [...document.querySelectorAll(groupSelector)].map((node) => node.getBoundingClientRect());
        return { glass: rect(glassSelector), group: rect(groupSelector),
          gap: first && second ? second.top - first.bottom : null,
          pills: document.querySelectorAll(`${glassSelector} [data-filter-row="top"] > *`).length,
          view: rect(`${glassSelector} [data-filter-row="bottom"] ${viewSelector}`) };
      }, [glass, group, view] as const);
      const skeleton = await measure('#index .board-filter-frame', '#index [data-skeleton] .ui-alphagroup', '.iconswitch');
      release();
      await page.locator('#index [data-alphabet]').waitFor({ timeout: 15_000 });
      await settle(page);
      const live = await measure('#index [data-filter-glass]', '#index [data-alpha-group]', '[aria-label="标签视图"]');
      assert.ok(skeleton.glass && skeleton.group && skeleton.view, '标签页骨架里缺筛选玻璃、开头那组或视图切换');
      assert.ok(skeleton.pills > 0, '骨架玻璃的上排没有铺药丸占位');
      assert.ok(live.glass && live.group && live.view, '接管后找不到筛选玻璃、开头那组或视图切换');
      for (const key of ['top', 'left', 'width', 'height'] as const) {
        assert.ok(Math.abs(skeleton.glass![key] - live.glass![key]) <= 1,
          `筛选玻璃的 ${key} 接管时跳了：骨架 ${skeleton.glass![key]}，接管后 ${live.glass![key]}`);
      }
      assert.ok(Math.abs(skeleton.group!.top - live.group!.top) <= 1,
        `开头那组内容的上沿接管时跳了：骨架 ${skeleton.group!.top}，接管后 ${live.group!.top}`);
      assert.ok(skeleton.gap !== null && live.gap !== null && Math.abs(skeleton.gap - live.gap) <= 1,
        `字母表各组之间的间距骨架与接管后不同：骨架 ${skeleton.gap}，接管后 ${live.gap}`);
      assert.ok(Math.abs(skeleton.view!.top - live.view!.top) <= 1 && Math.abs(skeleton.view!.left - live.view!.left) <= 1,
        `视图切换接管时挪了位：骨架 (${skeleton.view!.left}, ${skeleton.view!.top})，接管后 (${live.view!.left}, ${live.view!.top})`);
    } finally {
      await opened.close();
    }
  });

  it('艺人页骨架里就排着身份分类那一行，React 落地时分类行与名册网格都留在原位', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/performers', DESKTOP);
    try {
      const page = opened.page;
      await page.route('**/api/index?**', (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ items: [{ k: '七海ひな', n: 4 }, { k: 'Mira', n: 2 }], has_more: false,
          categories: { japanese_av: 1, western: 1 } }),
      }));
      const release = await holdApi(page);
      await page.reload({ waitUntil: 'load' });
      await page.locator('#index [data-skeleton^="index/performers/"]').waitFor({ timeout: 15_000 });
      const measure = (cell: string) => page.evaluate((cellSelector) => {
        const row = document.querySelector('#index [aria-label="身份分类"]');
        const box = row?.getBoundingClientRect();
        const first = row?.querySelector('[aria-pressed="true"]')?.getBoundingClientRect();
        return { row: box ? { top: box.top, left: box.left, height: box.height } : null,
          pressed: first ? { width: first.width, height: first.height } : null,
          cell: document.querySelector(cellSelector)?.getBoundingClientRect().top ?? null };
      }, cell);
      const skeleton = await measure('#index [data-skeleton] .icell');
      release();
      await page.locator('#index [data-index-cell]').first().waitFor({ timeout: 15_000 });
      await settle(page);
      const live = await measure('#index [data-index-cell]');
      assert.ok(skeleton.row && skeleton.pressed && skeleton.cell !== null, `艺人页骨架里缺身份分类行或名册格：${JSON.stringify(skeleton)}`);
      assert.ok(live.row && live.pressed && live.cell !== null, `接管后找不到身份分类行或名册格：${JSON.stringify(live)}`);
      for (const key of ['top', 'left', 'height'] as const) {
        assert.ok(Math.abs(skeleton.row![key] - live.row![key]) <= 1,
          `身份分类行的 ${key} 接管时跳了：骨架 ${skeleton.row![key]}，接管后 ${live.row![key]}`);
      }
      assert.deepEqual(skeleton.pressed, live.pressed, '选中的那枚分类键接管时换了尺寸');
      assert.ok(Math.abs(skeleton.cell! - live.cell!) <= 1, `名册第一格的上沿接管时跳了：骨架 ${skeleton.cell}，接管后 ${live.cell}`);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of VIEWPORTS) {
    it(`首页吸顶筛选条的上沿在深浅主题中保持透明（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openHome(browser, viewport);
      try {
        const page = opened.page;
        await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<div style="height:200vh"></div>'));
        await page.evaluate(() => window.scrollTo(0, 600));
        await page.waitForFunction(() => document.querySelector('[data-filter-glass]')?.hasAttribute('data-stuck'));
        for (const theme of ['light', 'dark']) {
          const style = await page.evaluate(value => {
            document.documentElement.dataset.theme = value;
            const filter = document.querySelector('[data-filter-glass]')!;
            return { content: getComputedStyle(filter, '::before').content, shadow: getComputedStyle(filter).boxShadow };
          }, theme);
          assert.equal(style.content, 'none');
          assert.match(style.shadow, /inset/);
          await page.locator('[data-filter-glass]').screenshot({ path: `../build/sticky-${viewport.name}-${theme}.png` });
        }
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }

  it('筛选玻璃吸顶使用抬起阴影，深浅主题的上沿保持透明间隙', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/tags');
    try {
      const page = opened.page;
      const read = () => page.evaluate(() => {
        const glass = document.querySelector('[data-filter-glass]')!;
        const root = getComputedStyle(document.documentElement);
        return {
          stuck: glass.hasAttribute('data-stuck'),
          shadow: getComputedStyle(glass).boxShadow,
          strip: getComputedStyle(glass, '::before'),
          lift: root.getPropertyValue('--glass-lift'),
          rest: root.getPropertyValue('--glass-shadow'),
        };
      });
      const readStrip = () => page.evaluate(() => {
        const strip = getComputedStyle(document.querySelector('[data-filter-glass]')!, '::before');
        return { content: strip.content, top: strip.top, height: strip.height, z: strip.zIndex, mask: strip.maskImage };
      });
      const resting = await read();
      assert.equal(resting.stuck, false, '还没滚就标成吸顶');
      assert.equal(farthestShadow(resting.shadow), farthestShadow(resting.rest), '静止态的影不是 --glass-shadow');
      assert.equal((await readStrip()).content, 'none', '静止态就画了遮带');
      // 桩数据只有两枚标签，页面不够长滚不动：垫一块高度再滚。
      await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<div style="height:200vh"></div>'));
      await page.evaluate(() => window.scrollTo(0, 600));
      await page.waitForFunction(() => document.querySelector('[data-filter-glass]')!.hasAttribute('data-stuck'),
        undefined, { timeout: 5_000 });
      const stuck = await read();
      const strip = await readStrip();
      assert.equal(farthestShadow(stuck.shadow), farthestShadow(stuck.lift), '吸顶后的影不是 --glass-lift');
      assert.ok(farthestShadow(stuck.shadow) > farthestShadow(resting.shadow), '吸顶后影没有抬起来');
      assert.match(stuck.shadow, /inset/, '吸顶后四条内嵌 rim 线丢了');
      assert.equal(strip.content, 'none');
      for (const theme of ['light', 'dark']) {
        await page.evaluate(value => { document.documentElement.dataset.theme = value }, theme);
        assert.equal((await readStrip()).content, 'none', `${theme} 主题的吸顶上沿存在遮带`);
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForFunction(() => !document.querySelector('[data-filter-glass]')!.hasAttribute('data-stuck'),
        undefined, { timeout: 5_000 });
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('图片灯箱：竖图按高度整张收进视口，上下让出工具条与缩略图条，左右让出翻页键', { timeout: 60_000 }, async () => {
    const opened = await openLightbox(browser, { count: 3, width: 1200, height: 4000 });
    try {
      const read = await opened.page.evaluate(() => {
        const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect().toJSON() as DOMRect;
        return { img: rect('[data-photo-main] .swiper-slide-active img'), bar: rect('[data-photo-bar]'),
          strip: rect('[data-photo-strip]'), back: rect('[data-photo-nav="back"]'), viewport: innerHeight };
      });
      const { img } = read;
      assert.ok(img.top >= 24 - .5 && img.bottom <= read.viewport - 148 + .5, `大图越出了上下安全区：${img.top}–${img.bottom}`);
      assert.ok(Math.abs(img.width / img.height - 1200 / 4000) < .01, `竖图被拉变形：${img.width}×${img.height}`);
      assert.ok(Math.abs(img.left + img.width / 2 - DESKTOP.width / 2) <= 1, '大图没有水平居中');
      assert.ok(read.bar.bottom <= read.strip.top, '工具条压到了缩略图条上');
      assert.equal(Math.round(read.viewport - read.strip.bottom), 14);
      assert.ok(read.back.right <= 72, '翻页键伸进了图片那一栏');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('图片灯箱只有一张时不画缩略图条、页码点和翻页键，底部空间还给图片', { timeout: 60_000 }, async () => {
    const opened = await openLightbox(browser, { count: 1, width: 4000, height: 3000 });
    try {
      const read = await opened.page.evaluate(() => {
        const style = (selector: string) => getComputedStyle(document.querySelector(selector)!);
        return {
          strip: style('[data-photo-strip]').display,
          nav: [...document.querySelectorAll('[data-photo-nav]')].map((button) => getComputedStyle(button).display),
          pagination: document.querySelectorAll('[data-photo-pagination]').length,
          padding: style('[data-photo-main] .swiper-zoom-container').paddingBottom,
          count: document.querySelector('[data-photo-count]')!.textContent,
        };
      });
      assert.deepEqual(read, { strip: 'none', nav: ['none', 'none'], pagination: 0, padding: '76px', count: '1 / 1' });
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('灯箱圆钮：图标压在圆心，线头是圆的、不填充；前进那枚是同一枚箭头转半圈，到头那枚让开', { timeout: 60_000 }, async () => {
    const opened = await openLightbox(browser, { count: 3, width: 4000, height: 3000 });
    try {
      const read = await opened.page.evaluate(() => [
        '[data-photo-close]', '[data-photo-nav="back"]', '[data-photo-nav="fwd"]', '[data-photo-detail-toggle]',
      ].map((selector) => {
        const button = document.querySelector(selector)!, svg = button.querySelector('svg')!;
        const outer = button.getBoundingClientRect(), inner = svg.getBoundingClientRect(), style = getComputedStyle(svg);
        return {
          selector, glyph: svg.querySelector('use')?.getAttribute('href'),
          off: Math.max(Math.abs(outer.left + outer.width / 2 - inner.left - inner.width / 2),
            Math.abs(outer.top + outer.height / 2 - inner.top - inner.height / 2)),
          fill: style.fill, cap: style.strokeLinecap, turn: style.transform,
          visibility: getComputedStyle(button).visibility,
        };
      }));
      for (const icon of read) {
        assert.ok(icon.off <= .5, `${icon.selector} 的图标偏离圆心 ${icon.off}px`);
        assert.equal(icon.fill, 'none', `${icon.selector} 的描边图标被填实了`);
        // Lucide 的 info 圆点是长度 .01 的短线，没有圆头就缩成看不见的一横。
        assert.equal(icon.cap, 'round', `${icon.selector} 的线头不是圆的`);
      }
      const [close, back, fwd, info] = read;
      assert.equal(close.glyph, '#i-x');
      assert.equal(info.glyph, '#i-info');
      assert.deepEqual([back.glyph, fwd.glyph], ['#i-chevron-left', '#i-chevron-left']);
      assert.equal(back.turn, 'none');
      assert.match(fwd.turn, /^matrix\(-1, /, '前进键的箭头没有转半圈');
      assert.deepEqual([back.visibility, fwd.visibility], ['hidden', 'visible'], '停在第一张时后退键该让开');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('关注页：标题 24px、窄屏 20px，药丸间距同首页 7px，读数用等宽字', { timeout: 90_000 }, async () => {
    const read = (page: Page) => page.evaluate(() => {
      const style = (selector: string) => getComputedStyle(document.querySelector(selector)!);
      return {
        title: style('[data-follow-title]').fontSize,
        gaps: ['[data-follow-filter-glass] [data-entity-states]', '[data-follow-filter-glass] [data-entity-tags]']
          .map((selector) => style(selector).columnGap),
        readout: style('[data-follow-readout]').fontFamily,
      };
    });
    const wide = await openFollowFeed(browser, '/follow', DESKTOP);
    try {
      const shown = await read(wide.page);
      assert.equal(shown.title, '32px');
      assert.deepEqual(shown.gaps, ['7px', '7px'], '关注页的药丸间距和首页那条不一样');
      assert.match(shown.readout, /Cascadia Mono|Consolas|monospace/, `读数不是等宽字：${shown.readout}`);
      assert.deepEqual(wide.problems, []);
    } finally {
      await wide.close();
    }
    const narrow = await openFollowFeed(browser, '/follow', MOBILE);
    try {
      assert.equal((await read(narrow.page)).title, '24px', '窄屏标题使用统一的页面标题字号');
      assert.deepEqual(narrow.problems, []);
    } finally {
      await narrow.close();
    }
  });
});
