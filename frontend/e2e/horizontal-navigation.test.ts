/* 横向导航的溢出提示：方向跟随滚动位置，容器伸缩后重算，原生滚动条不占位。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser, Locator } from 'playwright-core';
import { launch, settle, visit, VIEWPORTS } from './harness.ts';
import { openFollowFeed } from './follow-fixture.ts';

const MOBILE = VIEWPORTS.find(viewport => viewport.mobile)!;

async function edges(row: Locator, left: boolean, right: boolean) {
  await row.page().waitForFunction(([selector, expectedLeft, expectedRight]) => {
    const node = document.querySelector<HTMLElement>(selector);
    return node?.dataset.overflowLeft === String(expectedLeft)
      && node?.dataset.overflowRight === String(expectedRight);
  }, [await row.evaluate(node => {
    node.setAttribute('data-scroll-test', 'active');
    return '[data-scroll-test="active"]';
  }), left, right], { timeout: 5_000 });
  const style = await row.evaluate(node => ({
    bar: getComputedStyle(node).scrollbarWidth,
    mask: getComputedStyle(node).maskImage,
  }));
  assert.equal(style.bar, 'none', '横向导航露出了原生滚动条');
  assert.equal(style.mask.includes('linear-gradient'), left || right, '边缘提示与可滚动方向不一致');
  await row.evaluate(node => node.removeAttribute('data-scroll-test'));
}

async function scrollPositions(row: Locator) {
  const range = await row.evaluate(node => node.scrollWidth - node.clientWidth);
  assert.ok(range > 10, `夹具没有产生横向溢出：${range}`);
  await row.evaluate(node => { node.scrollLeft = 0 });
  await edges(row, false, true);
  await row.evaluate(node => { node.scrollLeft = (node.scrollWidth - node.clientWidth) / 2 });
  await edges(row, true, true);
  await row.evaluate(node => { node.scrollLeft = node.scrollWidth });
  await edges(row, true, false);
}

describe('横向导航提示', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });

  it('复核分类在窄屏横滚，桌面分类列不保留横滚提示', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/review', MOBILE);
    try {
      const row = opened.page.getByRole('tablist', { name: '复核分类' });
      await row.waitFor();
      await settle(opened.page);
      await scrollPositions(row);
      await opened.page.setViewportSize({ width: 1280, height: 844 });
      await opened.page.waitForFunction(() => {
        const node = document.querySelector('[aria-labelledby="reviewcategories"]');
        return node?.closest('[data-orientation]')?.getAttribute('data-orientation') === 'vertical';
      });
      assert.equal(await row.evaluate(node => getComputedStyle(node).maskImage), 'none');
      await opened.page.setViewportSize({ width: 390, height: 844 });
      await row.evaluate(node => { node.scrollLeft = 0 });
      await edges(row, false, true);
      await row.getByRole('tab').first().focus();
      await opened.page.keyboard.press('End');
      await edges(row, true, false);
      const geometry = await row.evaluate(node => {
        const selected = node.querySelector('[aria-selected="true"]')!.getBoundingClientRect();
        const host = node.getBoundingClientRect();
        return selected.left >= host.left - 1 && selected.right <= host.right + 1;
      });
      assert.ok(geometry, '键盘选中的分类没有进入可见范围');
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });

  it('标签类型和首字导航都显示左右提示，放宽后取消首字提示', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/tags', MOBILE, async page => {
      await page.route('**/api/index?**', route => route.fulfill({ json: {
        items: Array.from('ABCDEFGHIJKLMNOPQRSTUVWXYZ', (letter, at) => ({ k: `${letter}标签`, n: at + 1, cat: 'general' })),
        categories: { meta: 3, relationship: 3, role: 3, appearance: 3, scene: 3, story: 3, position: 3, general: 5 },
        has_more: false,
      } }));
    });
    try {
      const page = opened.page;
      const row = page.getByRole('group', { name: '标签类型' });
      await row.waitFor();
      await settle(page);
      await scrollPositions(row);
      const alphabet = page.getByRole('navigation', { name: '按首字跳转' });
      await scrollPositions(alphabet);
      await page.setViewportSize({ width: 1600, height: 900 });
      await edges(alphabet, false, false);
      await alphabet.getByRole('button').last().evaluate(node => { node.textContent = '很长的分类名称'.repeat(40) });
      await edges(alphabet, false, true);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });

  it('关注图片视图的下排操作支持横滚和边缘提示', { timeout: 60_000 }, async () => {
    const opened = await openFollowFeed(browser, '/follow?media=images', MOBILE);
    try {
      await scrollPositions(opened.page.locator('[data-follow-filter-glass] > [data-filter-row="bottom"]'));
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
});
