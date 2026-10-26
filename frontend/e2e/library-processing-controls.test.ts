import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('扫描与采集控制', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：选择单个视频后只发起该文件的入库任务`, async () => {
      const file = 'E:\\视频收藏\\新加入的视频\\ABC-101.mp4';
      let state: Record<string, unknown> = { status: 'idle' };
      const writes: unknown[] = [];
      const opened = await visit(browser, '/data-cleanup', viewport, async page => {
        await page.route('**/api/library-processing', route => route.fulfill({ json: state }));
        await page.route('**/api/pick-media-file', route => route.fulfill({ json: { path: file } }));
        await page.route('**/api/library-processing/import-file', async route => {
          writes.push(route.request().postDataJSON());
          state = { status: 'running', job_id: 'single-file', requested_stage: 'collect',
            asset_ids: [17], total: 1, checked: 0, current_asset_name: 'ABC-101.mp4' };
          await route.fulfill({ json: state });
        });
      });
      try {
        const { page } = opened;
        await page.getByRole('button', { name: '添加单个视频', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '添加单个视频', exact: true });
        await dialog.getByRole('button', { name: '浏览', exact: true }).click();
        await page.waitForFunction(expected => document.querySelector<HTMLInputElement>('[role="dialog"] input')?.value === expected, file);
        assert.equal(await dialog.getByRole('textbox', { name: '视频文件路径' }).inputValue(), file);
        const measured = await layout(page);
        assert.ok(measured.scrollWidth <= measured.viewportWidth, JSON.stringify(measured));
        assert.deepEqual(measured.offenders, []);
        const artifacts = path.resolve('..', 'build', 'agent-verification', 'single-file-import');
        await mkdir(artifacts, { recursive: true });
        await dialog.screenshot({ path: path.join(artifacts, `${viewport.name}-dialog.png`) });
        await dialog.getByRole('button', { name: '添加并采集', exact: true }).click();
        await dialog.waitFor({ state: 'hidden' });
        assert.deepEqual(writes, [{ path: file }]);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });

    it(`${viewport.name}：资源提示、粗估、模式切换与暂停继续`, async () => {
      let state = { status: 'idle', job_id: '', checked: 0, total: 100, speed_mode: 'standard',
        steps: [{ id: 'sources', label: '扫描文件', status: 'complete' },
          { id: 'collect', label: '采集外部资料与封面', status: 'current' }],
        current_asset_name: '', current_action: '', stage: '',
        stalled: false, waited_seconds: 0,
        eta_kind: 'historical', eta_initial: true, eta_seconds_min: 1200, eta_seconds_max: 2400 };
      const writes: Record<string, unknown>[] = [];
      const opened = await visit(browser, '/data-cleanup', viewport, async page => {
        await page.route('**/api/library-processing', async route => {
          if (route.request().method() === 'POST') {
            const body = route.request().postDataJSON();
            writes.push(body);
            if (body.action === 'pause') state.status = 'pausing';
            else if (body.action === 'resume') state = { ...state, status: 'running', eta_initial: false,
              eta_kind: 'measured', eta_seconds_min: 600, eta_seconds_max: 900 };
            else if (body.action === 'set_speed') state.speed_mode = body.speed_mode;
            else state = { ...state, status: 'running', job_id: 'controlled', speed_mode: body.speed_mode,
              checked: 38, current_asset_name: '示例影片.mp4', current_action: 'querying_metadata', stage: '采集缺失资料' };
          }
          await route.fulfill({ json: state });
        });
      });
      const { page } = opened;
      const card = page.getByRole('region', { name: '扫描与采集', exact: true });
      const artifacts = path.resolve('..', 'build', 'agent-verification', 'processing-controls');
      await mkdir(artifacts, { recursive: true });
      try {
        await card.waitFor();
        await settle(page);
        await card.getByRole('button', { name: '同步媒体库', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '同步媒体库', exact: true });
        await dialog.waitFor();
        assert.match(await dialog.innerText(), /可能让电脑变慢/);
        assert.equal(writes.length, 0);
        const modalLayout = await layout(page);
        assert.ok(modalLayout.scrollWidth <= modalLayout.viewportWidth, JSON.stringify(modalLayout));
        assert.deepEqual(modalLayout.offenders, []);
        const optionFits = await dialog.locator('label').evaluateAll(labels => labels.every(label => {
          const text = label.querySelector('span[aria-hidden="true"]')!.getBoundingClientRect();
          const box = label.getBoundingClientRect();
          return text.top >= box.top && text.bottom <= box.bottom && text.right <= box.right;
        }));
        assert.equal(optionFits, true, '处理模式文字应保持单行并落在控件内');
        await dialog.screenshot({ path: path.join(artifacts, `${viewport.name}-startup.png`) });
        await dialog.getByRole('radio', { name: '低占用', exact: true }).check();
        await dialog.getByRole('button', { name: '同步媒体库', exact: true }).click();
        await card.getByText('预计剩余约 20–40 分钟（粗估）', { exact: true }).waitFor();
        assert.equal(writes[0]?.speed_mode, 'low');
        const dock = page.locator('#toasts [data-processing-dock]');
        const pause = dock.getByRole('button', { name: '暂停', exact: true });
        assert.equal(await pause.innerText(), '');
        assert.equal(await pause.locator('svg').count(), 1);
        await dock.getByRole('button', { name: '暂停', exact: true }).click();
        await dock.getByText('正在暂停，当前步骤结束后暂停', { exact: true }).waitFor();
        state.status = 'paused';
        const resume = dock.getByRole('button', { name: '继续', exact: true });
        await resume.waitFor();
        assert.equal(await resume.innerText(), '');
        assert.equal(await resume.locator('svg').count(), 1);
        assert.equal(await card.getByRole('button', { name: '同步媒体库', exact: true }).getAttribute('aria-disabled'), 'true');
        await page.reload();
        await resume.waitFor();
        const lowSpeed = dock.getByRole('button', { name: '处理速度：低占用' });
        assert.equal(await lowSpeed.innerText(), '低占用');
        assert.equal(await lowSpeed.locator('use[href="#i-snail"]').count(), 1);
        await dock.getByRole('button', { name: '处理速度：低占用' }).click();
        const effort = page.getByRole('dialog', { name: '处理速度', exact: true });
        const scale = effort.locator('.processing-effort-scale');
        const assertSelectedSpeed = async (expected: number) => {
          const marks = await scale.locator(':scope > span').evaluateAll(elements => elements.map(el => ({
            selected: el.hasAttribute('data-selected'), color: getComputedStyle(el).color,
            iconColor: getComputedStyle(el.querySelector('svg')!).color,
          })));
          assert.deepEqual(marks.map(mark => mark.selected), [0, 1, 2].map(index => index === expected));
          assert.ok(marks.every(mark => mark.iconColor === mark.color));
          assert.ok(marks.every((mark, index) => index === expected || mark.color !== marks[expected]!.color));
        };
        await assertSelectedSpeed(0);
        assert.deepEqual(await scale.locator(':scope > span').allTextContents(), ['最慢', '标准', '最快']);
        assert.deepEqual(await scale.locator(':scope > span').evaluateAll(marks => marks.map(mark => mark.querySelectorAll('svg > use').length)), [1, 1, 2]);
        const scaleLayout = await scale.evaluate(el => {
          const marks = [...el.children].map(mark => mark.getBoundingClientRect());
          const track = el.parentElement!.querySelector('.processing-effort-track')!.getBoundingClientRect();
          return { above: marks.every(mark => mark.bottom <= track.top),
            centered: Math.abs((marks[1]!.left + marks[1]!.right) / 2 - (track.left + track.right) / 2) < 1 };
        });
        assert.deepEqual(scaleLayout, { above: true, centered: true });
        assert.equal((await effort.innerText()).match(/低占用/g)?.length, 1);
        await effort.screenshot({ path: path.join(artifacts, `${viewport.name}-effort-low.png`) });
        await effort.getByRole('slider').press('ArrowRight');
        await dock.getByRole('button', { name: '处理速度：标准' }).waitFor();
        const standardSpeed = dock.getByRole('button', { name: '处理速度：标准' });
        assert.equal(await standardSpeed.innerText(), '标准');
        assert.equal(await standardSpeed.locator('use[href="#i-zap"]').count(), 1);
        await assertSelectedSpeed(1);
        await effort.screenshot({ path: path.join(artifacts, `${viewport.name}-effort-standard.png`) });
        await effort.press('Escape');
        await resume.click();
        await card.getByText('预计剩余约 10–15 分钟', { exact: true }).waitFor();
        assert.ok(writes.some(body => body.action === 'pause' && body.job_id === 'controlled'));
        assert.ok(writes.some(body => body.action === 'resume' && body.job_id === 'controlled'));
        assert.ok(writes.some(body => body.action === 'set_speed' && body.speed_mode === 'standard'));
        // 同步入口在后台任务期间保持忙态；这里只等待有限的控制请求结束。
        await settle(page, 'section[aria-label="扫描与采集"] [data-processing-controls]');
        const measured = await layout(page);
        assert.ok(measured.scrollWidth <= measured.viewportWidth, JSON.stringify(measured));
        assert.deepEqual(measured.offenders, []);
        await page.screenshot({ path: path.join(artifacts, `${viewport.name}-page.png`), fullPage: true });
        await card.screenshot({ path: path.join(artifacts, `${viewport.name}.png`) });
        await page.goto(new URL('/', page.url()).href);
        assert.equal(await page.locator('[data-library-completion]').count(), 0, '运行期间首页由常驻任务卡承接进度');
        await dock.getByRole('button', { name: '暂停', exact: true }).waitFor();
        await dock.getByRole('button', { name: '收起任务详情' }).click();
        const fixed = await dock.boundingBox();
        await dock.hover();
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await page.waitForTimeout(500);
        const hovered = await dock.boundingBox();
        await page.mouse.move(1, 1);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(500);
        const unhovered = await dock.boundingBox();
        assert.ok(fixed && hovered && unhovered);
        assert.ok(Math.abs(fixed.y - hovered.y) < 1 && Math.abs(fixed.y - unhovered.y) < 1,
          '收起详情后滚动与悬停保持任务卡位置');
        await dock.getByRole('button', { name: '处理速度：标准' }).click();
        const speed = page.getByRole('dialog', { name: '处理速度', exact: true });
        await speed.getByRole('slider').press('End');
        await dock.getByRole('button', { name: '处理速度：全速' }).waitFor();
        const highSpeed = dock.getByRole('button', { name: '处理速度：全速' });
        assert.equal(await highSpeed.innerText(), '全速');
        assert.equal(await highSpeed.locator('svg > use[href="#i-zap"]').count(), 2);
        await assertSelectedSpeed(2);
        const theme = await page.evaluate(() => ({ value: document.documentElement.dataset.theme,
          dark: document.documentElement.classList.contains('dark') }));
        for (const dark of [false, true]) {
          await page.evaluate(dark => { document.documentElement.dataset.theme = dark ? 'dark' : 'light';
            document.documentElement.classList.toggle('dark', dark) }, dark);
          for (const hovered of [false, true]) {
            if (hovered) await highSpeed.hover();
            else await page.mouse.move(1, 1);
            await page.waitForFunction(() => {
              const button = document.querySelector('.processing-effort-trigger[data-speed="high"] > button')!;
              return getComputedStyle(button.querySelector('.processing-effort-double > use:last-child')!).fill === getComputedStyle(button).backgroundColor;
            });
            const paint = await highSpeed.evaluate(el => ({ background: getComputedStyle(el).backgroundColor,
              foreground: getComputedStyle(el.querySelector('.processing-effort-double > use:last-child')!).fill }));
            assert.equal(paint.foreground, paint.background, '前方闪电用控件背景遮住后方轮廓');
          }
        }
        await page.mouse.move(1, 1);
        await page.evaluate(theme => {
          if (theme.value) document.documentElement.dataset.theme = theme.value;
          else delete document.documentElement.dataset.theme;
          document.documentElement.classList.toggle('dark', theme.dark);
        }, theme);
        assert.ok(writes.some(body => body.action === 'set_speed' && body.speed_mode === 'high'));
        assert.match(await speed.innerText(), /最慢[\s\S]*最快[\s\S]*占用少[\s\S]*占用多/);
        await page.locator('[data-effort-flow]').waitFor();
        const flow = page.locator('.processing-flow-outer');
        const blue = await flow.evaluate(el => getComputedStyle(el).fill);
        const highColor = await highSpeed.evaluate(el => getComputedStyle(el).color);
        assert.equal(await scale.locator(':scope > span[data-selected]').evaluate(el => getComputedStyle(el).color), highColor);
        const readFeedback = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--feedback-success').trim());
        const feedbackBlue = await readFeedback();
        await page.evaluate(() => { document.documentElement.dataset.accent = 'emerald' });
        const emerald = await flow.evaluate(el => getComputedStyle(el).fill);
        assert.notEqual(emerald, blue, '最高档流光跟随站点强调色');
        assert.notEqual(await highSpeed.evaluate(el => getComputedStyle(el).color), highColor, '全速入口跟随站点强调色');
        assert.equal(await scale.locator(':scope > span[data-selected]').evaluate(el => getComputedStyle(el).color),
          await highSpeed.evaluate(el => getComputedStyle(el).color), '全速刻度和入口共用强调色');
        assert.notEqual(await readFeedback(), feedbackBlue, '旧进度反馈色跟随站点强调色');
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--feed-mark').trim()), '#F78422', 'RSS 语义色保持橙色');
        await page.screenshot({ path: path.join(artifacts, `${viewport.name}-effort.png`) });
        await page.emulateMedia({ reducedMotion: 'reduce' });
        assert.equal(await page.locator('[data-effort-flow] svg').evaluate(el => getComputedStyle(el).animationName), 'none');
        await page.emulateMedia({ reducedMotion: 'no-preference' });
        await speed.press('Escape');
        await settle(page, '#toasts [data-processing-dock]');
        await dock.getByRole('link', { name: '任务详情' }).click();
        await dock.getByRole('button', { name: '展开任务详情' }).waitFor();
        assert.equal(await dock.count(), 1, '换页保留同一张任务卡');
        await dock.getByRole('button', { name: '展开任务详情' }).click();
        assert.equal(await dock.locator('[data-processing-step="complete"] s').innerText(), '扫描文件');
        assert.equal(await dock.locator('[aria-current="step"] s').count(), 0);
        await page.evaluate(async () => {
          const entry = '/dist/peach-app.js', ui = await import(entry);
          ui.showToast(document.getElementById('toasts'), { success: '', error: '' }, 'processing-proof',
            { html: '设置已保存', alert: false, timeout: 60000, action: null });
        });
        const receipt = page.locator('#toasts [data-sonner-toast]').filter({ hasText: '设置已保存' });
        await receipt.waitFor();
        await page.waitForFunction(() => {
          const task = document.querySelector('#toasts [data-processing-dock]');
          const notice = [...document.querySelectorAll('#toasts [data-sonner-toast]')]
            .find(node => node.textContent?.includes('设置已保存'));
          return task && notice && notice.getBoundingClientRect().bottom <= task.getBoundingClientRect().top - 7;
        });
        const dockBox = await dock.boundingBox(), receiptBox = await receipt.boundingBox();
        assert.ok(dockBox && receiptBox && receiptBox.y + receiptBox.height <= dockBox.y - 7,
          JSON.stringify({ message: '普通回执位于任务卡上方', dockBox, receiptBox,
            space: await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--processing-toast-space')) }));
        const homeLayout = await layout(page);
        assert.ok(homeLayout.scrollWidth <= homeLayout.viewportWidth, JSON.stringify(homeLayout));
        assert.deepEqual(homeLayout.offenders, []);
        await page.screenshot({ path: path.join(artifacts, `${viewport.name}-notice.png`) });
        // 先核对定位，再让回执超时；渲染耗时不占回执的可见窗口。
        await page.evaluate(async () => {
          const entry = '/dist/peach-app.js', ui = await import(entry);
          ui.showToast(document.getElementById('toasts'), { success: '', error: '' }, 'processing-proof',
            { html: '设置已保存', alert: false, timeout: 500, action: null });
        });
        await receipt.waitFor({ state: 'hidden' });
        assert.equal(await dock.isVisible(), true, '任务卡不会随普通回执超时消失');
        state = { ...state, stalled: true, current_action: 'reading_local_cover',
          current_asset_name: '2026-07-15 创作者合集 多机位完整版.mp4',
          waited_seconds: 33,
          eta_seconds_min: 63, eta_seconds_max: 94 };
        await dock.getByText('等待当前文件响应，剩余时间待更新', { exact: true }).waitFor();
        await page.waitForFunction(() => document.querySelector('[data-processing-dock]')?.textContent?.includes('已等待 33 秒'));
        await page.waitForFunction(() => document.querySelector('[data-processing-dock]')?.textContent?.includes('已等待 34 秒'),
          undefined, { timeout: 1800 });
        assert.match(await dock.innerText(), /读取配套图片/);
        assert.doesNotMatch(await dock.innerText(), /预计剩余/);
        const stalledLayout = await layout(page);
        assert.ok(stalledLayout.scrollWidth <= stalledLayout.viewportWidth, JSON.stringify(stalledLayout));
        assert.deepEqual(stalledLayout.offenders, []);
        await page.screenshot({ path: path.join(artifacts, `${viewport.name}-stalled.png`) });
        state.status = 'complete';
        await dock.waitFor({ state: 'hidden' });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
