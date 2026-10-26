import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';
import type { Browser } from 'playwright-core';

import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('馆藏与口味分享', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：离线圆点与文字均为错误色，浅色和暗色可辨识`, async () => {
      const opened = await visit(browser, '/settings/library', viewport, async page => {
        await page.route('**/api/configuration', async route => {
          const response = await route.fetch();
          await route.fulfill({ json: { ...await response.json(), editable: false,
            media_sources: [{ location: 'local', root: 'R:\\Media', path: 'R:\\Media', online: false }] } });
        });
      });
      try {
        const status = opened.page.locator('[data-mount="offline"]').first();
        await status.waitFor();
        for (const theme of ['light', 'dark']) {
          await opened.page.evaluate(value => {
            document.documentElement.dataset.theme = value;
            document.documentElement.classList.toggle('dark', value === 'dark');
          }, theme);
          const colors = await status.evaluate(element => {
            const halo = element.firstElementChild!;
            const dot = halo.firstElementChild!;
            const probe = document.createElement('span');
            probe.style.color = 'var(--color-text-error-primary)';
            element.append(probe);
            const expected = getComputedStyle(probe).color;
            probe.remove();
            return { text: getComputedStyle(element).color, dot: getComputedStyle(dot).backgroundColor,
              halo: getComputedStyle(halo).backgroundColor, expected };
          });
          assert.equal(colors.dot, colors.expected, JSON.stringify(colors));
          assert.equal(colors.text, colors.expected, JSON.stringify(colors));
          assert.notEqual(colors.halo, 'rgba(0, 0, 0, 0)');
        }
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
    it(`${viewport.name}：预览说明与独立开关可访问`, async () => {
      const posts: Record<string, unknown>[] = [];
      const state = { enabled: false, available: true, schema: 4,
        fields: ['作品编号、标签、人物、评分与全部偏好维度'],
        notice: '匿名处理后上传馆藏与各类偏好明细。' };
      const opened = await visit(browser, '/settings/library?section=' + encodeURIComponent('应用与维护'), viewport,
        async page => {
          await page.route('**/api/configuration', async route => {
            const response = await route.fetch();
            await route.fulfill({ json: { ...await response.json(), library_sharing: state } });
          });
          await page.route('**/api/configuration/library-sharing/preview', route => route.fulfill({ json: {
            token: 'preview-token', schema: 4, bytes: 128,
            data: { schema: 4, period: '2026-W42', library: [{ code: 'EXM-001', manual_tags: ['长标签'.repeat(500)] }],
              preferences: { performer: [] }, coverage: { uncoded_items: 0 } },
          } }));
          await page.route('**/api/configuration/library-sharing', route => {
            const body = route.request().postDataJSON();
            posts.push(body);
            return route.fulfill({ json: { ...state, enabled: body.enabled } });
          });
        });
      const { page } = opened;
      try {
        const region = page.getByRole('region', { name: '数据上传', exact: true });
        await region.waitFor();
        assert.equal(await region.getByRole('switch').count(), 2, '两项上传开关同属一个分区');
        const initialToggle = region.getByRole('switch', { name: '上传馆藏与口味明细', exact: true });
        assert.equal(await initialToggle.count(), 1, '关闭状态也应显示上传开关');
        await region.getByRole('button', { name: '预览上传内容', exact: true }).click();
        const preview = region.getByLabel('馆藏与口味上传预览', { exact: true });
        await preview.waitFor();
        assert.equal(posts.length, 0);
        assert.equal(await region.getByText(state.fields[0], { exact: true }).count(), 0);
        assert.equal(await region.getByText(/当前预览包含.*不含账号、设备、文件路径和浏览器历史记录/).count(), 1);
        assert.match(await preview.innerText(), /EXM-001/);
        assert.match(await preview.innerText(), /performer/);
        assert.equal(await region.locator('[data-code-block]').count(), 1);
        assert.equal(await region.getByRole('button', { name: '复制代码', exact: true }).count(), 1);
        await preview.focus();
        assert.ok(await preview.evaluate(element => element.scrollWidth > element.clientWidth), '长代码在代码块内部横向滚动');
        await settle(page);
        const measured = await layout(page);
        assert.ok(measured.scrollWidth <= measured.viewportWidth + 1, JSON.stringify(measured));
        assert.deepEqual(measured.offenders, []);
        await page.screenshot({ path: fileURLToPath(new URL(`../../build/library-sharing-${viewport.name}.png`, import.meta.url)) });
        const toggle = initialToggle;
        await toggle.locator('xpath=ancestor::label').click();
        await settle(page);
        assert.equal(posts.length, 0);
        await region.getByRole('checkbox').focus();
        await region.getByRole('checkbox').press('Space');
        await region.getByRole('button', { name: '同意并开启上传', exact: true }).click();
        await preview.waitFor({ state: 'hidden' });
        await settle(page);
        assert.deepEqual(posts[0], { enabled: true, schema: 4, consent: true, preview_token: 'preview-token' });
        assert.equal(await preview.count(), 0, '同意后收起数据预览');
        assert.equal(await region.getByRole('checkbox').count(), 0, '同意后收起同意框');
        assert.equal(await region.getByText(state.fields[0], { exact: true }).count(), 0, '同意后收起字段明细');
        assert.equal(await toggle.isChecked(), true);
        await region.getByRole('button', { name: '预览上传内容', exact: true }).click();
        await preview.waitFor();
        assert.equal(posts.length, 1, '查看预览不重复保存同意');
        assert.equal(await region.getByRole('checkbox').count(), 0, '已开启时查看预览不重复要求同意');
        await toggle.focus();
        const disabled = page.waitForResponse(response => response.url().endsWith('/api/configuration/library-sharing')
          && response.request().method() === 'POST');
        await toggle.press('Space');
        await disabled;
        await toggle.and(page.locator(':not(:checked)')).waitFor({ state: 'attached' });
        await settle(page);
        assert.equal(posts[1]?.enabled, false);
        assert.equal(await toggle.isChecked(), false);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
