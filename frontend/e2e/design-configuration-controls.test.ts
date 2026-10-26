/* 配置与设置入口的语义样式：图标、状态对齐、主次动作与单行入口。 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { configurationBody, expectBody, layout, settle, visit, VIEWPORTS } from './harness.ts';
import { installDesignBrowser, openActivity, settledRun } from './design-fixture.ts';
import { openHome, openPanel, PANEL } from './settings-fixture.ts';

describe('设计决定：配置控件', () => {
  let browser: Browser;
  installDesignBrowser((next) => { browser = next; });

  for (const viewport of VIEWPORTS) {
    it(`设置导航字形可见，口味入口呈按钮并进入对应页面（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openHome(browser, viewport);
      try {
        const page = opened.page;
        await openPanel(page);
        const marks = await page.locator(`${PANEL} [data-settings-nav] button svg`).evaluateAll((icons) => icons.map((icon) => {
          const shape = (icon as SVGSVGElement).getBBox();
          const symbol = document.querySelector(icon.querySelector('use')!.getAttribute('href')!);
          return { width: shape.width, height: shape.height, text: symbol?.textContent, name: icon.closest('button')!.textContent };
        }));
        for (const mark of marks) assert.ok(mark.width > 0 && mark.height > 0, `${mark.name} 字形为空`);
        assert.ok(marks.find((mark) => mark.name === 'JAV 与新作')?.text?.includes('番'));
        await page.locator(PANEL).getByRole('tab', { name: '搜索与推荐' }).click();
        const link = page.locator(PANEL).getByRole('link', { name: '打开口味与记录' });
        const style = await link.evaluate((node) => ({ display: getComputedStyle(node).display,
          height: node.getBoundingClientRect().height, decoration: getComputedStyle(node).textDecorationLine }));
        assert.ok(['flex', 'inline-flex'].includes(style.display));
        assert.ok(style.height >= 32);
        assert.equal(style.decoration, 'none');
        await link.click();
        await page.waitForURL((url) => url.pathname === '/taste');
        await page.locator(PANEL).waitFor({ state: 'hidden' });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });

    it(`媒体状态居中，辅助动作与保存有层级，安装教程是单行（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, '/settings/library', viewport);
      try {
        const page = opened.page;
        await page.route('**/api/configuration', async (route) => {
          const data = await (await route.fetch()).json();
          const paths = ['R:\\media', 'B:\\', 'A:\\'];
          await route.fulfill({ json: { ...data, editable: true, windows: true, media_dirs: paths, media_sources: paths.map((path, index) => ({
            path, location: ['local', '115', 'pikpak'][index], online: index > 0, library: ['media', '115', 'PikPak'][index],
          })) } });
        });
        await page.reload({ waitUntil: 'load' });
        await expectBody(page, '/settings/library', configurationBody(page));
        await settle(page);
        await page.locator('[data-mount]').first().waitFor();
        const centers = await page.locator('[data-folder-row]').evaluateAll((rows) => rows.map((row) => {
          const input = row.querySelector('input')!.getBoundingClientRect();
          const status = row.querySelector('[data-mount]')!.getBoundingClientRect();
          return Math.abs(input.top + input.height / 2 - status.top - status.height / 2);
        }));
        assert.equal(centers.length, 3);
        for (const delta of centers) assert.ok(delta <= 1, `状态偏离输入框中线 ${delta}px`);
        const media = page.getByRole('form', { name: '媒体目录', exact: true });
        assert.notEqual(await media.getByRole('button', { name: '添加文件夹', exact: true }).evaluate((node) => getComputedStyle(node).backgroundImage),
          await media.getByRole('button', { name: '保存配置', exact: true }).evaluate((node) => getComputedStyle(node).backgroundImage));
        await page.getByRole('tab', { name: '应用与维护', exact: true }).click();
        const tutorial = page.getByRole('region', { name: '安装教程', exact: true });
        await tutorial.waitFor();
        const aligned = await tutorial.evaluate((node) => {
          const label = [...node.querySelectorAll('p')].find((item) => item.textContent === '安装教程')!.getBoundingClientRect();
          const button = node.querySelector('button')!.getBoundingClientRect();
          return { delta: Math.abs(label.top + label.height / 2 - button.top - button.height / 2), gap: button.left - label.right };
        });
        assert.ok(aligned.delta <= 1 && aligned.gap > 0, JSON.stringify(aligned));
        await page.screenshot({ path: `../build/agent-verification/browser/configuration-${viewport.name}.png`, fullPage: true });
        assert.deepEqual((await layout(page)).offenders, []);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }

  for (const viewport of VIEWPORTS) for (const path of ['/settings/library', '/review']) {
    it(`分类目录在桌面左侧、手机上方，选中态区别于悬停（${path} · ${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, path, viewport);
      try {
        const page = opened.page;
        const nav = page.locator('#stats [data-section-nav]:not([data-skeleton] *)');
        const selected = nav.getByRole('tab', { selected: true });
        await selected.waitFor();
        await settle(page);
        const content = page.getByRole('tabpanel').filter({ visible: true }).first();
        const a = (await nav.boundingBox())!;
        const b = (await content.boundingBox())!;
        assert.ok(viewport.name === 'desktop' ? a.x + a.width <= b.x + 1 : a.y + a.height <= b.y + 1,
          JSON.stringify({ nav: a, content: b }));
        const fill = await selected.evaluate((node) => getComputedStyle(node).backgroundColor);
        const unselected = nav.getByRole('tab', { selected: false }).first();
        await unselected.hover();
        await settle(page);
        assert.notEqual(await unselected.evaluate((node) => getComputedStyle(node).backgroundColor), fill);
        assert.equal(await unselected.evaluate((node) => getComputedStyle(node).backgroundColor), 'rgba(0, 0, 0, 0)');
        assert.deepEqual((await layout(page)).offenders, []);
        await page.screenshot({ path: `../build/agent-verification/browser/${path.slice(1)}-navigation-${viewport.name}.png`, fullPage: true });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }

  it('大量已完成后继可通过键盘展开和收起', { timeout: 60_000 }, async () => {
    const parent = settledRun(1000, 'succeeded', '订阅源拉取');
    const children = Array.from({ length: 20 }, (_, index) => ({ ...settledRun(index + 1, 'succeeded', '补全资料图片'),
      parent_run_id: parent.id, root_run_id: parent.id, followup_key: `entity-avatar:${index}`, followup_depth: 1 }));
    const opened = await openActivity(browser, [parent, ...children]);
    try {
      const page = opened.page;
      const summary = page.locator('summary').filter({ hasText: '任务明细 · 20 项' });
      assert.equal(await page.locator('[data-followup-key]:visible').count(), 0);
      await summary.focus();
      await page.keyboard.press('Enter');
      await settle(page);
      assert.equal(await page.locator('[data-followup-key]:visible').count(), 20);
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => ![...document.querySelectorAll('details')].find((node) => node.textContent?.includes('任务明细 · 20 项'))?.open);
      assert.equal(await page.locator('[data-followup-key]:visible').count(), 0);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });
});
