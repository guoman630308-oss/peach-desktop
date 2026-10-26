/* 设置面板（`settings-panel` 岛，ADR-0031）：开合与焦点、每一类设置改完立刻生效并落到该去的地方。
 * 写接口的桩在 `settings-fixture.ts`；面板的版式与像素由 `design-settings.test.ts` 量，这里只写行为。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, BrowserContext, Page } from 'playwright-core';

import { launch, layout, requiredEnv, settle, visit, VIEWPORTS } from './harness.ts';
import { openHome, openPanel, ORDER, PANEL, stubServer, type Server } from './settings-fixture.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const MOBILE = VIEWPORTS.find((viewport) => viewport.mobile)!;

/** 等到条件成立，最多五秒。 */
async function until(page: Page, condition: () => boolean, message: string): Promise<void> {
  for (let tries = 0; tries < 100 && !condition(); tries += 1) await page.waitForTimeout(50);
  assert.ok(condition(), message);
}

/** 等到 `path` 收到一次带 `key` 的写入，返回那一次的请求体。 */
async function posted(page: Page, server: Server, path: string, key: string): Promise<Record<string, unknown>> {
  const find = () => server.posts.find((post) => post.path === path && key in post.body);
  await until(page, () => !!find(), `${path} 没有收到 ${key} 的写入`);
  return find()!.body;
}

const stored = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem('peach.settings.v1') || '{}'));

async function pick(page: Page, field: string, value: string): Promise<void> {
  await page.locator(`${field} [data-select-trigger]`).click();
  await page.locator(`${field} [data-select-option="${value}"]`).click();
}

/** 自建一份上下文：起始设置只在本地还没有时写一次，刷新之后不会被冲掉。 */
async function persistentContext(browser: Browser): Promise<BrowserContext> {
  const context = await browser.newContext({
    viewport: { width: DESKTOP.width, height: DESKTOP.height }, reducedMotion: 'reduce', colorScheme: 'light',
  });
  await context.addInitScript(() => {
    if (!localStorage.getItem('peach.settings.v1')) {
      localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false }));
    }
  });
  return context;
}

