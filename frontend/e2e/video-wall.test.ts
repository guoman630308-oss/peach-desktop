import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { layout, launch, requiredEnv, settle, VIEWPORTS, visit } from './harness.ts';
import { openItemPage } from './item-fixture.ts';

let browser: Browser;
before(async () => { browser = await launch() });
after(async () => { await browser?.close() });
const camera = (page: Page) => page.locator('[data-wall-viewport]').evaluate(node => ({
  x: (node as HTMLElement).dataset.cameraX, y: (node as HTMLElement).dataset.cameraY,
}));
async function enter(page: Page) {
  await page.getByRole('button', { name: '进入播放墙', exact: true }).click();
  await page.waitForURL('**/wall?*');
  await page.locator('[data-wall-tile]').first().waitFor();
}
async function pick(page: Page) {
  const key = await page.locator('[data-wall-tile]').evaluateAll(nodes => {
    const viewport = document.querySelector('[data-wall-viewport]')!.getBoundingClientRect();
    return (nodes.find(node => {
      const box = node.getBoundingClientRect();
      return box.left >= viewport.left && box.right <= viewport.right && box.top >= viewport.top && box.bottom <= viewport.bottom;
    }) as HTMLElement | undefined)?.dataset.wallTile;
  });
  assert.ok(key, '视口内有完整磁贴');
  await page.locator(`[data-wall-tile="${key}"]`).click();
  await page.locator('[data-wall-focus]').waitFor();
}

