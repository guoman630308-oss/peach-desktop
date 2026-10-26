/* 管理区页头与底部批量条（`react/manage-header/`、`react/batch-dock/`）在真浏览器里的行为。
 *
 * 页头：标题、面包屑与导航对齐内容左边缘，回收站说明行按正文宽度居中；
 * 窄屏标题收档的那几页，以及面包屑普通左键走路由不整页重载。回收站说明行先占位后落读数、同页重画
 * 不闪回占位，由 `design-cards.test.ts` 的回收站几何用例量。
 *
 * 批量条：目录、回收站、垃圾文件（待判断与已排除）与关注页各自列出的键和计数。回收站与垃圾文件的
 * 名单由桩给：演示库里两处都是空的。写接口一次都不点。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Locator, Page } from 'playwright-core';

import { openFollowFeed } from './follow-fixture.ts';
import { launch, settle, visit, VIEWPORTS, type Viewport } from './harness.ts';

/** 1280 的桌面视口里窄列被侧栏挤得铺满，量不出「按 --board-content 居中」。 */
const WIDE: Viewport = { name: 'wide', width: 1600, height: 900, mobile: false };
const MOBILE = VIEWPORTS.find((viewport) => viewport.mobile)!;

/** 数据管理 hub 进页会顺手查一次站外链接状态，演示库里那一问的结果与本文件无关。 */
const unrelated = (problems: string[]) => problems.filter((line) => !line.includes('/api/links/check'));

/** `[路径, 标题, 面包屑当前项, 窄屏收档]`。数据管理之下的子页标题让给子页自己的名字。 */
const HEADS = [
  ['/stats', '统计', '', false],
  ['/taste', '口味', '', false],
  ['/review', '人工复核', '人工复核', false],
  ['/data-cleanup', '数据管理', '', true],
  ['/junk-files', '垃圾文件', '垃圾文件', false],
  ['/duplicates', '重复文件', '重复文件', false],
  ['/quality-goals', '高清版', '高清版', false],
  ['/sources', '来源和凭证', '来源和凭证', true],
  ['/trash', '回收站', '回收站', false],
  ['/subscriptions', '关注管理', '', false],
  ['/tasks', '任务中心', '', false],
  ['/settings/library', 'Peach 配置', '', true],
] as const;

const BLOCKS = '[data-manage-header] > :is([data-manage-bar], [data-manage-crumb], [data-manage-title])';

async function readHead(page: Page) {
  await page.locator('[data-manage-title]').waitFor({ timeout: 15_000 });
  return page.evaluate((blocks) => {
    const title = document.querySelector<HTMLElement>('[data-manage-title]')!;
    const crumb = document.querySelector('[data-manage-crumb] [aria-current="true"]');
    return {
      title: title.textContent,
      crumb: crumb?.textContent ?? '',
      font: getComputedStyle(title).fontSize,
      pressed: [...document.querySelectorAll<HTMLElement>('[data-manage][aria-pressed="true"]')].map((node) => node.dataset.manage),
      columns: [...document.querySelectorAll(blocks)].map((node) => {
        const rect = node.getBoundingClientRect();
        return [Math.round(rect.left), Math.round(rect.width)];
      }),
      order: [...document.querySelector('[data-manage-header]')!.children].map(node =>
        node.hasAttribute('data-manage-bar') ? 'bar' : node.hasAttribute('data-manage-crumb') ? 'crumb'
          : node.hasAttribute('data-manage-title') ? 'title' : 'lede'),
      lede: (() => {
        const rect = document.querySelector('[data-manage-lede]')?.getBoundingClientRect();
        return rect ? [rect.left, rect.width] : null;
      })(),
      main: (() => {
        const rect = document.querySelector('#main')!.getBoundingClientRect();
        const style = getComputedStyle(document.querySelector('#main')!);
        return [rect.left + parseFloat(style.paddingLeft), rect.right - parseFloat(style.paddingRight)];
      })(),
    };
  }, BLOCKS);
}