describe('设置面板', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });

  it('五个偏好分区各自显示对应设置，Peach 配置单独直达配置页', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page } = opened;
    try {
      await openPanel(page);
      assert.deepEqual(await page.locator(PANEL).getByRole('tab').allTextContents(),
        ['外观与导航', '浏览与播放', 'JAV 与新作', '搜索与推荐', '内容显示']);
      for (const [name, field] of [
        ['外观与导航', '#themeSetting'], ['浏览与播放', '#detailAutoplaySetting'],
        ['JAV 与新作', '#javImageSetting'], ['搜索与推荐', '#relatedLimitSetting'], ['内容显示', '#censorSetting'],
      ]) {
        await page.locator(PANEL).getByRole('tab', { name, exact: true }).click();
        await page.locator(field!).waitFor({ state: 'visible' });
        assert.deepEqual((await layout(page)).offenders, []);
      }
      assert.equal(await page.locator(`${PANEL} #videoThumbnailSetting, ${PANEL} #followScheduleSetting`).count(), 0);
      const configuration = page.locator(PANEL).getByRole('button', { name: 'Peach 配置', exact: true });
      await configuration.click();
      await page.waitForURL((url) => url.pathname === '/settings/library');
      await page.locator(PANEL).waitFor({ state: 'hidden' });
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });

  it('齿轮打开、关闭键与 Escape 关上；焦点进来落在关闭键、Tab 在面板里转圈、关上后回到齿轮', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page } = opened;
    try {
      await page.locator('#settingsBtn').click();
      await page.locator(`${PANEL}:not([hidden])`).waitFor({ timeout: 10_000 });
      await page.waitForFunction(() => document.activeElement?.id === 'settingsClose');
      assert.equal(await page.locator(PANEL).getAttribute('aria-modal'), 'true');
      assert.equal(await page.evaluate(() => document.body.classList.contains('settings-open')), true);

      await page.keyboard.press('Shift+Tab');
      assert.equal(await page.evaluate((panel) => !!document.activeElement?.closest(panel), PANEL), true,
        '从第一枚往回 Tab 跳出了面板');
      assert.notEqual(await page.evaluate(() => document.activeElement?.id), 'settingsClose', '往回 Tab 没有落到最后一枚');
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'settingsClose', '最后一枚往后 Tab 没有回到第一枚');

      await page.locator('#settingsClose').click();
      await page.locator(PANEL).waitFor({ state: 'hidden', timeout: 5_000 });
      await page.waitForFunction(() => document.activeElement?.id === 'settingsBtn');
      assert.equal(await page.evaluate(() => document.body.classList.contains('settings-open')), false);

      await page.locator('#settingsBtn').click();
      await page.locator(`${PANEL}:not([hidden])`).waitFor({ timeout: 10_000 });
      await page.keyboard.press('Escape');
      await page.locator(PANEL).waitFor({ state: 'hidden', timeout: 5_000 });
      await page.waitForFunction(() => document.activeElement?.id === 'settingsBtn');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('主题选深色当场换上，刷新之后仍是深色', { timeout: 60_000 }, async () => {
    const context = await persistentContext(browser);
    try {
      const page = await context.newPage();
      await stubServer(page);
      await page.goto(`${requiredEnv('PEACH_E2E_ORIGIN')}/`, { waitUntil: 'load' });
      await settle(page);
      assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), false);
      await openPanel(page);
      await page.locator('#themeSetting label:has(input[value="dark"])').click();
      await page.waitForFunction(() => document.documentElement.classList.contains('dark'));
      assert.equal((await stored(page)).theme, 'dark');

      await page.reload({ waitUntil: 'load' });
      await settle(page);
      assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), true, '刷新后主题没有保持');
      await openPanel(page);
      assert.equal(await page.locator('#themeSetting input[value="dark"]').isChecked(), true, '刷新后面板里选中的不是深色');
    } finally {
      await context.close();
    }
  });

  it('界面音效关掉后点按不再出声，打开后又出声', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    const { page } = opened;
    try {
      /* 每一声都要新建一枚振荡器或噪声源；数它们就知道响没响。 */
      await page.context().addInitScript(() => {
        const counted = window as unknown as { __uiTones: number };
        counted.__uiTones = 0;
        for (const method of ['createOscillator', 'createBufferSource'] as const) {
          const native = AudioContext.prototype[method];
          AudioContext.prototype[method] = function (this: AudioContext) {
            counted.__uiTones += 1;
            return native.call(this);
          } as never;
        }
      });
      await stubServer(page);
      await page.reload({ waitUntil: 'load' });
      await settle(page);
      await openPanel(page);
      const tones = () => page.evaluate(() => (window as unknown as { __uiTones: number }).__uiTones);
      const box = page.locator('#uiSoundsSetting');
      assert.equal(await box.isChecked(), true, '界面音效默认没有打开');
      await box.click();
      assert.equal(await box.isChecked(), false);
      assert.equal((await stored(page)).uiSounds, false);
      const silent = await tones();
      await page.locator(PANEL).getByRole('tab', { name: '浏览与播放', exact: true }).click();
      await page.locator(PANEL).getByRole('tab', { name: '外观与导航', exact: true }).click();
      assert.equal(await tones(), silent, '关掉界面音效之后点分区还在出声');
      await box.click();
      assert.equal((await stored(page)).uiSounds, true);
      await page.locator(PANEL).getByRole('tab', { name: '浏览与播放', exact: true }).click();
      assert.ok(await tones() > silent, '打开界面音效之后点分区不出声');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('光晕预设点一档就换上；拉条按一下方向键改参数并落盘', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page } = opened;
    try {
      await openPanel(page);
      const grid = page.locator('#homeGlowControls [data-glow-grid]');
      await grid.waitFor({ state: 'visible', timeout: 10_000 });
      const target = grid.locator('[data-glow-preset][aria-pressed="false"]').first();
      const key = await target.getAttribute('data-glow-preset');
      const label = await target.getAttribute('aria-label');
      await target.click();
      await page.waitForFunction((preset) => document.querySelector(
        `#homeGlowControls [data-glow-preset="${preset}"]`)?.getAttribute('aria-pressed') === 'true', key);
      assert.equal(await page.locator('#homeGlowControls [data-glow-preset-name]').textContent(), label);
      assert.equal((await stored(page)).homeGlow.preset, key);

      /* 「玻璃原色」那一档收起强度；挑一档有三枚光晕的，再动强度。 */
      if (await page.locator('[data-glow-field="strength"]').isHidden()) {
        await grid.locator('[data-glow-preset="ash"]').click();
      }
      const dial = page.locator('[data-glow-dial="strength"] [role="slider"]');
      const before = Number(await dial.getAttribute('aria-valuenow'));
      await dial.focus();
      await page.keyboard.press(before > 0 ? 'ArrowLeft' : 'ArrowRight');
      const expected = before > 0 ? before - 1 : before + 1;
      await page.waitForFunction((value) => document.querySelector(
        '[data-glow-dial="strength"] [role="slider"]')?.getAttribute('aria-valuenow') === String(value), expected);
      assert.equal((await stored(page)).homeGlow.strength, expected, '拉条改的强度没有落盘');

      await page.locator('#homeGlowSetting').click();
      await page.locator('#homeGlowControls').waitFor({ state: 'hidden', timeout: 5_000 });
      assert.equal((await stored(page)).homeGlow.on, false);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('JAV 默认封面与默认排序方向改完落盘；随机排序时方向收起', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page } = opened;
    try {
      await openPanel(page, 'JAV 与新作');
      await page.locator('#javImageSetting label:has(input[value="thumbnail"])').click();
      assert.equal((await stored(page)).javImage, 'thumbnail');
      assert.equal(await page.locator('#javImageSetting input[value="thumbnail"]').isChecked(), true);

      await page.locator(PANEL).getByRole('tab', { name: '浏览与播放', exact: true }).click();
      await pick(page, '#defaultSortSetting', 'seed');
      await page.locator('#defaultSortDirectionSetting').waitFor({ state: 'hidden', timeout: 5_000 });

      await pick(page, '#defaultSortSetting', 'rating');
      await page.locator('#defaultSortDirectionSetting').waitFor({ state: 'visible', timeout: 5_000 });
      await pick(page, '#defaultSortDirectionSetting', 'asc');
      await page.waitForFunction(() =>
        document.querySelector('#defaultSortDirectionSetting [data-sort-direction-label]')?.textContent === '升序');
      const saved = await stored(page);
      assert.equal(saved.defaultSort, 'rating');
      assert.equal(saved.defaultSortDirection, 'asc');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('侧栏排序拖动一行：侧栏当场按新顺序重排，再写进数据库', { timeout: 60_000 }, async () => {
    const opened = await openHome(browser);
    const { page, server } = opened;
    try {
      await openPanel(page);
      const list = page.locator('#sidebarOrderSetting');
      await list.evaluate((node) => node.scrollIntoView({ block: 'center' }));
      const rows = list.locator('[data-sidebar-row]');
      const keys = () => rows.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-sidebar-row')));
      const initial = await keys();
      assert.deepEqual(initial, ORDER, '面板里的顺序不是数据库那一份');
      const from = (await rows.nth(1).boundingBox())!, to = (await rows.nth(3).boundingBox())!;
      await page.mouse.move(from.x + 30, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + 34, from.y + from.height / 2 + 6, { steps: 4 });
      await page.mouse.move(to.x + 40, to.y + to.height * 0.75, { steps: 8 });
      assert.equal(await rows.nth(1).getAttribute('data-dragging'), '', '拖动中的那一行没有标出来');
      await page.mouse.up();
      const moved = [initial[0], initial[2], initial[3], initial[1], ...initial.slice(4)];
      await page.waitForFunction((want) => JSON.stringify([...document.querySelectorAll(
        '#sidebarOrderSetting [data-sidebar-row]')].map((node) => node.getAttribute('data-sidebar-row'))) === want,
      JSON.stringify(moved));
      const nav = await page.locator('#drawer [data-sidebar-nav] [data-nav]').evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('data-nav')));
      assert.deepEqual(nav, moved, '侧栏没有跟着面板里的新顺序重排');
      assert.deepEqual((await posted(page, server, '/api/settings', 'sidebarOrder')).sidebarOrder, moved);
      assert.deepEqual((await stored(page)).sidebarOrder, moved);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it(`手机 ${MOBILE.width} 宽：面板铺满不横向溢出，分区横排可切换`, { timeout: 60_000 }, async () => {
    const opened = await openHome(browser, MOBILE);
    const { page } = opened;
    try {
      await openPanel(page);
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth, `页面横向溢出：${geometry.scrollWidth}px`);
      assert.deepEqual(geometry.offenders, []);
      const card = (await page.locator(`${PANEL} [data-settings-card]`).boundingBox())!;
      assert.ok(card.x >= 0 && card.x + card.width <= MOBILE.width, '设置卡片越出了视口');
      const tops = await page.locator(PANEL).getByRole('tab').evaluateAll((nodes) =>
        nodes.slice(0, 3).map((node) => Math.round(node.getBoundingClientRect().top)));
      assert.equal(new Set(tops).size, 1, '窄屏下分区没有横排');
      await page.locator(PANEL).getByRole('tab', { name: 'JAV 与新作', exact: true }).click();
      await page.locator('#javImageSetting').waitFor({ state: 'visible', timeout: 5_000 });
      assert.deepEqual((await layout(page)).offenders, []);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });
});
