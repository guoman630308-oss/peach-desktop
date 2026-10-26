import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { after, before, describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { launch, settle, visit, VIEWPORTS } from './harness.ts';

async function glass(page: Page) {
  return page.locator('[data-sidebar-glide]').evaluate(node => {
    const box = node.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  });
}

describe('侧栏选中高亮与返回按钮外观', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });

  for (const viewport of VIEWPORTS) {
    it(`高亮保持在当前菜单，返回键共用侧栏图标色（${viewport.name}）`, { timeout: 90_000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      const { page } = opened;
      const nav = page.locator('[data-sidebar-nav]');
      const back = page.locator('#navigationBack');
      try {
        await nav.locator('[aria-pressed="true"]').waitFor({ state: 'attached' });
        await settle(page);
        if (!await page.locator('#drawer').evaluate(node => node.classList.contains('open')))
          await page.locator('#filterBtn').click();

        for (const theme of ['light', 'dark']) {
          await page.locator(`[data-board-theme="${theme}"]`).click();
          await settle(page);
          const before = await glass(page);
          const inactive = nav.locator('[data-nav="tags"]');
          await inactive.hover();
          await page.evaluate(async () => {
            await new Promise(requestAnimationFrame);
            await Promise.all(document.querySelector('[data-sidebar-glide]')!.getAnimations().map(animation => animation.finished));
          });
          assert.deepEqual(await glass(page), before, '悬停菜单时高亮应留在当前项');
          await page.mouse.move(viewport.width - 10, viewport.height - 10);
          assert.deepEqual(await glass(page), before, '移开鼠标时高亮应保持原位');

          const styles = await back.evaluate(node => {
            const button = getComputedStyle(node), peer = getComputedStyle(document.querySelector('#filterBtn')!);
            return { color: button.color, peer: peer.color, border: button.borderWidth, shadow: button.boxShadow };
          });
          assert.equal(styles.border, '0px');
          assert.equal(styles.shadow, 'none');
          assert.equal(styles.color, styles.peer);
        }

        await nav.locator('[data-nav="performers"]').click();
        await page.waitForURL(url => url.pathname === '/performers');
        await settle(page);
        if (viewport.mobile) await page.locator('#filterBtn').click();
        await page.waitForFunction(() => {
          const pane = document.querySelector('[data-sidebar-glide]')!.getBoundingClientRect();
          const current = document.querySelector('[data-sidebar-nav] [aria-pressed="true"]')!.getBoundingClientRect();
          return Math.abs(pane.top - current.top) < 1;
        });
        assert.equal(await nav.locator('[data-nav="performers"]').getAttribute('aria-pressed'), 'true');
        assert.equal(await back.isDisabled(), false);
        await back.hover();
        await back.evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished)) });
        assert.equal(await back.evaluate(node => getComputedStyle(node).borderWidth), '0px');
        const hoveredColor = await back.evaluate(node => getComputedStyle(node).color);
        await page.locator('#filterBtn').hover();
        await page.locator('#filterBtn').evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished)) });
        assert.equal(hoveredColor, await page.locator('#filterBtn').evaluate(node => getComputedStyle(node).color));
        await back.focus();
        await page.keyboard.press('Tab');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await back.evaluate(node => node.matches(':focus-visible')), true);
        assert.notEqual(await back.evaluate(node => getComputedStyle(node).outlineStyle), 'none');
        await back.evaluate(node => (node as HTMLElement).blur());
        await mkdir('../build/agent-verification/sidebar-selection', { recursive: true });
        await page.mouse.move(viewport.width - 10, viewport.height - 10);
        await page.screenshot({ path: `../build/agent-verification/sidebar-selection/${viewport.name}.png` });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
