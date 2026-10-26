import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

const SITES = JSON.parse(readFileSync(new URL('../../web/integrations/jav-sites.json', import.meta.url), 'utf8')).sites;

describe('浏览器添加 JAV 设置', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：启用、轮换与关闭，令牌只在签发响应后显示`, async () => {
      const opened = await visit(browser, '/settings/library?section=' + encodeURIComponent('下载渠道'), viewport);
      const { page } = opened;
      try {
        let enabled = false;
        const posted: Record<string, unknown>[] = [];
        await page.route('**/api/configuration/jav-browser', async route => {
          const request = route.request();
          const state = { enabled, provider: '115', available: true, sites: SITES, providers: [
            { key: '115', label: '115', configured: true, target: '/115/Movies' },
            { key: 'pikpak', label: 'PikPak', configured: true, target: '/Movies' },
          ] };
          if (request.method() === 'POST') {
            const body = request.postDataJSON();
            posted.push(body);
            enabled = body.action !== 'disable';
            await route.fulfill({ json: { ...state, enabled,
              ...(enabled ? { token: 'test-issued-token-' + posted.length } : {}) } });
          } else await route.fulfill({ json: state });
        });
        await page.reload();
        const region = page.getByRole('region', { name: '浏览器添加 JAV', exact: true });
        const install = region.getByRole('link', { name: '安装 Peach 用户脚本' });
        await install.waitFor();
        const channel = region.locator('button[aria-haspopup="listbox"]');
        const installBox = await install.locator('span').first().boundingBox();
        const channelBox = await channel.boundingBox();
        assert.ok(installBox && channelBox && Math.abs(installBox.x - channelBox.x) <= 1,
          JSON.stringify({ installBox, channelBox }));
        assert.equal(await channel.locator('img').count(), 1);
        const status = region.getByRole('status');
        assert.equal(await status.innerText(), '未启用');
        assert.equal(await status.locator('span[aria-hidden="true"]').count(), 1);
        const statusColor = () => status.locator('svg')
          .evaluate(node => getComputedStyle(node).color);
        const disabledColor = await statusColor();
        const themeColor = (token: string) => page.evaluate(token => {
          const sample = document.createElement('span');
          sample.style.color = `var(${token})`;
          document.body.append(sample);
          const color = getComputedStyle(sample).color;
          sample.remove();
          return color;
        }, token);
        assert.equal(disabledColor, await themeColor('--color-text-tertiary'));
        await channel.scrollIntoViewIfNeeded();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await channel.click();
        const pikpak = page.getByRole('option', { name: 'PikPak', exact: true });
        await pikpak.waitFor();
        assert.equal(await pikpak.locator('img').count(), 1);
        const cloud115 = page.getByRole('option', { name: '115', exact: true });
        assert.equal(await cloud115.locator('img').count(), 1);
        await cloud115.click();
        await region.getByRole('button', { name: '启用并生成令牌', exact: true }).click();
        const token = region.getByLabel('浏览器添加专用令牌', { exact: true });
        await token.waitFor();
        assert.equal(await status.innerText(), '已启用');
        assert.equal(await status.locator('span[aria-hidden="true"]').count(), 1);
        assert.notEqual(await statusColor(), disabledColor);
        assert.equal(await statusColor(), await themeColor('--color-notification-success-foreground'));
        const channelLabelBox = await region.getByText('浏览器添加的下载渠道', { exact: true }).boundingBox();
        const channelFieldBox = await channel.boundingBox();
        assert.ok(channelLabelBox && channelFieldBox);
        const channelGap = channelFieldBox.y - channelLabelBox.y - channelLabelBox.height;
        const inputGaps = await region.locator('[data-input-size]').evaluateAll(fields => fields.map(field => {
          const label = field.querySelector('label')!;
          const control = label.nextElementSibling!;
          return control.getBoundingClientRect().top - label.getBoundingClientRect().bottom;
        }));
        assert.equal(inputGaps.length, 2);
        assert.ok(inputGaps.every(gap => Math.abs(gap - channelGap) <= 0.5),
          JSON.stringify({ channelGap, inputGaps }));
        assert.equal(await token.inputValue(), 'test-issued-token-1');
        assert.equal(await token.getAttribute('type'), 'password');
        assert.equal(await region.getByRole('link', { name: '安装 Peach 用户脚本' }).getAttribute('href'),
          'https://sleazyfork.org/zh-CN/scripts/600821');
        assert.equal(await region.getByLabel('Peach 地址', { exact: true }).inputValue(), new URL(page.url()).origin);
        await region.getByRole('button', { name: '更新渠道与令牌', exact: true }).click();
        await page.waitForFunction(() => document.querySelector<HTMLInputElement>(
          'input[type="password"][value="test-issued-token-2"]') !== null);
        await region.getByRole('button', { name: '关闭浏览器添加', exact: true }).click();
        await token.waitFor({ state: 'detached' });
        assert.equal(await status.innerText(), '未启用');
        assert.equal(await status.locator('span[aria-hidden="true"]').count(), 1);
        assert.equal(await statusColor(), disabledColor);
        assert.deepEqual(posted.map(body => body.action), ['enable', 'rotate', 'disable']);
        assert.ok(posted.every(body => body.provider === '115'));
        await settle(page);
        assert.ok((await layout(page)).scrollWidth <= viewport.width + 1);
        assert.deepEqual(opened.problems, []);
        await region.screenshot({ path: `../build/jav-browser-settings-${viewport.name}.png` });
      } finally { await opened.close(); }
    });
  }
});
