import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, requiredEnv, settle, VIEWPORTS } from './harness.ts';
import { JAV_DETAIL, mockJavDetail } from './jav-detail-fixture.ts';

describe('库外 JAV 作品详情', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  it('评论异步展开与收起时详情正文留在滚动区域', { timeout: 60000 }, async () => {
    const context = await browser.newContext({ viewport: { width: 1730, height: 1032 }, reducedMotion: 'no-preference' });
    try {
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await mockJavDetail(page);
      let releaseReviews!: () => void;
      const reviewsReady = new Promise<void>(resolve => { releaseReviews = resolve });
      await page.route(/\/api\/jav\/reviews\?/, async route => {
        await reviewsReady;
        await route.fulfill({ json: { ok: true, page: 1, more: false, items: Array.from({ length: 10 }, (_, i) => ({
          id: String(i), username: '来源评论者', created_at: '2026-10-12', score: 4,
          content: '评论正文及作品信息。'.repeat(40),
        })) } });
      });
      await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001');
      const side = page.locator('[data-jav-info]');
      await side.locator('h1').waitFor();
      await page.locator('#stage').evaluate(node => Promise.all(node.getAnimations().map(animation => animation.finished)));
      const toggle = page.locator('[data-jav-reviews] summary');
      const snapshot = () => side.evaluate(node => {
        const side = node.getBoundingClientRect(), title = node.querySelector('h1')!.getBoundingClientRect();
        return { height: side.height, scroll: node.scrollTop, titleVisible: title.bottom > side.top && title.top < side.bottom };
      });
      const initial = await snapshot();
      await toggle.click();
      const loading = page.locator('[data-skeleton="reviews"]');
      await loading.waitFor();
      const placeholder = await loading.evaluate(node => ({ height: node.getBoundingClientRect().height,
        rows: node.querySelectorAll('[data-jav-review-skeleton]').length,
        radii: [...node.querySelectorAll('.skeleton-sheen')].map(bar => parseFloat(getComputedStyle(bar).borderRadius)),
      }));
      releaseReviews();
      assert.equal(placeholder.rows, 3);
      assert.ok(placeholder.height >= 240, JSON.stringify(placeholder));
      assert.ok(placeholder.radii.every(radius => radius > 0), JSON.stringify(placeholder));
      await page.locator('[data-jav-review]').first().waitFor();
      await loading.waitFor({ state: 'detached' });
      const expanded = await snapshot();
      assert.ok(expanded.titleVisible && Math.abs(expanded.height - initial.height) < 1 && Math.abs(expanded.scroll - initial.scroll) < 1,
        JSON.stringify({ initial, expanded }));
      await side.evaluate(node => { node.scrollTop = node.scrollHeight });
      await toggle.scrollIntoViewIfNeeded();
      await toggle.click();
      await page.locator('[data-jav-review]').first().waitFor({ state: 'hidden' });
      await side.evaluate(node => { node.scrollTop = 0 });
      assert.ok((await snapshot()).titleVisible);
      assert.deepEqual(errors, []);
      await page.screenshot({ path: '../build/jav-comments-expanded.png' });
    } finally { await context.close() }
  });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：页面重建后评论与资源复用成功快照`, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: 'reduce' });
      try {
        const page = await context.newPage();
        await mockJavDetail(page);
        await page.route(/\/api\/jav\/detail\?/, route => route.fulfill({ json: { ...JAV_DETAIL,
          release_date: '2020-01-01', preview_count: 0, source_image: false } }));
        let reviews = 0, resources = 0;
        const checked = new Date().toISOString();
        await page.route(/\/api\/jav\/reviews\?/, route => {
          reviews++;
          return route.fulfill({ json: { ok: true, page: 1, more: false, checked_at: checked,
            items: [{ id: 'saved', username: '示例用户', content: '已保存的评论正文', created_at: checked }] } });
        });
        await page.route(/\/api\/jav\/resources\?/, route => {
          resources++;
          return route.fulfill({ json: { state: 'ready', items: [], warnings: [], error: '', checked_at: checked } });
        });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001');
        for (let visit = 0; visit < 2; visit++) {
          await page.locator('[data-jav-info] h1').waitFor();
          await page.locator('[data-jav-reviews] summary').click();
          await page.getByText('已保存的评论正文').waitFor();
          await page.locator('[data-jav-resources] summary').click();
          await page.locator('[data-want-magnets]').scrollIntoViewIfNeeded();
          await page.getByText('暂未找到资源链接').waitFor();
          await settle(page, '#stage');
          assert.deepEqual({ reviews, resources }, { reviews: 1, resources: 1 });
          assert.ok((await layout(page)).scrollWidth <= viewport.width + 1);
          if (visit === 0) await page.reload();
        }
        const refreshed = page.waitForResponse(response => response.url().includes('/api/jav/reviews?') && response.url().includes('refresh=1'));
        await page.getByRole('button', { name: '刷新评论', exact: true }).click();
        await refreshed;
        await settle(page, '#stage');
        assert.deepEqual({ reviews, resources }, { reviews: 2, resources: 1 });
        await page.route(/\/api\/jav\/reviews\?/, route => {
          reviews++;
          return route.fulfill({ json: { ok: false, page: 1, items: [], more: false, message: '来源暂时不可用' } });
        });
        await page.getByRole('button', { name: '刷新评论', exact: true }).click();
        await page.getByText('来源暂时不可用').waitFor();
        assert.ok(await page.getByText('已保存的评论正文').isVisible());
        assert.equal(reviews, 3);
      } finally { await context.close() }
    });
    it(`${viewport.name}：加载骨架的两栏保持在详情画框内`, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: 'reduce' });
      let release!: () => void;
      const held = new Promise<void>(resolve => { release = resolve });
      try {
        await context.addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false })));
        const page = await context.newPage();
        await mockJavDetail(page);
        await page.route(/\/api\/jav\/detail\?/, async route => {
          await held;
          await route.fulfill({ json: { ...JAV_DETAIL, preview_count: 0 } });
        });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001', { waitUntil: 'domcontentloaded' });
        const skeleton = page.locator('#stage [data-skeleton="detail"]');
        await skeleton.waitFor();
        const bounds = await skeleton.evaluate(node => {
          const grid = node.querySelector('[data-stage-grid]')!.getBoundingClientRect();
          const media = node.querySelector('[data-stage-media]')!.getBoundingClientRect();
          const side = node.querySelector('[data-stage-side]')!.getBoundingClientRect();
          const shell = node.closest('[data-stage]')!.getBoundingClientRect();
          const lines = [...node.querySelectorAll('[data-stage-side] .skeleton')].map(line => line.getBoundingClientRect());
          return { grid: { width: grid.width, height: grid.height }, shellHeight: shell.height, mediaHeight: media.height,
            mediaWithin: media.left >= grid.left - 1 && media.right <= grid.right + 1,
            columnsSeparate: side.top >= media.bottom - 1 || media.right <= side.left + 1,
            linesWithin: lines.every(line => line.left >= side.left - 1 && line.right <= side.right + 1) };
        });
        assert.ok(bounds.mediaWithin && bounds.columnsSeparate && bounds.linesWithin, JSON.stringify(bounds));
        await page.screenshot({ path: `../build/jav-detail-pending-${viewport.name}.png` });
        release();
        await page.locator('#stage [data-jav-detail] h1').waitFor();
        const ready = await page.locator('#stage [data-jav-detail][data-stage-grid]').boundingBox();
        const shell = await page.locator('#stage').boundingBox();
        const media = await page.locator('#stage [data-jav-detail] > [data-stage-media]').boundingBox();
        assert.ok(ready && shell && media && Math.abs(ready.width - bounds.grid.width) <= 1
          && Math.abs(shell.height - bounds.shellHeight) <= 1 && Math.abs(media.height - bounds.mediaHeight) <= 1,
          JSON.stringify({ ready, bounds }));
      } finally { release(); await context.close() }
    });
    it(`${viewport.name} 新作在站内打开、预览切换、评论分页与关闭返回`, { timeout: 60000 }, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, isMobile: viewport.mobile, hasTouch: viewport.mobile, reducedMotion: 'reduce' });
      try {
        await context.addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false, feedAutoScroll: false })));
        const page = await context.newPage();
        const errors: string[] = [];
        page.on('pageerror', error => errors.push(error.message));
        await mockJavDetail(page);
        await page.route(/\/api\/jav\/image\?/, async route => {
          const portrait = new URL(route.request().url()).searchParams.get('image') === '1';
          if (portrait) await new Promise(resolve => setTimeout(resolve, 350));
          const [width, height] = portrait ? [600, 900] : [900, 600];
          await route.fulfill({ json: { ok: true, image: 'data:image/svg+xml,' + encodeURIComponent(
            `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#777"/></svg>`) } });
        });
        await page.route(/\/api\/entity\/shapes/, route => route.fulfill({ json: { home: { feed: true }, entities: [] } }));
        await page.route(/\/api\/feeds\/discoveries\?/, route => route.fulfill({ json: { ok: true, items: [
          { id: 1, code: 'EXM-001', title: '示例新作', performers: '示例人物', release_date: '2026-10-30' },
        ] } }));
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/');
        const card = page.locator('#feedNew [data-feed-id="1"]');
        await card.waitFor({ state: 'visible' });
        await card.getByRole('link', { name: '打开 EXM-001 的作品详情' }).click();
        await page.waitForURL('**/works/EXM-001');
        await page.locator('#stage [data-jav-detail] h1').waitFor({ state: 'visible' });
        assert.ok(await page.locator('#stage').evaluate(el => (el as HTMLDialogElement).open));
        assert.equal(await card.count(), 1, '原列表留在详情底下');
        await page.getByRole('img', { name: 'EXM-001 封面', exact: true }).waitFor({ state: 'visible' });
        const shared = await page.locator('[data-jav-info]').evaluate(el => {
          const faces = [...el.querySelectorAll('[data-identity-primary] [data-id-face]')].map(face => {
            const box = face.getBoundingClientRect(); return { width: box.width, height: box.height, y: box.y };
          });
          const stars = [...el.querySelectorAll('[data-jav-rating] [data-rate]')];
          const code = el.querySelector('[data-detail-title] b')!.getBoundingClientRect();
          const range = document.createRange();
          const text = el.querySelector('[data-detail-title]')!.lastChild!;
          range.setStart(text, 1); range.setEnd(text, 2);
          return { faces, stars: stars.length, filled: stars.filter(star => star.getAttribute('data-on') === 'true').length,
            icons: el.querySelectorAll('[data-stage-meta] [data-spec-item] svg').length,
            codeY: code.y, titleY: range.getBoundingClientRect().y,
            tag: el.querySelector('[data-jav-tags] [data-tag]')?.textContent,
            tagCat: el.querySelector('[data-jav-tags] [data-tag-cat]')?.getAttribute('data-tag-cat') };
        });
        assert.equal(shared.faces.length, 3, '女优、未知出演者与厂牌各有独立身份格');
        assert.equal(await page.locator('[data-id-gender="male"]').count(), 0);
        for (const [gender, label] of [['female', '女优'], ['unknown', '出演者']]) {
          assert.equal(await page.locator(`[data-id-gender="${gender}"] [data-id-label]`).textContent(), label);
          assert.equal(await page.locator(`[data-id-gender="${gender}"] [data-id-face]`).count(), 1);
        }
        assert.ok(shared.faces.every(face => face.width === 46 && face.height === 46));
        assert.ok(shared.faces.every(face => Math.abs(face.y - shared.faces[0]!.y) < 1));
        assert.equal(shared.stars, 5);
        assert.equal(shared.filled, 4);
        assert.equal(await page.locator('[data-jav-art]').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(0, 0, 0)');
        await page.locator('[data-jav-rating] [data-rate="1"]').hover();
        const fraction = await page.locator('[data-jav-info] > [data-jav-rating] [data-rate="5"]').evaluate(el =>
          el.querySelector('[data-rating-fill]')!.getBoundingClientRect().width / el.querySelector('[data-rating-icon]')!.getBoundingClientRect().width);
        assert.ok(Math.abs(fraction - 0.4) < 0.01, '4.4 的第五颗星填充 40%');
        const starLayers = await page.locator('[data-jav-info] > [data-jav-rating] [data-rate]').evaluateAll(nodes => nodes.map(node => {
          const [outline, fill] = node.querySelectorAll('svg');
          const a = outline!.getBoundingClientRect(), b = fill!.getBoundingClientRect();
          return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y), Math.abs(a.width - b.width), Math.abs(a.height - b.height));
        }));
        assert.ok(starLayers.every(error => error < .5), JSON.stringify(starLayers));
        assert.equal(shared.icons, 2);
        assert.ok(Math.abs(shared.codeY - shared.titleY) < 2, '番号与标题在同一行开始');
        assert.equal(shared.tag, '痴女'); assert.equal(shared.tagCat, 'role');
        await page.locator('[data-id-gender="female"] [data-id-face] img').waitFor({ state: 'visible' });
        assert.equal(await page.locator('[data-id-cell="studio"]').evaluate(node => node.tagName), 'SPAN', '未登记厂牌只显示名称');
        assert.equal(await page.locator('[data-follow-image-dots] button').count(), 13);
        const dots = await page.locator('[data-follow-image-dots]').evaluate(el => {
          const box = el.getBoundingClientRect();
          const rect = el.querySelector('[aria-current="true"]')!.getBoundingClientRect();
          return el.scrollWidth > el.clientWidth && rect.left >= box.left && rect.right <= box.right;
        });
        assert.ok(dots, '圆点条可滚动且当前项完整可见');
        assert.equal(await page.locator('[data-carousel-video]').evaluate(el => el.getBoundingClientRect().width), 20);
        assert.equal(await page.locator('[data-media-thumbnails]').count(), 1);
        await page.locator('[data-media-thumbnail="1"] img').waitFor({ state: 'visible' });
        const indicator = page.locator('[data-follow-image-dots]');
        const stripBox = await indicator.boundingBox(), mainBox = await page.locator('[data-jav-picture]').boundingBox();
        assert.ok(stripBox && mainBox && stripBox.y >= mainBox.y + mainBox.height);
        await indicator.hover();
        await page.mouse.wheel(0, 180);
        await page.waitForFunction(() => (document.querySelector('[data-follow-image-dots]')?.scrollLeft || 0) > 0);
        await page.evaluate(() => {
          const frames: { width: number; height: number }[] = [];
          Object.assign(window, { detailHeightFrames: frames });
          const sample = () => { const frame = document.querySelector('[data-stage-media]');
            if (frame) frames.push({ width: innerWidth, height: frame.getBoundingClientRect().height });
            if (frames.length < 120) requestAnimationFrame(sample);
          }; sample();
        });
        const imageDot = page.getByRole('button', { name: '预览 2', exact: true });
        await page.keyboard.press('Tab');
        await imageDot.focus();
        assert.equal(await page.locator('[data-media-preview-tip]').count(), 0);
        assert.equal(await page.locator('[data-follow-image-item][aria-current="true"]').getAttribute('data-follow-image-item'), '-1', '聚焦预览保持当前图片');
        await imageDot.click();
        await page.getByRole('img', { name: 'EXM-001 预览 2', exact: true }).waitFor();
        await page.getByRole('button', { name: '封面', exact: true }).click();
        if (viewport.mobile) {
          await page.setViewportSize({ width: 350, height: viewport.height });
          assert.equal(await page.locator('[data-follow-image-dots] button').count(), 13);
          assert.equal(await page.locator('[data-follow-image-count]').count(), 0);
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await page.locator('[data-follow-image-dots]').waitFor({ state: 'visible' });
        }
        await page.getByRole('button', { name: '下一项媒体', exact: true }).click();
        await page.getByRole('img', { name: 'EXM-001 预览 1', exact: true }).waitFor({ state: 'visible' });
        await page.getByRole('button', { name: '上一项媒体', exact: true }).click();
        await page.getByRole('button', { name: '视频预览', exact: true }).first().click();
        await page.waitForFunction(() => { const video = document.querySelector<HTMLVideoElement>('[data-jav-video] video'); return video && video.readyState >= 1 && Number.isFinite(video.duration) && video.duration > 0 });
        assert.ok(await page.locator('[data-jav-video] video.vjs-tech').evaluate(video => (video as HTMLVideoElement).paused), '预览等待用户播放');
        const preview = page.locator('[data-jav-video]');
        const playerBox = await preview.locator('.video-js').boundingBox();
        assert.ok(playerBox && Math.abs(playerBox.width / playerBox.height - 16 / 9) < 0.02, JSON.stringify(playerBox));
        const indicatorBox = await page.locator('[data-follow-image-dots]').boundingBox();
        assert.ok(playerBox && indicatorBox && indicatorBox.y >= playerBox.y + playerBox.height, '视频 indicator 位于播放器下方');
        await preview.getByRole('button', { name: '播放', exact: true }).waitFor({ state: 'visible' });
        assert.equal(await preview.locator('.vjs-control-bar').evaluate(el => getComputedStyle(el).visibility), 'visible');
        await preview.locator('video.vjs-tech').evaluate(node => { (node as HTMLVideoElement).muted = true });
        await preview.getByRole('button', { name: '播放', exact: true }).click();
        await page.waitForFunction(() => (document.querySelector<HTMLVideoElement>('[data-jav-video] video')?.currentTime || 0) > 0);
        await preview.getByRole('button', { name: '暂停', exact: true }).click();
        assert.ok(await preview.locator('video.vjs-tech').evaluate(node => (node as HTMLVideoElement).paused));
        await preview.getByRole('button', { name: '设置', exact: true }).click();
        await preview.getByRole('menuitem', { name: /播放速度/ }).waitFor({ state: 'visible' });
        await preview.getByRole('button', { name: '设置', exact: true }).click();
        await page.getByRole('button', { name: '查看图片', exact: true }).click();
        assert.equal(await page.locator('[data-jav-video] video.vjs-tech').count(), 0, '切回图片卸下预览播放器');
        const frames = await page.evaluate(() => (window as unknown as { detailHeightFrames: { width: number; height: number }[] }).detailHeightFrames);
        assert.ok(frames.length > 3);
        for (const width of new Set(frames.map(frame => frame.width))) {
          const heights = frames.filter(frame => frame.width === width).map(frame => frame.height);
          assert.ok(Math.max(...heights) - Math.min(...heights) < 1, JSON.stringify({ width, heights }));
        }
        const reviewsToggle = page.locator('[data-jav-reviews] summary');
        const resourcesToggle = page.locator('[data-jav-resources] > details > summary');
        for (const toggle of [reviewsToggle, resourcesToggle]) {
          assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
          assert.equal(await toggle.locator('svg').count(), 2, '对象图标和展开箭头齐备');
        }
        const sourceButton = page.getByRole('button', { name: '作品来源', exact: true });
        assert.deepEqual(await page.locator('[data-jav-actions] button').allTextContents(), ['查找资源', '想要', '作品来源']);
        assert.ok(await sourceButton.evaluate(el => parseFloat(getComputedStyle(el).borderWidth) > 0));
        const side = page.locator('[data-jav-info]');
        const before = await page.locator('#stage').boundingBox();
        const title = await side.locator('h1').elementHandle();
        await reviewsToggle.evaluate(node => {
          const state = window as unknown as { commentFrames: { height: number; y: number; media: number }[]; commentSampling: boolean };
          state.commentFrames = []; state.commentSampling = true;
          node.addEventListener('pointerdown', () => {
            const sample = () => {
              const shell = document.querySelector('#stage')!.getBoundingClientRect();
              const media = document.querySelector('[data-jav-art]')!.getBoundingClientRect();
              state.commentFrames.push({ height: shell.height, y: shell.y, media: media.height });
              if (state.commentSampling && state.commentFrames.length < 600) requestAnimationFrame(sample);
            };
            requestAnimationFrame(sample);
          }, { once: true });
        });
        await reviewsToggle.click();
        await page.waitForFunction(() => document.querySelectorAll('[data-jav-review]').length === 10);
        assert.equal(await page.locator('[data-jav-review] img').count(), 0, '评论正文按文本呈现');
        assert.equal(await page.locator('[data-jav-review] time').first().textContent(), '2026-10-12');
        assert.equal(await page.locator('[data-jav-review] [data-rating-stars]').first().locator('[data-rate]').count(), 5);
        const centers = await page.locator('[data-jav-review-meta]').first().evaluate(node => [...node.children].map(child => {
          const box = child.getBoundingClientRect(); return box.y + box.height / 2;
        }));
        assert.ok(Math.max(...centers) - Math.min(...centers) < 1, JSON.stringify(centers));
        assert.ok(await title!.evaluate(node => node.isConnected), '评论加载保留标题节点');
        assert.ok(await side.locator('h1').isVisible(), '评论加载后详情标题可见');
        assert.ok(await page.locator('[data-jav-actions]').isVisible(), '评论加载后详情操作可见');
        if (!viewport.mobile) {
          const after = await page.locator('#stage').boundingBox();
          assert.ok(before && after && Math.abs(before.height - after.height) < 1 && Math.abs(before.y - after.y) < 1, '评论展开保持详情框几何');
        }
        await page.getByRole('button', { name: '更多评论', exact: true }).click();
        await page.waitForFunction(() => document.querySelectorAll('[data-jav-review]').length === 12);
        assert.equal(await page.getByRole('button', { name: '更多评论', exact: true }).count(), 0);
        await reviewsToggle.click();
        await page.locator('[data-jav-review]').first().waitFor({ state: 'hidden' });
        assert.equal(await reviewsToggle.getAttribute('aria-expanded'), 'false');
        await reviewsToggle.click();
        await page.locator('[data-jav-review]').last().waitFor({ state: 'visible' });
        const commentFrames = await page.evaluate(() => {
          const state = window as unknown as { commentFrames: { height: number; y: number; media: number }[]; commentSampling: boolean };
          state.commentSampling = false; return state.commentFrames;
        });
        assert.ok(commentFrames.length > 3);
        for (const field of ['height', 'y', 'media'] as const) {
          const values = commentFrames.map(frame => frame[field]);
          assert.ok(Math.max(...values) - Math.min(...values) < 1, `评论切换 ${field} 保持一致：${JSON.stringify(values)}`);
        }
        await resourcesToggle.click();
        await page.getByText('EXM-001 示例资源 1', { exact: false }).waitFor({ state: 'visible' });
        assert.equal(await page.locator('[data-jav-resources] [data-resource-protocol]:visible').count(), 3);
        await page.getByText('更多资源（3 条）', { exact: true }).click();
        await page.getByText('EXM-001 示例资源 6', { exact: true }).waitFor({ state: 'visible' });
        assert.equal(await page.locator('[data-jav-resources] [data-resource-protocol]:visible').count(), 6);
        const resources = await page.locator('[data-jav-resources] [data-resource-protocol]:visible').evaluateAll(rows => rows.map(row => {
          const box = row.getBoundingClientRect(), title = row.querySelector('[data-resource-main]')!.getBoundingClientRect();
          const actions = row.querySelector('[data-resource-actions]')!.getBoundingClientRect();
          return { width: box.width, titleWidth: title.width, titleBottom: title.bottom, actionsTop: actions.top, actionsRight: actions.right, right: box.right };
        }));
        assert.ok(resources.every(row => row.titleWidth >= row.width - 1 && row.actionsTop >= row.titleBottom - 1 && row.actionsRight <= row.right + 1), JSON.stringify(resources));
        const lines = await page.locator('[data-jav-resources] [data-resource-protocol]:visible').evaluateAll(rows => rows.map(row => {
          const style = getComputedStyle(row);
          return { width: style.borderBottomWidth, color: style.borderBottomColor,
            expected: getComputedStyle(row.closest('[data-want-magnets]')!).borderTopColor };
        }));
        assert.ok(lines.every(line => line.width === '1px' && line.color === line.expected), JSON.stringify(lines));
        assert.ok(await page.locator('[data-jav-resources]').getByRole('button', { name: '添加下载', exact: true }).first().isDisabled());
        assert.deepEqual(await page.locator('[data-resource-actions]').first().locator('button, a').allTextContents(), ['添加下载', '复制链接', '链接来源']);
        assert.equal(await page.locator('[data-resource-facts]').first().getByText('高清', { exact: true }).count(), 0);
        const facts = await page.locator('[data-resource-facts]').first().evaluate(node => {
          const boxes = [...node.children].map(child => child.getBoundingClientRect());
          return boxes.map(box => box.y);
        });
        assert.ok(Math.max(...facts) - Math.min(...facts) < 1, JSON.stringify(facts));
        await resourcesToggle.click();
        await page.locator('[data-jav-resources] [data-resource-protocol]').first().waitFor({ state: 'hidden' });
        await resourcesToggle.click();
        await page.locator('[data-jav-resources] [data-resource-protocol]').first().waitFor({ state: 'visible' });
        await page.getByRole('button', { name: '想要', exact: true }).click();
        await page.getByRole('button', { name: '取消想要', exact: true }).waitFor({ state: 'visible' });
        await page.getByRole('button', { name: '取消想要', exact: true }).click();
        await page.getByRole('button', { name: '想要', exact: true }).waitFor({ state: 'visible' });
        await settle(page, '#stage[open]');
        const geometry = await layout(page);
        assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1);
        assert.deepEqual(errors, []);
        await page.screenshot({ path: `../build/jav-detail-${viewport.name}.png` });
        await page.locator('#closeStage').click();
        await page.waitForURL(requiredEnv('PEACH_E2E_ORIGIN') + '/');
        await card.waitFor({ state: 'visible' });
        assert.equal(await page.locator('#stage').count(), 0);
        await page.goBack();
        await page.locator('#stage [data-jav-detail] h1').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        await page.waitForURL(requiredEnv('PEACH_E2E_ORIGIN') + '/');
        await card.getByRole('link', { name: '打开 EXM-001 的作品详情' }).click();
        const detailUrl = page.url();
        await page.locator('[data-id-cell="studio"]').click();
        assert.equal(page.url(), detailUrl);
        assert.ok(await page.locator('#stage').isVisible(), '未登记厂牌不跳转筛选');
      } finally { await context.close() }
    });
    it(`${viewport.name}：已登记厂牌打开资料页`, { timeout: 60000 }, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: 'reduce' });
      try {
        const page = await context.newPage();
        await mockJavDetail(page);
        await page.route(/\/api\/jav\/detail\?/, route => route.fulfill({ json: { ...JAV_DETAIL,
          preview_count: 0, source_image: false, studio_refs: [{ id: 50, name: '示例厂牌', has_logo: false }] } }));
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001');
        const studio = page.locator('button[data-id-cell="studio"]');
        await studio.waitFor();
        await studio.click();
        await page.waitForURL(url => url.pathname === '/studios/50');
        assert.equal(await page.locator('#stage').count(), 0);
      } finally { await context.close() }
    });
    it(`${viewport.name} 深链刷新与来源未取得仍可关闭`, { timeout: 60000 }, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: 'reduce' });
      try {
        const page = await context.newPage();
        await mockJavDetail(page);
        await page.addInitScript(() => {
          const heights: number[] = [];
          Object.assign(window, { coldDetailHeights: heights });
          const sample = () => {
            const dialog = document.querySelector<HTMLDialogElement>('#stage[open]');
            if (dialog) heights.push(dialog.clientHeight);
            if (heights.length < 180) requestAnimationFrame(sample);
          }; requestAnimationFrame(sample);
        });
        await page.route(/\/api\/jav\/detail\?/, async route => {
          await new Promise(resolve => setTimeout(resolve, 400));
          await route.fulfill({ json: { ...JAV_DETAIL, state: 'partial', source_image: false,
            preview_count: 0, message: '作品资料未取得，请稍后重试' } });
        });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001');
        await page.getByText('作品资料未取得，请稍后重试', { exact: true }).waitFor({ state: 'visible' });
        const heights = await page.evaluate(() => (window as unknown as { coldDetailHeights: number[] }).coldDetailHeights);
        assert.ok(heights.length > 5 && Math.max(...heights) - Math.min(...heights) < 1, JSON.stringify(heights));
        await page.reload();
        await page.getByText('作品资料未取得，请稍后重试', { exact: true }).waitFor({ state: 'visible' });
        await page.locator('#closeStage').click();
        await page.waitForURL('**/jav?tab=discover');
      } finally { await context.close() }
    });
    for (const readOnly of [false, true]) it(`${viewport.name}：下载服从数据库只读状态`, { timeout: 60000 }, async () => {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: 'reduce' });
      try {
        const page = await context.newPage();
        await mockJavDetail(page);
        await page.route('**/healthz', async route => {
          const response = await route.fetch();
          await route.fulfill({ json: { ...await response.json(), configurable: false, ledger_read_only: readOnly } });
        });
        let submitted: Record<string, unknown> | null = null;
        await page.route(/\/api\/downloads(?:\?|$)/, route => {
          if (route.request().method() === 'POST') {
            submitted = route.request().postDataJSON();
            return route.fulfill({ json: { ok: true, outcome: 'submitted', task: { id: 1 } } });
          }
          return route.fulfill({ json: { available: true, providers: [{ key: '115', label: '115', configured: true, target: '/fixture' }], tasks: [] } });
        });
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/works/EXM-001');
        assert.equal(await page.getByRole('button', { name: '想要', exact: true }).isDisabled(), readOnly);
        await page.locator('[data-jav-resources] > details > summary').click();
        const add = page.locator('[data-jav-resources]').getByRole('button', { name: '添加下载', exact: true }).first();
        await add.waitFor({ state: 'visible' });
        if (readOnly) assert.ok(await add.isDisabled());
        else {
          await page.waitForFunction(() => !document.querySelector<HTMLButtonElement>('[data-jav-resources] [data-resource-actions] button:last-child')?.disabled);
          await add.click();
          await page.getByRole('button', { name: '已添加', exact: true }).first().waitFor();
          assert.equal(submitted?.provider, '115');
          assert.equal(submitted?.code, 'EXM-001');
        }
      } finally { await context.close() }
    });
  }
});
