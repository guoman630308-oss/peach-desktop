import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('非 Windows 媒体目录配置', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：只填写媒体目录，保存时保留已有路径对应关系`, async () => {
      const opened = await visit(browser, '/configuration', viewport);
      const { page } = opened;
      const writes: Array<{ media_sources: Array<{ path: string; root: string }> }> = [];
      try {
        await page.route('**/api/configuration', async route => {
          if (route.request().method() === 'POST') {
            writes.push(route.request().postDataJSON());
            await route.fulfill({ json: { saved: true, url: new URL('/configuration', page.url()).href, revision: 'saved' } });
            return;
          }
          const data = await (await route.fetch()).json();
          await route.fulfill({ json: { ...data, editable: true, windows: false,
            media_dirs: ['/media/movies'], media_sources: [
              { location: 'local', path: '/media/movies', root: 'R:\\media', library: '电影', cost: 'free', online: true },
            ] } });
        });
        await page.reload();
        const form = page.getByRole('form', { name: '媒体目录', exact: true });
        const path = form.getByRole('textbox', { name: '媒体目录 1', exact: true });
        await path.waitFor({ state: 'visible' });
        const internal = form.getByRole('textbox', { name: '数据库中的路径前缀', exact: true });
        assert.equal(await internal.count(), 0);
        await path.fill('/media/影视');
        await path.blur();
        await page.mouse.move(0, 0);
        await page.getByRole('tooltip').waitFor({ state: 'hidden' });
        await settle(page);
        await form.screenshot({ path: `../build/media-path-default-${viewport.name}.png` });
        if (!viewport.mobile) await page.screenshot({ path: '../build/media-path-page.png', fullPage: true });
        assert.equal(await form.getByText('高级路径设置', { exact: true }).count(), 0);
        assert.deepEqual((await layout(page)).offenders, []);
        await form.screenshot({ path: `../build/media-path-${viewport.name}.png` });
        assert.equal(await path.inputValue(), '/media/影视');
        await form.getByRole('button', { name: '添加文件夹', exact: true }).click();
        await form.getByRole('textbox', { name: '媒体目录 2', exact: true }).fill('/media/other');
        assert.equal(await internal.count(), 0);
        await form.getByRole('button', { name: '保存配置', exact: true }).click();
        await page.getByText('配置已保存', { exact: true }).waitFor();
        assert.deepEqual(writes.at(-1)?.media_sources.map(({ path, root }) => ({ path, root })), [
          { path: '/media/影视', root: 'R:\\media' }, { path: '/media/other', root: '' },
        ]);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
