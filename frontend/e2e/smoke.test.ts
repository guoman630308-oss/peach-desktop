/* 每条主路由在桌面与 390×844 下都要守住的不变量。
 *
 * 这些是界面验收里每一轮都要重测的几何与运行期事实：写成用例之后由测试入口执行，
 * 浏览器面板只留给新布局首次成形和观感判断。新发现的同类问题先在这里补一条再修。
 *
 * 每条路由先等它自己的主体出现，再 settle、再量几何。报错、失败请求、横向溢出和等待态
 * 这几条在页面完全没渲染时全都成立，不写主体的话白屏也是绿的。主体按 `tests/test_web_e2e.py`
 * 生成的演示库写：12 条视频，没有播放列表、关注来源和任务记录，这几页等到的是各自的空态。
 * 女优、厂牌与标签的索引在一轮里会从空变成有：前面的路由打开过作品之后条目就出现了，
 * 所以索引页的主体是「条目或空态」二选一，不绑死其中一种。标题只能拿来认页面，不能单独
 * 当主体：管理页的 `[data-manage-title]` 由外壳先写好，内容区失败时它照样在。
 *
 * 分区 tab（`.ui-board-local-nav`）只显示当前那一格，其余面板不参与布局；每一格都切过去
 * 再测一遍，否则配置页「网络与访问」这类不在首屏的分区永远不会被量到。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Locator, Page } from 'playwright-core';

import { configurationBody, expectBody, layout, launch, requiredEnv, settle, visit, VIEWPORTS } from './harness.ts';
import { mockJavDetail } from './jav-detail-fixture.ts';

interface Route {
  path: string;
  /** 目标页面主体：每个定位器都要可见。骨架屏的占位标题带 `aria-hidden`，按角色找不到。 */
  body(page: Page): Locator[];
}

const heading = (page: Page, scope: string, name: string): Locator =>
  page.locator(scope).getByRole('heading', { name, exact: true });

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;

/** 索引条目（`data-k`）或明确的空态，先出现哪个算哪个。两样都只有 React 那棵树画，
 * 壳铺的骨架一样都不带。选择器里就带 `:visible`：不然 `.first()` 可能落在一个隐藏的
 * 匹配上，等它可见等到超时，主体其实早画好了。 */
const indexEntries = (page: Page): Locator =>
  page.locator('#index [data-k]:visible, #index [data-empty-state]:visible').first();

/** 统计页画完的标志：四张读数卡兼页签，第一张是馆藏。名字里还带读数和体积，按前缀匹配。 */
const statsInventoryTab = (page: Page): Locator =>
  page.locator('#stats').getByRole('tab', { name: /^馆藏视频/ });

