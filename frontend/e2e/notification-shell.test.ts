import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, visit, VIEWPORTS } from './harness.ts';

describe('通知和任务卡的共用外框', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) for (const theme of ['light', 'dark']) {
    it(`${viewport.name} ${theme}：外框一致，正文布局稳定`, async () => {
      const opened = await visit(browser, '/data-cleanup', viewport, page => page.route('**/api/library-processing',
        route => route.fulfill({ json: { status: 'paused', job_id: 'shell-preview', checked: 38, total: 100,
          stage: '采集缺失资料', current_asset_name: '示例影片.mp4', speed_mode: 'standard' } })));
      const { page } = opened;
      const artifacts = path.resolve('..', 'build', 'agent-verification', 'processing-controls');
      await mkdir(artifacts, { recursive: true });
      try {
        const task = page.locator('#toasts [data-processing-dock]');
        await task.waitFor();
        await page.evaluate(async theme => {
          document.documentElement.dataset.theme = theme;
          document.documentElement.classList.toggle('dark', theme === 'dark');
          const entry = '/dist/peach-app.js', ui = await import(entry);
          ui.showToast(document.getElementById('toasts'), { success: '', error: '' }, 'shell-receipt',
            { html: '配置已保存', alert: false, timeout: Infinity, action: null });
        }, theme);
        const receipt = page.locator('#toasts [data-sonner-toast][data-styled="true"]').filter({ hasText: '配置已保存' });
        await receipt.waitFor();
        await page.waitForFunction(() => [...document.querySelectorAll('#toasts [data-sonner-toast]')]
          .every(node => node.getAttribute('data-mounted') === 'true' && node.getAnimations().length === 0));
        const measure = () => page.evaluate(() => [...document.querySelectorAll<HTMLElement>(
          '#toasts [data-sonner-toast][data-styled="true"],#toasts [data-processing-dock]')].map(node => {
          const css = getComputedStyle(node), box = node.getBoundingClientRect();
          return { kind: node.hasAttribute('data-processing-dock') ? 'task' : 'receipt',
            visibility: css.visibility, radius: css.borderRadius, shadow: css.boxShadow, border: css.border,
            x: box.x, y: box.y, width: box.width, height: box.height, padding: css.padding };
        }));
        const aligned = await measure();
        assert.equal(aligned.length, 2);
        assert.equal(aligned[0]!.radius, '16px');
        assert.equal(aligned[0]!.radius, aligned[1]!.radius);
        assert.equal(aligned[0]!.shadow, aligned[1]!.shadow);
        assert.equal(aligned[0]!.border, aligned[1]!.border);
        // 外观与坐标使用同一次测量，尺寸必须来自可见的布局框。
        for (const item of aligned) assert.ok(item.visibility === 'visible' && item.width > 0 && item.height > 0, JSON.stringify(item));
        const box = aligned.find(item => item.kind === 'task')!, top = aligned.find(item => item.kind === 'receipt')!.y;
        const clip = { x: Math.max(0, viewport.width - 600), y: Math.max(0, top - 20),
          width: Math.min(viewport.width, 600), height: viewport.height - Math.max(0, top - 20) };
        assert.ok(box.x >= 0 && box.x + box.width <= viewport.width);
        await page.screenshot({ path: path.join(artifacts, `${viewport.name}-${theme}-shell-after.png`), clip });
        // 同一任务和布局下回放外框基线参数。
        await page.evaluate(() => document.querySelectorAll<HTMLElement>('#toasts [data-sonner-toaster]')
          .forEach(node => node.style.removeProperty('--floating-radius')));
        const baseline = await page.addStyleTag({ content:
          '#toasts :is([data-sonner-toast][data-styled=true],[data-processing-dock]){box-shadow:var(--elevation-dropdown)!important}' });
        const original = await measure();
        for (let at = 0; at < aligned.length; at++) {
          assert.equal(aligned[at]!.width, original[at]!.width);
          assert.equal(aligned[at]!.height, original[at]!.height);
          assert.equal(aligned[at]!.padding, original[at]!.padding);
        }
        await page.screenshot({ path: path.join(artifacts, `${viewport.name}-${theme}-shell-before.png`), clip });
        await baseline.evaluate(node => node.remove());
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
