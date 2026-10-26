import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('配置表单与行间距', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：应用更新包说明和体积不越出视口`, async () => {
      const opened = await visit(browser, '/settings/library?section=' + encodeURIComponent('应用与维护'), viewport);
      const { page } = opened;
      try {
        await page.route('**/api/configuration', async route => {
          const data = await (await route.fetch()).json();
          await route.fulfill({ json: { ...data, updates: { ...data.updates, installation: '独立测试包',
            state: 'available', latest_version: '1.0.0', message: '有新版本可下载。', package_kind: 'update',
            asset_size: 12 * 1048576, release_url: 'https://github.com/peach-mitao/peach/releases/tag/v1.0.0' },
            update_job: { state: 'idle', progress: 0 } } });
        });
        await page.reload();
        const region = page.getByRole('region', { name: '检查更新', exact: true });
        await region.getByText('应用更新包 · 12.0 MB', { exact: true }).waitFor();
        assert.ok((await region.innerText()).includes('工具缺失、损坏或版本变化时会另行下载'));
        assert.equal(await region.getByRole('link', { name: '完整安装包', exact: true }).count(), 0);
        await settle(page);
        assert.ok((await layout(page)).scrollWidth <= viewport.width + 1);
        await region.screenshot({ path: `../build/update-download-${viewport.name}.png` });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
    it(`${viewport.name}：挂载目录的代理流量保护可切换且不越出视口`, async () => {
      const opened = await visit(browser, '/settings/library?section=' + encodeURIComponent('这台电脑'), viewport);
      const { page } = opened;
      try {
        await page.route('**/api/configuration', async route => {
          const data = await (await route.fetch()).json();
          await route.fulfill({ json: { ...data, editable: true, windows: true, media_sources: [
            { location: 'onedrive', root: 'O:\\Movies', path: 'O:\\Movies', cost: 'metered' },
            { location: 'mounted', root: 'M:\\Movies', path: 'M:\\Movies', cost: 'free' },
            { location: 'guangya', root: 'G:\\Movies', path: 'G:\\Movies', cost: 'free' },
            { location: '115', root: 'B:\\Movies', path: 'B:\\Movies', cost: 'free' },
            { location: 'pikpak', root: 'A:\\Movies', path: 'A:\\Movies', cost: 'metered' },
          ] } });
        });
        await page.reload();
        const form = page.getByRole('form', { name: '媒体目录', exact: true });
        const protectedFolder = form.locator('[data-media-read-policy="0"] [aria-haspopup="listbox"]');
        await protectedFolder.waitFor();
        await page.waitForFunction(() => {
          const marks = [...document.querySelectorAll<HTMLImageElement>('[data-folder-row] img')];
          return marks.length === 8 && marks.every(image => image.complete && image.naturalWidth > 0);
        });
        const marks = await form.locator('[data-folder-row] img').evaluateAll(images => images.map(image => {
          const box = image.getBoundingClientRect();
          return { inline: image.getAttribute('src')?.startsWith('data:image/png;base64,'),
            width: box.width, height: box.height };
        }));
        assert.deepEqual(marks, Array.from({ length: 8 }, () => ({ inline: true, width: 16, height: 16 })));
        assert.ok(!(await form.innerText()).includes('（只读）'));
        assert.ok((await protectedFolder.textContent())?.includes('代理流量保护'));
        assert.ok((await form.locator('[data-media-read-policy="1"]').textContent())?.includes('正常读取'));
        await protectedFolder.click();
        await page.getByRole('option', { name: '正常读取', exact: true }).click();
        await settle(page);
        assert.ok((await protectedFolder.textContent())?.includes('正常读取'));
        assert.deepEqual((await layout(page)).offenders, []);
        await form.screenshot({ path: `../build/cloud-source-icons-${viewport.name}.png` });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });

    it(`${viewport.name}：PikPak 表单直接显示，登录按钮同排`, async () => {
      const opened = await visit(browser, '/settings/library?section=' + encodeURIComponent('下载渠道'), viewport);
      const { page } = opened;
      try {
        await page.route('**/api/configuration/downloads', async route => {
          const body = await (await route.fetch()).json();
          await route.fulfill({ json: { ...body, available: true, pikpak: { ...body.pikpak, logged_in: false },
            pikpak_browser: { available: true, state: 'idle', message: '' } } });
        });
        await page.reload();
        const form = page.getByRole('form', { name: 'PikPak 账号', exact: true });
        await form.getByLabel('账号', { exact: true }).waitFor();
        assert.equal(await form.locator('details').count(), 0);
        const password = await form.getByRole('button', { name: '登录', exact: true }).boundingBox();
        const browserLogin = await form.getByRole('button', { name: '用浏览器登录', exact: true }).boundingBox();
        assert.ok(password && browserLogin && Math.abs(password.y - browserLogin.y) <= 1);
        assert.ok((await layout(page)).scrollWidth <= viewport.width + 1);
        await form.screenshot({ path: `../build/pikpak-form-${viewport.name}.png` });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
    it(`${viewport.name}：完全卸载行与设置卡片正文对齐`, async () => {
      const opened = await visit(browser, '/settings/library?section=' + encodeURIComponent('应用与维护'), viewport);
      const { page } = opened;
      try {
        await page.route('**/api/configuration', async route => {
          const data = await (await route.fetch()).json();
          await route.fulfill({ json: { ...data, uninstall: { ...data.uninstall, available: false } } });
        });
        await page.reload();
        const region = page.getByRole('region', { name: '卸载 Peach', exact: true });
        await region.getByText('完全卸载', { exact: true }).waitFor();
        await settle(page);
        const geometry = await region.evaluate(node => {
          const card = node.lastElementChild!.getBoundingClientRect();
          const label = [...node.querySelectorAll('p')].find(item => item.textContent === '完全卸载')!.getBoundingClientRect();
          return { top: label.top - card.top, left: label.left - card.left };
        });
        assert.ok(geometry.top <= 12 && geometry.top >= 9, JSON.stringify(geometry));
        assert.ok(Math.abs(geometry.left - 12) <= 1, JSON.stringify(geometry));
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
