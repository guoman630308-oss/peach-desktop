/* 关注里「这一条出自哪个站」一律用站点图标表示，站名只落在图标的 alt／title 上。
 *
 * 守两处：详情下方视频合集的队列卡（出处图标、标记与时间同一行居中），以及卡片上的
 * 「另见」徽章（连同版本字样的状态标记外观）。演示库没有关注来源，这里按 `_group_payload`（`src/peach/web_follow.py`）
 * 造一组跨站的同一条：主条目在 Fanbox，另两条在 Rule34.xxx。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, settle, visit, VIEWPORTS, type Visit } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;

/** 1×1 的透明 PNG：站点图标的请求有图可落，量的是它排出来的几何。 */
const PIXEL = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

/** 一条可播的关注视频。字段以 `_item_payload` 为准。 */
const followVideo = (id: number, provider: string, label: string, publishedAt: string) => ({
  id, provider, provider_label: label, resource_provider: provider, source_id: 1,
  source_label: `演示 · ${label}`, external_id: String(id), title: `演示视频 ${id}`,
  author: null, summary: null, url: `https://example.com/${id}`, thumb_url: null,
  published_at: publishedAt, published_precision: 'exact', version: null, duration: 20,
  variant_kind: null, variant_label: null, status: 'new', asset_id: null,
  media_needs_credential: false, media_error: null, has_media: true, media_kind: 'video',
  width: null, height: null, playable: true, media_type: 'video/mp4', media_items: [],
  hidden_media: [], resource_urls: [], tags: [], detail_tags: [], tag_types: {},
});

const GROUP = {
  release_key: 'demo-release',
  primary: followVideo(1, 'fanbox', 'pixivFANBOX', '2026-09-16T00:20:00Z'),
  variants: [],
  duplicates: [
    followVideo(2, 'rule34xxx', 'Rule34.xxx', '2026-09-16T00:19:00Z'),
    followVideo(3, 'rule34xxx', 'Rule34.xxx', '2026-09-16T00:16:00Z'),
  ],
  providers: ['fanbox', 'rule34xxx'],
  has_wip: false,
  is_release: false,
  newest_at: '2026-09-16T00:20:00Z',
  stack: null,
};

/** 主条目在 Fanbox 却没有视频：卡面换成组里最新那条 Rule34.xxx，另见就该列 Fanbox 与 Pawchive。 */
const FRONTED = {
  ...GROUP,
  release_key: 'demo-fronted',
  primary: { ...followVideo(11, 'fanbox', 'pixivFANBOX', '2026-09-15T00:20:00Z'),
    has_media: false, media_kind: null, playable: false, duration: null },
  duplicates: [
    followVideo(12, 'rule34xxx', 'Rule34.xxx', '2026-09-15T00:19:00Z'),
    followVideo(13, 'pawchive', 'Pawchive', '2026-09-15T00:16:00Z'),
  ],
  providers: ['fanbox', 'pawchive', 'rule34xxx'],
  newest_at: '2026-09-15T00:20:00Z',
};

async function stubFollow(page: Page): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route((url) => url.pathname === '/api/follow', (route) => route.fulfill(json({
    groups: [GROUP, FRONTED], sources: [], counts: { new: 2, seen: 0, saved: 0, ignored: 0 },
    facets: { authors: [], providers: ['fanbox', 'pawchive', 'rule34xxx'] }, has_more: false,
  })));
  await page.route('**/api/follow/credentials', (route) => route.fulfill(json({ providers: [] })));
  /* 服务端的定时检查可能正在跑，那一趟会让检查键挂着 aria-busy，settle 等不到头。 */
  await page.route('**/api/follow/check', (route) => (route.request().method() === 'GET'
    ? route.fulfill(json({ status: 'idle' })) : route.fallback()));
  await page.route('**/source-icon**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  // 造出来的条目没有正片；这一条量的是队列卡，播放器拿到空响应即可。
  await page.route('**/follow-stream**', (route) => route.fulfill({ status: 204, body: '' }));
  await page.route('**/follow-qualities**', (route) => route.fulfill(json({})));
}