async function addWallCovers(page: Page) {
  await page.route(url => url.pathname === '/api/items', async route => {
    const response = await route.fetch(), payload = await response.json();
    await route.fulfill({ json:{ ...payload, items:payload.items.map((item: Record<string, unknown>) => ({ ...item, has_cover:true, cover_key:'WALL-CLIP' })) } });
  });
  await page.route(url => url.pathname === '/cover', route => route.fulfill({ contentType:'image/svg+xml', body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="gray"/></svg>' }));
}

describe('播放墙的浏览与详情', () => {
  for (const viewport of VIEWPORTS) it(`${viewport.name}：受限范围提示可清除条件并恢复探索，保留返回位置`, async () => {
    const params = new URLSearchParams({ q:'作品', performer:'Alice', tag:'风景', tag_match:'all', state:'fresh',
      jav:'1', loc:'local', returnTo:'/?q=原来的搜索' });
    const opened = await openItemPage(browser, `/wall?${params}`, viewport, { ready:'[data-wall-tile]', catalogIds:[18,21] });
    const { page } = opened;
    const requests: URL[] = [];
    page.on('request', request => { const url = new URL(request.url()); if (url.pathname === '/api/items') requests.push(url) });
    try {
      const notice = page.locator('[data-wall-filter-notice]');
      assert.equal(await notice.isVisible(), true);
      assert.match(await notice.innerText(), /当前仅显示筛选结果/);
      assert.equal(await page.locator('[data-wall-refresh]').count(), 0);
      const geometry = await notice.evaluate(node => {
        const box = node.getBoundingClientRect(), back = document.querySelector('[data-wall-back]')!.getBoundingClientRect();
        const text = node.querySelector('p')!.getBoundingClientRect(), action = node.querySelector('button')!.getBoundingClientRect();
        return { left:box.left, right:box.right, backRight:back.right, width:innerWidth,
          overlap:action.left < text.right && action.right > text.left && action.top < text.bottom && action.bottom > text.top };
      });
      assert(geometry.left >= geometry.backRight + 8 && geometry.right <= geometry.width, JSON.stringify(geometry));
      assert.equal(geometry.overlap, false);
      await notice.getByRole('button', { name:'清除筛选', exact:true }).click();
      await notice.waitFor({ state:'detached' });
      await page.getByRole('button', { name:'换一批', exact:true }).waitFor();
      await page.locator('[data-wall-tile]').first().waitFor();
      const current = new URL(page.url());
      assert.equal(current.searchParams.get('jav'), '1');
      assert.equal(current.searchParams.get('loc'), 'local');
      assert.equal(current.searchParams.get('returnTo'), '/?q=原来的搜索');
      for (const key of ['q','performer','tag','tag_match','state']) assert.equal(current.searchParams.has(key), false);
      assert(requests.some(url => !url.searchParams.has('q') && !url.searchParams.has('performer')
        && !url.searchParams.has('tag') && !url.searchParams.has('state')), requests.map(String).join('\n'));
      const look = await layout(page);
      assert(look.scrollWidth <= look.viewportWidth, JSON.stringify(look));
      await page.reload(); await page.locator('[data-wall-tile]').first().waitFor();
      assert.equal(await notice.count(), 0);
      await page.getByRole('button', { name:'退出播放墙', exact:true }).click();
      await page.waitForURL(url => url.pathname === '/' && url.searchParams.get('q') === '原来的搜索');
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
  it('选中作品具有发光描边，其余变暗；队列滚轮只滚列表；换批等待时墙面变糊', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready:'[data-media-card]' });
    const { page } = opened;
    let release = () => {};
    let hold = false;
    const gate = new Promise<void>(resolve => { release = resolve });
    try {
      await page.route(url => url.pathname === '/api/items', async route => {
        const params = new URL(route.request().url()).searchParams;
        if (params.has('orient')) return route.fulfill({ json:{ items:[],total:0 } });
        const offset = Number(params.get('offset') || 0);
        if (offset && hold) await gate;
        await route.fulfill({ json:{ items:Array.from({ length:60 }, (_, index) => ({
          id:1000 + offset + index,name:`作品 ${offset + index}`,medium:'video',location:'local',has_cover:true,cover_key:'NON-JAV',
        })),total:360,has_more:offset < 300 } });
      });
      await page.goto(new URL('/wall', page.url()).href);
      await page.locator('[data-wall-tile]').first().waitFor(); await pick(page);
      await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-wall-dim]')!).opacity) > .6);
      const glow = await page.locator('[data-wall-focus]').evaluate(node => {
        const style = getComputedStyle(node, '::before'); return { width:style.borderTopWidth, shadow:style.boxShadow };
      });
      assert.equal(glow.width, '2px'); assert.match(glow.shadow, /inset/);
      await page.getByRole('button', { name:'切换视频',exact:true }).click();
      const list = page.locator('[data-wall-queue] [data-mix-list]');
      const box = await list.boundingBox(); assert.ok(box);
      const position = await camera(page);
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.wheel(0, 480);
      await page.waitForFunction(() => document.querySelector('[data-wall-queue] [data-mix-list]')!.scrollTop > 0);
      assert.deepEqual(await camera(page), position);
      await page.getByRole('button', { name:'收起作品',exact:true }).click();
      await page.waitForFunction(() => document.querySelector('[data-wall-refresh] button')?.getAttribute('aria-busy') !== 'true');
      const before = await camera(page);
      hold = true;
      await page.getByRole('button', { name:'换一批',exact:true }).click();
      await page.locator('[data-video-wall][data-refreshing]').waitFor();
      assert.match(await page.locator('[data-wall-viewport]').evaluate(node => getComputedStyle(node).filter), /blur\(8px\).*grayscale\(1\)/);
      assert.deepEqual(await camera(page), before);
      release(); await page.locator('[data-video-wall][data-refreshing]').waitFor({ state:'hidden' });
      await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>('[data-wall-id]')].some(node => Number(node.dataset.wallId) >= 1060));
      assert.equal(await page.locator('[data-wall-viewport]').evaluate(node => getComputedStyle(node).filter), 'none');
    } finally { release(); await opened.close() }
  });
  it('右上角播放墙入口和沉浸模式入口在非多选页面同样常驻', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready:'[data-media-card]' });
    const { page } = opened;
    try {
      await addWallCovers(page);
      for (const path of ['/stats','/trash']) {
        await page.goto(new URL(path, page.url()).href); await settle(page);
        assert.equal(await page.getByRole('button', { name:'进入沉浸模式',exact:true }).isVisible(), true);
        assert.equal(await page.getByRole('button', { name:'进入播放墙',exact:true }).isVisible(), true);
        await enter(page);
        assert.equal(new URL(page.url()).searchParams.get('state'), null);
        await page.getByRole('button', { name:'退出播放墙',exact:true }).click();
        await page.waitForURL(url => url.pathname === path);
      }
    } finally { await page.unrouteAll({ behavior:'wait' }); await opened.close() }
  });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：四向浏览自动续页，磁贴位置稳定且优先展示未出现作品`, async () => {
    const opened = await openItemPage(browser, '/', viewport, { ready:'[data-media-card]' });
    const { page } = opened;
    const offsets: number[] = [];
    try {
      await page.route(url => url.pathname === '/api/items', route => {
        const params = new URL(route.request().url()).searchParams;
        if (params.has('orient')) return route.fulfill({ json:{ items:[],total:0 } });
        const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || 60);
        offsets.push(offset);
        return route.fulfill({ json:{ items:Array.from({ length:limit }, (_, index) => ({
          id:1000 + offset + index, name:`作品 ${offset + index}`, medium:'video', location:'local',
          has_cover:offset !== 60, cover_key:'NON-JAV',
        })), total:1800, has_more:offset + limit < 1800 } });
      });
      await page.goto(new URL('/wall', page.url()).href);
      await page.locator('[data-wall-tile]').first().waitFor();
      const seen = new Map<string, string>(), identities = new Map<string, string>();
      const record = async () => {
        await page.waitForFunction(() => document.querySelector('[data-wall-viewport]')?.getAttribute('aria-busy') !== 'true');
        await page.waitForFunction(() => !document.querySelector('[data-wall-skeleton]'));
        for (const [key, id] of await page.locator('[data-wall-tile]').evaluateAll(nodes => nodes.map(node => [
          (node as HTMLElement).dataset.wallTile!, (node as HTMLElement).dataset.wallId!,
        ]))) {
          if (seen.has(key!)) assert.equal(id, seen.get(key!), '已有位置保持作品身份');
          else { assert.equal(identities.has(id!), false, '新位置优先使用未出现作品'); seen.set(key!, id!); identities.set(id!, key!) }
        }
      };
      await record();
      for (const direction of ['ArrowRight','ArrowDown','ArrowLeft','ArrowUp']) {
        await page.locator('[data-wall-viewport]').focus();
        for (let step = 0; step < 15; step++) { await page.keyboard.press(direction); await record() }
      }
      assert.ok(offsets.includes(120), '无封面页继续续页');
      assert.ok(identities.size > 60, '浏览超过首批作品');
      assert.equal([...identities.keys()].some(id => Number(id) >= 1060 && Number(id) < 1120), false);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
  it('自动续页失败后停止请求，重试填入新作品并保留镜头', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready:'[data-media-card]' });
    const { page } = opened;
    let recover = false, failures = 0;
    try {
      await page.route(url => url.pathname === '/api/items', route => {
        const params = new URL(route.request().url()).searchParams;
        if (params.has('orient')) return route.fulfill({ json:{ items:[],total:0 } });
        const offset = Number(params.get('offset') || 0);
        if (offset && !recover) { failures++; return route.fulfill({ status:503,json:{ error:'续页暂时不可用' } }) }
        return route.fulfill({ json:{ items:Array.from({ length:60 }, (_, index) => ({
          id:1000 + offset + index, name:`作品 ${offset + index}`, medium:'video', location:'local', has_cover:true,cover_key:'NON-JAV',
        })), total:120,has_more:!offset } });
      });
      await page.goto(new URL('/wall', page.url()).href);
      await page.locator('[data-wall-tile]').first().waitFor();
      await page.locator('[data-wall-viewport]').focus();
      for (let step = 0; step < 20; step++) await page.keyboard.press('ArrowRight');
      await page.locator('[data-wall-error]').waitFor();
      const attempts = failures, position = await camera(page);
      await page.waitForTimeout(400);
      assert.equal(failures, attempts, '失败后不连续触发新请求');
      recover = true; await page.getByRole('button', { name:'重试',exact:true }).click();
      await page.locator('[data-wall-error]').waitFor({ state:'hidden' });
      await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>('[data-wall-id]')].some(node => Number(node.dataset.wallId) >= 1060));
      assert.deepEqual(await camera(page), position);
    } finally { await opened.close() }
  });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：刷新直接进入墙面，模块和数据等待均不展示首页骨架`, async () => {
    const context = await browser.newContext({ viewport:{ width:viewport.width,height:viewport.height } });
    const page = await context.newPage();
    let releaseModule = () => {}, releaseItems = () => {};
    const moduleGate = new Promise<void>(resolve => { releaseModule = resolve });
    const itemsGate = new Promise<void>(resolve => { releaseItems = resolve });
    try {
      await context.addInitScript(() => {
        const boot = { home:0 }; (window as unknown as { wallBoot:typeof boot }).wallBoot = boot;
        new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
          if (node instanceof Element) boot.home += Number(node.matches('.catalog-skeleton')) + node.querySelectorAll('.catalog-skeleton').length;
        }))).observe(document, { childList:true,subtree:true });
      });
      await page.route('**/dist/peach-app.js', async route => { await moduleGate; await route.continue() });
      await page.route(url => url.pathname === '/api/items', async route => {
        await itemsGate; await route.fulfill({ json:{ items:[],total:0 } });
      });
      await page.goto(`${requiredEnv('PEACH_E2E_ORIGIN')}/wall?jav=1`, { waitUntil:'commit' });
      await page.locator('header.top').waitFor({ state:'attached' });
      assert.equal(await page.locator('header.top').isVisible(), false);
      assert.equal(await page.locator('body').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(7, 7, 7)');
      releaseModule(); await page.locator('[data-wall-skeleton]').first().waitFor();
      assert.equal(await page.locator('header.top').isVisible(), false);
      releaseItems(); await page.locator('[data-wall-empty]').waitFor();
      assert.equal(await page.evaluate(() => (window as unknown as { wallBoot:{ home:number } }).wallBoot.home), 0);
      await page.getByRole('button', { name:'退出播放墙' }).click();
      await page.locator('header.top').waitFor({ state:'visible' });
      assert.equal(await page.locator('html').getAttribute('data-wall-entry'), null);
    } finally { releaseModule(); releaseItems(); await context.close() }
  });
  it('无封面作品不入墙，有正封框时保留完整人物高度', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready:'[data-media-card]', catalogIds:[11,18,21] });
    const { page } = opened;
    try {
      await page.route(url => url.pathname === '/api/items', route => route.fulfill({ json:{ items:[1,2,3,4,21].map(id => ({
        id, medium:'video', location:'local', is_jav:true, code:`PCH-${id}`, has_cover:true, has_thumb:true,
      })).concat([{ id:11, medium:'video', location:'local', is_jav:false, code:'', has_cover:false, has_thumb:true }]), total:6 } }));
      await page.goto(new URL('/wall?q=作品', page.url()).href); await page.locator('[data-wall-tile]').first().waitFor();
      assert.equal(await page.locator('[data-wall-id="11"]').count(), 0);
      const img = page.locator('[data-wall-id="21"] img').first();
      await page.waitForFunction(() => document.querySelector<HTMLImageElement>('[data-wall-id="21"] img')?.dataset.frame === 'sleeve');
      const framing = await img.evaluate(node => ({ panel:node.classList.contains('panel'), height:node.style.getPropertyValue('--panel-height'), clip:node.style.getPropertyValue('--panel-clip'), fit:getComputedStyle(node).objectFit }));
      assert.equal(framing.panel, true); assert.equal(framing.height, '100%'); assert.equal(framing.fit, 'fill');
      const [top,,bottom] = framing.clip.split(' ');
      assert.equal(top, '0%'); assert.equal(bottom, '0%');
      assert.equal(await img.evaluate(node => getComputedStyle(node.closest('[data-media-pic]')!).clipPath), 'inset(0px)');
      const wide = await page.locator('[data-wall-id="1"] img').evaluate(node => ({ whole:node.classList.contains('whole'), fit:getComputedStyle(node).objectFit }));
      assert.deepEqual(wide, { whole:true, fit:'cover' });
      await page.getByRole('button', { name:'播放墙工具', exact:true }).click();
      await page.getByText('缩略图', { exact:true }).click();
      assert.equal(await page.locator('[data-wall-id="11"]').count(), 0);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
  it('在墙内直接切换分卷和版本，使用同一个作品队列缓存', async () => {
    for (const [id, target] of [[21,22],[31,32]]) {
      const opened = await openItemPage(browser, '/wall?q=作品', VIEWPORTS[0]!, { ready:'[data-wall-tile]', catalogIds:[id!] });
      const { page } = opened;
      try {
        await pick(page);
        await page.getByRole('button', { name:'切换分卷或版本', exact:true }).waitFor();
        await page.waitForFunction(() => !document.querySelector('[data-wall-play][aria-busy="true"]'));
        await page.getByRole('button', { name:'切换分卷或版本', exact:true }).click();
        const requested = page.waitForRequest(request => new URL(request.url()).pathname === '/api/item' && new URL(request.url()).searchParams.get('id') === String(target));
        await page.locator(`[data-wall-source="${target}"]`).click();
        await requested;
        await page.locator('[data-wall-player] video.vjs-tech').waitFor();
        assert.equal(new URL(page.url()).pathname, '/wall');
        assert.equal(await page.getByText('请打开详情选择分卷或版本', { exact:true }).count(), 0);
        await page.getByRole('button', { name:'切换分卷或版本', exact:true }).click();
        assert.equal(await page.locator(`[data-wall-source="${target}"]`).getAttribute('aria-current'), 'true');
        const box = await page.locator('[data-wall-queue]').boundingBox(), focus = await page.locator('[data-wall-focus]').boundingBox();
        assert.ok(box && focus && box.x >= focus.x && box.x + box.width <= focus.x + focus.width + 1);
      } finally { await opened.close() }
    }
  });
  it('切换墙内另一作品时播放器挂到选中磁贴内', async () => {
    const opened = await openItemPage(browser, '/wall?q=作品', VIEWPORTS[0]!, { ready:'[data-wall-tile]', catalogIds:[18,41] });
    const { page } = opened;
    try {
      await pick(page);
      await page.getByRole('button', { name:'切换视频', exact:true }).click();
      const requested = page.waitForRequest(request => new URL(request.url()).pathname === '/api/item' && new URL(request.url()).searchParams.get('id') === '41');
      await page.locator('[data-wall-source="41"]').click();
      await requested;
      await page.locator('[data-wall-focus][data-wall-id="41"] [data-wall-player] video.vjs-tech').waitFor();
      assert.equal(await page.locator('[data-wall-player] video.vjs-tech').count(), 1);
      assert.equal(new URL(page.url()).pathname, '/wall');
    } finally { await opened.close() }
  });
  it('鼠标展开在同一磁贴内让位，收起沿同一路径返回', async () => {
    const opened = await openItemPage(browser, '/wall?q=作品', VIEWPORTS[0]!, { ready:'[data-wall-tile]', catalogIds:[18,21,31] });
    const { page } = opened;
    try {
      await page.emulateMedia({ reducedMotion:'no-preference' });
      const tile = page.locator('[data-wall-tile]').first();
      const key = await tile.getAttribute('data-wall-tile');
      const neighbor = page.locator('[data-wall-tile]').nth(1);
      const readSlot = () => neighbor.evaluate(node => ['--wall-left','--wall-top','--wall-width','--wall-height'].map(name => (node as HTMLElement).style.getPropertyValue(name)));
      const before = await readSlot();
      await tile.evaluate(node => node.setAttribute('data-motion-instance','original'));
      await tile.click();
      const focus = page.locator('[data-wall-focus]');
      await focus.waitFor();
      assert.equal(await focus.getAttribute('data-wall-tile'), key);
      assert.equal(await focus.getAttribute('data-motion-instance'), 'original');
      assert.notDeepEqual(await readSlot(), before);
      await page.waitForFunction(() => {
        const node = document.querySelector<HTMLElement>('[data-wall-focus]')!;
        const box = node.getBoundingClientRect();
        return Math.abs(box.width - box.height) < .5 && Math.abs(box.left + box.width / 2 - innerWidth / 2) < 1;
      });
      await page.getByRole('button', { name:'收起作品' }).click();
      assert.equal(await page.locator('[data-wall-focus]').count(), 0);
      assert.deepEqual(await readSlot(), before);
      assert.equal(await page.locator('[data-motion-instance="original"]').count(), 1);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}：长番号按字宽缩小并完整显示`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready:'[data-media-card]',catalogIds:[18] });
      const { page } = opened;
      try {
        const code = 'FC2-PPV-1694654';
        await page.route(url => url.pathname === '/api/items', route => route.fulfill({ json:{ items:[{
          id:18,medium:'video',location:'local',is_jav:true,code,has_cover:true,name:'一行显示的长作品标题',
        }],total:1 } }));
        await page.reload(); await page.locator('[data-media-card]').first().waitFor();
        await enter(page); await pick(page);
        const read = () => page.locator('[data-wall-code]').evaluate(node => {
          const range = document.createRange(); range.selectNodeContents(node);
          const rect = range.getBoundingClientRect(), box = node.getBoundingClientRect();
          return { text:node.textContent,needed:rect.width,width:box.width,height:rect.height,
            font:parseFloat(getComputedStyle(node).fontSize),wrap:getComputedStyle(node).whiteSpace };
        });
        let text = await read();
        assert.equal(text.text, code); assert.equal(text.wrap, 'nowrap'); assert.ok(text.needed <= text.width + 1);
        assert.ok(text.font >= 12); assert.ok(text.height < 70);
        await page.setViewportSize({ width:390,height:844 });
        await page.waitForFunction(() => {
          const node = document.querySelector('[data-wall-code]')!, range = document.createRange(); range.selectNodeContents(node);
          return range.getBoundingClientRect().width <= node.getBoundingClientRect().width + 1;
        });
        text = await read(); assert.equal(text.text, code); assert.ok(text.needed <= text.width + 1);
      } finally { await opened.close() }
    });
    it(`${viewport.name}：独立页面、拖动、键盘、封面切换与详情返回`, async () => {
      const opened = await openItemPage(browser, '/', viewport, { ready: '[data-media-card]', catalogIds: [11,17,18] });
      const { page } = opened;
      try {
        await enter(page);
        assert.equal(await page.locator('header.top').isVisible(), false);
        const wallBox = await page.locator('[data-video-wall]').boundingBox();
        assert.deepEqual(wallBox, { x:0, y:0, width:viewport.width, height:viewport.height });
        const region = page.locator('[data-wall-viewport]');
        const initial = await camera(page);
        const box = await region.boundingBox(); assert.ok(box);
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width / 2 + 85, box.y + box.height / 2 + 70, { steps: 8 });
        await page.mouse.up();
        assert.notDeepEqual(await camera(page), initial);
        assert.equal(await page.locator('[data-wall-focus]').count(), 0, '拖动不展开作品');
        await region.focus(); await page.keyboard.press('ArrowRight');
        await page.getByRole('button', { name: '播放墙工具', exact: true }).click();
        await page.getByRole('button', { name: '回到起点', exact: true }).click();
        await page.getByText('缩略图', { exact: true }).click();
        await page.getByText('封面', { exact: true }).click();
        await pick(page);
        const visuals = await page.locator('[data-video-wall]').evaluate(root => {
          const tile = root.querySelector('[data-wall-tile]')!;
          const focus = root.querySelector('[data-wall-focus]')!;
          const box = focus.getBoundingClientRect();
          const title = root.querySelector('[data-wall-focus-head] strong')!;
          title.textContent = '测试长标题'.repeat(30);
          return { background:getComputedStyle(root).backgroundColor, radius:getComputedStyle(tile).borderRadius,
            square:Math.abs(box.width - box.height) < 2,
            blur:getComputedStyle(root.querySelector('[data-wall-focus-backdrop]')!).filter,
            scene:root.querySelector('[data-wall-scene]')!.getBoundingClientRect().height / box.height,
            title:(root.querySelector('[data-wall-focus-head]')!.getBoundingClientRect().top - box.top) / box.height,
            wrap:getComputedStyle(title).whiteSpace, ellipsis:getComputedStyle(title).textOverflow,
            single:title.getBoundingClientRect().height < parseFloat(getComputedStyle(title).lineHeight) + 1,
            clipped:title.scrollWidth > title.clientWidth };
        });
        assert.equal(visuals.background, 'rgb(7, 7, 7)');
        assert.equal(visuals.radius, '0px'); assert.equal(visuals.square, true);
        assert.match(visuals.blur, /blur\(36px\)/);
        assert.ok(visuals.scene > .98); assert.ok(visuals.title > .3);
        assert.equal(visuals.wrap, 'nowrap'); assert.equal(visuals.ellipsis, 'ellipsis');
        assert.equal(visuals.single, true); assert.equal(visuals.clipped, true);
        const selectedCamera = await camera(page);
        await page.screenshot({ path: `../build/video-wall-${viewport.mobile ? 'mobile' : 'desktop'}.png` });
        await page.getByRole('button', { name: '详情', exact: true }).click();
        await page.locator('#stage[open] [data-item-side]').waitFor();
        await page.keyboard.press('Escape');
        await page.locator('#stage[open]').waitFor({ state: 'hidden' });
        await page.locator('[data-video-wall]').waitFor({ state: 'visible' });
        assert.deepEqual(await camera(page), selectedCamera);
        await page.getByRole('button', { name: '收起作品', exact: true }).click();
        await page.getByRole('button', { name: '退出播放墙', exact: true }).click();
        assert.equal(new URL(page.url()).pathname, '/');
        const geometry = await layout(page);
        assert.ok(geometry.scrollWidth <= geometry.viewportWidth + 1, JSON.stringify(geometry));
        assert.deepEqual(geometry.offenders, []);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
  it('搜索使用不重复的有限墙，回收站使用网格并保留全局播放墙入口', async () => {
    const opened = await openItemPage(browser, '/?q=作品', VIEWPORTS[0]!, { ready: '[data-media-card]', catalogIds:[18,21,31] });
    const { page } = opened;
    try {
      await enter(page);
      const ids = await page.locator('[data-wall-id]').evaluateAll(nodes => nodes.map(node => (node as HTMLElement).dataset.wallId));
      assert.equal(new Set(ids).size, ids.length);
      await page.getByRole('button', { name:'播放墙工具', exact:true }).click();
      const searched = page.waitForRequest(request => {
        const url = new URL(request.url());
        return url.pathname === '/api/items' && url.searchParams.get('q') === '新的关键词';
      });
      await page.getByRole('searchbox', { name:'搜索播放墙' }).fill('新的关键词');
      await page.getByRole('searchbox', { name:'搜索播放墙' }).press('Enter');
      await searched;
      await page.locator('[data-wall-tile]').first().waitFor();
      const returned = page.waitForRequest(request => {
        const url = new URL(request.url());
        return url.pathname === '/api/items' && url.searchParams.get('q') === '作品';
      });
      await page.getByRole('button', { name:'退出播放墙', exact:true }).click();
      await returned;
      assert.equal(new URL(page.url()).searchParams.get('q'), '作品');
      await page.goto(new URL('/trash', page.url()).href);
      await settle(page);
      assert.equal(await page.getByRole('button', { name: '进入播放墙', exact: true }).isVisible(), true);
    } finally { await opened.close() }
  });
  it('非番号作品也能切换封面与视频缩略图', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready: '[data-media-card]' });
    const { page } = opened;
    try {
      await page.route(url => url.pathname === '/api/items', route => {
        const params = new URL(route.request().url()).searchParams;
        return route.fulfill({ json: { items: params.has('orient') ? [] : [{ id: 11, name: '演示作品 11', medium: 'video',
          location: 'local', is_jav: false, has_cover: true, cover_key: 'NON-JAV', has_thumb: true }], total: params.has('orient') ? 0 : 1 } });
      });
      await page.reload(); await page.locator('[data-media-card]').first().waitFor(); await enter(page);
      await page.getByRole('button', { name: '播放墙工具', exact: true }).click();
      const source = () => page.locator('[data-wall-tile] img').first().getAttribute('src');
      assert.match((await source())!, /\/cover\?/);
      await page.getByText('缩略图', { exact: true }).click();
      assert.match((await source())!, /\/poster\?/);
      await page.getByText('封面', { exact: true }).click();
      assert.match((await source())!, /\/cover\?/);
    } finally { await opened.close() }
  });
  it('换到下一批后进出详情仍保留该批次和镜头', async () => {
    const opened = await openItemPage(browser, '/', VIEWPORTS[0]!, { ready: '[data-media-card]' });
    const { page } = opened;
    try {
      const first = Array.from({ length: 60 }, (_, index) => ({ id: 1000 + index, name: `作品 ${index}`, medium: 'video', location: 'local', has_cover:true, cover_key:'NON-JAV' }));
      const next = [11,17,18].map(id => ({ id, name: `作品 ${id}`, medium: 'video', location: 'local', has_cover:true, cover_key:'NON-JAV' }));
      await page.route(url => url.pathname === '/api/items', route => {
        const params = new URL(route.request().url()).searchParams;
        if (params.has('orient')) return route.fulfill({ json: { items: [], total: 0 } });
        const offset = Number(params.get('offset') || 0);
        return route.fulfill({ json: { items: offset ? next : first, total: 63, has_more: !offset } });
      });
      await page.reload(); await page.locator('[data-media-card]').first().waitFor();
      await enter(page);
      for (const viewport of VIEWPORTS) {
        await page.setViewportSize({ width:viewport.width, height:viewport.height });
        const controls = await page.locator('[data-video-wall]').evaluate(root => {
          const back = root.querySelector('[data-wall-back]')!, refresh = root.querySelector('[data-wall-refresh] button')!;
          const a = back.getBoundingClientRect(), b = refresh.getBoundingClientRect();
          return { back:[a.width,a.height,a.top], refresh:[b.width,b.height,b.top], gap:b.left - a.right,
            backgrounds:[getComputedStyle(back).backgroundColor,getComputedStyle(refresh).backgroundColor],
            radii:[getComputedStyle(back).borderRadius,getComputedStyle(refresh).borderRadius],
            glyph:refresh.querySelector('use')?.getAttribute('href'), text:refresh.textContent?.trim() };
        });
        assert.deepEqual(controls.back, controls.refresh);
        assert.equal(controls.gap, 8); assert.equal(controls.backgrounds[0], controls.backgrounds[1]);
        assert.deepEqual(controls.radii, ['50%','50%']); assert.equal(controls.glyph, '#i-shuffle'); assert.equal(controls.text, '');
      }
      await page.getByRole('button', { name: '换一批', exact: true }).click();
      await page.waitForFunction(() => !!document.querySelector('[data-wall-id="11"]'));
      await page.locator('[data-video-wall][data-refreshing]').waitFor({ state: 'hidden' });
      const ids = () => page.locator('[data-wall-id]').evaluateAll(nodes => [...new Set(nodes.map(node => (node as HTMLElement).dataset.wallId))].sort());
      assert.ok((await ids()).includes('11'));
      await page.locator('[data-wall-id="11"]').first().dispatchEvent('click');
      await page.locator('[data-wall-focus][data-wall-id="11"]').waitFor();
      await page.evaluate(async () => {
        for (let frame = 0; frame < 2; frame++) await new Promise(requestAnimationFrame);
      });
      const position = await camera(page), visibleIds = await ids();
      await page.getByRole('button', { name: '详情', exact: true }).click();
      await page.locator('#stage[open] [data-item-side]').waitFor();
      await page.keyboard.press('Escape'); await page.locator('#stage[open]').waitFor({ state: 'hidden' });
      await page.locator('[data-wall-tile]').first().waitFor();
      await page.evaluate(async () => {
        for (let frame = 0; frame < 2; frame++) await new Promise(requestAnimationFrame);
      });
      assert.deepEqual(await ids(), visibleIds);
      assert.deepEqual(await camera(page), position);
    } finally { await opened.close() }
  });
  it('真实演示短片在墙内播放，拖动不卸载播放器，详情认领同一实例', async () => {
    const opened = await visit(browser, '/', VIEWPORTS[0]!);
    const { page } = opened;
    try {
      await page.locator('[data-media-card]').first().waitFor();
      await addWallCovers(page);
      await enter(page); await pick(page);
      await page.getByRole('button', { name: '播放', exact: true }).click();
      await page.waitForFunction(() => {
        const video = document.querySelector<HTMLVideoElement>('[data-wall-player] video');
        return !!video && video.readyState >= 2 && !video.paused && video.currentTime > 0;
      }, undefined, { timeout: 30_000 });
      const playingStyle = await page.locator('[data-wall-focus]').evaluate(root => {
        const box = root.getBoundingClientRect(), title = root.querySelector('[data-wall-focus-head]')!;
        return { blur:getComputedStyle(root.querySelector('[data-wall-scene]')!).filter,
          title:(title.getBoundingClientRect().top - box.top) / box.height,
          font:parseFloat(getComputedStyle(title.querySelector('b')!).fontSize),
          close:getComputedStyle(root.querySelector('[data-wall-focus-close]')!).backgroundColor,
          overlays:[...root.querySelectorAll('[data-player-stats-button],[data-player-net],[data-player-stats]')].map(node => getComputedStyle(node).display) };
      });
      assert.match(playingStyle.blur, /blur\(36px\)/); assert.ok(playingStyle.title < .2);
      assert.equal(playingStyle.font, 22); assert.equal(playingStyle.close, 'rgba(0, 0, 0, 0.66)');
      assert.equal(playingStyle.overlays.length, 3); assert.ok(playingStyle.overlays.every(value => value === 'none'));
      await page.getByRole('button', { name:'暂停', exact:true }).click();
      await page.waitForFunction(() => document.querySelector<HTMLVideoElement>('[data-wall-player] video')?.paused);
      await page.getByRole('button', { name:'播放', exact:true }).click();
      await page.waitForFunction(() => !document.querySelector<HTMLVideoElement>('[data-wall-player] video')?.paused);
      await page.evaluate(() => {
        const video = document.querySelector('[data-wall-player] video')!;
        video.setAttribute('data-wall-instance', 'same');
      });
      await page.locator('[data-wall-viewport]').focus(); await page.keyboard.press('ArrowLeft');
      assert.equal(await page.locator('[data-wall-player] video[data-wall-instance="same"]').count(), 1);
      await page.getByRole('button', { name: '详情', exact: true }).click();
      await page.locator('#stage video[data-wall-instance="same"]').waitFor();
      await page.keyboard.press('Escape');
      await page.locator('#stage[open]').waitFor({ state: 'hidden' });
    } finally { await page.unrouteAll({ behavior:'wait' }); await opened.close() }
  });
  it('搜索无结果时保留返回和工具，清空搜索可继续浏览', async () => {
    const opened = await visit(browser, '/wall?q=不会匹配的查询', VIEWPORTS[0]!);
    const { page } = opened;
    try {
      await page.locator('[data-wall-empty]').waitFor();
      await addWallCovers(page);
      assert.equal(await page.locator('[data-wall-tile]').count(), 0);
      assert.equal(await page.getByRole('button', { name:'退出播放墙' }).isVisible(), true);
      assert.equal(await page.getByRole('button', { name:'清除筛选', exact:true }).isVisible(), true);
      await page.getByRole('button', { name:'播放墙工具', exact:true }).click();
      await page.getByRole('searchbox', { name:'搜索播放墙' }).fill('');
      await page.getByRole('searchbox', { name:'搜索播放墙' }).press('Enter');
      await page.locator('[data-wall-tile]').first().waitFor();
      assert.deepEqual(opened.problems, []);
    } finally { await page.unrouteAll({ behavior:'wait' }); await opened.close() }
  });
  it('读取失败留在墙内并可原地重试', async () => {
    const opened = await visit(browser, '/', VIEWPORTS[0]!);
    const { page } = opened;
    try {
      await page.locator('[data-media-card]').first().waitFor();
      let fail = true;
      await page.route(url => url.pathname === '/api/items', route => fail
        ? route.fulfill({ status:500, json:{ error:'预览读取失败' } }) : route.continue());
      await page.goto(new URL('/wall?q=不会匹配的查询', page.url()).href);
      await page.locator('[data-wall-error]').waitFor();
      assert.equal(await page.getByRole('button', { name:'退出播放墙' }).isVisible(), true);
      fail = false;
      await page.getByRole('button', { name:'重试', exact:true }).click();
      await page.locator('[data-wall-empty]').waitFor();
      assert.equal(await page.locator('[data-wall-error]').count(), 0);
    } finally { await opened.close() }
  });
});
