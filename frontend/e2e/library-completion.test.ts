import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';

import { launch, layout, VIEWPORTS } from './harness.ts';
import { openItemPage } from './item-fixture.ts';

describe('缺预览时的统一补全入口', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });

  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：菜单可用且不溢出，补全结束后卡面显示预览`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-media-card]' });
      const { page } = opened;
      try {
        let complete = false;
        let started = false;
        let pendingStage = '';
        const posts: Record<string, unknown>[] = [];
        await page.route(url => url.pathname === '/api/items', route => route.fulfill({ json: {
          items: [{ id: 11, name: '没有预览的本地影片.mp4', medium: 'video', location: 'local',
            has_thumb: complete, has_local_poster: complete, size: 12345, duration: 3600 }],
          total: 1, has_more: false,
        } }));
        await page.route(url => url.pathname === '/api/library-processing', async route => {
          if (route.request().method() === 'POST') {
            const command = route.request().postDataJSON();
            posts.push(command);
            pendingStage = command.stage;
            started = true;
            return route.fulfill({ json: { status: 'running', job_id: 'preview-one', current_action: 'generating_preview' } });
          }
          if (started) complete = true;
          return route.fulfill({ json: started
            ? { status: 'complete', job_id: 'preview-one', requested_stage: pendingStage, previews: 1 }
            : { status: 'idle' } });
        });
        await page.reload({ waitUntil: 'load' });
        const prompt = page.locator('[data-library-completion]');
        await prompt.waitFor();
        assert.match(await prompt.innerText(), /1 部暂无预览/);
        const placement = await prompt.evaluate(element => {
          const text = element.querySelector('p')!;
          const link = text.querySelector('a')!;
          const action = element.querySelector('[data-note-action]')!;
          return { inline: link.parentElement === text, arrow: !!link.querySelector('svg'),
            text: text.getBoundingClientRect().toJSON(), action: action.getBoundingClientRect().toJSON() };
        });
        assert.ok(placement.inline && placement.arrow, JSON.stringify(placement));
        if (viewport.mobile) {
          assert.ok(placement.action.top >= placement.text.bottom + 8, JSON.stringify(placement));
          assert.ok(Math.abs(placement.action.left - placement.text.left) < 1, JSON.stringify(placement));
        } else {
          assert.ok(placement.action.left >= placement.text.right + 8,
            `宽容器中按钮应位于说明右侧：${JSON.stringify(placement)}`);
          assert.ok(placement.action.top < placement.text.bottom,
            `宽容器中按钮不应另起一行：${JSON.stringify(placement)}`);
          const textCenter = placement.text.top + placement.text.height / 2;
          const actionCenter = placement.action.top + placement.action.height / 2;
          assert.ok(Math.abs(textCenter - actionCenter) <= 1,
            `说明文字应与按钮垂直居中：${JSON.stringify(placement)}`);
        }
        await prompt.evaluate(element => { element.style.width = '320px' });
        const narrowText = await prompt.locator('p').boundingBox();
        const narrowAction = await prompt.locator('[data-note-action]').boundingBox();
        assert.ok(narrowText && narrowText.width >= 280, '窄容器中的提示正文应独占一行宽度');
        assert.ok(narrowAction && narrowAction.y >= narrowText.y + narrowText.height + 8,
          '窄容器中按钮应退到说明下方');
        await prompt.evaluate(element => { element.style.width = '' });
        if (viewport.mobile) {
          const text = await prompt.locator('p').boundingBox();
          assert.ok(text && text.width >= viewport.width - 80, '手机提示正文应独占一行宽度');
        }
        const more = prompt.getByRole('button', { name: '更多同步方式' });
        await more.focus();
        await page.keyboard.press('Enter');
        const menu = page.getByRole('dialog', { name: '同步方式' });
        await menu.waitFor();
        assert.deepEqual(await menu.getByRole('button').allTextContents(), ['同步这些影片', '仅生成视频预览', '仅补全资料']);
        const box = await menu.boundingBox();
        assert.ok(box && box.x >= 0 && box.x + box.width <= viewport.width);
        await page.keyboard.press('Escape');
        await menu.waitFor({ state: 'hidden' });
        await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label')
          === '更多同步方式', null, { timeout: 5_000 });
        for (const dark of [false, true]) {
          await page.evaluate(value => {
            document.documentElement.dataset.theme = value ? 'dark' : 'light';
            document.documentElement.classList.toggle('dark', value);
          }, dark);
          await more.click();
          await menu.waitFor();
          const measured = await layout(page);
          assert.ok(measured.scrollWidth <= measured.viewportWidth, JSON.stringify(measured));
          assert.deepEqual(measured.offenders, []);
          const artifacts = path.resolve('..', 'build', 'agent-verification', 'library-completion');
          await mkdir(artifacts, { recursive: true });
          await page.screenshot({ path: path.join(artifacts, `${viewport.name}-${dark ? 'dark' : 'light'}.png`) });
          await page.keyboard.press('Escape');
          await menu.waitFor({ state: 'hidden' });
        }
        await more.click();
        await menu.getByRole('button', { name: '仅生成视频预览' }).click();
        await prompt.waitFor({ state: 'hidden' });
        await page.locator('[data-media-card] img.poster').waitFor();
        assert.deepEqual(posts, [{ stage: 'previews', asset_ids: [11] }]);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
