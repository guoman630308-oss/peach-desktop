import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, settle, visit, VIEWPORTS, requiredEnv } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';
import { DETAIL, openFollowFeed } from './follow-fixture.ts';
import { openPanel } from './settings-fixture.ts';
import { JAV_DETAIL, mockJavDetail } from './jav-detail-fixture.ts';

describe('规范路由与设置历史', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} SPA 作品导航采用接口规范番号并保留历史上下文`, { timeout: 60000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      const { page } = opened;
      try {
        await mockJavDetail(page);
        let details = 0;
        await page.route(/\/api\/jav\/detail\?/, route => {
          details++;
          return route.fulfill({ json: JAV_DETAIL });
        });
        const depth = await page.evaluate(() => history.length);
        await page.evaluate(async () => {
          const entry = '/dist/peach-app.js';
          (await import(entry)).peachHistory.push('/works/exm001?t=42#sample');
        });
        await page.waitForURL('**/works/EXM-001?t=42#sample');
        await page.locator('[data-jav-info] h1').waitFor();
        await settle(page, '#stage');
        assert.equal(details, 1);
        assert.equal(await page.evaluate(() => history.length), depth + 1);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
    it(`${viewport.name} 本地标签 ID 深链可展示聚合并刷新`, { timeout: 60000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      const { page } = opened;
      try {
        const seeded = await page.request.post(new URL('/api/item-tag', page.url()).href, { data: {
          id: Number(requiredEnv('PEACH_E2E_ITEM')), operation: 'add', tag: '巨乳',
        } });
        assert.equal(seeded.status(), 200, '临时演示库创建标签身份');
        const response = await page.request.get(new URL('/api/index?kind=tags&q=巨乳&limit=1', page.url()).href);
        const data = await response.json() as { items: { entity_id: number; k: string }[] };
        const tag = data.items[0]!;
        assert.ok(tag?.entity_id > 0);
        await page.goto(new URL(`/tags/${tag.entity_id}`, page.url()).href);
        const title = page.locator('#index [data-entity-title] h2');
        await title.waitFor();
        await settle(page);
        assert.equal(await title.innerText(), tag.k);
        assert.equal(await page.locator('#index [data-namepick-toggle]').count(), 0);
        await page.reload();
        await title.waitFor();
        await settle(page);
        assert.equal(await title.innerText(), tag.k);
        assert.equal(new URL(page.url()).pathname, `/tags/${tag.entity_id}`);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
    it(`${viewport.name} 保存的来源记录使用关注条目的规范地址`, { timeout: 60000 }, async () => {
      const opened = await openFollowFeed(browser, '/follow', viewport);
      const { page } = opened;
      try {
        await page.route(url => url.pathname === '/api/item', route => route.fulfill({ json: {
          id: 70010, location: 'online', medium: 'video', follow_item_id: DETAIL.media,
        } }));
        const depth = await page.evaluate(() => history.length);
        await page.evaluate(async () => {
          const entry = '/dist/peach-app.js';
          (await import(entry)).peachHistory.push('/assets/70010');
        });
        await page.waitForURL(`**/posts/${DETAIL.media}`);
        await page.locator('#stage [data-follow-detail]').waitFor();
        assert.equal(await page.evaluate(() => history.length), depth + 1);
        await page.goBack();
        await page.waitForURL(url => url.pathname === '/follow');
        await page.locator('[data-follow-list] > [data-follow-item]').first().waitFor();
      } finally { await opened.close() }
    });
    it(`${viewport.name} 设置分区返回同一份列表`, { timeout: 60000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      const { page } = opened;
      try {
        await page.locator('#grid [data-media-card]').first().waitFor();
        await page.evaluate(() => Object.assign(window, { routeGrid: document.querySelector('#grid .peach-react') }));
        await openPanel(page);
        await page.waitForURL('**/settings/appearance');
        const panel = page.locator('#settingsPanel');
        await panel.getByRole('tab', { name: '浏览与播放', exact: true }).click();
        await page.waitForURL('**/settings/playback');
        await panel.getByRole('button', { name: '关闭设置' }).click();
        await page.waitForURL(url => url.pathname === '/');
        await panel.waitFor({ state: 'hidden' });
        assert.ok(await page.evaluate(() => (window as unknown as { routeGrid: Element }).routeGrid === document.querySelector('#grid .peach-react')));
        await page.goForward();
        await page.waitForURL('**/settings/playback');
        await panel.getByRole('heading', { name: '浏览与播放', exact: true }).waitFor();
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
    it(`${viewport.name} 设置覆盖播放详情保留播放器实例`, { timeout: 60000 }, async () => {
      const opened = await openItemPage(browser, `/assets/${ITEM.plain}`, viewport);
      const { page } = opened;
      try {
        await page.locator('#stage video.vjs-tech').waitFor({ state: 'attached' });
        await page.evaluate(() => {
          Object.assign(window, { routeVideo: document.querySelector('#stage video') });
          document.querySelector<HTMLElement>('#settingsBtn')!.click();
        });
        await page.waitForURL('**/settings/appearance');
        await page.locator('#settingsClose').dispatchEvent('click');
        await page.waitForURL(`**/assets/${ITEM.plain}`);
        await page.locator('#settingsPanel').waitFor({ state: 'hidden' });
        await settle(page);
        assert.ok(await page.evaluate(() => (window as unknown as { routeVideo: Element }).routeVideo === document.querySelector('#stage video')));
        assert.ok(await page.locator('#stage').evaluate(node => (node as HTMLDialogElement).open));
      } finally { await opened.close() }
    });
  }
});
