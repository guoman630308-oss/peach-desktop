/* 设计决定：控件、状态与首页顶部。读 `getComputedStyle` 断言用户定过的外观；共用的桩与浏览器生命周期在 `design-fixture.ts`。 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { expectBody, settle } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';
import {
  DESKTOP, MOBILE, tokenColor, openAccess, settledRun, openActivity, qualityGoal, openQualityGoals,
  openScraping, openProcessing, openCatalog, openCatalogBars, openCatalogFixture, RUNNING_JOB, FAILED_JOB,
  installDesignBrowser,
} from './design-fixture.ts';

describe('设计决定：控件、状态与首页顶部', () => {
  let browser: Browser;
  installDesignBrowser((next) => { browser = next; });

  it('聚焦 React 输入框只画 BoardUI 外框，旧样式表的焦点环不进来', { timeout: 60_000 }, async () => {
    const opened = await openAccess(browser);
    try {
      const input = opened.form.locator('#access-password');
      await input.focus();
      const style = await input.evaluate((element) => ({
        outline: getComputedStyle(element).outlineStyle,
        // TextField 经 GroupContext 把外框的 role 设成 presentation。
        ring: getComputedStyle(element.closest('[role="presentation"]')!).boxShadow,
      }));
      assert.equal(style.outline, 'none', '输入框自己画了 outline：旧的全局 :focus-visible 进了 React 子树');
      assert.notEqual(style.ring, 'none', '外框没有聚焦描边');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('详情舞台的焦点：骨架期间留在浮窗本身、不画焦点环，内容到了交给关闭键', { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/', DESKTOP, { ready: `#grid [data-media-card][data-id="${ITEM.plain}"]` });
    try {
      const page = opened.page;
      let release = () => {};
      const held = new Promise<void>((resolve) => { release = resolve });
      await page.route((url) => url.pathname === '/api/item', async (route) => { await held; await route.fallback() });
      await page.locator(`#grid [data-media-card][data-id="${ITEM.plain}"] [data-media-title]`).first().press('Enter');
      await page.locator('#stage[open] [data-skeleton="detail"]').waitFor();
      /* 键盘打开也一样：骨架里没有可操作的东西，焦点环画在浮窗外沿上只是一圈噪声。 */
      const waiting = await page.evaluate(() => {
        const focused = document.activeElement as HTMLElement;
        return { id: focused?.id, outline: getComputedStyle(focused).outlineStyle, visible: focused.matches(':focus-visible') };
      });
      assert.equal(waiting.id, 'stage', '骨架期间焦点不在浮窗上');
      assert.equal(waiting.outline, 'none', `骨架期间浮窗画了焦点环（:focus-visible=${waiting.visible}）`);
      release();
      await page.locator('#stage[open] [data-item-side]').waitFor();
      await page.waitForFunction(() => document.activeElement?.id === 'closeStage', null, { timeout: 5_000 });
      assert.deepEqual(opened.problems.filter((line) => !line.includes('VIDEOJS')), []);
    } finally {
      await opened.close();
    }
  });

  it('岛外与 React 子树的语义色是同一份：board.css 根上的每个 --color-* 两边画出同一个像素', { timeout: 60_000 }, async () => {
    const opened = await openAccess(browser);
    try {
      const shell = await opened.form.locator('#access-password').evaluate(
        (element) => getComputedStyle(element.closest('[role="presentation"]')!).backgroundColor);
      // BoardUI theme.css 把浅色 neutral-200 改成了 #ebebeb，不是 Tailwind 默认的 #e5e5e5。
      assert.equal(shell, 'rgb(235, 235, 235)');
      // 岛外的骨架读根上的值，接管后的组件读岛内的值；同一块面两帧要同色。岛内写 oklch、
      // board.css 写十六进制或引用，字面不同，画成像素再比。
      const drift = await opened.form.evaluate((island) => {
        const sheet = [...document.styleSheets].find((candidate) => candidate.href?.includes('/board.css'))!;
        const names = new Set<string>();
        const walk = (rules: CSSRuleList) => {
          for (const rule of rules) {
            if ('cssRules' in rule) walk((rule as CSSGroupingRule).cssRules);
            if (!(rule instanceof CSSStyleRule) || !rule.selectorText.includes(':root')) continue;
            for (const name of rule.style) if (name.startsWith('--color-') && !name.startsWith('--color-accent-')) names.add(name);
          }
        };
        walk(sheet.cssRules);
        const paint = document.createElement('canvas').getContext('2d', { willReadFrequently: true })!;
        const pixel = (host: Element, name: string) => {
          const probe = host.appendChild(document.createElement('i'));
          probe.style.color = `var(${name})`;
          paint.clearRect(0, 0, 1, 1);
          paint.fillStyle = getComputedStyle(probe).color;
          paint.fillRect(0, 0, 1, 1);
          probe.remove();
          return [...paint.getImageData(0, 0, 1, 1).data].join(',');
        };
        const root = document.documentElement;
        const before = { theme: root.dataset.theme, dark: root.classList.contains('dark') };
        const drift: string[] = [];
        for (const theme of ['light', 'dark']) {
          root.dataset.theme = theme;
          root.classList.toggle('dark', theme === 'dark');
          for (const name of names) {
            const page = pixel(document.body, name), inside = pixel(island, name);
            if (page !== inside) drift.push(`${theme} ${name}：岛外 ${page}，岛内 ${inside}`);
          }
        }
        if (before.theme === undefined) delete root.dataset.theme;
        else root.dataset.theme = before.theme;
        root.classList.toggle('dark', before.dark);
        return { count: names.size, drift };
      });
      assert.ok(drift.count >= 15, `只从 board.css 根上读到 ${drift.count} 个语义色`);
      assert.deepEqual(drift.drift, []);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('持久警示是状态色块，不和字段说明共用灰色小字', { timeout: 60_000 }, async () => {
    const opened = await openAccess(browser);
    try {
      await opened.form.getByText('关闭访问密码', { exact: true }).click({ timeout: 5_000 });
      const note = opened.form.locator('[role="note"]');
      await note.waitFor({ timeout: 5_000 });
      const surface = await note.evaluate((element) => getComputedStyle(element).backgroundColor);
      assert.notEqual(surface, 'rgba(0, 0, 0, 0)', '警示没有底色');
      assert.equal(surface, await tokenColor(opened.page, '.peach-react', '--color-status-yellow-background'));
      const ink = await note.evaluate((element) => getComputedStyle(element).color);
      const hint = await opened.form.getByText('关闭访问密码时无需填写。').evaluate((element) => getComputedStyle(element).color);
      assert.notEqual(ink, hint, '警示文字和字段说明同色');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('跑失败的那一轮整张卡框线换成 danger 色，结束原因还是正文色', { timeout: 60_000 }, async () => {
    const opened = await openActivity(browser, [
      settledRun(1, 'failed', '扫描与采集'), settledRun(2, 'succeeded', '追更检查'),
    ]);
    try {
      const danger = await tokenColor(opened.page, '.peach-react', '--color-border-error-default');
      const border = (status: string) => opened.page.locator(`li[data-status="${status}"]`)
        .evaluate((element) => getComputedStyle(element).borderTopColor);
      assert.equal(await border('failed'), danger, '失败卡的框线不是 danger 色');
      assert.notEqual(await border('succeeded'), danger, '没失败的卡也用了 danger 框线');
      // 一屏十几行里逐行读红字比看一眼哪张卡的框是红的慢：原因那行留正文色。
      const reason = await opened.page.locator('li[data-status="failed"] p').last()
        .evaluate((element) => getComputedStyle(element).color);
      assert.notEqual(reason, danger, '结束原因那行字被涂成了 danger 色');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('状态徽章只有三档颜色：成功绿、失败红、被叫停黄，其余中性', { timeout: 60_000 }, async () => {
    const opened = await openActivity(browser, [
      settledRun(1, 'succeeded', '追更检查'), settledRun(2, 'failed', '扫描与采集'),
      settledRun(3, 'cancelled', '批量操作'), settledRun(4, 'pending', '命令行批处理'),
    ]);
    try {
      const badge = (status: string) => opened.page.locator(`li[data-status="${status}"] span`).first()
        .evaluate((element) => getComputedStyle(element).backgroundColor);
      const token = (name: string) => tokenColor(opened.page, '.peach-react', name);
      assert.equal(await badge('succeeded'), await token('--color-status-lime-background'));
      assert.equal(await badge('failed'), await token('--color-status-rose-background'));
      assert.equal(await badge('cancelled'), await token('--color-status-yellow-background'));
      // 第四种状态不另给颜色：三档之外都读同一个中性底，颜色才还说得出「成功／失败／被叫停」。
      assert.equal(await badge('pending'), await token('--color-background-tertiary-default'));
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('高清版卡片的封面是 150px 宽的 16/10 方块，长标题从头显示、最多两行', { timeout: 60_000 }, async () => {
    const long = '这是一个长到必须省略才放得下的文件名，用来盯住标题从头显示而番号不被截掉.mp4';
    const opened = await openQualityGoals(browser, [
      qualityGoal(1, long), qualityGoal(2, 'short.mp4'),
    ]);
    try {
      const cover = opened.page.locator('li[data-goal-id="1"] button').first();
      const box = await cover.evaluate((element) => ({
        width: getComputedStyle(element).width,
        ratio: getComputedStyle(element).aspectRatio,
      }));
      assert.equal(box.width, '150px');
      assert.equal(box.ratio.replaceAll(' ', ''), '16/10');
      const title = opened.page.locator('li[data-goal-id="1"] h3 button');
      await title.waitFor({ timeout: 5_000 });
      const shown = await title.evaluate((element) => ({
        clamp: getComputedStyle(element).webkitLineClamp,
        middle: element.classList.contains('middle-truncated') || element.hasAttribute('data-middle-truncate'),
        text: element.textContent,
        hint: element.getAttribute('title'),
      }));
      assert.equal(shown.clamp, '2');
      assert.equal(shown.middle, false, '标题仍被中间截断');
      assert.equal(shown.text, long);
      assert.equal(shown.hint, long);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('一张来源卡底下只有写入那一颗是主按钮，其余次级', { timeout: 60_000 }, async () => {
    const opened = await openScraping(browser);
    try {
      const card = opened.page.locator('form[aria-label="演示来源甲"]');
      const fill = (name: string) => card.getByRole('button', { name, exact: true })
        .evaluate((element) => getComputedStyle(element).backgroundColor);
      // 收 Cookie 且已存了一份的来源，底下三颗：撤销、检查、保存；只有保存是写入。
      assert.equal(await fill('保存'), await tokenColor(opened.page, '.peach-react', '--color-button-primary'));
      const secondary = await tokenColor(opened.page, '.peach-react', '--color-background-primary-default');
      assert.equal(await fill('检查连接'), secondary, '检查连接被画成了主按钮');
      assert.equal(await fill('撤销 Cookie'), secondary, '撤销 Cookie 被画成了主按钮');
      // 没存过 Cookie 的来源没有可撤的对象，那一颗不画。
      const plain = opened.page.locator('form[aria-label="演示来源乙"]');
      assert.equal(await plain.getByRole('button', { name: '撤销 Cookie', exact: true }).count(), 0);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('常驻任务面在运行时使用中性色，失败时使用状态红并跨页保留', { timeout: 60_000 }, async () => {
    const running = await openProcessing(browser, '/', RUNNING_JOB);
    try {
      const banner = running.page.locator('#toasts [data-processing-dock][role="status"]');
      await banner.waitFor({ state: 'visible', timeout: 15_000 });
      assert.match(await banner.innerText(), /采集缺失资料 · 38 \/ 100/);
      // 一圈长度钉成 100，画出来的那一段就是百分比本身。
      assert.equal(await banner.locator('[role="progressbar"]').getAttribute('aria-valuenow'), '38');
      // 判据是「不是语气色」，不钉某一个具体的底：在跑那条走中性底，黄与红留给出事的时候。
      const tint = await banner.evaluate((element) => getComputedStyle(element).backgroundColor);
      for (const tone of ['--color-background-tertiary-error', '--color-status-yellow-background']) {
        assert.notEqual(tint, await tokenColor(running.page, '.peach-react', tone),
          '任务在跑是正在发生的事，配上状态底色就和「出事了」一个分量');
      }
    } finally {
      await running.close();
    }

    const failed = await openProcessing(browser, '/', { status: 'failed', error: '来源离线' });
    try {
      const banner = failed.page.locator('#toasts [data-processing-dock][role="alert"]');
      await banner.waitFor({ state: 'visible', timeout: 15_000 });
      /* 计算值现在是 `oklab()`／`oklch()`，两种记法之间没法直接比字符串，所以统一在
         canvas 上取回 RGBA 再比。 */
      const readSurface = () => banner.evaluate((element) => {
        const style = getComputedStyle(element);
        const context = document.createElement('canvas').getContext('2d')!;
        const paint = (color: string) => {
          context.clearRect(0, 0, 1, 1);
          context.fillStyle = color;
          context.fillRect(0, 0, 1, 1);
          return [...context.getImageData(0, 0, 1, 1).data];
        };
        return { base: paint(style.backgroundColor), tint: style.backgroundImage,
          line: paint(style.borderTopColor), ink: paint(style.color),
          button: getComputedStyle(element.querySelector('button[aria-expanded]')!).backgroundColor,
          link: getComputedStyle(element.querySelector('a')!).color };
      });
      for (const theme of ['light', 'dark']) {
        await failed.page.evaluate(value => {
          document.documentElement.dataset.theme = value;
          document.documentElement.classList.toggle('dark', value === 'dark');
        }, theme);
        const surface = await readSurface();
        const errorTint = await tokenColor(failed.page, '.peach-react', '--color-background-tertiary-error');
        assert.equal(surface.base[3], 255, '任务卡的底层不透明');
        assert.equal(surface.tint, `linear-gradient(${errorTint}, ${errorTint})`);
        assert.equal(surface.button, await tokenColor(failed.page, '.peach-react', '--color-background-primary-default'));
        assert.equal(surface.link, await tokenColor(failed.page, '.peach-react', '--color-text-secondary'));
        for (const at of [0, 1, 2]) {
          assert.ok(Math.abs(surface.line[at]! - surface.ink[at]!) <= 2,
            `横幅的线没跟着语气走：线 ${surface.line} 与字 ${surface.ink} 不是同一个色`);
        }
        assert.ok(surface.line[3]! < surface.ink[3]!, '状态分隔线比正文更淡');
      }
      // 数据管理子页继续保留同一趟尚未处理的失败提示。
      await failed.page.goto(new URL('/junk-files', failed.page.url()).href, { waitUntil: 'load' });
      await failed.page.locator('[data-junk-filters]').waitFor({ state: 'visible', timeout: 15_000 });
      const retainedFailure = failed.page.locator('#toasts [data-processing-dock][role="alert"]');
      await retainedFailure.waitFor({ state: 'visible', timeout: 15_000 });
      assert.equal(await retainedFailure.count(), 1,
        '未处理的失败在换页后仍可找到');
    } finally {
      await failed.close();
    }
  });

  it('扫描卡的进度条走焦点环色，底槽是三级底，按下的那颗键转成忙态', { timeout: 60_000 }, async () => {
    const opened = await openProcessing(browser, '/data-cleanup', RUNNING_JOB);
    try {
      const card = opened.page.locator('section[aria-label="扫描与采集"]');
      await card.waitFor({ state: 'visible', timeout: 15_000 });
      const bar = card.locator('[role="progressbar"]');
      assert.equal(await bar.getAttribute('aria-valuenow'), '38');
      assert.equal(await bar.getAttribute('aria-valuemax'), '100');
      const fill = (at: number) =>
        bar.locator('rect').nth(at).evaluate((element) => getComputedStyle(element).fill);
      assert.equal(await fill(0),
        await tokenColor(opened.page, '.peach-react', '--color-background-tertiary-default'));
      assert.equal(await fill(1),
        await tokenColor(opened.page, '.peach-react', '--color-border-focus-ring'));
      // 忙态不改 `disabled`：控件仍可聚焦，重复触发由页面自己挡。
      const scan = card.getByRole('button', { name: '同步媒体库', exact: true });
      assert.equal(await scan.getAttribute('aria-busy'), 'true');
      assert.equal(await scan.evaluate((element) => (element as HTMLButtonElement).disabled), false,
        '用原生 disabled 挡的话按钮连焦点都拿不到');
      const split = card.locator('[data-split-button]');
      const parts = split.locator(':scope > button');
      assert.equal(await parts.count(), 2, '拆分按钮没有保持主操作与菜单两区');
      const boxes = await parts.evaluateAll((buttons) => buttons.map((button) => {
        const rect = button.getBoundingClientRect();
        return { left: rect.left, right: rect.right, height: rect.height };
      }));
      assert.ok(Math.abs(boxes[0]!.right - boxes[1]!.left) < 0.5,
        '拆分按钮两区之间留了空隙');
      assert.equal(boxes[0]!.height, boxes[1]!.height, '拆分按钮两区高度不一致');
      const divider = await parts.nth(1).evaluate((element) => {
        const style = getComputedStyle(element, '::after');
        return { width: style.width, color: style.backgroundColor };
      });
      assert.equal(divider.width, '1px', '拆分按钮分隔线宽度不对');
      assert.notEqual(divider.color, 'rgba(0, 0, 0, 0)', '拆分按钮分隔线没有颜色');
    } finally {
      await opened.close();
    }
  });

  it('拆分按钮两半各自抬填充：悬停哪半只有哪半变，中缝那条线不动', { timeout: 60_000 }, async () => {
    // Geist 实测（`vercel-geist-split-button.md`「悬停」）：两半各是一颗有自己底色的按钮，
    // 悬停只抬指针下那一颗；分隔线是触发档 `::before`，`left:-1px` 盖在主动作最后一列上，
    // 高度顶满、颜色不随悬停变。
    const opened = await openProcessing(browser, '/data-cleanup', { status: 'idle' });
    try {
      // 骨架那张卡同名同结构，等到 React 接管、两半不再带骨架标记才量。
      const split = opened.page.locator('section[aria-label="扫描与采集"] [data-split-button]:not(:has(> [data-skeleton-action]))');
      await split.waitFor({ state: 'visible', timeout: 15_000 });
      await settle(opened.page);
      const parts = split.locator(':scope > button');
      const faces = () => parts.evaluateAll((buttons) => buttons.map((button) => {
        const style = getComputedStyle(button), lift = getComputedStyle(button, '::before');
        return { fill: style.backgroundImage, lift: lift.opacity, ink: style.color };
      }));
      const seam = () => parts.nth(1).evaluate((element) => {
        const line = getComputedStyle(element, '::after');
        return { color: line.backgroundColor, width: line.width, left: line.left,
          height: line.height, full: `${element.getBoundingClientRect().height}px`,
          clip: getComputedStyle(element).overflowX };
      });
      assert.equal(await split.evaluate((element) => getComputedStyle(element).backgroundImage), 'none',
        '底色画在整组上，两半就没法各自抬填充');
      await opened.page.mouse.move(0, 0);
      const rest = await faces();
      assert.match(rest[0]!.fill, /gradient/, '主动作那半没有自己的蓝色填充');
      assert.equal(rest[1]!.fill, rest[0]!.fill, '两半静止时不是同一档填充');
      assert.deepEqual(rest.map((face) => face.lift), ['0', '0'], '没悬停就抬了填充');
      const line = await seam();
      assert.equal(line.width, '1px');
      assert.equal(line.left, '-1px', '分隔线该盖在主动作最后一列上，不占触发档的宽度');
      assert.equal(line.height, line.full, '分隔线没有上下顶满');
      assert.equal(line.clip, 'visible', '触发档把伸到左邻上的分隔线裁掉了，屏幕上看不见');
      assert.notEqual(line.color, 'rgba(0, 0, 0, 0)');
      for (const [at, other] of [[0, 1], [1, 0]] as const) {
        await parts.nth(at).hover();
        // 悬停层按 150ms 淡入淡出，直接读会落在半路；走到终点再比。
        await opened.page.evaluate(() => document.getAnimations().forEach((animation) => animation.finish()));
        const hovered = await faces();
        assert.equal(hovered[at]!.lift, '1', `悬停第 ${at + 1} 半没有抬填充`);
        assert.equal(hovered[other]!.lift, '0', `悬停第 ${at + 1} 半时另一半也跟着变了`);
        assert.deepEqual(hovered.map((face) => face.ink), rest.map((face) => face.ink), '悬停改了字色');
        assert.deepEqual(await seam(), line, '悬停时分隔线变了');
      }
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('失败提示里重试键靠右，问题清单铺满提示并走覆盖式滚动条', { timeout: 60_000 }, async () => {
    const opened = await openProcessing(browser, '/data-cleanup', FAILED_JOB);
    try {
      const alert = opened.page.locator('#libraryProcessing [role="alert"]');
      await expectBody(opened.page, '/data-cleanup 处理失败提示', [alert], opened.problems);
      await alert.locator('summary').click();
      const list = alert.locator('ul');
      await list.waitFor({ state: 'visible', timeout: 5_000 });
      const geometry = await alert.evaluate((element) => {
        const note = element.getBoundingClientRect();
        const button = element.querySelector('button')!.getBoundingClientRect();
        const items = element.querySelector('ul')!;
        return {
          padding: parseFloat(getComputedStyle(element).paddingRight),
          noteRight: note.right, buttonRight: button.right,
          listRight: items.getBoundingClientRect().right,
          client: items.clientWidth, offset: items.offsetWidth,
          track: !!element.querySelector('.ovtrack'),
        };
      });
      assert.ok(geometry.noteRight - geometry.buttonRight - geometry.padding < 1,
        '重试键没有贴着提示的右内边，读起来就不是这条提示的主动作');
      assert.ok(geometry.noteRight - geometry.listRight - geometry.padding < 1,
        '问题清单没有铺满提示的宽度');
      assert.equal(geometry.client, geometry.offset, '原生滚动条还占着清单右边一列');
      assert.ok(geometry.track, '清单没有挂上全站那条覆盖式滚动条');
      /* 同一块红底上叠着三段：结论、清单、完整记录。条与条之间要看得见界，完整记录
         要退回灰字——它不是这条提示在说的事，是出事之后自己去翻的东西。 */
      const layering = await alert.evaluate((element) => {
        const context = document.createElement('canvas').getContext('2d')!;
        const paint = (color: string) => {
          context.clearRect(0, 0, 1, 1);
          context.fillStyle = color;
          context.fillRect(0, 0, 1, 1);
          return [...context.getImageData(0, 0, 1, 1).data];
        };
        // `divide-y` 把线画在每条的下缘（末条除外），所以量的是第一条的下边。
        const first = element.querySelectorAll('li')[0]!;
        const log = element.querySelector('details p')!;
        return {
          divider: parseFloat(getComputedStyle(first).borderBottomWidth),
          dividerInk: paint(getComputedStyle(first).borderBottomColor),
          logInk: paint(getComputedStyle(log).color),
          bodyInk: paint(getComputedStyle(element).color),
        };
      });
      assert.ok(layering.divider > 0, '两条明细之间没有界，几十条连成一片');
      assert.ok(layering.dividerInk[3]! < layering.bodyInk[3]!, '条间的线和正文一样实');
      assert.ok([0, 1, 2].some((at) => Math.abs(layering.logInk[at]! - layering.bodyInk[at]!) > 8),
        '完整记录还跟着提示是红的，读起来像又出了一件事');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('新窗口打开的来源地址两项 rel 都写：不带走会话，也不带走来处', { timeout: 60_000 }, async () => {
    const opened = await openScraping(browser);
    try {
      const link = opened.page.locator('form[aria-label="演示来源甲"] a[target="_blank"]');
      assert.equal(await link.getAttribute('href'), 'https://demoa.example/login');
      assert.equal((await link.getAttribute('rel'))!.split(/\s+/).sort().join(' '), 'noopener noreferrer');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('顶栏版式键保留两枚字形并原地换态', { timeout: 60_000 }, async () => {
    const opened = await openCatalog(browser);
    try {
      await opened.page.emulateMedia({ reducedMotion: 'no-preference' });
      const button = opened.page.locator('#density');
      const swap = button.locator('[data-icon-swap]');
      await swap.waitFor({ timeout: 5_000 });
      assert.equal(await swap.locator('[data-icon]').count(), 2);
      const before = await swap.getAttribute('data-icon-state');
      await swap.evaluate((element) => { element.setAttribute('data-test-identity', 'density-swap'); });
      await button.click();
      assert.notEqual(await swap.getAttribute('data-icon-state'), before,
        '按下后仍停在同一枚字形');
      assert.equal(await swap.locator('[data-icon]').count(), 2,
        '换态不应销毁其中一枚字形');
      await button.click();
      assert.equal(await swap.getAttribute('data-icon-state'), before, '第二次按下没有回到原字形');
      assert.equal(await button.locator('[data-icon-swap][data-test-identity="density-swap"]').count(), 1,
        '两次换态之间重建了字形容器');
    } finally {
      await opened.close();
    }
  });

  it('读数首次静态落笔，变值时每一位都有有效动画', { timeout: 60_000 }, async () => {
    const opened = await openCatalogFixture(browser, (payload, url) => {
      payload.total = url.searchParams.has('q') ? 34 : 12;
    });
    try {
      await opened.page.emulateMedia({ reducedMotion: 'no-preference' });
      const result = await opened.page.evaluate(async () => {
        const { popCount } = await import('/dist/peach-app.js');
        const host = document.createElement('span');
        document.body.append(host);
        popCount(host, '12');
        const firstWasStatic = !host.firstElementChild?.classList.contains('popping');
        popCount(host, '34');
        const digit = host.querySelector<HTMLElement>('.digits.popping > span');
        const style = digit && getComputedStyle(digit);
        const answer = {
          firstWasStatic,
          animationName: style?.animationName || '',
          duration: style?.animationDuration || '',
        };
        host.remove();
        return answer;
      });
      assert.equal(result.firstWasStatic, true, '首次写入不应弹动');
      assert.equal(result.animationName, 'digit-pop-in');
      assert.equal(result.duration, '0.25s');

      const input = opened.page.locator('#q');
      await opened.page.evaluate(() => {
        document.documentElement.removeAttribute('data-count-animation');
        const count = document.querySelector('[data-catalog-filter]');
        const observer = new MutationObserver(() => {
          const digit = count?.querySelector<HTMLElement>('[data-count-readout] .digits.popping > span');
          if (!digit) return;
          const style = getComputedStyle(digit);
          document.documentElement.dataset.countAnimation = JSON.stringify({
            animationName: style.animationName,
            duration: style.animationDuration,
          });
          observer.disconnect();
        });
        observer.observe(count!, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
      });
      await input.fill('读数变值');
      await input.press('Enter');
      await opened.page.waitForFunction(() =>
        document.querySelector('[data-catalog-filter] [data-count-readout]')?.textContent?.includes('34 个符合'));
      /* 读数更新后还可能因自动续页再重画。在触发前观察真实节点，既能验收
         动画的计算值，也不把断言绑在之后某一次采样恰好撞上短暂节点。 */
      await opened.page.waitForFunction(() =>
        document.documentElement.hasAttribute('data-count-animation'), undefined, { timeout: 5_000 });
      const liveAnimation = await opened.page.evaluate(() =>
        JSON.parse(document.documentElement.dataset.countAnimation || '{}') as {
          animationName?: string; duration?: string;
        });
      assert.equal(liveAnimation.animationName, 'digit-pop-in', '真实读数节点变值后没有播放动画');
      assert.equal(liveAnimation.duration, '0.25s');
    } finally {
      await opened.close();
    }
  });

  it('五枚叠放头像只朝标题方向展开，左缘和窄卡边界不动', { timeout: 60_000 }, async () => {
    const names = Array.from({ length: 7 }, (_, index) => `演示演员 ${index + 1}`);
    const opened = await openCatalogFixture(browser, (payload) => {
      const item = payload.items[0];
      if (!item) throw new Error('演示目录没有可替换的卡片');
      item.creator = '';
      item.performers = names;
      item.performer_total = names.length;
      item.performer_entities = names.map((name, index) => ({ id: 90_000 + index, name, has_image: false }));
    });
    try {
      await opened.page.emulateMedia({ reducedMotion: 'no-preference' });
      const stack = opened.page.locator('[data-media-card][data-id] [data-media-avatars]').first();
      await stack.waitFor({ timeout: 5_000 });
      const avatars = stack.locator('[data-media-avatar]');
      assert.equal(await avatars.count(), 5, 'API 给七位表演者时卡片没有收在五枚以内');
      const before = await avatars.evaluateAll((items) => items.map((item) => item.getBoundingClientRect().x));
      const left = (await stack.boundingBox())!.x;
      const waitForAvatarMotion = () => stack.evaluate(async (element) => {
        const animations = element.getAnimations({ subtree: true });
        await Promise.all(animations.map((animation) => animation.finished.catch(() => undefined)));
      });

      await avatars.first().hover();
      await waitForAvatarMotion();
      const firstSpread = await avatars.evaluateAll((items) => items.map((item) => item.getBoundingClientRect().x));
      for (let index = 1; index < firstSpread.length; index += 1) {
        assert.ok(firstSpread[index] > before[index], `首枚悬停时第 ${index + 1} 枚没有向标题方向展开`);
      }

      /* 第三枚要从静止位置指上去：首枚悬停时它被推开 20px，直接移过去，它让回原位后指针会落到右邻上。 */
      await opened.page.mouse.move(0, 0);
      await waitForAvatarMotion();
      await avatars.nth(2).hover();
      await waitForAvatarMotion();
      const after = await avatars.evaluateAll((items) => items.map((item) => item.getBoundingClientRect().x));
      const card = stack.locator('xpath=ancestor::article[@data-media-card]').first();
      const bounds = await card.evaluate((cardElement) => {
        const stackRect = cardElement.querySelector('[data-media-avatars]')!.getBoundingClientRect();
        const lastRect = cardElement.querySelector('[data-media-avatar]:last-child')!.getBoundingClientRect();
        const firstRect = cardElement.querySelector('[data-media-avatar]')!.getBoundingClientRect();
        const cardRect = cardElement.getBoundingClientRect();
        return {
          stackLeft: stackRect.left,
          firstRingLeft: firstRect.left - 2,
          lastRingRight: lastRect.right + 2,
          cardLeft: cardRect.left,
          cardRight: cardRect.right,
        };
      });
      assert.equal(bounds.stackLeft, left, '展开时左缘发生位移');
      // Chrome 合成层结束 transition 时会留下远小于一个物理像素的浮点残差；这里守的是
      // 头像没有发生可见位移，不把 303 与 302.9933 这种同一像素内的取整差判成布局回退。
      assert.ok(Math.abs(after[0] - before[0]) <= .5, '指向第三枚时第一枚被往左推');
      assert.ok(Math.abs(after[1] - before[1]) <= .5, '指向第三枚时第二枚被往左推');
      assert.ok(after[3] > before[3] && after[4] > before[4], '右侧邻座没有朝标题方向让开');
      assert.ok(bounds.firstRingLeft >= bounds.cardLeft - .5, '首枚放大加描边后被卡片左缘裁切');
      assert.ok(bounds.lastRingRight <= bounds.cardRight + .5, '头像展开越出卡片右缘');

      await opened.page.mouse.move(0, 0);
      await avatars.nth(2).focus();
      await waitForAvatarMotion();
      const layers = await avatars.evaluateAll((items) => items.map((item) => Number(getComputedStyle(item).zIndex)));
      assert.equal(layers[2], Math.max(...layers), '键盘焦点所在头像没有升到最高层');

      await opened.page.setViewportSize({ width: 390, height: 844 });
      const drawer = opened.page.locator('#drawer');
      if (await drawer.evaluate((element) => element.classList.contains('open'))) {
        await opened.page.locator('#scrim').evaluate((element) => (element as HTMLElement).click());
        await opened.page.waitForFunction(() => !document.querySelector('#drawer')?.classList.contains('open'));
      }
      await avatars.first().hover();
      await waitForAvatarMotion();
      const narrow = await card.evaluate((cardElement) => {
        const cardRect = cardElement.getBoundingClientRect();
        const firstRect = cardElement.querySelector('[data-media-avatar]')!.getBoundingClientRect();
        const lastRect = cardElement.querySelector('[data-media-avatar]:last-child')!.getBoundingClientRect();
        let clip: Element | null = cardElement.parentElement;
        while (clip && clip !== document.documentElement) {
          const style = getComputedStyle(clip);
          if (style.overflowX !== 'visible' || style.overflowY !== 'visible') break;
          clip = clip.parentElement;
        }
        const clipRect = (clip || document.documentElement).getBoundingClientRect();
        return {
          firstRingLeft: firstRect.left - 2,
          lastRingRight: lastRect.right + 2,
          cardLeft: cardRect.left,
          cardRight: cardRect.right,
          clipLeft: clipRect.left,
          clipRight: clipRect.right,
          viewport: document.documentElement.clientWidth,
        };
      });
      assert.ok(narrow.firstRingLeft >= Math.max(narrow.cardLeft, narrow.clipLeft) - .5,
        '390px 视口下首枚头像或描边被最近的裁切祖先截掉');
      assert.ok(narrow.lastRingRight <= Math.min(narrow.cardRight, narrow.clipRight, narrow.viewport) + .5,
        '390px 视口下展开头像越出卡片、裁切祖先或视口');
    } finally {
      await opened.close();
    }
  });

  it('搜索框从非空变空时立即清值并溶解原内容', { timeout: 60_000 }, async () => {
    const opened = await openCatalog(browser);
    try {
      await opened.page.emulateMedia({ reducedMotion: 'no-preference' });
      const input = opened.page.locator('#q');
      const text = '一段长到会在搜索框里横向滚动的内容 0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ '.repeat(4).trim();
      await input.fill(text);
      await input.evaluate((element) => {
        const field = element as HTMLInputElement;
        field.setSelectionRange(field.value.length, field.value.length);
        field.scrollLeft = field.scrollWidth;
        field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
      });
      assert.ok(await input.evaluate((element) => (element as HTMLInputElement).scrollLeft) > 0,
        '测试内容没有真正让搜索框横向滚动');
      await input.fill('');
      const ghost = opened.page.locator('.search > .cleardissolve');
      await ghost.waitFor({ timeout: 1_000 });
      assert.equal(await input.inputValue(), '', '动画阻塞了真实输入框清空');
      assert.equal(await ghost.locator('[data-dissolve-value]').textContent(), text);
      // 残影节点先挂载，placeholder 再经 CSS transition 淡出；慢速 Windows runner 上
      // 两件事可能跨帧。等待过渡的最终状态，仍然守住 placeholder 必须完全不可见。
      await opened.page.waitForFunction(() => {
        const field = document.querySelector('#q');
        return field !== null && getComputedStyle(field, '::placeholder').opacity === '0';
      }, undefined, { timeout: 1_000 });
      assert.match(await ghost.locator('[data-dissolve-value]').getAttribute('style') || '', /translateX\(-\d+px\)/);
      await ghost.waitFor({ state: 'detached', timeout: 2_000 });
      assert.equal(await input.evaluate((element) => element.classList.contains('dissolving')), false,
        '动画结束后仍压着输入框的溶解状态');

      await input.fill(text);
      await input.fill('');
      await opened.page.locator('.search > .cleardissolve').waitFor({ timeout: 1_000 });
      await input.type('新输入');
      assert.equal(await opened.page.locator('.search > .cleardissolve').count(), 0,
        '清空后继续输入仍被旧残影覆盖');
      assert.equal(await input.inputValue(), '新输入');

      await input.fill('输入法候选');
      await input.fill('');
      await opened.page.locator('.search > .cleardissolve').waitFor({ timeout: 1_000 });
      await input.evaluate((element) => element.dispatchEvent(new CompositionEvent('compositionstart', {
        bubbles: true,
        data: '候',
      })));
      assert.equal(await opened.page.locator('.search > .cleardissolve').count(), 0,
        '输入法开始组字后旧残影仍覆盖候选字');
    } finally {
      await opened.close();
    }
  });

  /* 搜索框住在粘性顶栏里，永远在视口上沿；可 `html` 的 `scroll-padding-top` 把那一条
     划成「被顶栏盖住」的区域，浏览器每敲一个字就把光标往下滚一次，页面一路往回退。
     点击走鼠标坐标：定位器的 click 会先替元素滚进视口，那一下不是页面自己的行为。 */
  it('往下翻过之后在搜索框里打字，页面停在原处', { timeout: 60_000 }, async () => {
    const opened = await openCatalog(browser);
    try {
      const { page } = opened;
      await page.setViewportSize({ width: DESKTOP.width, height: 480 });
      await page.mouse.move(DESKTOP.width / 2, 300);
      await page.mouse.wheel(0, 600);
      await page.waitForFunction(() => scrollY > 300, undefined, { timeout: 5_000 });
      const before = await page.evaluate(() => scrollY);
      const box = await page.locator('#q').boundingBox();
      if (!box) throw new Error('搜索框不可见');
      await page.mouse.click(box.x + 40, box.y + box.height / 2);
      await page.keyboard.type('演示abc', { delay: 60 });
      await page.waitForTimeout(300);
      assert.equal(await page.evaluate(() => scrollY), before, '打字时页面跟着滚动了');
    } finally {
      await opened.close();
    }
  });

  it('首页版式默认小图，大图统一作品画面框，选择单独记住', { timeout: 60_000 }, async () => {
    /* 首张卡使用番号封面，第二张使用普通预览图。 */
    const opened = await openCatalogFixture(browser, (payload) => {
      Object.assign(payload.items[0], { is_jav: true, code: 'ABC-123', display_code: 'ABC-123', has_cover: true });
    });
    const { page } = opened;
    const ratio = (index: number) => page.locator('#grid [data-media-grid] > [data-media-card][data-id]').nth(index).locator('[data-media-pic]')
      .evaluate((element) => element.getBoundingClientRect().width / element.getBoundingClientRect().height);
    const choice = (value: string) => page.locator(`[data-catalog-filter] input[name="home-layout"][value="${value}"]`);
    try {
      assert.equal(await choice('small').isChecked(), true, '首页版式默认不是小图');
      assert.ok(Math.abs(await ratio(0) - 16 / 9) < 0.05, '小图下番号卡不是 16:9');
      await choice('big').check({ force: true });
      assert.ok(Math.abs(await ratio(0) - 0.75) < 0.05, '大图下番号卡没有拉成正封比例');
      assert.ok(Math.abs(await ratio(1) - 0.75) < 0.05, '大图下普通作品画面框不是 3:4');
      const box = (index: number) => page.locator('#grid [data-media-grid] > [data-media-card][data-id]').nth(index).locator('[data-media-pic]')
        .evaluate((element) => ({ top: element.getBoundingClientRect().top, height: element.getBoundingClientRect().height }));
      const [jav, plain] = [await box(0), await box(1)];
      assert.ok(Math.abs(jav.top - plain.top) < 1, '前两张卡不在同一行，量不到撑高');
      assert.ok(Math.abs(plain.height - jav.height) < 1, '同行作品画面框不等高');
      // `visit()` 的初始化脚本每次导航都重写设置，刷新验不了；直接读存下来的那份。
      const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('peach.settings.v1') || '{}'));
      assert.equal(saved.homeLayout, 'big', '首页版式没有存下来');
    } finally {
      await opened.close();
    }
  });

  it('首页标签：没加上去是 --field-ring-hover 的虚线，加上去是玻璃上那圈实线，视图不画虚线；触屏上排 48、视图与标签 30', { timeout: 60_000 }, async () => {
    /* 虚线只说「这条筛选可加、此刻没加」，取 Vercel 筛选令牌未生效那一档；四枚视图恒有一枚生效，不画。
       圆角同卡片、详情与交集条那一颗（`--tag-radius`）。触屏控件共用 30px 高度。 */
    const opened = await openCatalogBars(browser);
    try {
      const { page } = opened;
      const scope = '[data-catalog-filter]';
      const ring = await tokenColor(page, scope, '--field-ring-hover');
      const low = await tokenColor(page, scope, '--glass-low');
      const edge = (selector: string) => page.locator(`${scope} ${selector}`).first().evaluate((element) => {
        const style = getComputedStyle(element);
        const probe = document.createElement('div');
        probe.style.borderRadius = 'var(--tag-radius)';
        element.append(probe);
        const radius = getComputedStyle(probe).borderTopLeftRadius;
        probe.remove();
        return { style: style.borderTopStyle, color: style.borderTopColor, radius: style.borderTopLeftRadius === radius };
      });
      assert.deepEqual(await edge('[data-catalog-tag][aria-pressed="false"]'), { style: 'dashed', color: ring, radius: true },
        '没加上去的标签不是 --field-ring-hover 的虚线，或圆角不是 --tag-radius');
      assert.notEqual((await edge('[data-catalog-view]')).style, 'dashed', '视图恒有一枚生效，不画虚线');
      await page.locator(`${scope} [data-catalog-tag][aria-pressed="false"]`).first().click();
      await page.locator(`${scope} [data-catalog-tag][aria-pressed="true"]`).waitFor({ timeout: 5_000 });
      assert.deepEqual(await edge('[data-catalog-tag][aria-pressed="true"]'), { style: 'solid', color: low, radius: true },
        '加上去的标签那圈线不是玻璃上的 --glass-low');
    } finally {
      await opened.close();
    }
    const phone = await openCatalogBars(browser, MOBILE);
    try {
      const { page } = phone;
      const heights = await page.evaluate(() => {
        const height = (selector: string) => {
          const element = document.querySelector(`[data-catalog-filter] ${selector}`);
          return element ? Math.round(element.getBoundingClientRect().height) : null;
        };
        return { coarse: matchMedia('(pointer: coarse)').matches, row: height('[data-filter-row="top"]'),
          view: height('[data-catalog-view]'), tag: height('[data-catalog-tag]') };
      });
      assert.deepEqual(heights, { coarse: true, row: 48, view: 30, tag: 30 }, '触屏筛选按钮高度不一致');
    } finally {
      await phone.close();
    }
  });

  it('首页换一批：头像、厂牌与标签原地藏起来只露一层微光，名字条收成 52px，四枚视图不盖', { timeout: 60_000 }, async () => {
    /* 框就是它们自己的框，零位移；`visibility:hidden` 的控件也不可聚焦。视图由 state 决定，这一趟不改它们。 */
    const opened = await openCatalogBars(browser);
    let release = () => {};
    try {
      const { page } = opened;
      const held = new Promise<void>((resolve) => { release = resolve; });
      // 后注册的先拦：拖住这一趟，放行后交回夹具那一份。
      await page.route((url) => url.pathname === '/api/tops', async (route) => { await held; await route.fallback(); });
      await page.locator('[data-catalog-filter] [data-entity-batch]').click();
      await page.locator('[data-catalog-filter] [data-catalog-root][data-refreshing]').waitFor({ timeout: 5_000 });
      const shown = await page.evaluate(() => {
        const read = (selector: string) => {
          const element = document.querySelector(`[data-catalog-filter] ${selector}`);
          if (!element) return null;
          return { hidden: getComputedStyle(element).visibility, sheen: getComputedStyle(element, '::after').visibility,
            width: Math.round(element.getBoundingClientRect().width) };
        };
        return { tag: read('[data-catalog-tag]'), ring: read('[data-tier-ring]'), name: read('[data-tier-name]'),
          studio: read('[data-tier-studio]'), view: read('[data-catalog-view]') };
      });
      for (const key of ['tag', 'ring', 'name', 'studio'] as const) {
        assert.equal(shown[key]?.hidden, 'hidden', `换一批时${key}没有藏起来：${JSON.stringify(shown[key])}`);
        assert.equal(shown[key]?.sheen, 'visible', `换一批时${key}上没有那层微光：${JSON.stringify(shown[key])}`);
      }
      assert.equal(shown.name?.width, 52, '名字条没有收成首屏骨架那一宽');
      assert.equal(shown.view?.hidden, 'visible', '视图不随这一趟变，不该盖');
    } finally {
      release();
      await opened.close();
    }
  });
});
