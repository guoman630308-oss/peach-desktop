/* 换主题那一刻，控件上的颜色过渡不播。
 *
 * 夹具默认按「减少动态效果」开页面，全局规则会把过渡一并关掉，这里要的正是过渡开着的样子，
 * 所以页面级改回 `no-preference`。同一页先绕过 `applyTheme` 直接写属性换一次，确认这枚按钮
 * 在这种换法下确实会起过渡，后面那句「为空」才说明是 `applyTheme` 关掉的。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, settle, visit, VIEWPORTS } from './harness.ts';

const desktop = VIEWPORTS.find((viewport) => !viewport.mobile)!;

describe('切换主题', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });

  it('切换瞬间带颜色过渡的控件没有动画，明暗键滑块照常位移，下一帧过渡恢复', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/resource-sync', desktop);
    try {
      const { page } = opened;
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      const section = page.locator('#stats section#resource-sync');
      await section.waitFor({ state: 'visible', timeout: 15_000 });
      // 侧栏收起时滑块不显示，也就没有位移可看；桌面视口默认展开。
      await page.locator('#drawer.open .board-theme-thumb').waitFor({ state: 'visible', timeout: 5_000 });
      await settle(page);

      const result = await section.evaluate(async (host) => {
        const entry = '/dist/peach-app.js', ui = await import(entry);
        const root = document.documentElement;
        const thumb = document.querySelector<HTMLElement>('.board-theme-thumb')!;
        /* `.resourceaction` 只在这一页的加载骨架里出现，页面画完就换成 React 卡片；
           这里放一枚同类按钮，吃的是同一份 web/css 规则。 */
        const node = host.appendChild(Object.assign(document.createElement('button'), { className: 'resourceaction', textContent: '检查文件' }));
        const frame = () => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 150)));
        // 只结束过渡：动效开着时页面上还有无限循环的光晕动画，`finish()` 会对它们抛错。
        const finish = () => document.getAnimations()
          .forEach((animation) => { if (animation instanceof CSSTransition) animation.finish() });
        const transitions = (element: Element) => element.getAnimations()
          .map((animation) => (animation as CSSTransition).transitionProperty);
        const colours = () => document.getAnimations()
          .filter((animation): animation is CSSTransition => animation instanceof CSSTransition)
          .filter((animation) => /color|fill|stroke|shadow/.test(animation.transitionProperty))
          .map((animation) => {
            const target = (animation.effect as KeyframeEffect).target;
            return `${target?.tagName.toLowerCase()}.${target?.getAttribute('class')} ${animation.transitionProperty}`;
          });

        ui.applyTheme('light');
        await frame();
        finish();
        const declared = getComputedStyle(node).transitionProperty;

        root.dataset.theme = 'dark';
        root.classList.add('dark');
        const raw = transitions(node);
        finish();
        ui.applyTheme('light');
        await frame();
        finish();

        ui.applyTheme('dark');
        const switched = transitions(node);
        const page = colours();
        const slid = transitions(thumb);
        await frame();
        const restored = getComputedStyle(node).transitionProperty;

        /* 上面那次切换后立刻读动画，读的时候临时样式还挂着。真实页面没人读：rAF 里摘掉样式，
           紧接着这一帧才算样式。这里切换后什么都不读，等两帧再看，0.12s 的过渡若在第一帧起了还在跑。 */
        finish();
        ui.applyTheme('light');
        await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
        const settled = [...transitions(node), ...colours()];
        node.remove();
        return { declared, raw, switched, page, slid, restored, settled };
      });

      assert.match(result.declared, /background-color/, `这枚按钮没有声明颜色过渡：${result.declared}`);
      assert.ok(result.raw.length > 0, '直接写主题属性时按钮没有起过渡，对照不成立');
      assert.deepEqual(result.switched, [], `applyTheme 之后按钮仍在过渡：${result.switched.join(', ')}`);
      assert.deepEqual(result.page, [], `applyTheme 之后页面上仍有颜色过渡：${result.page.join('; ')}`);
      assert.deepEqual(result.slid, ['transform'], `明暗键滑块没有跟着位移：${result.slid.join(', ')}`);
      assert.equal(result.restored, result.declared, '下一帧之后按钮的过渡没有恢复，关过渡的临时样式还挂着');
      assert.deepEqual(result.settled, [], `摘掉临时样式那一帧补播了颜色过渡：${result.settled.join('; ')}`);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });
});
