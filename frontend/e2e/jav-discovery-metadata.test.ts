import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('发现资料与排序', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：资料轮询补齐标签、五种排序和停止恢复`, { timeout: 60000 }, async () => {
    const opened = await visit(browser, '/?jav=1', viewport);
    const { page } = opened;
    const requests: URLSearchParams[] = [];
    let details = 0, status = 'running', completed = false;
    try {
      await settle(page);
      await page.route('**/api/taste?*', route => route.fulfill({ json: { summary: { history_visits: 20 }, rankings: {} } }));
      await page.route('**/api/jav/detail?*', route => { details++; return route.fulfill({ json: {} }) });
      await page.route('**/api/jav/discoveries?*', route => {
        const params = new URL(route.request().url()).searchParams;
        requests.push(params);
        if (params.has('metadata_cancel')) status = 'stopped';
        else if (params.has('metadata_refresh')) status = completed ? 'complete' : 'running';
        else if (params.has('metadata_job_id') && status === 'running') { status = 'complete'; completed = true }
        const items = [777, 778].map((id, index) => ({ id, code: `DEMO-${id}`, title: '示例推荐', performers: '', studio: '',
          release_date: `2026-10-${20 + index}`, has_cover: false, cover_url: '', wanted: false,
          tags: completed ? ['恋足'] : [], taste_score: index ? 0 : 0.004, ranking_score: index ? -0.02 : 0.003,
          source_score: completed ? 4.2 : null, reasons: ['匹配标签'] }));
        if (params.get('direction') === 'asc') items.reverse();
        return route.fulfill({ json: { items, candidate_total: 2, limited: false,
          ranking_method: params.get('sort') === 'taste' ? 'mmr' : 'field-sort',
          metadata_job: { job_id: 'fixed-batch', status, completed: completed ? 2 : 0, total: 2,
            failed: 0, requests: completed ? 4 : 0, bytes: 1000, message: status === 'stopped' ? '作品资料读取已停止' : '' }, poll_after_ms: 1000 } });
      });
      await page.goto(new URL('/jav?tab=discover', page.url()).href);
      await page.getByRole('button', { name: '停止读取', exact: true }).waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-jav-filter]').getByRole('button', { name: '我的收藏', exact: true }).count(), 1);
      await page.locator('[data-catalog-tag="恋足"]').waitFor({ state: 'visible' });
      assert.equal(details, 0);
      assert.ok(requests.some(params => params.get('metadata_job_id') === 'fixed-batch'));
      const cards = page.locator('[data-jav-card]');
      assert.equal(await cards.first().locator('[data-jav-taste-score]').textContent(), '探索');
      const score = cards.first().getByRole('button', { name: '推荐分 0，探索', exact: true });
      await score.scrollIntoViewIfNeeded();
      await score.focus();
      await page.getByRole('tooltip').filter({ hasText: '口味 0.004 · 排序 0.003 · 来源评分 4.2 / 5' }).waitFor({ state: 'visible' });
      const otherScore = cards.last().getByRole('button', { name: '推荐分 0，探索', exact: true });
      await otherScore.scrollIntoViewIfNeeded();
      await otherScore.focus();
      await page.getByRole('tooltip').filter({ hasText: '口味 0.000 · 排序 -0.020' }).waitFor({ state: 'visible' });
      await page.getByRole('button', { name: '按口味匹配排序', exact: true }).focus();
      assert.equal(await cards.first().getByRole('button', { name: '恋足', exact: true }).count(), 1);
      for (const [sort, name, direction] of [
        ['source_score', '按来源评分排序', 'desc'], ['duration', '按时长排序', 'desc'],
        ['code', '按番号排序', 'asc'], ['release_date', '按发行日期排序', 'desc'], ['taste', '按口味匹配排序', 'desc'],
      ]) {
        await Promise.all([page.getByRole('button', { name, exact: true }).click(),
          page.waitForResponse(response => { const params = new URL(response.url()).searchParams;
            return response.url().includes('/api/jav/discoveries?') && params.get('sort') === sort && params.get('direction') === direction })]);
        assert.equal(await page.getByRole('button', { name, exact: true }).getAttribute('aria-pressed'), 'true');
      }
      await page.getByRole('button', { name: '按口味匹配排序', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('[data-jav-card] h3')?.textContent?.startsWith('DEMO-778'));
      completed = false; status = 'running';
      await page.getByRole('button', { name: '更新资料', exact: true }).click();
      await page.getByRole('button', { name: '停止读取', exact: true }).click();
      await page.getByText('作品资料读取已停止', { exact: true }).waitFor({ state: 'visible' });
      const count = requests.length;
      await page.waitForTimeout(2200);
      assert.equal(requests.length, count, '停止后没有自动重启或轮询');
      completed = true;
      await page.getByRole('button', { name: '重新读取资料', exact: true }).click();
      await page.getByRole('button', { name: '更新资料', exact: true }).waitFor({ state: 'visible' });
      await settle(page);
      assert.equal(details, 0);
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1);
      assert.deepEqual(opened.problems.filter(line => !line.includes('/api/links/check')), []);
    } finally { await opened.close() }
  });
});