async function openFollow(browser: Browser, path: string, ready: string): Promise<Visit> {
  const opened = await visit(browser, path, DESKTOP);
  await stubFollow(opened.page);
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator(ready).first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 一行里每个子盒的竖直中线与文字；站名文字是否混在行里看的是这一行自己的 textContent。 */
async function lineGeometry(page: Page, selector: string) {
  return page.locator(selector).evaluateAll((lines) => lines.map((line) => {
    const mid = (node: Element | null) => {
      if (!node) return null;
      const box = node.getBoundingClientRect();
      return box.top + box.height / 2;
    };
    const icon = line.querySelector('img');
    return {
      text: (line.textContent || '').trim(),
      alt: icon?.getAttribute('alt') ?? null,
      title: icon?.getAttribute('title') ?? null,
      icon: mid(icon),
      mark: mid(line.querySelector('[data-follow-variant]')),
      time: mid(line.querySelector('time')),
      line: mid(line),
    };
  }));
}

describe('关注的出处用站点图标', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  it('合集队列卡：出处是带站名的图标，与时间同一条中线', { timeout: 60_000 }, async () => {
    const opened = await openFollow(browser, '/posts/1', '[data-follow-queue-item]');
    try {
      const rows = await lineGeometry(opened.page, '[data-follow-queue-item] [data-follow-queue-meta]');
      assert.equal(rows.length, 3, `队列卡数不对：${JSON.stringify(rows)}`);
      const duplicates = rows.filter((row) => row.alt !== null);
      assert.equal(duplicates.length, 2, `另一站的两条没有出处图标：${JSON.stringify(rows)}`);
      for (const row of duplicates) {
        assert.equal(row.alt, 'Rule34.xxx', '出处图标没有站名作无障碍名称');
        assert.equal(row.title, 'Rule34.xxx', '出处图标悬停不报站名');
        assert.ok(!row.text.includes('Rule34'), `这一行还写着站名文字：${row.text}`);
        assert.ok(Math.abs(row.icon! - row.time!) <= 1, `图标与时间中线差 ${Math.abs(row.icon! - row.time!)}px`);
      }
      // 本站那一条仍是标记加时间，二者同样按盒子居中。
      const own = rows.find((row) => row.alt === null)!;
      assert.ok(own.mark !== null && Math.abs(own.mark - own.time!) <= 1,
        `标记与时间中线差 ${own.mark === null ? '未取得' : Math.abs(own.mark - own.time!)}px`);
    } finally {
      await opened.close();
    }
  });

  it('卡片的「另见」徽章：另一站按图标列出，站名不写成文字', { timeout: 60_000 }, async () => {
    const opened = await openFollow(browser, '/follow', '[data-follow-item] [data-follow-seealso]');
    try {
      const [badge] = await lineGeometry(opened.page, '[data-follow-item] [data-follow-seealso]');
      assert.ok(badge, '卡片上没有「另见」徽章');
      assert.equal(badge.text, '另见');
      assert.equal(badge.alt, 'Rule34.xxx');
      assert.ok(Math.abs(badge.icon! - badge.line!) <= 1, `图标偏离徽章中线 ${Math.abs(badge.icon! - badge.line!)}px`);
      await opened.page.locator('[data-follow-item]:has([data-follow-seealso])').first().hover();
      const source = await opened.page.locator('[data-follow-item]:has([data-follow-seealso]) [data-follow-sources]').first().evaluate(node => {
        const style = getComputedStyle(node);
        return { opacity: style.opacity, visibility: style.visibility, events: style.pointerEvents };
      });
      assert.deepEqual(source, { opacity: '0', visibility: 'hidden', events: 'none' });
    } finally {
      await opened.close();
    }
  });

  /* 设计决定，与谁挂哪枚字样（`test/react/follow-marks.test.ts`）分开量：另见与版本字样是状态标记，
     不是标签，圆角走 `--badge-radius`；「含 WIP」说的是同组另一条，比 WIP 本体弱一档；SFW 模式下
     站点图标和品牌标一样不糊。字样按 `follow-marks.ts` 的形状临时摆进卡片标题里量。 */
  it('另见与来源图标共用封面角标，含 WIP 弱于 WIP，SFW 模式不糊站点图标', { timeout: 60_000 }, async () => {
    const opened = await openFollow(browser, '/follow', '[data-follow-item] [data-follow-seealso]');
    try {
      const look = await opened.page.evaluate(() => {
        const badge = document.querySelector('[data-follow-item] [data-follow-seealso]')!;
        const probe = document.createElement('span');
        probe.style.cssText = 'border-radius:var(--badge-radius);color:var(--muted);background:var(--meter)';
        badge.parentElement!.append(probe);
        const want = getComputedStyle(probe);
        const expected = { radius: want.borderTopLeftRadius, muted: want.color, meter: want.backgroundColor };
        probe.remove();
        const title = badge.closest('[data-follow-item]')!.querySelector('[data-media-title]') ?? badge.parentElement!;
        title.insertAdjacentHTML('afterbegin', '<small class="javedition" data-follow-edition="wip">WIP</small>'
          + '<small class="javedition" data-follow-edition="has-wip">含 WIP</small>');
        const [wip, hasWip] = [...title.querySelectorAll('[data-follow-edition]')].map((node) => getComputedStyle(node));
        const shape = getComputedStyle(badge);
        const source = badge.closest('[data-follow-sources]')!.querySelector('[data-media-badge]')!;
        const sourceShape = getComputedStyle(source);
        const sameSource = { radius: sourceShape.borderTopLeftRadius, background: sourceShape.backgroundColor, shadow: sourceShape.boxShadow };
        document.body.classList.add('censor');
        const icon = getComputedStyle(badge.querySelector('[data-follow-site-icon]')!).filter;
        document.body.classList.remove('censor');
        return { expected, sameSource, radius: shape.borderTopLeftRadius, border: shape.borderTopStyle,
          background: shape.backgroundColor, shadow: shape.boxShadow,
          editionRadius: wip!.borderTopLeftRadius, wip: wip!.color, hasWip: hasWip!.color, icon };
      });
      assert.equal(look.radius, look.sameSource.radius, '另见与来源角标同圆角');
      assert.equal(look.background, look.sameSource.background);
      assert.equal(look.shadow, look.sameSource.shadow);
      assert.equal(look.editionRadius, look.expected.radius, '版本字样同是状态标记');
      assert.equal(look.border, 'none');
      assert.equal(look.wip, look.expected.meter, 'WIP 本体着 --meter');
      assert.equal(look.hasWip, look.expected.muted, '「含 WIP」弱化成 --muted');
      assert.equal(look.icon, 'none', 'SFW 模式下站点图标不糊');
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('卡面换成别的站那条时，另见列主条目的站、不列卡面自己的站', { timeout: 60_000 }, async () => {
    const opened = await openFollow(browser, '/follow', '[data-follow-item="12"] [data-follow-seealso]');
    try {
      const card = opened.page.locator('[data-follow-item="12"]');
      const shown = await card.locator('[data-media-badge]').getAttribute('title');
      const alts = await card.locator('[data-follow-seealso] img').evaluateAll((icons) =>
        icons.map((icon) => icon.getAttribute('alt')));
      assert.equal(shown, 'Rule34.xxx', '卡面没有换成有视频的那条');
      assert.deepEqual(alts, ['pixivFANBOX', 'Pawchive'], `另见列的站不对：${JSON.stringify(alts)}`);
    } finally {
      await opened.close();
    }
  });
});
