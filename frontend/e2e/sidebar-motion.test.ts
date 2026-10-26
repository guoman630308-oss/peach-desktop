/* 侧栏开合的运行期约束：过渡中不重画玻璃贴图，停下后按终态尺寸补画，主区落在让出的位置上。
 *
 * 冒烟默认按减少动态效果打开页面，那时过渡是 0s，这些约束无从观察；这里在页面里切回有动效。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { expectBody, launch, settle, visit, VIEWPORTS } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;

interface Toggle {
  /** 第一段过渡结束那一刻，已经画过几张位移贴图。 */
  mapsDuring: number;
  /** 两段过渡都结束、补画之后，一共画过几张。 */
  mapsAfter: number;
  opticDuring: string;
  opticAfter: string;
  open: boolean;
  drawerWidth: number;
  drawerRight: number;
  mainLeft: number;
  mainRight: number;
  railW: number;
  viewport: number;
  /** 点击后 300ms（过渡的时长）里出了几帧，首帧算点击那一刻。 */
  frames: number;
}

/** 点一下左上角的切换键，等 `body` 的 `padding-left` 与抽屉的 `width` 两段过渡都停下。 */
async function toggle(page: Page): Promise<Toggle> {
  return page.evaluate(() => new Promise<Toggle>((resolve, reject) => {
    const counter = window as unknown as { __maps: number };
    const drawer = document.querySelector<HTMLElement>('#drawer')!;
    const start = counter.__maps;
    const pending = new Set(['padding-left', 'width']);
    let mapsDuring = -1;
    let opticDuring = '';
    const timer = setTimeout(() => reject(new Error(`过渡没有结束，还差 ${[...pending].join('、')}`)), 5_000);
    /* 帧数从点击那一刻数到 300ms：主线程被一次整页重排占住多久，这段里就少出多少帧。 */
    let clicked = 0;
    let frames = 1;
    let counting = true;
    const tick = (now: number) => {
      if (!counting) return;
      if (now - clicked <= 300) frames++;
      requestAnimationFrame(tick);
    };
    const window300 = new Promise<void>((done) => setTimeout(done, 320));
    const onEnd = (event: TransitionEvent) => {
      const key = event.target === document.body && event.propertyName === 'padding-left' ? 'padding-left'
        : event.target === drawer && event.propertyName === 'width' ? 'width' : '';
      if (!key || !pending.delete(key)) return;
      if (mapsDuring < 0) {
        mapsDuring = counter.__maps - start;
        opticDuring = drawer.style.getPropertyValue('--glass-optic');
      }
      if (pending.size) return;
      clearTimeout(timer);
      window.removeEventListener('transitionend', onEnd, true);
      void window300.then(() => requestAnimationFrame(() => requestAnimationFrame(() => {
        counting = false;
        const drawerBox = drawer.getBoundingClientRect();
        const mainBox = document.querySelector('#main')!.getBoundingClientRect();
        resolve({
          mapsDuring,
          mapsAfter: counter.__maps - start,
          opticDuring,
          opticAfter: drawer.style.getPropertyValue('--glass-optic'),
          open: drawer.classList.contains('open'),
          drawerWidth: Math.round(drawerBox.width),
          drawerRight: drawerBox.right,
          mainLeft: mainBox.left,
          mainRight: mainBox.right,
          railW: parseFloat(getComputedStyle(document.body).getPropertyValue('--railW')),
          viewport: document.documentElement.clientWidth,
          frames,
        });
      })));
    };
    // 挂在 window 的捕获阶段：比页面挂在 document 上的补画先拿到这一次结束。
    window.addEventListener('transitionend', onEnd, true);
    clicked = performance.now();
    requestAnimationFrame(tick);
    document.querySelector<HTMLElement>('#filterBtn')!.click();
  }));
}

describe('侧栏开合', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  it('过渡中玻璃退成纯模糊不重画贴图，停下后按终态补画，主区左缘落在让出的位置', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/', DESKTOP);
    try {
      const { page } = opened;
      await expectBody(page, '/', [page.locator('#drawer.open')]);
      await settle(page);
      await page.waitForFunction(() => document.querySelector<HTMLElement>('#drawer')?.dataset.opticGlass === 'true');
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.evaluate(() => {
        const counter = window as unknown as { __maps: number };
        counter.__maps = 0;
        const native = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function (this: HTMLCanvasElement, ...args: Parameters<HTMLCanvasElement['toDataURL']>) {
          counter.__maps++;
          return native.apply(this, args);
        };
      });

      const collapsed = await toggle(page);
      const expanded = await toggle(page);
      for (const [name, state] of [['收起', collapsed], ['展开', expanded]] as const) {
        assert.equal(state.mapsDuring, 0, `${name}过渡中重画了 ${state.mapsDuring} 张玻璃贴图`);
        assert.equal(state.opticDuring, 'blur(14px)', `${name}过渡中抽屉仍挂着旧尺寸的位移贴图`);
        assert.ok(state.mapsAfter >= 1, `${name}停下后没有按终态尺寸补画贴图`);
        assert.match(state.opticAfter, /url\("#peach-optic-\d+"\)/, `${name}停下后抽屉没有恢复位移滤镜`);
        assert.ok(Math.abs(state.mainLeft - state.railW) <= .5, `${name}后主区左缘 ${state.mainLeft}，应落在 --railW ${state.railW}`);
        assert.ok(Math.abs(state.mainRight - state.viewport) <= 1, `${name}后主区右缘 ${state.mainRight} 没有贴到视口右缘 ${state.viewport}`);
        assert.ok(state.drawerRight <= state.mainLeft, `${name}后抽屉右缘 ${state.drawerRight} 压进了主区`);
        /* 下限只拦「整段过渡被一次长任务吞掉、直接跳到终态」：除了点击那一帧，300ms 里至少
           还要出一帧中间帧。本机无头 Chrome 收起 10～15 帧、展开 30～38 帧；CPU 降速 6 倍后
           收起 2 帧、展开 4 帧，CI 的慢机落在这两档之间。 */
        assert.ok(state.frames >= 2, `${name}过渡的 300ms 里只出了 ${state.frames} 帧，主线程整段被占住`);
      }
      assert.equal(collapsed.open, false);
      assert.equal(expanded.open, true);
      assert.ok(collapsed.drawerWidth < expanded.drawerWidth, `收起 ${collapsed.drawerWidth}px 不比展开 ${expanded.drawerWidth}px 窄`);
      assert.ok(collapsed.railW < expanded.railW, '收起后主区没有往左让回来');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });
});
