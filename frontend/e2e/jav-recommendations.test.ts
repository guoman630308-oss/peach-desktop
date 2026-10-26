import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';

describe('JAV 推荐与加载结构', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：关闭馆藏详情保留探索页与筛选位置`, { timeout: 60_000 }, async () => {
    const opened = await openItemPage(browser, '/', viewport, { ready: '[data-media-grid]' });
    const { page } = opened;
    const reads: string[] = [];
    try {
      await page.route('**/api/taste?*', route => route.fulfill({ json: { summary: {}, rankings: {} } }));
      await page.route('**/api/jav/discoveries?*', route => {
        reads.push(route.request().url());
        return route.fulfill({ json: { items: Array.from({ length: 24 }, (_, index) => ({
          id: index + 100, asset_id: ITEM.plain, code: `PCH-${index + 100}`, title: '探索作品',
          performers: '来源原名', performer_refs: [{ id: 42, name: '规范姓名', has_image: false }],
          has_cover: false, wanted: false, tags: ['恋足'], taste_score: 0.8, release_date: '2024-06-03',
        })) } });
      });
      await page.goto(new URL('/jav?tab=discover', page.url()).href);
      const section = page.getByRole('region', { name: '为你推荐', exact: true });
      const search = page.getByRole('searchbox', { name: '筛选推荐', exact: true });
      await section.locator('[data-jav-card]').first().waitFor();
      await page.getByRole('button', { name: '馆藏探索', exact: true }).click();
      await page.getByRole('button', { name: '按发行日期排序', exact: true }).click();
      await section.locator('[data-catalog-tag="恋足"]').click();
      await search.fill('PCH');
      await section.evaluate(node => { (node as HTMLElement).dataset.returnProbe = 'kept' });
      const count = reads.length;
      for (const exit of ['close', 'escape', 'back']) {
        const link = section.locator('[data-jav-card]').nth(8).locator('[data-jav-cover-box]');
        await link.scrollIntoViewIfNeeded();
        const top = await page.evaluate(() => window.scrollY);
        await link.click();
        await page.locator('#stage[open] [data-item-side]').waitFor();
        assert.equal(await section.getAttribute('data-return-probe'), 'kept');
        if (exit === 'close') await page.locator('#closeStage').click();
        else if (exit === 'escape') await page.keyboard.press('Escape');
        else await page.goBack();
        await page.waitForFunction(() => location.pathname === '/jav' && !document.querySelector('#stage[open]'));
        assert.equal(await search.inputValue(), 'PCH');
        assert.equal(await section.locator('[data-entity-tag="library"]').getAttribute('aria-pressed'), 'true');
        assert.equal(await section.locator('[data-catalog-tag="恋足"]').getAttribute('aria-pressed'), 'true');
        assert.equal(await section.locator('[data-entity-sort="release_date"]').getAttribute('aria-pressed'), 'true');
        assert.equal(await section.getAttribute('data-return-probe'), 'kept');
        assert.equal(reads.length, count);
        assert.ok(Math.abs(await page.evaluate(() => window.scrollY) - top) < 2);
      }
      assert.deepEqual(opened.problems.filter(line => !line.includes('VIDEOJS')), []);
    } finally { await opened.close() }
  });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：按实体身份推荐，封面骨架占据卡片尺寸`, { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/?jav=1', viewport);
    const { page } = opened;
    let release!: () => void;
    const responseReady = new Promise<void>(resolve => { release = resolve; });
    try {
      await settle(page);
      await page.context().addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false, theme: 'dark', javLayout: 'big' })));
      await page.route('**/api/taste?*', async route => {
        await responseReady;
        await route.fulfill({ json: { summary: { history_visits: 20 }, rankings: {
          peach_performers: [{ entity_id: 42, name: '规范姓名', score: 8 }],
        } } });
      });
      await page.route('**/api/jav/discoveries?*', async route => {
        await responseReady;
        await route.fulfill({ json: { items: [
          { id: 777, code: 'DEMO-777', title: '演示新作', performers: '來源姓名' },
          { id: 778, code: 'DEMO-778', title: '很长的作品标题用于检查截断与卡片等高'.repeat(8), performers: '來源姓名' },
          { id: 779, code: 'DEMO-779', title: '短标题', performers: '來源姓名' },
        ].map((item, index) => ({ ...item, performer_refs: [{ id: 42, name: '规范姓名', has_image: false }],
          link: 'https://example.com/work', release_date: '2010-10-01', has_cover: false, wanted: false,
          score: 1, tags: ['恋足'], reasons: ['常看的女优：规范姓名'],
          ...(index === 0 ? { has_cover: true } : index === 1 ? { cover_url: 'https://example.com/sleeve.svg' } : {}),
          poster_box: { x0: 800, y0: 50, x1: 1460, y1: 950, px: [1480, 1000], method: 'manual' },
        })) } });
      });
      const sleeve = '<svg xmlns="http://www.w3.org/2000/svg" width="1480" height="1000"><rect width="1480" height="1000" fill="#135590"/><rect x="800" y="50" width="660" height="900" fill="#f4ad43"/><text x="1050" y="500" font-size="100">FRONT</text></svg>';
      await page.route('**/cover?*', route => route.fulfill({ contentType: 'image/svg+xml', body: sleeve }));
      await page.route('https://example.com/sleeve.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: sleeve }));
      await page.goto(new URL('/jav?tab=discover', page.url()).href, { waitUntil: 'load' });
      await page.locator('[data-jav-skeleton-cover]').first().waitFor();
      const cover = await page.locator('[data-jav-skeleton-cover]').first().boundingBox();
      assert.ok(cover && cover.width >= 100 && cover.height >= 60, JSON.stringify(cover));
      assert.ok(cover && Math.abs(cover.width / cover.height - 0.75) < 0.02, '大图封面占位应与馆藏正封同一比例');
      const skeletonWidth = await page.locator('[data-skeleton="jav-discover"]').first().boundingBox();
      const skeletonCard = await page.locator('[data-jav-skeleton-card]').first().boundingBox();
      const searchBox = await page.locator('input[aria-label="筛选推荐"]').locator('xpath=../../..').boundingBox();
      const main = await page.locator('#main').boundingBox();
      assert.ok(skeletonWidth && main && skeletonWidth.width >= main.width - 40);
      const pendingFilter = await page.locator('[data-jav-filter]').boundingBox();
      const pendingBottom = await page.locator('[data-jav-filter] [data-filter-row="bottom"]').boundingBox();
      const pendingSize = await page.locator('[data-jav-filter] [data-entity-layout]').boundingBox();
      const pendingShortcut = await page.locator('[data-jav-filter] [data-shortcuts-trigger]').boundingBox();
      assert.ok(pendingShortcut && pendingShortcut.width === 30 && pendingShortcut.height === 30, JSON.stringify(pendingShortcut));
      const tagPlaceholders = page.locator('[data-jav-filter] [data-catalog-tags] [data-skeleton="tag"]');
      assert.equal(await tagPlaceholders.count(), 6);
      const pendingTags = await tagPlaceholders.evaluateAll(nodes => nodes.map(node => {
        const style = getComputedStyle(node), box = node.getBoundingClientRect();
        return { height: box.height, y: box.y, pointer: style.pointerEvents, position: style.position, overflow: style.overflow };
      }));
      assert.ok(pendingTags.every(chip => chip.height === 30 && chip.y === pendingTags[0].y && chip.pointer === 'none'), JSON.stringify(pendingTags));
      assert.ok(pendingTags.every(chip => chip.position === 'relative' && chip.overflow === 'hidden'), '标签光效在各自占位内定位和裁切');
      const avatarShape = await page.locator('[data-jav-skeleton-avatar]').first().evaluate(node => {
        const style = getComputedStyle(node); return { width: style.width, height: style.height, radius: style.borderRadius };
      });
      assert.deepEqual(avatarShape, { width: '38px', height: '38px', radius: '50%' });
      assert.deepEqual(await page.locator('[data-jav-skeleton-actions]').first().locator('.skeleton').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).height)), ['36px', '36px', '36px']);
      assert.deepEqual(await page.locator('[data-jav-skeleton-card]').first().locator('[data-jav-skeleton-text]').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).height)), ['20px', '20px', '20px']);
      assert.equal(await page.locator('[data-jav-skeleton-cover]').first().evaluate(node => getComputedStyle(node).borderRadius),
        await page.locator('#main').evaluate(node => getComputedStyle(node).getPropertyValue('--surface-radius').trim()));
      assert.equal(await page.locator('[data-jav-filter] [data-entity-sep]').count(), 1);
      assert.equal(await page.locator('[data-jav-filter] [data-entity-sort]').count(), 5);
      assert.equal(await page.locator('[data-jav-filter]').getByText('正在读取', { exact: true }).count(), 0);
      await page.screenshot({ path: `../build/jav-discover-pending-${viewport.name}.png` });
      release();
      await page.getByRole('heading', { name: 'DEMO-777 演示新作', exact: true }).waitFor();
      const search = page.getByRole('searchbox', { name: '筛选推荐', exact: true });
      const cards = page.getByRole('region', { name: '为你推荐', exact: true }).locator('article');
      assert.equal(await cards.count(), 3);
      assert.equal(await cards.first().locator('[data-media-who]').textContent(), '规范姓名');
      assert.equal(await cards.first().locator('[data-media-who]').getAttribute('title'), '规范姓名');
      assert.equal(await cards.first().locator('[data-media-release]').textContent(), '2010-10-01');
      const metadata = await cards.first().locator('[data-media-meta]').boundingBox();
      const title = await cards.first().locator('h3').boundingBox();
      const face = await cards.first().locator('[data-media-avatar]').boundingBox();
      const inset = await cards.first().locator('[data-media-meta]').evaluate(node => parseFloat(getComputedStyle(node).paddingTop));
      assert.ok(metadata && title && face && title.x >= face.x + face.width && Math.abs(title.y - metadata.y - inset) < 2, JSON.stringify({ metadata, title, face, inset }));
      await page.screenshot({ path: `../build/jav-layout-${viewport.name}.png` });
      const refresh = page.locator('[data-jav-filter] [data-jav-metadata-update]');
      assert.equal(await refresh.count(), 1);
      assert.equal(await refresh.textContent(), '');
      assert.equal(await cards.first().locator('[data-jav-taste-score]').textContent(), '强烈推荐');
      const tierBox = await cards.first().locator('[data-jav-taste-score]').boundingBox();
      const tagsBox = await cards.first().locator('[data-jav-tags]').boundingBox();
      const contentTag = await cards.first().locator('[data-media-tag]').first().boundingBox();
      assert.ok(tierBox && tagsBox && contentTag && Math.abs(tierBox.y - contentTag.y) <= 4
        && tierBox.x + tierBox.width <= contentTag.x, '推荐档位与内容标签同行，排在内容标签前');
      const recommendation = cards.first().getByRole('button', { name: '推荐分 100，强烈推荐', exact: true });
      await recommendation.focus();
      await page.getByRole('tooltip').filter({ hasText: '常看的女优：规范姓名' }).waitFor({ state: 'visible' });
      await recommendation.evaluate(node => (node as HTMLButtonElement).blur());
      for (const cover of await cards.locator('img.poster').all()) await cover.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => [...document.querySelectorAll<HTMLImageElement>('[data-jav-card] img.poster')].every(img => img.complete && img.naturalWidth > 0));
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `../build/jav-discover-${viewport.name}.png` });
      const frames = await cards.evaluateAll(nodes => nodes.slice(0, 2).map(node => {
        const pic = node.querySelector('[data-jav-cover-box]')!.getBoundingClientRect();
        const img = node.querySelector<HTMLImageElement>('img.poster')!;
        const bounds = img.getBoundingClientRect();
        const scale = bounds.width / img.naturalWidth;
        return { center: bounds.left + (800 + 660 / 2) * scale, expected: pic.left + pic.width / 2,
          top: bounds.top + 50 * scale, y: pic.top, height: 900 * scale, expectedHeight: pic.height,
          position: getComputedStyle(img).position, slot: getComputedStyle(img.parentElement!).position };
      }));
      for (const frame of frames) {
        assert.equal(frame.position, 'absolute');
        assert.equal(frame.slot, 'absolute');
        assert.ok(Math.abs(frame.center - frame.expected) <= 1, JSON.stringify(frame));
        assert.ok(Math.abs(frame.top - frame.y) <= 1 && Math.abs(frame.height - frame.expectedHeight) <= 1, JSON.stringify(frame));
      }
      const liveFilter = await page.locator('[data-jav-filter]').boundingBox();
      const liveBottom = await page.locator('[data-jav-filter] [data-filter-row="bottom"]').boundingBox();
      const liveSize = await page.locator('[data-jav-filter] [data-entity-layout]').boundingBox();
      const liveShortcut = await page.locator('[data-jav-filter] [data-shortcuts-trigger]').boundingBox();
      for (const [pending, live] of [[pendingFilter, liveFilter], [pendingBottom, liveBottom], [pendingSize, liveSize], [pendingShortcut, liveShortcut]]) {
        assert.ok(pending && live && Math.abs(pending.y - live.y) <= 1 && Math.abs(pending.height - live.height) <= 1, JSON.stringify({ pending, live, pendingBottom, liveBottom, pendingSize, liveSize, pendingShortcut, liveShortcut }));
      }
      const controls = await page.locator('[data-jav-filter] [data-entity-sorts]').boundingBox();
      assert.ok(liveSize && controls && liveSize.x >= controls.x && liveSize.x + liveSize.width <= controls.x + controls.width);
      assert.equal(await page.locator('[data-jav-filter] [data-catalog-tag]').first().evaluate(node => getComputedStyle(node).borderStyle), 'dashed');
      assert.equal(await tagPlaceholders.count(), 0);
      assert.equal(await page.locator('[data-jav-filter] [data-catalog-tag]').first().evaluate(node => node.getBoundingClientRect().height), pendingTags[0].height);
      const heights = await cards.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
      if (!viewport.mobile) assert.ok(Math.max(...heights) - Math.min(...heights) <= 1, JSON.stringify(heights));
      if (viewport.mobile) {
        assert.ok(skeletonCard && skeletonCard.height >= Math.min(...heights) - 2
          && skeletonCard.height <= Math.max(...heights) + 2, JSON.stringify({ skeletonCard, heights }));
      } else {
        assert.ok(skeletonCard && Math.abs(skeletonCard.height - heights[0]!) <= 2, JSON.stringify({ skeletonCard, heights }));
      }
      assert.deepEqual(await search.locator('xpath=../../..').boundingBox(), searchBox);
      assert.equal(await cards.nth(1).locator('h3').evaluate(node => getComputedStyle(node).webkitLineClamp), '2');
      const lookup = page.getByRole('button', { name: '查找资源', exact: true }).first();
      assert.notEqual(await lookup.evaluate(node => getComputedStyle(node).backgroundColor),
        await cards.first().getByRole('button', { name: '查找资源', exact: true }).evaluate(node => getComputedStyle(node).backgroundColor));
      const avatar = cards.first().getByRole('link', { name: '打开女优页：规范姓名', exact: true });
      assert.equal(await avatar.getAttribute('href'), '/performers/42');
      assert.equal(await avatar.locator('.ini').textContent(), '规');
      const avatarBox = await avatar.boundingBox();
      assert.ok(avatarBox && avatarBox.width === 38 && avatarBox.height === 38, JSON.stringify(avatarBox));
      if (!viewport.mobile) {
        const before = await avatar.evaluate(node => getComputedStyle(node).boxShadow);
        await avatar.hover();
        await page.waitForFunction(() => {
          const face = document.querySelector('[data-jav-card] [data-media-avatar]');
          return face && getComputedStyle(face).boxShadow !== 'none';
        });
        assert.notEqual(await avatar.evaluate(node => getComputedStyle(node).boxShadow), before);
        await cards.first().hover();
        assert.notEqual(await cards.first().evaluate(node => getComputedStyle(node).boxShadow), 'none');
      }
      const source = cards.first().getByRole('link', { name: '作品来源', exact: true });
      const buttonShape = (node: Element) => {
        const style = getComputedStyle(node);
        return { height: style.height, border: style.borderWidth, radius: style.borderRadius,
          background: style.backgroundColor };
      };
      assert.deepEqual(await source.evaluate(buttonShape),
        await cards.first().getByRole('button', { name: '查找资源', exact: true }).evaluate(buttonShape));
      assert.equal(await source.getAttribute('target'), '_blank');
      assert.equal(await source.locator('svg').count(), 1);
      await search.fill('DEMO-777');
      await search.press('Enter');
      assert.equal(await cards.count(), 1);
      await search.fill('');
      await page.waitForFunction(() => document.querySelectorAll('[aria-label="为你推荐"] article').length === 3);
      await page.locator('label:has(input[aria-label="小图"])').click();
      const smallCover = await cards.first().locator('[data-jav-cover-box]').boundingBox();
      assert.ok(smallCover && Math.abs(smallCover.width / smallCover.height - 16 / 9) < 0.02);
      assert.equal(await cards.first().locator('img.poster').evaluate(node => getComputedStyle(node).objectFit), 'contain');
      await page.locator('label:has(input[aria-label="大图"])').click();
      await cards.first().getByRole('button', { name: '恋足', exact: true }).click();
      await page.getByRole('button', { name: '清除筛选', exact: true }).click();
      assert.equal(await cards.count(), 3);
      await cards.first().getByRole('button', { name: '查找资源', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: '关闭资源面板', exact: true }).click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await settle(page);
      assert.equal(await page.locator('[data-jav-skeleton-cover]').count(), 0);
      await search.fill('规范姓名');
      await search.press('Enter');
      await page.getByRole('heading', { name: 'DEMO-777 演示新作', exact: true }).waitFor();
      const geometry = await layout(page);
      assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1);
      assert.deepEqual(geometry.offenders, []);
      assert.deepEqual(opened.problems.filter(line => !line.includes('/api/links/check')), []);
      await cards.first().locator('[data-media-meta]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `../build/jav-card-${viewport.name}.png` });
      await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) });
      await page.screenshot({ path: `../build/jav-discover-${viewport.name}.png` });
    } finally { release(); await opened.close(); }
  });
});