describe('管理区页头', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  it('管理页标题、导航与正文同宽居中', { timeout: 180_000 }, async () => {
    const opened = await visit(browser, '/stats', WIDE);
    try {
      for (const [path, title, crumb] of HEADS) {
        await opened.page.goto(new URL(path, opened.page.url()).href, { waitUntil: 'load' });
        const head = await readHead(opened.page);
        assert.deepEqual([head.title, head.crumb], [title, crumb], `${path} 的标题或面包屑不对`);
        assert.ok(head.columns.length >= 2, `${path} 页头只量到 ${head.columns.length} 块`);
        for (const column of head.columns) assert.deepEqual(column, head.columns[0], `${path} 页头几块没落在同一列：${JSON.stringify(head.columns)}`);
        const [left, width] = head.columns[0]!;
        const [start, end] = head.main;
        assert.deepEqual(head.order.slice(0, crumb ? 3 : 2), crumb ? ['bar', 'crumb', 'title'] : ['bar', 'title'],
          `${path} 页头没有按管理导航、面包屑、标题排列`);
        assert.equal(width, Math.min(1120, Math.round(end - start)), `${path} 页头没有使用正文宽度`);
        assert.ok(Math.abs((left - start) - (end - left - width)) <= 1, `${path} 页头没有与正文居中对齐`);
        if (head.lede) {
          const [ledeLeft, ledeWidth] = head.lede;
          assert.equal(Math.round(ledeWidth), Math.min(1120, Math.round(end - start)));
          assert.ok(Math.abs((ledeLeft - start) - (end - ledeLeft - ledeWidth)) <= 1,
            `${path} 回收站说明没有按正文宽度居中`);
        }
      }
      assert.deepEqual(unrelated(opened.problems), []);
    } finally {
      await opened.close();
    }
  });

  it('390px 下只有数据管理 hub、来源和凭证与配置的标题收一档', { timeout: 180_000 }, async () => {
    const opened = await visit(browser, '/stats', MOBILE);
    try {
      const fonts: Record<string, string> = {};
      for (const [path, , , compact] of HEADS) {
        await opened.page.goto(new URL(path, opened.page.url()).href, { waitUntil: 'load' });
        const head = await readHead(opened.page);
        fonts[path] = head.font;
        assert.equal(head.font === '22px', compact, `${path} 窄屏标题是 ${head.font}`);
      }
      assert.equal(new Set(Object.entries(fonts).filter(([path]) => !HEADS.find((row) => row[0] === path)![3]).map(([, font]) => font)).size, 1,
        `其余管理页的窄屏标题不是同一档：${JSON.stringify(fonts)}`);
    } finally {
      await opened.close();
    }
  });

  for (const viewport of VIEWPORTS) {
    it(`面包屑普通左键回到数据管理，不整页重载；管理条跟着换按下项（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, '/duplicates', viewport);
      try {
        const page = opened.page;
        await readHead(page);
        await page.evaluate(() => { (window as unknown as { stayed?: boolean }).stayed = true });
        const link = page.locator('[data-manage-crumb] a');
        assert.equal(await link.getAttribute('href'), '/data-cleanup');
        await link.click();
        await page.waitForFunction(() => location.pathname === '/data-cleanup');
        await page.waitForFunction(() => document.querySelector('[data-manage-title]')?.textContent === '数据管理');
        assert.equal(await page.evaluate(() => (window as unknown as { stayed?: boolean }).stayed), true, '面包屑整页重载了');
        const head = await readHead(page);
        assert.deepEqual([head.crumb, head.pressed], ['', ['cleanup']]);
        await settle(page);
        assert.deepEqual(unrelated(opened.problems), []);
      } finally {
        await opened.close();
      }
    });
  }
});

/* ── 批量条 ── */

const DOCK = '[data-batch-dock] [data-selection-dock]';

async function pickTwo(cards: Locator) {
  for (const at of [0, 1]) await cards.nth(at).click({ modifiers: ['Control'], position: { x: 20, y: 20 } });
}

async function readDock(page: Page) {
  await page.locator(DOCK).waitFor({ timeout: 10_000 });
  return page.locator(DOCK).evaluate((dock) => ({
    count: dock.querySelector('[role="status"]')?.textContent,
    buttons: [...dock.querySelectorAll('button')].map((button) => button.textContent?.trim()),
  }));
}

/** 回收站列表照首页那一页回：演示库的回收站是空的。只改读，写接口不经这里。 */
async function stubTrash(page: Page) {
  await page.route((url) => url.pathname === '/api/items' && url.searchParams.get('state') === 'trash', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.delete('state');
    const response = await route.fetch({ url: url.href });
    const payload = await response.json();
    for (const item of payload.items ?? []) item.disposal = 'trash';
    await route.fulfill({ response, json: payload });
  });
}

/** 垃圾候选挑没有预览的类型：造出来的 id 取不到缩略图。 */
const junkItem = (id: number) => ({ id, name: `推广-${id}.zip`, junk_kind: 'archive', why: '文件名像推广',
  size: 1048576 * id, location: 'local', cost: '' });

async function stubJunk(page: Page) {
  await page.route('**/api/ads?**', (route) => route.fulfill({ json: {
    items: [9301, 9302, 9303].map(junkItem), total: 3, all_total: 3, pending_total: 3, dismissed_total: 3, counts: { archive: 3 },
  } }));
}

describe('批量条', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  for (const viewport of VIEWPORTS) {
    it(`目录、回收站与垃圾文件两份各自的键和计数（${viewport.name}）`, { timeout: 120_000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      try {
        const page = opened.page;
        const cards = page.locator('#grid [data-media-card][data-id]');
        await cards.first().waitFor({ timeout: 15_000 });
        await settle(page);
        assert.equal(await page.locator(DOCK).count(), 0, '没选中时批量条就画出来了');
        await pickTwo(cards);
        assert.deepEqual(await readDock(page),
          { count: '已选 2 项', buttons: ['喜欢', '看过', '稍后看', '判定产地', '移入回收站', '取消'] });
        await page.locator(`${DOCK} [data-batch-group="clear"]`).click();
        await page.locator(DOCK).waitFor({ state: 'detached', timeout: 10_000 });

        await stubTrash(page);
        await page.goto(new URL('/trash', page.url()).href, { waitUntil: 'load' });
        await cards.first().waitFor({ timeout: 15_000 });
        await settle(page);
        // 说明行右端的「清空回收站」：桌面是 Geist 32px 小键，手机回到本项目 44px 命中区。
        const empty = await page.locator('[data-manage-lede] [data-empty-trash]').boundingBox();
        assert.equal(Math.round(empty!.height), viewport.mobile ? 44 : 32, '清空回收站的键高');
        await pickTwo(cards);
        assert.deepEqual(await readDock(page), { count: '已选 2 项', buttons: ['还原', '彻底删除', '取消'] });

        await stubJunk(page);
        const junk = page.locator('#grid [data-junk-card]');
        for (const [path, first] of [['/junk-files', '不是垃圾'], ['/junk-files?view=dismissed', '重新判断']] as const) {
          await page.goto(new URL(path, page.url()).href, { waitUntil: 'load' });
          await junk.first().waitFor({ timeout: 15_000 });
          await settle(page);
          await pickTwo(junk);
          assert.deepEqual(await readDock(page), { count: '已选 2 项', buttons: [first, '移入回收站', '取消'] }, path);
        }
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });

    it(`关注页的键和计数（${viewport.name}）`, { timeout: 60_000 }, async () => {
      const opened = await openFollowFeed(browser, '/follow', viewport);
      try {
        await pickTwo(opened.page.locator('[data-follow-list] > [data-follow-item]'));
        assert.deepEqual(await readDock(opened.page), { count: '已选 2 项', buttons: ['保存到数据库', '标记已看', '忽略', '取消'] });
        assert.deepEqual(opened.stub.writes.filter((write) => write.url !== '/api/follow/check'), []);
      } finally {
        await opened.close();
      }
    });
  }
});
