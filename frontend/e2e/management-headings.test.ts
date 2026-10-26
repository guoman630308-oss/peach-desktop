import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { settledRun } from './design-fixture.ts';
import { launch, VIEWPORTS, visit } from './harness.ts';

describe('任务中心标题排版', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); await mkdir('../build/table-evidence', { recursive: true }); });
  after(async () => { await browser.close(); });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：加载与内容标题采用同一套 medium 字体`, async () => {
    const opened = await visit(browser, '/tasks', viewport);
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    try {
      const { page } = opened;
      await page.route('**/api/tasks', async (route) => {
        await held;
        await route.fulfill({ json: { available: true,
          running: [{ ...settledRun(1, 'running', '正在运行的示例任务'), finished_at: null }],
          skipped: [settledRun(2, 'skipped', '被挡下的示例任务')],
          finished: [settledRun(3, 'succeeded', '已完成的示例任务')],
        } });
      });
      await page.reload({ waitUntil: 'domcontentloaded' });
      const pending = page.locator('[data-skeleton="board/tasks"] h3').first();
      await pending.waitFor();
      const read = (node: Element) => {
        const style = getComputedStyle(node);
        return { weight: style.fontWeight, size: style.fontSize, line: style.lineHeight, font: style.fontFamily };
      };
      const waiting = await pending.evaluate(read);
      assert.equal(waiting.weight, '500');
      assert.equal(waiting.size, '20px');
      release();
      await page.locator('[data-skeleton="board/tasks"]').waitFor({ state: 'detached' });
      for (const title of ['正在进行', '被挡下的', '最近完成']) {
        const heading = page.getByRole('heading', { name: title, exact: true });
        await heading.waitFor();
        assert.deepEqual(await heading.evaluate(read), waiting);
      }
      await page.screenshot({ path: `../build/table-evidence/tasks-${viewport.name}.png`, fullPage: true });
      assert.deepEqual(opened.problems, []);
    } finally { release(); await opened.close(); }
  });
});
