/* 首次运行页的整条流程：加载、填错、原位写回、改对、完成态。
 *
 * 跑在 `peach serve --setup` 上，不是路由冒烟那台已配置的服务：服务与临时 Peach 数据目录由
 * `tests/test_web_e2e.py` 的 `SetupE2ETests` 准备，经 PEACH_E2E_SETUP_ORIGIN、
 * PEACH_E2E_SETUP_DATA（临时 Peach 数据目录）和 PEACH_E2E_SETUP_MEDIA（一个已建好的空媒体目录）传进来。
 * 本文件由首启专用入口执行；单独执行时缺少这些变量则整组跳过。 */
import assert from 'node:assert/strict';
import { readFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, layout, VIEWPORTS } from './harness.ts';

const origin = process.env.PEACH_E2E_SETUP_ORIGIN ?? '';
const media = process.env.PEACH_E2E_SETUP_MEDIA ?? '';
const dataRoot = process.env.PEACH_E2E_SETUP_DATA ?? '';

/** 填错那一轮的 400 是预期内的：Chrome 会把它同时记成一条控制台错误和一条 4xx 响应。 */
const expectedRejection = (problem: string) =>
  problem === '400 /api/setup' || problem === '400 /api/setup/restore'
  || problem.startsWith('console Failed to load resource: the server responded with a status of 400');