const ROUTES: readonly Route[] = [
  { path: '/works/EXM-001', body: page => [page.locator('#stage [data-jav-detail] h1'), page.getByRole('img', { name: 'EXM-001 封面', exact: true })] },
  { path: '/jav', body: page => [heading(page, '#javLibraryHeader', 'JAV'), page.getByRole('tab', { name: '馆藏', exact: true, selected: true }), page.locator('#grid [data-media-card]:visible, #grid [data-media-empty]:visible').first()] },
  { path: '/jav?tab=discover', body: page => [heading(page, '#stats', 'JAV'), page.getByRole('tab', { name: '发现', exact: true, selected: true }), page.getByText('暂无匹配口味的作品', { exact: true })] },
  { path: '/jav?tab=wants', body: page => [heading(page, '#stats', 'JAV'), page.getByRole('tab', { name: '想要', exact: true, selected: true }), page.getByText('还没有想要的作品', { exact: true })] },
  { path: '/', body: (page) => [page.locator('#grid [data-media-card]').first()] },
  { path: '/wall', body: (page) => [page.locator('[data-wall-tile]').first(), page.getByRole('button', { name: '退出播放墙' })] },
  { path: '/performers', body: (page) => [heading(page, '#index', '艺人'), indexEntries(page)] },
  { path: '/creators', body: (page) => [heading(page, '#index', '卖家'), indexEntries(page)] },
  { path: '/creators/', body: (page) => [heading(page, '#index', '卖家'), indexEntries(page)] },
  { path: '/performers?category=animation', body: (page) => [heading(page, '#index', '艺人'), indexEntries(page)] },
  { path: '/studios', body: (page) => [heading(page, '#index', '厂牌'), indexEntries(page)] },
  { path: '/agencies', body: (page) => [heading(page, '#index', '事务所'), indexEntries(page)] },
  { path: '/tags', body: (page) => [heading(page, '#index', '标签'), indexEntries(page)] },
  {
    path: '/playlists',
    body: (page) => [heading(page, '#stats', '播放列表'), heading(page, '#stats', '还没有播放列表')],
  },
  {
    path: '/follow',
    body: (page) => [heading(page, '#stats', '关注'), heading(page, '#stats', '还没有关注任何来源')],
  },
  {
    // 骨架里也有「关注列表」那一排分段，可整块带 `aria-hidden`；按角色找得到页签的只有真页面。
    path: '/subscriptions',
    body: (page) => [heading(page, '#main', '关注管理'),
      page.locator('#stats').getByRole('tab', { name: '添加关注', exact: true })],
  },
  {
    // 订阅源页签由地址参数直达；演示库没有订阅源，等到的是那一节的页脚按钮。
    path: '/subscriptions?tab=feeds',
    body: (page) => [page.locator('#stats').getByRole('tab', { name: 'JAV 订阅源', exact: true, selected: true }),
      page.locator('#stats').getByRole('button', { name: '立即拉取', exact: true })],
  },
  {
    // 骨架里那条指标带也写着「馆藏视频」，认不出画完没有；只有真页面把四张读数卡做成页签。
    path: '/stats',
    body: (page) => [heading(page, '#main', '统计'), statsInventoryTab(page)],
  },
  {
    // 骨架那条指标带也写着「浏览记录」，认不出画完没有；把两套证据做成页签的只有真页面。
    path: '/taste',
    body: (page) => [heading(page, '#main', '口味'),
      page.locator('#stats').getByRole('tab', { name: '浏览器记录', exact: true })],
  },
  {
    // 骨架那一列分类也写着「复核分类」，认不出画完没有；把十个分类做成页签的只有真页面。
    path: '/review',
    body: (page) => [heading(page, '#main', '人工复核'),
      page.locator('#stats').getByRole('tab', { name: '元数据字段' })],
  },
  {
    path: '/data-cleanup',
    // 挂载前的遗留骨架用 heading，最终 React 卡片用带名字的 section；等最终语义才能证明挂载完成。
    body: (page) => [heading(page, '#main', '数据管理'),
      page.locator('#stats section[aria-label="扫描与采集"]'),
      page.locator('#stats section[aria-label="媒体修复"]')],
  },
  {
    // 演示库里没有垃圾候选，等到的是垃圾队列那一页的空态标题；壳铺的骨架只有卡片占位。
    path: '/junk-files',
    body: (page) => [heading(page, '#main', '垃圾文件'), heading(page, '#grid', '没有待判断的垃圾文件')],
  },
  {
    // 骨架只写「正在比对重复内容」；汇总块上那一格「重复内容」有没有重复组都会画，只有真页面有。
    path: '/duplicates',
    body: (page) => [heading(page, '#main', '重复文件'), page.locator('#stats').getByText('重复内容', { exact: true })],
  },
  {
    // 旧直达地址落到数据管理页的锚点上；骨架里那一段没有 `id`，带 `id` 的只有演示库配了根目录后的真卡片。
    path: '/resource-sync',
    body: (page) => [heading(page, '#main', '数据管理'), page.locator('#stats section#resource-sync')],
  },
  {
    path: '/quality-goals',
    // 演示库里没有标记中的目标，等到的是空态那一句：它是真的标题元素，不是一段文字。
    body: (page) => [heading(page, '#main', '高清版'), heading(page, '#stats', '没有标记中的高清版目标')],
  },
  {
    // 「高清图片可能需要代理」那句遗留骨架里也有，认不出画完没有；「高清封面」那一张只有真页面有。
    path: '/sources',
    body: (page) => [heading(page, '#main', '来源和凭证'), page.locator('#stats form[aria-label="高清封面"]')],
  },
  // 名字对不上任何一家时要换成空态；骨架一直留着，读起来就是还在取。
  { path: '/agencies/不存在的事务所', body: (page) => [heading(page, '#index', '找不到这个事务所')] },
  { path: '/settings/library', body: configurationBody },
  { path: '/diagnostics', body: page => [page.getByRole('tab', { name: '系统诊断', selected: true }), page.locator('#stats section[aria-label="库健康"]')] },
  { path: '/settings/diagnostics', body: page => [page.getByRole('tab', { name: '系统诊断', selected: true }), page.locator('#stats section[aria-label="库健康"]')] },
  { path: '/tasks', body: (page) => [heading(page, '#main', '任务中心'), heading(page, '#stats', '还没有任务记录')] },
  ...['', '/appearance', '/playback', '/jav', '/search', '/content'].map(section => ({ path: '/settings' + section,
    body: (page: Page) => [page.getByRole('dialog', { name: /外观与导航|浏览与播放|JAV 与新作|搜索与推荐|内容显示/ })] })),
  ...['subscriptions', 'downloads', 'network', 'maintenance'].map(section => ({ path: `/settings/${section}`, body: configurationBody })),
  {
    path: `/assets/${requiredEnv('PEACH_E2E_ITEM')}`,
    body: (page) => {
      const stage = page.getByRole('dialog', { name: '作品详情' });
      return [stage, stage.locator('video.vjs-tech')];
    },
  },
];

