/* 设计决定：数据管理、索引页与资料页头。读 `getComputedStyle` 断言用户定过的外观；共用的桩与浏览器生命周期在 `design-fixture.ts`。 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { layout, settle, visit } from './harness.ts';
import {
  DESKTOP, MOBILE, tokenColor, openDuplicates, openIndexPage, openPlaylistsPage, openFollowManage, holdApi,
  controlFaces, openPerformer, PROFILED, openProfiledPerformer, popmenuShadow, heroGeometry, disabledTokens,
  waitingActionFaces, assertDisabledFace, followTab, followSource, installDesignBrowser,
} from './design-fixture.ts';

describe('设计决定：数据管理、索引页与资料页头', () => {
  let browser: Browser;
  installDesignBrowser((next) => { browser = next; });

  it('数据管理骨架里等数据的操作键是禁用态，数据到了才换回蓝色主按钮', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/data-cleanup', DESKTOP);
    try {
      const page = opened.page;
      // 没有任务在跑：跑着的那一档会把扫描键压成忙碌态，那是数据不是骨架。
      await page.route('**/api/library-processing', (route) => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'idle' }),
      }));
      const release = await holdApi(page);
      await page.reload({ waitUntil: 'load' });
      await page.locator('[data-skeleton="cleanup"] .cleanupscraping [data-split-button]').waitFor({ timeout: 15_000 });
      const expected = await disabledTokens(page);
      const waiting = await waitingActionFaces(page, '[data-skeleton="cleanup"] [data-skeleton-action]');
      assert.deepEqual(waiting.map((face) => face.name),
        ['同步媒体库', '更多同步方式', '开始修复', '预览', '检查死链', '检查文件']);
      for (const face of waiting) assertDisabledFace(face, expected);
      const ring = await page.locator('[data-skeleton="cleanup"] [data-split-button]')
        .evaluate((element) => getComputedStyle(element).boxShadow);
      assert.ok(ring.includes(expected.ring), `骨架里的分体键外圈不是禁用那一档描边：${ring}`);
      const sizes = await controlFaces(page, {
        同步媒体库: '.cleanupscraping [data-split-button] > button:first-child',
        更多方式: '.cleanupscraping [data-split-button] > button:last-child',
        开始修复: '.cleanupmediarepair footer > button',
      });
      release();
      await page.locator('[data-skeleton="cleanup"]').waitFor({ state: 'detached', timeout: 15_000 });
      // 正式页面先摆一份同样的骨架卡，等 React 岛接管；键上没了 `data-skeleton-action` 才算接管完。
      await page.locator('section[aria-label="扫描与采集"] [data-split-button] > button:first-child:not([data-skeleton-action])')
        .waitFor({ timeout: 15_000 });
      await page.locator('section[aria-label="媒体修复"] footer > button:not([data-skeleton-action])')
        .waitFor({ timeout: 15_000 });
      await settle(page);
      const final = await controlFaces(page, {
        同步媒体库: 'section[aria-label="扫描与采集"] [data-split-button] > button:first-child',
        更多方式: 'section[aria-label="扫描与采集"] [data-split-button] > button:last-child',
        开始修复: 'section[aria-label="媒体修复"] footer > button',
      });
      for (const name of Object.keys(sizes)) {
        assert.equal(sizes[name]!.size, final[name]!.size, `${name} 接管时尺寸跳了`);
        assert.match(final[name]!.face, /gradient/, `${name} 接管后没换回蓝色主按钮`);
      }
      const enabled = await page.locator('section[aria-label="扫描与采集"] [data-split-button] > button')
        .evaluateAll((buttons) => buttons.map((button) => (button as HTMLButtonElement).disabled));
      assert.deepEqual(enabled, [false, false], '数据到了扫描键还是禁用的');
    } finally {
      await opened.close();
    }
  });

  it('数据管理面板内标题采用 medium，链接读数使用既定字号与行高', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/data-cleanup', DESKTOP);
    try {
      const page = opened.page;
      await page.locator('#link-manager .link-stat-grid b').first().waitFor({ timeout: 15_000 });
      await page.locator('#resource-sync-title').waitFor({ timeout: 15_000 });
      await settle(page);
      const faces = await page.evaluate(() => {
        const style = (selector: string) => {
          const computed = getComputedStyle(document.querySelector(selector)!);
          return {
            size: computed.fontSize, weight: computed.fontWeight, line: computed.lineHeight,
            top: computed.marginTop, bottom: computed.marginBottom,
          };
        };
        return {
          links: style('#link-manager-title'),
          sync: style('#resource-sync-title'),
          label: style('#link-manager .link-stat-grid span'),
          figure: style('#link-manager .link-stat-grid b'),
          outsideTitles: document.querySelectorAll('#link-manager > h2, #link-manager > h3, #resource-sync > h2, #resource-sync > h3').length,
        };
      });
      assert.equal(faces.outsideTitles, 0, '单面板分区出现了框外标题');
      for (const [name, heading] of [['链接管理', faces.links], ['资源同步', faces.sync]] as const) {
        assert.equal(heading.size, '20px', `${name}标题字号不是 Title 2`);
        assert.equal(heading.weight, '500', `${name}标题不是 medium 字重`);
        assert.equal(heading.line, '26px', `${name}标题行高不是 Title 2`);
        assert.equal(heading.bottom, '8px', `${name}标题与面板正文间距不一致`);
      }
      assert.deepEqual([faces.label.size, faces.label.line], ['12px', '20px'], '链接读数的标签不是 12/20');
      assert.deepEqual([faces.figure.size, faces.figure.weight, faces.figure.line], ['20px', '700', '20px'],
        '链接读数的数字不是 20px 粗体、20px 行高');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('可点的卡悬停时铺卡面掺 5% 主文字色的那一档面，不是透明', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/data-cleanup', DESKTOP);
    try {
      const page = opened.page;
      const selector = '.peach-react [data-cleanup-go]';
      const card = page.locator(selector).first();
      await card.waitFor({ timeout: 15_000 });
      await settle(page);
      const rest = await card.evaluate((element) => getComputedStyle(element).backgroundColor);
      await card.hover();
      // 过渡跑完才读：等到底色变了，或三秒后照实读出没变的那个值。
      await page.waitForFunction(([target, before]) =>
        getComputedStyle(document.querySelector(target)!).backgroundColor !== before,
      [selector, rest] as const, { timeout: 3_000 }).catch(() => undefined);
      const hovered = await card.evaluate((element) => getComputedStyle(element).backgroundColor);
      assert.notEqual(hovered, 'rgba(0, 0, 0, 0)', '悬停底色落成了透明：token 在根上就折掉了');
      assert.notEqual(hovered, rest, '悬停没有换面');
      assert.equal(hovered, await tokenColor(page, '.peach-react', '--card-hover'), '悬停底色不是卡面掺 5% 主文字色');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('索引页名册格用遗留卡面那一套：格底 --ground、头像框 --sunk、首字与读数 --muted 配 Bahnschrift，悬停掺 6% 主文字色；页头过滤框与版式切换同为 36px 高，切换两枚共 66px 宽', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/performers');
    try {
      const page = opened.page;
      const selector = '#index [data-index-cell]';
      const cell = page.locator(selector).first();
      const rest = await cell.evaluate((element) => getComputedStyle(element).backgroundColor);
      const face = await page.evaluate(() => {
        const ring = document.querySelector('#index [data-person-ring]')!;
        const initial = document.querySelector('#index [data-person-ring] .ini')!;
        const readout = document.querySelector('#index [data-index-readout]')!;
        return { ring: getComputedStyle(ring).backgroundColor, initial: getComputedStyle(initial).color,
          font: getComputedStyle(initial).fontFamily, readout: getComputedStyle(readout).color };
      });
      assert.equal(rest, await tokenColor(page, 'html', '--ground'), '名册格底不是 --ground');
      assert.equal(face.ring, await tokenColor(page, 'html', '--sunk'), '头像框底不是 --sunk');
      const muted = await page.evaluate(() => {
        const probe = document.createElement('div');
        probe.style.color = 'var(--muted)';
        document.documentElement.append(probe);
        const value = getComputedStyle(probe).color;
        probe.remove();
        return value;
      });
      assert.deepEqual([face.initial, face.readout], [muted, muted], '首字与读数不是 --muted');
      assert.match(face.font, /^Bahnschrift/, '首字不是 Bahnschrift');
      await cell.hover();
      await page.waitForFunction(([target, before]) =>
        getComputedStyle(document.querySelector(target)!).backgroundColor !== before,
      [selector, rest] as const, { timeout: 3_000 }).catch(() => undefined);
      const hovered = await cell.evaluate((element) => getComputedStyle(element).backgroundColor);
      const lifted = await page.evaluate(() => {
        const probe = document.createElement('div');
        probe.style.backgroundColor = 'color-mix(in srgb, var(--ink) 6%, var(--ground))';
        document.documentElement.append(probe);
        const value = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return value;
      });
      assert.equal(hovered, lifted, '名册格悬停不是格底掺 6% 主文字色');
      const head = await page.evaluate(() => {
        const input = document.querySelector('#index [data-index-search] input')!;
        const shell = input.closest('[role="presentation"]')!;
        const toggle = document.querySelector('#index [data-index-layout]')!;
        return { search: getComputedStyle(shell).height, toggle: getComputedStyle(toggle).height,
          width: getComputedStyle(toggle).width };
      });
      assert.deepEqual(head, { search: '36px', toggle: '36px', width: '66px' });
      // 人脸放大把 img 撑得比框还宽再负偏移；Preflight 的 `max-width:100%` 会把它压回框宽。
      const photo = page.locator('#index [data-person-ring] img').first();
      await photo.waitFor({ state: 'attached', timeout: 5_000 });
      await page.waitForFunction(() => document.querySelector('#index [data-person-ring]')?.hasAttribute('data-native-small'),
        undefined, { timeout: 5_000 });
      assert.equal(await photo.evaluate((element) => getComputedStyle(element).maxWidth), 'none');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('索引页分区使用中性填充选中态，方向键能切换', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/studios');
    try {
      const page = opened.page;
      const selected = page.locator('#index [role="tab"][aria-selected="true"]');
      const look = await selected.evaluate((element) => {
        const style = getComputedStyle(element);
        return { background: style.backgroundColor, border: style.borderBottomWidth, weight: style.fontWeight };
      });
      assert.notEqual(look.background, 'rgba(0, 0, 0, 0)');
      assert.equal(look.border, '0px');
      assert.equal(look.weight, '400');
      await selected.focus();
      await page.keyboard.press('ArrowRight');
      await page.locator('#index [role="tab"][aria-selected="true"][data-tab="agencies"]').waitFor({ timeout: 5_000 });
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('播放列表卡：封面后压两层纸边、黑底封面、玻璃徽标、38px 头像有图叠 22px、只有首字母叠 8px；标题 32/44，菜单键静止透明', { timeout: 60_000 }, async () => {
    const opened = await openPlaylistsPage(browser);
    try {
      const page = opened.page;
      const look = await page.evaluate(() => {
        const card = document.querySelector('#stats [data-playlist-card="1"]')!;
        const css = (selector: string) => getComputedStyle(card.querySelector(selector)!);
        const stack = card.querySelector('[data-mix-stack]')!;
        const back = getComputedStyle(stack, '::before');
        const mid = getComputedStyle(stack, '::after');
        const title = getComputedStyle(document.querySelector('#stats header h2')!);
        const cover = css('[data-mix-cover]');
        const badge = css('[data-mix-badge]');
        const menu = css('[data-playlist-menu]');
        const blank = getComputedStyle(document.querySelector('#stats [data-playlist-card="3"] [data-mix-cover] > span')!);
        return {
          title: [title.fontSize, title.lineHeight, title.fontWeight],
          gap: getComputedStyle(document.querySelector('#stats [data-playlist-grid]')!).gap,
          back: [back.borderTopWidth, back.inset, back.transform, back.opacity],
          mid: [mid.borderTopWidth, mid.inset, mid.transform, mid.opacity],
          cover: [cover.backgroundColor, cover.clipPath],
          badge: [badge.backgroundColor, badge.backdropFilter, badge.minHeight, badge.borderRadius, badge.right, badge.bottom],
          avatars: [...card.querySelectorAll('[data-mix-avatars] button')].map((button) => {
            const style = getComputedStyle(button);
            return [style.width, style.marginLeft, style.zIndex];
          }),
          menu: [menu.width, menu.height, menu.borderRadius, menu.backgroundColor],
          blank: [blank.letterSpacing, blank.textTransform],
        };
      });
      assert.deepEqual(look, {
        title: ['32px', '44px', '500'],
        gap: '18px',
        back: ['1px', '0px 12px 8px', 'matrix(1, 0, 0, 1, 0, -7)', '0.54'],
        mid: ['1px', '0px 6px 4px', 'matrix(1, 0, 0, 1, 0, -4)', '0.78'],
        cover: ['rgb(0, 0, 0)', 'inset(0px round 14px)'],
        badge: ['rgba(12, 8, 8, 0.72)', 'blur(10px)', '28px', '10px', '9px', '9px'],
        avatars: [['38px', '0px', '5'], ['38px', '-8px', '4'], ['38px', '-8px', '3']],
        menu: ['30px', '30px', '10px', 'rgba(0, 0, 0, 0)'],
        blank: ['1.44px', 'uppercase'],
      });
      // 指到的头像抬起 4px、放大到 1.05，右边那位朝标题让开 10px。
      await page.locator('#stats [data-playlist-card="1"] [data-mix-avatars] button').first().hover();
      const lifted = await page.evaluate(() => {
        document.getAnimations().forEach((animation) => animation.finish());
        return [...document.querySelectorAll('#stats [data-playlist-card="1"] [data-mix-avatars] button')]
          .slice(0, 2).map((button) => getComputedStyle(button).transform);
      });
      assert.deepEqual(lifted, ['matrix(1.05, 0, 0, 1.05, 0, -4)', 'matrix(1, 0, 0, 1, 10, -1.8)']);
      // 点点点菜单外宽 172px，同旧卡片菜单。
      await page.locator('#stats [data-playlist-card="1"] [data-playlist-menu]').click();
      const panel = page.locator(':has(> [role="dialog"][aria-label="播放列表操作：列表1"])');
      await panel.waitFor({ timeout: 5_000 });
      assert.equal(await panel.evaluate((element) => getComputedStyle(element).width), '172px');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('播放列表页窄屏：页头改竖排，新建框字号 16px 免得 iOS 聚焦时放大', { timeout: 60_000 }, async () => {
    const opened = await openPlaylistsPage(browser, MOBILE);
    try {
      const narrow = await opened.page.evaluate(() => ({
        header: getComputedStyle(document.querySelector('#stats header')!).flexDirection,
        input: getComputedStyle(document.querySelector('#stats [data-playlist-create] input')!).fontSize,
        title: getComputedStyle(document.querySelector('#stats header h2')!).fontSize,
      }));
      assert.deepEqual(narrow, { header: 'column', input: '16px', title: '32px' });
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('分段底板支持鼠标反向、键盘即时切换与减少动态效果', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/performers');
    const { page } = opened;
    try {
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      const track = page.locator('[data-index-layout]');
      const choices = track.locator('label');
      const pane = track.locator('[data-moving-surface]');
      await pane.waitFor();
      const aligned = async () => {
        await page.waitForFunction(() => {
          const host = document.querySelector('[data-index-layout]')!;
          const pane = host.querySelector('[data-moving-surface]')!.getBoundingClientRect();
          const selected = host.querySelector('label[data-selected]')!.getBoundingClientRect();
          return Math.abs(pane.x - selected.x) < 1 && Math.abs(pane.width - selected.width) < 1;
        });
      };
      const assertInstant = async () => {
        const offset = await pane.evaluate((node) => {
          const selected = node.parentElement!.querySelector('label[data-selected]')!.getBoundingClientRect();
          const box = node.getBoundingClientRect();
          return { x: box.x - selected.x, width: box.width - selected.width };
        });
        assert.ok(Math.abs(offset.x) < 1 && Math.abs(offset.width) < 1,
          `即时切换的底板位置：${JSON.stringify(offset)}`);
      };
      await choices.nth(0).click();
      await aligned();
      const endpoints = await choices.evaluateAll((nodes) => nodes.slice(0, 2).map((node) => node.getBoundingClientRect().x));
      await pane.evaluate((node) => {
        const target = node.parentElement!.querySelectorAll('label')[1];
        target.addEventListener('pointerdown', () => {
        const samples: number[] = [];
        const sample = () => {
          samples.push(node.getBoundingClientRect().x);
          if (samples.length < 24) requestAnimationFrame(sample);
          else (node as HTMLElement).dataset.motionSamples = JSON.stringify(samples);
        };
        requestAnimationFrame(sample);
        }, { once: true });
      });
      await choices.nth(1).click();
      await page.waitForFunction(() => document.querySelector('[data-index-layout] [data-motion-samples]'));
      const samples = JSON.parse((await pane.getAttribute('data-motion-samples'))!) as number[];
      assert.ok(samples.some((x) => x > Math.min(...endpoints) + 1 && x < Math.max(...endpoints) - 1),
        `鼠标切换应经过两项之间的位置：${JSON.stringify(samples)}`);
      await choices.nth(0).click();
      await choices.nth(1).click();
      await choices.nth(0).click();
      await aligned();
      assert.equal(await pane.count(), 1);
      assert.equal(await pane.getAttribute('aria-hidden'), 'true');
      await track.getByRole('radio').nth(0).focus();
      await page.keyboard.press('ArrowRight');
      await assertInstant();
      assert.equal(await track.getByRole('radio').nth(1).isChecked(), true);
      assert.equal(await pane.evaluate((node) => node.getAnimations().length), 0);
      assert.match(await choices.nth(1).evaluate((node) => getComputedStyle(node).getPropertyValue('--tw-ring-shadow')), /2px/,
        '选中底板不能抹掉键盘焦点环');
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await choices.nth(0).click();
      await assertInstant();
      assert.equal(await pane.evaluate((node) => node.getAnimations().length), 0);
      await page.setViewportSize({ width: 390, height: 844 });
      await aligned();
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  it('输入框失败态只把框线换成 danger 色：填充照静止态，原因用说明文字的灰', { timeout: 60_000 }, async () => {
    const opened = await openPlaylistsPage(browser);
    const { page } = opened;
    try {
      const form = page.locator('[data-playlist-create]');
      const resting = await form.locator('[data-input-size] > div[data-rac]').evaluate((node) => getComputedStyle(node).backgroundColor);
      await form.getByRole('button', { name: '新建' }).click();
      await form.locator('[data-input-size] > div[data-rac][data-invalid]').waitFor();
      const looks = await form.locator('[data-input-size]').evaluate((root) => {
        const probe = (token: string) => {
          const swatch = document.createElement('i');
          swatch.style.color = `var(${token})`;
          root.append(swatch);
          const color = getComputedStyle(swatch).color;
          swatch.remove();
          return color;
        };
        const group = getComputedStyle(root.querySelector(':scope > div[data-rac]')!);
        return {
          fill: group.backgroundColor, outline: group.outlineColor, width: group.outlineWidth,
          reason: getComputedStyle(root.querySelector(':scope > [slot="errorMessage"]')!).color,
          danger: probe('--color-border-error-default'), secondary: probe('--color-text-secondary'),
        };
      });
      assert.deepEqual([looks.fill, looks.outline, looks.width, looks.reason],
        [resting, looks.danger, '1px', looks.secondary]);
    } finally { await opened.close(); }
  });

  it('短菜单共享悬停面，弹窗关闭释放焦点与遮罩', { timeout: 60_000 }, async () => {
    const opened = await openPlaylistsPage(browser);
    const { page } = opened;
    try {
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      const trigger = page.locator('[data-playlist-card="1"] [data-playlist-menu]');
      await trigger.click();
      const menu = page.getByRole('dialog', { name: '播放列表操作：列表1' });
      await menu.getByRole('button', { name: '编辑名称' }).hover();
      const pane = menu.locator('[data-moving-surface]');
      await pane.waitFor();
      await menu.getByRole('button', { name: '删除播放列表' }).hover();
      await page.waitForFunction(() => {
        const host = document.querySelector('[data-surface-host="hover"]')!;
        const pane = host.querySelector('[data-moving-surface]')!.getBoundingClientRect();
        const button = [...host.querySelectorAll('button')].at(-1)!.getBoundingClientRect();
        return Math.abs(pane.y - button.y) < 1;
      });
      await page.keyboard.press('Tab');
      assert.equal(await pane.isVisible(), false);
      await menu.getByRole('button', { name: '编辑名称' }).click();
      const modal = page.locator('[data-modal-motion]');
      await modal.waitFor();
      await modal.getByRole('textbox', { name: '名称' }).waitFor();
      assert.equal(await modal.locator('[data-modal-surface]').evaluate((node) =>
        getComputedStyle(node).transitionProperty.includes('transform')), true);
      await modal.locator('form').getByRole('button', { name: '取消', exact: true }).click();
      await modal.waitFor({ state: 'detached' });
      await trigger.click();
      await menu.getByRole('button', { name: '编辑名称' }).focus();
      await page.keyboard.press('Enter');
      await modal.waitFor();
      assert.equal(await modal.getAttribute('data-motion-instant'), 'true');
      await page.waitForFunction(() => Boolean(document.activeElement?.closest('[data-modal-motion]')),
        null, { timeout: 5_000 });
      await page.keyboard.press('Escape');
      await modal.waitFor({ state: 'detached' });
      assert.equal(await page.evaluate(() => document.activeElement?.closest('[data-modal-motion]') !== null), false);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  it('批量选择条更新计数保持节点，清空后退出且不保留可操作控件', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/tags');
    const { page } = opened;
    try {
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.locator('#selectMode').click();
      const tags = page.locator('#index [data-alpha-tag]');
      await tags.nth(0).click();
      const dock = page.locator('[data-selection-dock]');
      await dock.waitFor();
      await dock.evaluate((node) => node.setAttribute('data-test-identity', 'retained'));
      await tags.nth(1).click();
      assert.equal(await dock.getAttribute('data-test-identity'), 'retained');
      assert.match(await dock.textContent() ?? '', /已选 2 个标签/);
      await dock.getByRole('button', { name: '清空', exact: true }).click();
      await page.waitForFunction(() => {
        const dock = document.querySelector<HTMLElement>('[data-selection-dock]');
        return !dock || (dock.inert && dock.getAttribute('aria-hidden') === 'true');
      });
      await dock.waitFor({ state: 'detached' });
      await tags.nth(0).click();
      await dock.waitFor();
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await dock.getByRole('button', { name: '清空', exact: true }).click();
      await dock.waitFor({ state: 'detached' });
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  it('标签多选坞出现时页底让出 96px；在线词表的类型色走上游 tag_type 那一套', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/tags');
    try {
      const page = opened.page;
      await page.locator('#selectMode').click({ timeout: 5_000 });
      await page.locator('#index [data-alpha-tag]').nth(0).click({ timeout: 5_000 });
      await page.locator('#index [data-alpha-tag]').nth(1).click({ timeout: 5_000 });
      const dock = page.locator('[data-selection-dock]');
      await dock.waitFor({ timeout: 5_000 });
      const padding = await page.evaluate(() =>
        getComputedStyle(document.querySelector('#index .peach-react')!).paddingBottom);
      assert.equal(padding, '96px', '选择坞盖住了最后一行标签');
      await page.locator('#index [role="tab"][data-tab="online"]').click({ timeout: 5_000 });
      const dot = page.locator('#index [data-alpha-tag][data-tag-cat="r34-artist"] [data-tag-dot]');
      await dot.waitFor({ timeout: 5_000 });
      assert.equal(await dot.evaluate((element) => getComputedStyle(element).backgroundColor), 'rgb(227, 108, 108)');
      assert.equal(await dock.count(), 0, '在线词表不给多选坞');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('标签云一枚里名字与计数只隔一个空格宽；筛选浮层的 Aa 字形摆进 16×16 的框', { timeout: 60_000 }, async () => {
    const opened = await openIndexPage(browser, '/tags?view=cloud');
    try {
      const page = opened.page;
      const spacing = await page.locator('#index [data-tag-chip]').first().evaluate((chip) => {
        // 名字那段文字按字符切开量：字的右缘到计数左缘，与末尾那个空格自己画出来的宽度相比。
        const name = chip.firstChild!;
        const end = name.textContent!.trimEnd().length;
        const box = (from: number, to: number) => {
          const part = document.createRange();
          part.setStart(name, from);
          part.setEnd(name, to);
          return part.getBoundingClientRect();
        };
        const count = chip.querySelector('[data-tag-n]')!.getBoundingClientRect();
        return { gap: count.left - box(0, end).right, space: box(end, name.textContent!.length).width,
          columnGap: getComputedStyle(chip).columnGap };
      });
      assert.equal(spacing.columnGap, 'normal', '名字与计数之间不另加 flex 间距');
      assert.ok(spacing.space > 2 && Math.abs(spacing.gap - spacing.space) < 0.5,
        `名字与计数隔了 ${spacing.gap}px，一个空格是 ${spacing.space}px`);
      const glyph = page.locator('#index svg:has(use[href="#i-text-aa"])');
      const letterGlyph = await glyph.evaluate((element) => {
        const style = getComputedStyle(element);
        const ink = (element as SVGSVGElement).getBBox();
        return { width: parseFloat(style.width), height: parseFloat(style.height), inkHeight: ink.height };
      });
      assert.equal(letterGlyph.height, 16);
      assert.ok(letterGlyph.width / letterGlyph.height > 1.4, '字母表字形按高度保留横向比例');
      assert.ok(letterGlyph.inkHeight / 24 > 0.8, '字母表字形的实际墨迹占满同级图标活区');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('重复文件的汇总条是玻璃面，组卡 14px 圆角、组头 20px 内边距，每行上方一条 1px 分隔线', { timeout: 60_000 }, async () => {
    const opened = await openDuplicates(browser);
    try {
      const faces = await opened.page.evaluate(() => {
        const glass = getComputedStyle(document.querySelector('[data-filter-glass]')!);
        const group = document.querySelector('#stats section[aria-label="DUPE-002"]')!;
        const head = group.firstElementChild!;
        const card = getComputedStyle(group);
        const header = getComputedStyle(head);
        const code = getComputedStyle(head.querySelector('b')!);
        return {
          glass: { filter: glass.backdropFilter, padding: glass.padding, height: glass.height },
          card: { radius: card.borderRadius, overflow: card.overflow, face: card.backgroundColor },
          head: { padding: header.padding, face: header.backgroundColor, size: code.fontSize, weight: code.fontWeight },
          rows: [...group.querySelectorAll('.duplicate-row')].map((row) => {
            const computed = getComputedStyle(row);
            return [computed.borderTopWidth, computed.padding];
          }),
        };
      });
      assert.match(faces.glass.filter, /blur\(/, '汇总条没有背景模糊，不是玻璃面');
      assert.deepEqual([faces.glass.padding, faces.glass.height], ['10px 16px', '50px']);
      assert.deepEqual([faces.card.radius, faces.card.overflow], ['14px', 'hidden'], '组卡圆角或裁切不对');
      assert.notEqual(faces.head.face, faces.card.face, '组头和组卡同色，番号那一行分不出来');
      assert.deepEqual([faces.head.padding, faces.head.size, faces.head.weight], ['20px', '20px', '500']);
      assert.deepEqual(faces.rows, [['1px', '16px 20px'], ['1px', '16px 20px']],
        '组内每一行上方不是 1px 分隔线，或内边距不是 16/20');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('整页空态沉一档底色、20px 圆角、至少 320px 高，图标装进 54px 描边方框；卡里的空态不带方框', { timeout: 60_000 }, async () => {
    // 演示库在一轮里凑得出重复组，桩成空的才一定落到整页空态上。
    const opened = await visit(browser, '/duplicates', DESKTOP);
    try {
      const page = opened.page;
      await page.route('**/api/duplicates?**', (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ total: 0, files: 0, reclaimable: 0, groups: [] }),
      }));
      await page.reload({ waitUntil: 'load' });
      await page.locator('#stats [data-empty-state]').waitFor({ timeout: 15_000 });
      await settle(page);
      const face = await page.evaluate(() => {
        const root = document.querySelector('#stats [data-empty-state]')!;
        const glyph = root.querySelector('svg')!;
        const box = glyph.parentElement!;
        const shell = getComputedStyle(root);
        const frame = getComputedStyle(box);
        const title = getComputedStyle(root.querySelector('h3')!);
        const copy = getComputedStyle(root.querySelector('p')!);
        return {
          shell: { face: shell.backgroundColor, line: shell.borderTopColor, width: shell.borderTopWidth,
            radius: shell.borderTopLeftRadius, minHeight: shell.minHeight },
          boxed: box !== root,
          frame: { width: frame.width, height: frame.height, radius: frame.borderTopLeftRadius,
            line: frame.borderTopWidth, glyph: getComputedStyle(glyph).width },
          title: [title.fontSize, title.fontWeight], copy: [copy.maxWidth, copy.lineHeight],
        };
      });
      assert.equal(face.shell.face, await tokenColor(page, '.peach-react', '--color-background-secondary-default'),
        '整页空态没有沉一档底色');
      assert.equal(face.shell.line, await tokenColor(page, '.peach-react', '--color-separator-border'), '外框不是分隔线色');
      assert.deepEqual([face.shell.width, face.shell.radius, face.shell.minHeight], ['1px', '20px', '320px']);
      assert.ok(face.boxed, '整页空态的图标没有装进方框');
      assert.deepEqual(face.frame, { width: '54px', height: '54px', radius: '20px', line: '1px', glyph: '32px' });
      assert.deepEqual(face.title, ['14px', '600']);
      assert.deepEqual(face.copy, ['340px', '20.15px']);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
    // 卡里那一档（活动页没有任务记录）图标直接挂在空态上，外面没有方框。
    const activity = await visit(browser, '/tasks', DESKTOP);
    try {
      await activity.page.route('**/api/tasks', (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ available: true, running: [], skipped: [], finished: [] }),
      }));
      await activity.page.reload({ waitUntil: 'load' });
      const empty = activity.page.locator('[data-empty-state]');
      await empty.waitFor({ timeout: 15_000 });
      await settle(activity.page);
      assert.equal(await empty.evaluate((root) => root.querySelector('svg')!.parentElement === root), true,
        '卡里的空态也套了图标方框');
      assert.deepEqual(activity.problems, []);
    } finally {
      await activity.close();
    }
  });

  it('骨架里的占位和按键悬停不给任何反馈，也点不中', { timeout: 120_000 }, async () => {
    const name = '七沢みあ';
    const pages: { path: string; ready: string; targets: string[]; prepare?: (page: Page) => Promise<void> }[] = [
      { path: `/performers/${encodeURIComponent(name)}`, ready: '[data-skeleton="entity/performer"] [data-entity-foot] [data-hero-ring]',
        targets: ['[data-skeleton] [data-entity-foot] [data-hero-ring]'],
        prepare: (page) => page.route(/\/api\/entity\/shapes/, (route) => route.fulfill({ json: {
          ok: true, entities: [{ id: 90_001, kind: 'performer', names: [name], parts: ['costars'] }] } })) },
      { path: '/data-cleanup', ready: '[data-skeleton="cleanup"] [data-skeleton-action]',
        targets: ['[data-skeleton] .board-plain-stat', '[data-skeleton] a[href="/sources"]',
          '[data-skeleton] [data-skeleton-action]', '[data-skeleton] .cleanupfieldset [data-skeleton-action]'] },
      { path: '/review', ready: '[data-skeleton="review"] .reviewtabs button',
        targets: ['[data-skeleton] .reviewtabs button', '[data-skeleton] .skeletoncard'] },
      { path: '/follow', ready: '.followauthors .avskeleton',
        targets: ['.followauthors .avskeleton', '.followworks .brandskeleton', '[data-skeleton^="cards/"] > div > *'] },
      { path: '/subscriptions', ready: '[data-skeleton="board/subscriptions"] .ui-follow-skeleton-toolbar',
        targets: ['[data-skeleton] .ui-follow-skeleton-toolbar [data-follow-toolbar-controls] > button:nth-of-type(1)',
          '[data-skeleton] .ui-follow-skeleton-toolbar > button:nth-of-type(1)'] },
    ];
    for (const { path, ready, targets, prepare } of pages) {
      const opened = await visit(browser, '/', DESKTOP);
      try {
        const page = opened.page;
        // 后登记的路由先拿到请求：桩要排在挂住 /api/ 那条之后，不然它也被挂住。
        await holdApi(page);
        await prepare?.(page);
        await page.goto(new URL(path, page.url()).href, { waitUntil: 'load' });
        await page.locator(ready).first().waitFor({ state: 'visible', timeout: 15_000 });
        for (const target of targets) {
          const node = page.locator(target).first();
          await node.waitFor({ state: 'visible', timeout: 5_000 });
          // 落在首屏以下的目标（数据管理的整理卡）先滚进视口：`elementFromPoint` 只认视口里的点。
          await node.scrollIntoViewIfNeeded();
          const box = (await node.boundingBox())!;
          const look = () => node.evaluate((element) => [element, ...element.querySelectorAll('*')].slice(0, 6).map((part) => {
            const style = getComputedStyle(part);
            return [style.borderColor, style.boxShadow, style.backgroundColor, style.backgroundImage,
              style.outlineStyle, style.color, style.scale, style.transform].join(' | ');
          }));
          await page.mouse.move(0, 0);
          const rest = await look();
          const [x, y] = [box.x + box.width / 2, box.y + box.height / 2];
          await page.mouse.move(x, y);
          assert.deepEqual(await look(), rest, `${path} ${target} 悬停时变了样子`);
          // 分层骨架那一排（`data-skeleton-tier`）是最终那条轨道本身，点中它等于点在页面底上；
          // 要问的是指针有没有落在某一枚占位或整块骨架里。
          const hit = await page.evaluate(([px, py]) => {
            const at = document.elementFromPoint(px!, py!);
            return { cursor: at ? getComputedStyle(at).cursor : '',
              inside: !!at?.closest('[data-skeleton],.avskeleton,.brandskeleton,.tagskeleton,.skeletoncard'),
              disabled: !!at?.closest('button:disabled') };
          }, [x, y]);
          assert.ok(['auto', 'default', 'not-allowed'].includes(hit.cursor), `${path} ${target} 悬停换了光标：${hit.cursor}`);
          assert.ok(!hit.inside || hit.disabled, `${path} ${target} 在骨架里还能点中`);
        }
      } finally {
        await opened.close();
      }
    }
  });

  it('勾中的来源在卡片和表格里都铺 BoardUI 数据表那一档选中底色', { timeout: 60_000 }, async () => {
    const opened = await openFollowManage(browser);
    try {
      const page = opened.page;
      const selected = await tokenColor(page, '.peach-react', '--color-background-secondary-default');
      await page.getByRole('checkbox', { name: '选择 kou · Kemono' }).check({ force: true, timeout: 5_000 });
      const cardRow = page.locator('[data-source-divider] > [data-selected]');
      assert.equal(await cardRow.count(), 1);
      assert.equal(await cardRow.evaluate((row) => getComputedStyle(row).backgroundColor), selected,
        '卡片里的选中行没有铺选中底色');

      await page.locator('button[aria-label="表格视图"]').click({ timeout: 5_000 });
      const tableRow = page.locator('[data-follow-selected]');
      await tableRow.waitFor({ timeout: 5_000 });
      assert.equal(await tableRow.evaluate((row) => getComputedStyle(row).backgroundColor), selected,
        '表格里的选中行和卡片里的不是同一档');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('别名的组数是次要字色的读数，不借提醒或主按钮的颜色', { timeout: 60_000 }, async () => {
    const opened = await openFollowManage(browser);
    try {
      await followTab(opened, '添加关注');
      const count = opened.page.getByText('1 组', { exact: true });
      const look = await count.evaluate((node) => ({
        ink: getComputedStyle(node).color, face: getComputedStyle(node).backgroundColor,
      }));
      assert.equal(look.ink, await tokenColor(opened.page, '.peach-react', '--color-text-secondary'));
      assert.equal(look.face, 'rgba(0, 0, 0, 0)', '组数画成了带底色的徽章');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  /* 建议由这里给，两组共 30 条，两个视口下字段下方都装不下。桌面取 1280×1000，
     字段下缘在视口中段偏下，正是只能往下开、又放不全的那种处境。 */
  for (const viewport of [{ ...DESKTOP, height: 1000 }, MOBILE]) {
    it(`添加关注的建议下拉压在字段下方剩下的空间里，装不下在菜单内滚，选中行滚进可见区（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openFollowManage(browser, viewport);
      try {
        const { page } = opened;
        await page.route(/\/api\/follow\/suggest\?/, (route) => route.fulfill({ json: {
          q: 'bulging',
          groups: [['归档站', 18], ['站方分类', 12]].map(([label, count]) => ({
            kind: String(label), label,
            items: Array.from({ length: Number(count) }, (_, at) => ({
              value: `bulging ${label} ${at + 1}`, matched: 'Kemono', n: 120 - at,
            })),
          })),
        } }));
        await followTab(opened, '添加关注');
        const before = await page.evaluate(() => document.documentElement.scrollHeight);
        const input = page.getByRole('textbox', { name: '来源链接、名字或 id' });
        await input.click({ timeout: 5_000 });
        await input.fill('bulging');
        const menu = page.locator('[aria-label="来源建议"]');
        await menu.locator('button').nth(29).waitFor({ state: 'attached', timeout: 10_000 });
        const measure = () => menu.evaluate((element) => {
          for (const animation of element.getAnimations({ subtree: true })) animation.finish();
          const box = element.getBoundingClientRect();
          const field = element.ownerDocument.querySelector('input[aria-label="来源链接、名字或 id"]')!
            .getBoundingClientRect();
          const row = element.querySelector('[aria-current="true"]')?.getBoundingClientRect();
          return {
            top: box.top, bottom: box.bottom, fieldBottom: field.bottom, viewport: innerHeight,
            overflowing: element.scrollHeight > element.clientHeight,
            overscroll: getComputedStyle(element).overscrollBehaviorY,
            page: document.documentElement.scrollHeight, pageWidth: document.documentElement.scrollWidth,
            width: innerWidth, y: scrollY,
            row: row ? { top: row.top, bottom: row.bottom } : null,
          };
        });
        const open = await measure();
        assert.ok(open.bottom <= open.viewport + .5, `下拉底边 ${open.bottom} 越出视口 ${open.viewport}`);
        assert.ok(open.top >= open.fieldBottom, `下拉上沿 ${open.top} 盖住了字段（下缘 ${open.fieldBottom}）`);
        assert.ok(open.overflowing, '30 条建议没有让下拉溢出，这条用例没练到内滚');
        assert.equal(open.overscroll, 'contain', '下拉滚到头会带着页面一起滚');
        assert.ok(open.page <= before, `下拉把页面从 ${before} 撑到 ${open.page}`);
        assert.ok(open.pageWidth <= open.width, `页面被撑出横向滚动：${open.pageWidth} > ${open.width}`);

        // 走到最后一条：它在初始可见区外面，得被滚进来，页面本身不动。
        for (let step = 0; step < 30; step++) await input.press('ArrowDown');
        const last = await measure();
        assert.ok(last.row, '上下键没有选中任何一行');
        assert.ok(last.row.top >= last.top - .5 && last.row.bottom <= last.bottom + .5,
          `选中行 ${last.row.top}–${last.row.bottom} 不在下拉可见区 ${last.top}–${last.bottom} 内`);
        assert.equal(last.y, open.y, '选中行滚进可见区时把页面也滚了');
        await input.press('ArrowDown');
        const first = await measure();
        assert.ok(first.row && first.row.top >= first.top - .5, '绕回第一条后它没有滚回可见区');
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  for (const viewport of [DESKTOP, { ...MOBILE, name: 'narrow', width: 320 }]) {
    it(`七位数的读数和单位留在卡里同一行；长名字与长报错的来源行不越出行，桌面上操作键和名字同一行（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const long = 'ThisIsAVeryLongUsernameThatPatreonAllowsWithoutAnySpaces2024';
      const opened = await openFollowManage(browser, viewport, {
        counts: { new: 1284000, seen: 22000, saved: 0, ignored: 0 },
        extra: [
          followSource(4, long, 'Patreon', `${long} · Patreon`),
          { ...followSource(5, 'broken', 'Kemono', 'broken · Kemono', 'error'),
            last_error: `peach.follow_providers.ProviderError: HTTP 403: <!DOCTYPE html>${'<div class="cf">'.repeat(40)}` },
        ],
      });
      try {
        const spill = await opened.page.evaluate(() => {
          const out = (node: Element, frame: Element) => node.getBoundingClientRect().right > frame.getBoundingClientRect().right + 1;
          const readings = [...document.querySelectorAll('[data-follow-reading]')]
            .filter((node) => [...node.children].some((part) => out(part, node.parentElement!)))
            .map((node) => node.textContent);
          const wrapped = [...document.querySelectorAll('[data-follow-reading]')].filter((node) => {
            const [figure, unit] = [...node.children].map((part) => part.getBoundingClientRect());
            return unit!.top >= figure!.bottom - 1;
          }).map((node) => node.textContent);
          const rows = [...document.querySelectorAll('[data-follow-source-row]')];
          const spilled = rows
            .filter((row) => row.scrollWidth > row.clientWidth + 1 || [...row.querySelectorAll('button, [role="switch"], input')]
              .some((control) => out(control, row)))
            .map((row) => row.textContent);
          // 末一枚操作键的竖直中线落在名字列的上下沿之间，就是同一行。
          const split = rows.filter((row) => {
            const name = row.querySelector('a')!.parentElement!.getBoundingClientRect();
            const last = [...row.querySelectorAll('button')].at(-1)!.getBoundingClientRect();
            const middle = (last.top + last.bottom) / 2;
            return middle < name.top - 1 || middle > name.bottom + 1;
          }).map((row) => row.textContent);
          return { readings, wrapped, spilled, split, rows: rows.length,
            text: document.querySelector('[data-follow-reading]')?.parentElement?.parentElement?.textContent };
        });
        assert.ok(spill.text?.includes('1,284,000'), `读数没有用上七位数：${spill.text}`);
        assert.equal(spill.rows, 5, '造的五条来源没有全部画出来');
        assert.deepEqual(spill.readings, [], '读数越出了卡片');
        assert.deepEqual(spill.wrapped, [], '七位数的读数把单位挤到了下一行');
        assert.deepEqual(spill.spilled, [], '来源行的控件越出了行');
        if (!viewport.mobile) assert.deepEqual(spill.split, [], '桌面上来源行的操作键掉到了名字下面');
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  it('凭据的四种处境四副底色：待办和完成一眼分得开', { timeout: 60_000 }, async () => {
    const opened = await openFollowManage(browser);
    try {
      await opened.page.getByRole('button', { name: /^来源与订阅设置/ }).click();
      await opened.page.getByRole('tab', { name: '来源与订阅', exact: true, selected: true }).waitFor();
      await opened.page.getByRole('region', { name: '可配置凭据', exact: true }).waitFor();
      await settle(opened.page);
      const faces: Record<string, string> = {};
      for (const state of ['需要', '已配置', '接不进来', '不需要']) {
        faces[state] = await opened.page.getByText(state, { exact: true }).first()
          .evaluate((chip) => getComputedStyle(chip).backgroundColor);
      }
      assert.equal(new Set(Object.values(faces)).size, 4, `有两种处境同色：${JSON.stringify(faces)}`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('浅色下看片那两枚标识悬停时有底，资料卡上带订阅新作开关', { timeout: 60_000 }, async () => {
    // 演示库里没有人物实体：资料由这里给，入口与开关字段照服务端拼好下发的形状写。
    const name = '七沢みあ';
    const opened = await visit(browser, '/', DESKTOP);
    try {
      await opened.page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
        id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [],
        user_aliases: [], asset_count: 0, tags: [], related_performers: [], links: [],
        metadata: {}, has_image: false, has_avatar: false, avatar_focus: null,
        representative_asset_id: null,
        entry_links: [{ site: 'javdb', label: 'JavDB', ordinal: '', slot: 'mark',
          mark: 'mark-javdb', url: 'https://javdb.com/actors/NPD3' }],
        feed: { following: false },
      } }));
      await opened.page.goto(new URL(`/performers/${encodeURIComponent(name)}`,
        opened.page.url()).href, { waitUntil: 'load' });
      const mark = opened.page.locator('[data-entry-marks] a[data-entry-mark]').first();
      await mark.waitFor({ timeout: 15_000 });
      await settle(opened.page);
      // 浅色下 `--hover` 与资料卡的 `--ground` 同是 #f7f7f7，垫上去等于没垫。
      await opened.page.evaluate(() => {
        document.documentElement.dataset.theme = 'light';
        document.documentElement.classList.remove('dark');
      });
      await mark.hover();
      const faces = await mark.evaluate((element) => ({
        mark: getComputedStyle(element).backgroundColor,
        card: getComputedStyle(element.closest('[data-entity-card]')!).backgroundColor,
      }));
      assert.notEqual(faces.mark, 'rgba(0, 0, 0, 0)', '悬停没有垫底');
      assert.notEqual(faces.mark, faces.card, '悬停底色和资料卡同色，看不出来');
      const toggle = opened.page.getByRole('switch', { name: '订阅新作' });
      assert.equal(await toggle.count(), 1, '资料卡上没有订阅新作开关');
      assert.equal(await toggle.isChecked(), false);
      const feed = opened.page.locator('[data-entry-feed]');
      await opened.page.emulateMedia({ reducedMotion: 'no-preference' });
      assert.equal(await feed.evaluate((element) => getComputedStyle(element).animationName),
        'entryfeed-breathe', '没订的那枚图标不呼吸，一枚墨色小图标没人注意到');
      const tip = opened.page.locator('#entityFeedTip');
      assert.equal(await tip.isVisible(), false);
      await feed.hover();
      assert.equal(await tip.isVisible(), true, '悬停没有说明这枚图标是干嘛的');
      assert.equal(await tip.innerText(), '订阅新作');
      const placed = await tip.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return box.left >= 0 && box.right <= innerWidth && box.bottom <= innerHeight;
      });
      assert.ok(placed, '说明浮层越出了视口');
      await toggle.focus();
      await opened.page.keyboard.press('Escape');
      assert.equal(await tip.isVisible(), false, 'Escape 收不起说明浮层');
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`订阅新作的说明浮层整块露在外面：不被资料卡裁掉，也不被同台艺人那条盖住（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openPerformer(browser, viewport);
      try {
        const page = opened.page;
        const feed = page.locator('[data-entry-feed]');
        await feed.hover();
        const tip = page.locator('#entityFeedTip');
        await tip.waitFor({ state: 'visible', timeout: 5_000 });
        const placement = await tip.evaluate((element) => {
          const box = element.getBoundingClientRect();
          const clippedBy: string[] = [];
          // 顶层里的元素不受祖先 overflow 裁切，只有写在文档流里的浮层才要逐层量。
          const topLayer = element.matches(':popover-open');
          for (let node = topLayer ? null : element.parentElement; node; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (style.overflowX === 'visible' && style.overflowY === 'visible') continue;
            const clip = node.getBoundingClientRect();
            if (box.left < clip.left - 0.5 || box.right > clip.right + 0.5
              || box.top < clip.top - 0.5 || box.bottom > clip.bottom + 0.5) clippedBy.push(node.className || node.tagName);
          }
          // 浮层本身不接指针；临时放开再问四条边和正中最上面是谁，盖在它上面的东西就现形了。
          // 取样点离角 16px：圆角外那一小块本来就不属于它。
          element.style.pointerEvents = 'auto';
          const [midX, midY] = [box.left + box.width / 2, box.top + box.height / 2];
          const covered = [[box.left + 16, box.top + 2], [box.right - 16, box.top + 2], [box.left + 16, box.bottom - 2],
            [box.right - 16, box.bottom - 2], [box.left + 2, midY], [box.right - 2, midY], [midX, midY]]
            .map(([x, y]) => document.elementFromPoint(x!, y!))
            .filter((hit) => !hit || !element.contains(hit)).map((hit) => hit?.className || 'null');
          element.style.pointerEvents = '';
          return { inView: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
            clippedBy, covered, below: box.top >= document.querySelector('[data-entry-feed]')!.getBoundingClientRect().bottom };
        });
        assert.ok(placement.inView, '说明浮层越出了视口');
        assert.deepEqual(placement.clippedBy, [], '说明浮层被外层容器裁掉了一截');
        assert.deepEqual(placement.covered, [], '说明浮层被别的东西盖住了');
        assert.ok(placement.below, '视口下方放得下时说明浮层应该在图标下面');
        const page_ = await layout(page);
        assert.ok(page_.scrollWidth <= page_.viewportWidth, '说明浮层把页面撑出了横向滚动');
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  // 1000px 窗口侧栏展开时内容区只剩七百来像素：三栏的判据量的是内容区，这一档要排成两层，
  // 身份那一栏不能被资料表挤到只剩一个字宽。
  for (const [viewport, stacked] of [[DESKTOP, false], [{ name: 'wide', width: 1440, height: 900, mobile: false }, false],
    [{ name: 'mid', width: 1000, height: 900, mobile: false }, true], [MOBILE, true]] as const) {
    it(`女优页头：宽屏资料表在身份信息右侧隔一道竖线，窄屏排到下面隔一道横线，整张卡不横向溢出（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openProfiledPerformer(browser, viewport);
      try {
        const geometry = await heroGeometry(opened.page);
        assert.deepEqual(geometry.labels, ['生日', '身材', '出道', '生涯', '标签'], '资料表不是那五项');
        assert.ok(geometry.facts && geometry.identity && geometry.hero, '页头缺了资料表');
        assert.ok(geometry.identity.right - geometry.identity.left >= 240, '身份那一栏被挤窄了');
        if (stacked) {
          assert.ok(geometry.facts.top >= geometry.identity.bottom - 0.5, '窄屏下资料表没有排到身份信息下面');
          assert.deepEqual(geometry.rule, { left: '0px', top: '1px' }, '窄屏下资料表该用横线和身份信息隔开');
        } else {
          assert.ok(geometry.facts.left >= geometry.identity.right - 0.5, '宽屏下资料表没有排在身份信息右侧');
          assert.deepEqual(geometry.rule, { left: '1px', top: '0px' }, '宽屏下资料表该用竖线和身份信息隔开');
        }
        assert.ok(geometry.facts.right <= geometry.hero.right + 0.5, '资料表越出了资料卡');
        assert.equal(geometry.heroScrolls, false, '资料卡里有东西被横向裁掉');
        const debut = await opened.page.locator('[data-entity-facts] dd[title]').evaluate((dd) => ({
          title: dd.getAttribute('title'), wrap: getComputedStyle(dd).whiteSpace,
          cut: getComputedStyle(dd).textOverflow, clipped: dd.scrollWidth > dd.clientWidth,
          lines: Math.round(dd.getBoundingClientRect().height / parseFloat(getComputedStyle(dd).lineHeight)),
        }));
        assert.equal(debut.title, PROFILED.profile.debut_title, '出道那一格的 title 没给全名');
        assert.deepEqual([debut.wrap, debut.cut, debut.lines], ['nowrap', 'ellipsis', 1], '出道片名没有单行截断');
        const page_ = await layout(opened.page);
        assert.ok(page_.scrollWidth <= page_.viewportWidth, `页头把页面撑出了横向滚动：${page_.offenders.join('，')}`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`公司资料页显示公司与品牌事实且长地址可折行（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const fact = (value: string) => ({ value, source_url: 'https://company.example/about' });
      const opened = await openProfiledPerformer(browser, viewport, 'light', {
        kind: 'studio', canonical_name: 'Company', profile: undefined, agency: undefined,
        company_profile: { legal_name: fact('Example Company, LLC'), founded: fact('2002-05'),
          launched: fact('2016'), operator: fact('General Media Systems, LLC'),
          location: fact('LongOfficeAddressWithoutSpaces'.repeat(5)) },
      });
      try {
        await opened.page.goto(new URL('/studios/Company', opened.page.url()).href, { waitUntil: 'load' });
        await opened.page.locator('[data-entity-facts]').waitFor();
        await settle(opened.page);
        const geometry = await heroGeometry(opened.page);
        assert.deepEqual(geometry.labels, ['公司名称', '公司成立', '品牌启动', '所在地', '运营公司']);
        assert.equal(geometry.heroScrolls, false);
        assert.ok(geometry.hero && geometry.facts && geometry.facts.right <= geometry.hero.right + 1);
        assert.equal(await opened.page.locator('[data-entity-facts] [title]').count(), 0);
        const page_ = await layout(opened.page);
        assert.ok(page_.scrollWidth <= page_.viewportWidth);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }

  it('女优页头名字下面一行：视频数、事务所与读音加前三个别名，外链一律 36px 纯图标方块', { timeout: 60_000 }, async () => {
    const opened = await openProfiledPerformer(browser, DESKTOP);
    try {
      const page = opened.page;
      const line = await page.locator('[data-entity-alias]').evaluate((alias) => ({
        glyphs: [...alias.querySelectorAll(':scope > [data-meta-item] > svg use')].map((use) => use.getAttribute('href')),
        names: [...alias.querySelectorAll('[data-alias-names] > span')].map((span) => span.textContent!.trim()),
        more: alias.querySelector('[data-hero-more="alias"]')?.textContent?.trim() ?? '',
        agency: alias.querySelector('a[data-agency]')?.textContent?.trim() ?? '',
      }));
      assert.deepEqual(line.glyphs, ['#i-film', '#i-briefcase', '#i-id-card'], '名字那一行的三项不是视频、事务所、别名');
      assert.deepEqual(line.names, ['しのだゆう', '篠崎ゆう子', '高木早希', '橋本真紀'], '读音没有排在别名最前，或别名不是前三个');
      assert.equal(line.more, '+4', '「+N」数的不是剩下那几个别名');
      assert.equal(line.agency, 'New Actor eXperience');
      const links = await page.locator('[data-entity-links] a').evaluateAll((anchors) => anchors.map((a) => {
        const box = a.getBoundingClientRect();
        return { size: `${Math.round(box.width)}x${Math.round(box.height)}`, kind: a.getAttribute('data-link'), title: a.getAttribute('title'),
          name: a.getAttribute('aria-label') ?? '', visibleText: a.querySelector('[data-link-label]') !== null };
      }));
      assert.equal(links.length, 3);
      for (const link of links) {
        assert.deepEqual([link.size, link.kind, link.visibleText], ['36x36', 'icon', false], `${link.title} 不是纯图标方块`);
        assert.equal(link.name, link.title, `${link.title} 给读屏的名字和悬停提示不一致`);
      }
      assert.deepEqual(links.map((link) => link.title), ['みんなのAV', 'New Actor eXperience 官方资料', 'X @shinoda_yu']);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`别名的「+N」浮层进顶层按名义分组：悬停、聚焦都出，不被资料卡裁掉，Escape 收起（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openProfiledPerformer(browser, viewport);
      try {
        const page = opened.page;
        const more = page.locator('[data-hero-more="alias"]');
        const pop = page.locator('#entityAliasPop');
        assert.equal(await pop.isVisible(), false);
        await more.hover();
        await pop.waitFor({ state: 'visible', timeout: 5_000 });
        assert.equal(await more.getAttribute('aria-expanded'), 'true');
        const shown = await pop.evaluate((element) => {
          const box = element.getBoundingClientRect();
          return {
            topLayer: element.matches(':popover-open'),
            inView: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
            title: element.querySelector('[data-hero-pop-head]')?.textContent?.trim(),
            groups: [...element.querySelectorAll('dt')].map((dt) => dt.textContent!.trim()),
            face: getComputedStyle(element).backgroundColor,
            shadow: getComputedStyle(element).boxShadow,
          };
        });
        assert.equal(shown.shadow, await popmenuShadow(page), '别名浮层的落影和 .popmenu 不是同一副');
        assert.equal(shown.topLayer, true, '别名浮层没进顶层，会被资料卡的 overflow:hidden 裁掉');
        assert.ok(shown.inView, '别名浮层越出了视口');
        assert.equal(shown.title, '7 个别名');
        assert.deepEqual(shown.groups, ['旧名义', '舞ワイフ', 'ラグジュTV', '其它']);
        assert.notEqual(shown.face, 'rgba(0, 0, 0, 0)', '别名浮层没有底色');
        // 指针从按钮挪到浮层上读名字，浮层不能在半路收起。
        const box = (await pop.boundingBox())!;
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 4 });
        await page.waitForTimeout(300);
        assert.equal(await pop.isVisible(), true, '指针移到浮层上它就收起了');
        await page.mouse.move(1, 1);
        await pop.waitFor({ state: 'hidden', timeout: 5_000 });
        await more.focus();
        await page.keyboard.press('Tab');
        await page.keyboard.press('Shift+Tab');
        await pop.waitFor({ state: 'visible', timeout: 5_000 });
        await page.keyboard.press('Escape');
        assert.equal(await pop.isVisible(), false, 'Escape 收不起别名浮层');
        const page_ = await layout(page);
        assert.ok(page_.scrollWidth <= page_.viewportWidth, '别名浮层把页面撑出了横向滚动');
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  // 名字右边那枚按钮往左开的话，260px 的菜单越过名字压在头像上（用户报过）。
  for (const viewport of [DESKTOP, MOBILE]) {
    it(`名字与别名菜单从按钮左缘往右开，不盖住页头头像（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openProfiledPerformer(browser, viewport);
      try {
        const page = opened.page;
        await page.locator('[data-namepick-toggle]').click();
        await page.locator('[data-namepick-menu]').waitFor({ state: 'visible', timeout: 5_000 });
        const box = await page.evaluate(() => {
          const rect = (selector: string) => {
            const b = document.querySelector(selector)!.getBoundingClientRect();
            return { left: b.left, right: b.right, top: b.top, bottom: b.bottom };
          };
          return { menu: rect('[data-namepick-menu]'), portrait: rect('[data-entity-portrait]'), toggle: rect('[data-namepick-toggle]') };
        });
        const overlaps = box.menu.left < box.portrait.right && box.menu.right > box.portrait.left
          && box.menu.top < box.portrait.bottom && box.menu.bottom > box.portrait.top;
        assert.equal(overlaps, false, `名字菜单盖住了头像：${JSON.stringify(box)}`);
        assert.ok(box.menu.left >= 0 && box.menu.right <= viewport.width, '名字菜单越出了视口');
        if (!viewport.mobile) assert.ok(Math.abs(box.menu.left - box.toggle.left) < 1, '宽屏下菜单没有对齐按钮左缘');
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }
});