describe('首次运行页', { skip: origin && media && dataRoot ? false : '只在 SetupE2ETests 起的首启服务上跑' }, () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  it('填错原位写回，改对后进入完成态', { timeout: 120_000 }, async () => {
    const viewport = VIEWPORTS.find((candidate) => candidate.name === 'desktop')!;
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
    const problems: string[] = [];
    try {
      const page: Page = await context.newPage();
      page.on('pageerror', (error) => problems.push(`pageerror ${error.message}`));
      page.on('console', (message) => { if (message.type() === 'error') problems.push(`console ${message.text()}`); });
      page.on('response', (response) => {
        if (response.url().startsWith(origin) && response.status() >= 400) {
          problems.push(`${response.status()} ${response.url().slice(origin.length)}`);
        }
      });
      await page.goto(`${origin}/`, { waitUntil: 'load' });

      // 加载：题目到齐后表单出现，等待态结束，页面不横向溢出。
      const heading = page.getByRole('heading', { name: '欢迎使用 Peach' });
      await heading.waitFor({ state: 'visible', timeout: 15_000 });
      const folder = page.getByRole('textbox', { name: '媒体库 1' });
      await folder.waitFor({ state: 'visible', timeout: 15_000 });
      assert.equal(await page.getByRole('textbox', { name: '数据库中的路径前缀' }).count(), 0,
        '普通首次设置不需要填写内部路径');
      await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), undefined, { timeout: 15_000 });
      const host = page.getByRole('radiogroup');
      assert.equal(await host.count(), 0, '局域网单选收在高级设置里，首屏不露出');
      assert.deepEqual((await layout(page)).offenders, []);

      // React Aria 的原生 input 是视觉隐藏的，点击落在外面那层画出来的开关上，所以按状态设值。
      await page.getByRole('switch', { name: '访问密码' }).setChecked(false, { force: true });
      await page.getByRole('checkbox', { name: /^完成设置后扫描/ }).setChecked(false, { force: true });

      // 填错：目录不存在、端口越界。
      const missing = join(media, 'missing');
      await folder.fill(missing);
      await page.getByText('高级设置', { exact: true }).click();
      const port = page.locator('#f-port');
      await port.waitFor({ state: 'visible' });
      const defaultPort = await port.inputValue();
      // 落盘位置跟着服务进程的 PEACH_DATA_ROOT 走；不是临时 Peach 数据目录就不往下提交。
      assert.equal(await page.locator('#f-data_root').inputValue(), dataRoot);
      await port.fill('70000');
      // 折叠关上，验证出错时它自己展开。
      await page.getByText('高级设置', { exact: true }).click();
      await port.waitFor({ state: 'hidden' });

      const rejected = page.waitForResponse((response) => response.url().endsWith('/api/setup')
        && response.request().method() === 'POST');
      await page.getByRole('button', { name: '完成设置' }).click();
      assert.equal((await rejected).status(), 400);

      // 写回：目录行与端口各标各的，高级设置展开，已填的值都在，焦点落在第一个错处。
      await page.waitForFunction(() => document.querySelectorAll('input[aria-invalid="true"]').length >= 2);
      assert.equal(await folder.getAttribute('aria-invalid'), 'true');
      assert.equal(await folder.inputValue(), missing);
      await port.waitFor({ state: 'visible' });
      assert.equal(await port.getAttribute('aria-invalid'), 'true');
      assert.equal(await port.inputValue(), '70000');
      await page.getByText(/目录不存在/).first().waitFor({ state: 'visible' });
      await page.getByText('端口要是 1 到 65535 之间的整数').waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '媒体库 1');

      // 改对：已有目录、默认端口，不扫描。
      await folder.fill(media);
      await port.fill(defaultPort);
      const selectedData = join(dataRoot, 'custom-data');
      await page.locator('#f-data_root').fill(selectedData);
      const accepted = page.waitForResponse((response) => response.url().endsWith('/api/setup')
        && response.request().method() === 'POST');
      await page.getByRole('button', { name: '完成设置' }).click();
      const acceptedResponse = await accepted;
      assert.equal(acceptedResponse.status(), 200, await acceptedResponse.text());

      // 完成态：入口链接拿到焦点，扫描没有排队，运行信息收起，口令不在页面上。
      await page.getByRole('heading', { name: '设置完成' }).waitFor({ state: 'visible' });
      const entry = page.getByRole('link', { name: '进入 Peach' });
      await entry.waitFor({ state: 'visible' });
      assert.ok(await entry.evaluate((element) => element === document.activeElement), '入口链接拿到焦点');
      assert.match((await entry.getAttribute('href')) ?? '', /^https?:\/\//);
      await page.getByText('稍后在配置页开始扫描媒体库。').waitFor({ state: 'visible' });
      assert.equal(await page.title(), 'Peach · 设置完成');
      assert.deepEqual((await layout(page)).offenders, []);
      await page.getByText('运行信息', { exact: true }).click();
      await page.getByText(selectedData, { exact: true }).waitFor({ state: 'visible' });
      const mobile = VIEWPORTS.find((candidate) => candidate.name === 'mobile')!;
      await page.setViewportSize({ width: mobile.width, height: mobile.height });
      assert.deepEqual((await layout(page)).offenders, []);

      // 已有数据目录的入口在首启页可操作，找不到配置时保留路径并说明原因。
      const originalConfig = await readFile(join(selectedData, 'config.toml'));
      await unlink(join(dataRoot, 'state', 'data-root.txt'));
      await page.reload({ waitUntil: 'load' });
      await page.getByRole('button', { name: '使用已有数据目录', exact: true }).click();
      const existing = page.getByRole('textbox', { name: '已有数据目录' });
      await existing.fill(join(dataRoot, 'missing'));
      const restoreRejected = page.waitForResponse((response) => response.url().endsWith('/api/setup/restore'));
      await page.getByRole('button', { name: '使用此目录', exact: true }).click();
      assert.equal((await restoreRejected).status(), 400);
      await page.getByRole('alert').filter({ hasText: 'config.toml' }).waitFor({ state: 'visible' });
      assert.deepEqual((await layout(page)).offenders, []);
      await existing.fill(selectedData);
      const restoreAccepted = page.waitForResponse((response) => response.url().endsWith('/api/setup/restore'));
      await page.getByRole('button', { name: '使用此目录', exact: true }).click();
      assert.equal((await restoreAccepted).status(), 200);
      await page.getByText('已连接已有数据目录。', { exact: true }).waitFor({ state: 'visible' });
      assert.equal(await readFile(join(dataRoot, 'state', 'data-root.txt'), 'utf8'), selectedData);
      assert.deepEqual(await readFile(join(selectedData, 'config.toml')), originalConfig);
      assert.deepEqual((await layout(page)).offenders, []);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      assert.deepEqual((await layout(page)).offenders, []);

      assert.deepEqual(problems.filter((problem) => !expectedRejection(problem)), []);
    } finally {
      await context.close();
    }
  });
});