async function assertHolds(page: Page, problems: string[], where: string): Promise<void> {
  assert.deepEqual(problems, [], `${where}：页面报错或有失败请求`);
  const box = await layout(page);
  assert.ok(box.scrollWidth <= box.viewportWidth,
    `${where}：横向溢出，scrollWidth ${box.scrollWidth} > 视口 ${box.viewportWidth}`);
  assert.deepEqual(box.offenders, [], `${where}：有元素越出视口右边缘`);
  if (where === '/') {
    const images = await page.locator('img[loading="lazy"]').evaluateAll(nodes => ({
      count: nodes.length,
      missing: nodes.filter(node => !(Number(node.getAttribute('width')) > 0 && Number(node.getAttribute('height')) > 0))
        .map(node => node.getAttribute('src')),
    }));
    assert.ok(images.count > 0, '首页没有懒加载图片');
    assert.deepEqual(images.missing, [], '首页懒加载图片需要声明尺寸');
  }
}

describe('路由冒烟', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  for (const viewport of VIEWPORTS) {
    for (const route of ROUTES) {
      // 单条超时远小于 Python 侧的整轮超时：卡住的那一条自己报名字，不拖垮整轮输出。
      it(`${route.path} @ ${viewport.name}`, { timeout: 60_000 }, async () => {
        const opened = await visit(browser, route.path.startsWith('/works/') ? '/stats' : route.path, viewport);
        try {
          if (route.path.startsWith('/works/')) {
            await mockJavDetail(opened.page);
            await opened.page.goto(requiredEnv('PEACH_E2E_ORIGIN') + route.path);
          }
          await expectBody(opened.page, route.path, route.body(opened.page));
          await settle(opened.page);
          await assertHolds(opened.page, opened.problems, route.path);
          // 设置浮层的那一排 tab 常驻 DOM、关着时不可见；只点页面上看得见的那一排。
          const tabs = opened.page.locator('.ui-board-local-nav [role="tab"]:visible');
          const count = await tabs.count();
          // 选择器一旦对不上，循环一格都不跑也照样绿；配置页至少有媒体、网络与访问、维护三格。
          if (route.path === '/settings/library') assert.ok(count >= 3, `配置页只看到 ${count} 个分区 tab`);
          for (let index = 0; index < count; index += 1) {
            const tab = tabs.nth(index);
            const name = (await tab.textContent())?.trim() || `第 ${index + 1} 格`;
            await tab.click({ timeout: 5_000 });
            await settle(opened.page);
            assert.equal(await tab.getAttribute('aria-selected'), 'true', `${route.path}「${name}」点了没有选中`);
            await assertHolds(opened.page, opened.problems, `${route.path}「${name}」`);
          }
        } finally {
          await opened.close();
        }
      });
    }
  }

  /* React 档的页面自己管取数：轮询跟着那棵根活，而不是跟着遗留层的「代」。遗留层换页时
     多数管理页直接 `innerHTML=`，根被挤出文档却照样活着——这一条盯的就是它有没有被卸掉。
     换页走 pushState + popstate：侧栏按钮、抽屉和浏览器后退最后走的都是这一条路。 */
  it('离开活动页之后轮询停下，回到这一页又接上', { timeout: 120_000 }, async () => {
    const opened = await visit(browser, '/tasks', DESKTOP);
    try {
      const polls: string[] = [];
      opened.page.on('request', (request) => {
        if (request.url().includes('/api/tasks')) polls.push(request.url());
      });
      const waitForPolls = async (least: number, what: string) => {
        for (let step = 0; step < 80; step += 1) {
          if (polls.length >= least) return;
          await opened.page.waitForTimeout(250);
        }
        assert.ok(polls.length >= least, `${what}：只看到 ${polls.length} 次 /api/tasks，要 ${least} 次`);
      };
      await expectBody(opened.page, '/tasks',
        [heading(opened.page, '#stats', '还没有任务记录')]);
      // 先确认轮询真的在跑，否则下面「停下来了」在它从没开始时也成立。
      await waitForPolls(2, '活动页的轮询没有跑起来');

      await opened.page.evaluate(() => {
        history.pushState({}, '', '/stats');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      await expectBody(opened.page, '/stats', [statsInventoryTab(opened.page)]);
      await settle(opened.page);
      const stopped = polls.length;
      // 统计页自己也是 React 档：管理区正文里应当只剩它那一棵根，活动页那一棵连同轮询一起撤掉。
      assert.equal(await opened.page.locator('#stats .peach-react').count(), 1,
        '管理区正文里不是只剩统计页那一棵 React 根');
      assert.equal(
        await opened.page.locator('#stats').getByRole('heading', { name: '还没有任务记录' }).count(), 0,
        '换页之后活动页的 React 根还留在管理区正文里');
      await opened.page.waitForTimeout(3_000);
      assert.equal(polls.length, stopped, '离开活动页之后 /api/tasks 还在轮询');

      await opened.page.goBack();
      await expectBody(opened.page, '/tasks',
        [heading(opened.page, '#stats', '还没有任务记录')]);
      await waitForPolls(stopped + 2, '回到活动页之后轮询没有接上');
      await assertHolds(opened.page, opened.problems, '/activity 换页往返');
    } finally {
      await opened.close();
    }
  });

  /* 首页那一行新作只属于目录页：从首页进统计页要收起清空，取数还没回来就离开的那一轮
     也不许回头把它画到管理区上。演示库没有订阅，新作由拦截的接口给一条。 */
  it('离开首页之后新作那一行收起，晚到的取数也不画回来', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/stats', DESKTOP);
    try {
      let hold: Promise<void> | null = null;
      let served = 0;
      await opened.page.route(/\/api\/feeds\/discoveries\?/, async (route) => {
        if (hold) await hold;
        // 请求可能已随换页撤掉，那时回包会报错；要核对的只是回包之后页面长什么样。
        await route.fulfill({ json: { ok: true, more: false, items: [{
          id: 1, code: 'MCSR-191', title: 'MCSR-191 示例作品', link: null, cover_url: null,
          has_cover: false, cover_frame: null, poster_box: null, release_date: '2025-07-19',
          studio: null, performers: null, read: false, ignored: false, scrape_error: null,
        }] } }).catch(() => undefined);
        served += 1;
      });
      const go = (path: string) => opened.page.evaluate((target) => {
        history.pushState({}, '', target);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, path);
      const feedCards = opened.page.locator('#feedNew article.feednewcard');
      await expectBody(opened.page, '/stats', [statsInventoryTab(opened.page)]);

      await go('/');
      await expectBody(opened.page, '/', [feedCards.first()]);
      await go('/stats');
      await expectBody(opened.page, '/stats', [statsInventoryTab(opened.page)]);
      assert.equal(await opened.page.locator('#feedNew').isHidden(), true, '进统计页之后新作那一行还露着');
      assert.equal(await feedCards.count(), 0, '进统计页之后新作那一行没有清空');

      await go('/');
      await expectBody(opened.page, '/', [feedCards.first()]);
      await go('/junk-files');
      await opened.page.locator('#count [data-junk-filters]').waitFor();
      assert.equal(await opened.page.locator('#feedNew').isHidden(), true, '垃圾文件页不展示订阅新作');
      assert.equal(await feedCards.count(), 0);

      for (const target of ['/stats', '/junk-files']) {
        let release!: () => void;
        hold = new Promise((resolve) => { release = resolve; });
        const asked = opened.page.waitForRequest(/\/api\/feeds\/discoveries\?/);
        await go('/');
        await asked;
        await go(target);
        const body = target === '/stats' ? statsInventoryTab(opened.page) : opened.page.locator('#count [data-junk-filters]');
        await expectBody(opened.page, target, [body]);
        const before = served;
        release();
        for (let step = 0; step < 40 && served === before; step += 1) await opened.page.waitForTimeout(100);
        assert.ok(served > before, '压住的那一次新作取数没有放行');
        // 回包之后页面只剩一次同步重画；目录区的旧加载态不属于当前页面。
        await opened.page.waitForTimeout(500);
        assert.equal(await feedCards.count(), 0, `晚到的新作取数画到了 ${target} 上`);
        await assertHolds(opened.page, opened.problems, `首页与 ${target} 往返`);
      }
    } finally {
      await opened.close();
    }
  });

  /* 还没装进本机的新作只有来源给的封套地址，没有边车。贴右缘的话 0.75 的卡片比正封宽，
     左边带进一条书脊；按正封先验比例 0.704 从右缘量回去才只剩正封。 */
  it('远程封套按正封先验比例切，不带进书脊', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/stats', DESKTOP);
    try {
      const remote = 'https://covers.example.test/sleeve.svg';
      await opened.page.route(remote, (route) => route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="1000">'
          + '<rect width="1500" height="1000" fill="#888"/></svg>',
      }));
      await opened.page.route(/\/api\/feeds\/discoveries\?/, (route) => route.fulfill({ json: {
        ok: true, more: false, items: [{
          id: 1, code: 'ABF-348', title: 'ABF-348 示例作品', link: null, cover_url: remote,
          has_cover: false, cover_frame: null, poster_box: null, release_date: '2026-09-30',
          studio: null, performers: null, read: false, ignored: false, scrape_error: null,
        }] } }));
      await opened.page.evaluate(() => {
        history.pushState({}, '', '/');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      const cover = opened.page.locator('#feedNew img.cover.panel');
      await expectBody(opened.page, '/', [cover]);
      // 1500×1000 的封套，正封宽 704，左缘在 796 列：切掉左边 53.07%，右缘不动。
      assert.equal(await cover.evaluate((img) => img.style.getPropertyValue('--panel-clip')),
        '0% 0% 0% 53.07%');
    } finally {
      await opened.close();
    }
  });

  /* 最近一轮取封面断在连接上、这一行又还有没封面的卡时，行上方挂一条 Note 指去采集页。
     一排「无封面」自己说不清是官方没出还是线路不通。 */
  it('封面连不上图片主机时新作那一行上方指去配连接方式', { timeout: 60_000 }, async () => {
    const opened = await visit(browser, '/stats', DESKTOP);
    try {
      await opened.page.route(/\/api\/feeds\/discoveries\?/, (route) => route.fulfill({ json: {
        ok: true, more: false, cover_network: 3, items: [{
          id: 1, code: 'MCSR-191', title: 'MCSR-191 示例作品', link: null, cover_url: null,
          has_cover: false, cover_frame: null, poster_box: null, release_date: '2025-07-19',
          studio: null, performers: null, read: false, ignored: false, scrape_error: null,
        }] } }));
      await opened.page.evaluate(() => {
        history.pushState({}, '', '/');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      const note = opened.page.locator('#feedNew .feednetwork');
      await expectBody(opened.page, '/', [note]);
      assert.match(await note.innerText(), /3 部新作的封面连不上 DMM 图片主机/);
      assert.equal(await note.locator('a').getAttribute('href'), '/sources');
      await assertHolds(opened.page, opened.problems, '新作连不上图片主机的提示');
    } finally {
      await opened.close();
    }
  });
});
