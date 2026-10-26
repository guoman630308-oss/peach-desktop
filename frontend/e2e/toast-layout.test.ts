import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { it } from 'node:test';
import type { Browser } from 'playwright-core';
import { installDesignBrowser } from './design-fixture.ts';
import { visit, VIEWPORTS } from './harness.ts';

let browser: Browser;
installDesignBrowser((value) => { browser = value });

const cases = [
  { name: 'short', html: '已保存配置', label: '' },
  { name: 'action', html: '已检查 105 个来源：更新 2933 条', label: '查看' },
  { name: 'long', html: '检查了 105 个来源：更新 2933 条 · 跳过 281 条无资源 · 排除 20 个超大合集 · 跳过 862 条超出首次采集范围的历史内容 · 回查 241 条 · 11 个来源没有更新', label: '查看' },
  { name: 'undo', html: '已把 12 项移入回收站', label: '撤销' },
  { name: 'error', html: '检查失败：来源暂时无法连接，请稍后重试。', label: '' },
];

for (const viewport of [...VIEWPORTS, { name: 'narrow', width: 320, height: 844, mobile: true }]) {
  it(`${viewport.name} 的短回执、长内容与操作按钮都留在视口内`, { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/follow', viewport);
    try {
      await opened.page.locator('#toasts').waitFor({ state: 'attached' });
      await mkdir(resolve('../build/toast-preview'), { recursive: true });
      for (const scenario of cases) {
        await opened.page.evaluate(async (scenario) => {
          const entry = '/dist/peach-app.js';
          const ui = await import(entry);
          ui.showToast(document.getElementById('toasts'), {
            success: '<svg viewBox="0 0 24 24"><path d="m4 12 5 5L20 6"/></svg>',
            error: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3v1"/></svg>',
          }, 'layout-preview', { html: scenario.html, alert: scenario.name === 'error', timeout: Infinity,
            action: scenario.label ? { label: scenario.label, run: () => {} } : null });
        }, scenario);
        const toast = opened.page.locator('[data-sonner-toast]').filter({ hasText: scenario.html });
        await toast.waitFor();
        await opened.page.waitForFunction((text) => [...document.querySelectorAll('[data-sonner-toast]')]
          .some((node) => node.textContent?.includes(text) && node.getAttribute('data-mounted') === 'true'
            && node.getAnimations().length === 0), scenario.html);
        const box = await toast.evaluate((node) => {
          const rect = node.getBoundingClientRect();
          const title = node.querySelector('[data-title]')!.getBoundingClientRect();
          const icon = node.querySelector('[data-icon]')!.getBoundingClientRect();
          const button = node.querySelector('[data-button]')?.getBoundingClientRect();
          return { left: rect.left, right: rect.right, width: rect.width,
            scrollWidth: node.scrollWidth, clientWidth: node.clientWidth,
            titleLeft: title.left, iconRight: icon.right,
            titleRight: title.right, titleBottom: title.bottom,
            buttonLeft: button?.left, buttonTop: button?.top };
        });
        assert.ok(box.left >= 0 && box.right <= viewport.width, JSON.stringify(box));
        assert.ok(box.scrollWidth <= box.clientWidth, JSON.stringify(box));
        assert.ok(box.titleLeft >= box.iconRight, JSON.stringify(box));
        if (scenario.name === 'long' && !viewport.mobile) {
          assert.ok(box.width > 400 && box.width <= 560, JSON.stringify(box));
        }
        if (scenario.label) {
          assert.ok(box.buttonLeft! >= box.titleRight || box.buttonTop! >= box.titleBottom, JSON.stringify(box));
          const inline = scenario.name === 'undo' || (scenario.name === 'action' && viewport.width >= 390);
          if (inline) assert.ok(box.buttonLeft! >= box.titleRight && box.buttonTop! < box.titleBottom, JSON.stringify(box));
          else assert.ok(box.buttonTop! >= box.titleBottom, JSON.stringify(box));
        }
        const rect = (await toast.boundingBox())!;
        await opened.page.screenshot({ path: resolve(`../build/toast-preview/${viewport.name}-${scenario.name}.png`),
          clip: { x: rect.x - 14, y: rect.y - 14, width: rect.width + 28, height: rect.height + 28 } });
      }
    } finally {
      await opened.close();
    }
  });
}
