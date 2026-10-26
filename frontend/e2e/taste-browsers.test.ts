import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('口味浏览器来源', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：添加、停止和恢复读取保留已导入记录`, { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/taste', viewport);
    const { page } = opened;
    const browsers = [{ id: 'aside', name: 'Aside', path: 'C:/Demo/Aside/User Data', enabled: true, available: true, profiles: 1,
      source_keys: ['d'.repeat(64), 'e'.repeat(64)] }];
    const data = () => ({ summary: { history_visits: 25 }, rankings: {},
      sources: [{ source_key: 'c'.repeat(64), browser: 'bookmarks', profile: '导入书签示例.html', host: 'demo', bookmarks: 3 },
        ...browsers.some(row => row.id === 'aside') ? [
          { source_key: 'd'.repeat(64), browser: 'aside', profile: 'Default', host: 'demo', visits: 25 },
          { source_key: 'e'.repeat(64), browser: 'aside', profile: 'Default · 书签', host: 'demo', bookmarks: 7 }] : []],
      browsers: [...browsers] });
    const requests: string[] = [];
    try {
      await page.route('**/api/taste?*', route => route.fulfill({ json: data() }));
      await page.route('**/api/taste/refresh', route => route.fulfill({ json: { status: 'idle' } }));
      await page.route('**/api/taste/source', async route => {
        const body = route.request().postDataJSON(); requests.push(body.operation);
        if (body.operation === 'add') browsers.push({ ...browsers[0], id: 'custom', name: body.name, path: body.path, source_keys: [] });
        else if (body.operation === 'delete_browser') browsers.splice(browsers.findIndex(row => row.id === body.id), 1);
        else browsers.find(row => row.id === body.id)!.enabled = body.operation === 'enable';
        await route.fulfill({ json: { dashboard: data() } });
      });
      await page.reload(); await settle(page);
      const sources = page.getByRole('region', { name: '数据源', exact: true });
      assert.equal(await sources.count(), 1);
      assert.equal(await sources.locator('[data-taste-source]').count(), 2);
      await sources.getByText('导入书签示例.html', { exact: true }).waitFor();
      await sources.getByText('25 条历史 · 7 枚书签', { exact: true }).waitFor();
      assert.equal(await sources.getByText('Default · 书签', { exact: true }).count(), 0);
      const cards = await sources.locator('[data-taste-source]').evaluateAll(elements => elements.map(element => {
        const style = getComputedStyle(element);
        return { background: style.backgroundColor, radius: style.borderRadius, parent: element.parentElement?.className };
      }));
      assert.deepEqual(cards[0], cards[1]);
      await sources.getByRole('button', { name: '添加浏览器', exact: true }).click();
      await sources.getByRole('textbox', { name: '浏览器名称', exact: true }).fill('演示浏览器');
      await sources.getByRole('textbox', { name: '资料目录或历史文件', exact: true }).fill('C:/Demo/Custom/User Data');
      await sources.getByRole('button', { name: '添加', exact: true }).click();
      await sources.getByText('演示浏览器', { exact: true }).waitFor();
      await sources.getByRole('button', { name: '停止读取 Aside', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '停止读取 Aside', exact: true });
      await dialog.getByRole('button', { name: '停止读取', exact: true }).click();
      await sources.getByText('已停止读取', { exact: true }).waitFor();
      await sources.getByRole('button', { name: '启用 Aside', exact: true }).click();
      await sources.getByRole('button', { name: '停止读取 Aside', exact: true }).waitFor();
      await sources.getByRole('button', { name: '删除 Aside', exact: true }).click();
      await page.getByRole('dialog', { name: '删除 Aside', exact: true }).getByRole('button', { name: '删除浏览器来源', exact: true }).click();
      await sources.getByRole('button', { name: '停止读取 Aside', exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await sources.getByRole('button', { name: '停止读取 Aside', exact: true }).count(), 0);
      assert.deepEqual(requests, ['add', 'disable', 'enable', 'delete_browser']);
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth, JSON.stringify(geometry));
      await page.screenshot({ path: `../build/taste-browsers-${viewport.name}.png` });
      assert.deepEqual(opened.problems, []);
    } finally { await opened.page.context().close(); }
  });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：书签导入入口、独立读数和来源删除`, { timeout: 60_000 }, async () => {
    const { page, problems } = await visit(browser, '/taste', viewport);
    let imported = false;
    const data = () => ({ summary: { history_visits: 0, bookmarks: imported ? 1284 : 0, history_sources: 2,
      range_start: '2026-10-12', range_end: '2026-10-17' }, rankings: {}, browsers: [],
      sources: imported ? [{ source_key: 'b'.repeat(64), browser: 'bookmarks', host: 'desktop',
        profile: 'Chrome 与 Firefox 收藏的创作者与作品书签.html', bookmarks: 1284 }] : [] });
    try {
      await page.route('**/api/taste?*', route => route.fulfill({ json: data() }));
      await page.route('**/api/taste/refresh', route => route.fulfill({ json: { status: 'idle' } }));
      await page.route('**/api/taste/import', async route => {
        assert.equal(decodeURIComponent(route.request().headers()['x-peach-filename']), '书签.html');
        assert.match(route.request().postData() || '', /NETSCAPE-Bookmark-file-1/);
        imported = true;
        await route.fulfill({ json: { dashboard: data() } });
      });
      await page.route('**/api/taste/source', async route => {
        assert.equal(route.request().postDataJSON().operation, 'remove');
        imported = false;
        await route.fulfill({ json: { removed: 1284, dashboard: data() } });
      });
      await page.reload(); await settle(page);
      await page.getByRole('button', { name: '更多取得浏览记录的方式', exact: true }).click();
      const chooser = page.waitForEvent('filechooser');
      await page.getByRole('button', { name: '导入书签文件', exact: true }).click();
      await (await chooser).setFiles({ name: '书签.html', mimeType: 'text/html',
        buffer: Buffer.from('<!DOCTYPE NETSCAPE-Bookmark-file-1><DL></DL>') });
      await page.getByText('导入文件 · desktop · 1,284 枚书签', { exact: true }).waitFor();
      const footer = page.getByText('2 个数据源 · 10/12–10/17', { exact: true });
      const footerStyle = await footer.evaluate(element => ({ height: element.getBoundingClientRect().height,
        line: parseFloat(getComputedStyle(element).lineHeight), whitespace: getComputedStyle(element).whiteSpace }));
      assert.equal(footerStyle.whitespace, 'nowrap');
      assert.ok(footerStyle.height <= footerStyle.line + 1);
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth, JSON.stringify(geometry));
      await page.screenshot({ path: `../build/taste-bookmarks-${viewport.name}.png` });
      await page.getByRole('button', { name: '移除 Chrome 与 Firefox 收藏的创作者与作品书签.html', exact: true }).click();
      await page.getByRole('dialog', { name: '移除口味数据源', exact: true })
        .getByRole('button', { name: '移除口味数据源', exact: true }).click();
      await page.getByText('还没有数据源', { exact: true }).waitFor();
      assert.deepEqual(problems, []);
    } finally { await page.context().close(); }
  });
});
