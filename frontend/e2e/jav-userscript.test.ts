import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { launch, requiredEnv, VIEWPORTS } from './harness.ts';

interface Site {
  id: string; label: string; kind: string; domains: string[]; mode: 'automatic' | 'selection';
  detail_pattern?: string; selectors?: string[]; query_keys?: string[];
}
const MANIFEST: { version: number; sites: Site[] } = JSON.parse(readFileSync(
  new URL('../../web/integrations/jav-sites.json', import.meta.url), 'utf8'));
const SCRIPT = readFileSync(new URL('../../web/integrations/peach-jav.user.js', import.meta.url), 'utf8');
const TOKEN = 'test-only-peach-jav-secret-8b4d';
const BASE = 'https://peach.example.invalid';
const ROOT = '#peach-jav-action';
type State = 'idle' | 'queued' | 'searching' | 'submitted' | 'ingested' | 'owned' | 'failed' | 'no_match';
type Body = { ok: boolean; code: string; state: State; message: string };
type Call = { method: string; url: string; headers: Record<string, string>; anonymous: boolean; data?: string };
type MockRequest = Call & { onload(value: unknown): void; ontimeout(): void; onerror(): void };
type Mock = {
  calls: Call[];
  pending: MockRequest[];
  values: Record<string, string>;
  menu?: () => void;
  menus: Record<string, () => void>;
  automatic: boolean;
  reply(index: number, body: unknown, status?: number): void;
};
declare global { interface Window { javMock: Mock } }

function body(state: State, code = 'SSIS-057'): Body {
  return { ok: true, code, state, message: state };
}

async function open(browser: Browser, options: {
  url?: string; title?: string; html?: string; configured?: boolean; viewport?: typeof VIEWPORTS[number]; initial?: State; script?: string;
} = {}) {
  const viewport = options.viewport || VIEWPORTS[0]!;
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
  const page = await context.newPage();
  const problems: string[] = [];
  page.on('pageerror', error => problems.push(error.message));
  // 所有页面均为内存夹具，不访问来源站点或 Peach 服务。
  await context.route('**/*', route => route.fulfill({
    contentType: 'text/html; charset=utf-8', body: `<!doctype html><html><head><meta charset="utf-8"><title>${options.title || 'SSIS-057 作品详情'}</title></head><body>${options.html ?? '<h1>SSIS-057 作品详情</h1>'}</body></html>`,
  }));
  await page.addInitScript(({ configured, token, base, initial }) => {
    const mock: Mock = {
      calls: [], pending: [], automatic: true, menus: {},
      values: configured ? { 'peach.jav.base': base, 'peach.jav.token': token } : {},
      reply(index, value, status = 200) {
        mock.pending[index]!.onload({ status, responseText: JSON.stringify(value) });
      },
    };
    window.javMock = mock;
    Object.assign(window, {
      GM_getValue: (key: string, fallback: string) => mock.values[key] ?? fallback,
      GM_setValue: (key: string, value: string) => { mock.values[key] = value; },
      GM_registerMenuCommand: (label: string, callback: () => void) => { mock.menu = callback; mock.menus[label] = callback; },
      GM_xmlhttpRequest: (request: MockRequest) => {
        mock.calls.push({ method: request.method, url: request.url, headers: request.headers,
          anonymous: request.anonymous, ...(request.data ? { data: request.data } : {}) });
        mock.pending.push(request);
        if (mock.automatic) setTimeout(() => {
          const code = new URL(request.url).searchParams.get('code') || 'SSIS-057';
          request.onload({ status: 200, responseText: JSON.stringify({ ok: true, code, state: initial, message: initial }) });
        }, 0);
      },
    });
  }, { configured: options.configured !== false, token: TOKEN, base: BASE, initial: options.initial || 'idle' });
  await page.goto(options.url || 'https://missav.ws/en/ssis-057?private=value#player');
  await page.clock.install();
  await page.addScriptTag({ content: options.script ?? SCRIPT });
  return { page, problems, close: () => context.close() };
}

const button = (page: Page) => page.locator(ROOT).getByRole('button');
async function ready(page: Page, name = '加入 Peach') {
  await page.waitForFunction(({ root, label }) => {
    const button = document.querySelector(root)?.shadowRoot?.querySelector('button');
    return button?.textContent === label && button.getAttribute('aria-busy') === 'false';
  }, { root: ROOT, label: name }, { timeout: 15_000 }).catch(async error => {
    const panel = await page.locator(ROOT).evaluate(root => ({ button: root.shadowRoot?.querySelector('button')?.textContent,
      status: root.shadowRoot?.querySelector('p')?.textContent,
      fields: [...document.querySelectorAll('dt, dd')].map(field => ({ name: field.tagName, text: field.textContent })) })).catch(() => '未取得');
    throw new Error(`${(error as Error).message}\n${JSON.stringify(panel)}`);
  });
}
async function pending(page: Page, count: number) {
  await page.waitForFunction(value => window.javMock.calls.length === value, count);
}
async function reply(page: Page, index: number, value: Body) {
  await page.evaluate(({ index, value }) => window.javMock.reply(index, value), { index, value });
}

async function selectCode(page: Page) {
  await page.locator('#selected-code').evaluate(element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    window.javMock.menus['选中番号加入 Peach']!();
  });
}

const AUTOMATIC: Record<string, { path: string; title: string; html: string; source?: string }> = {
  missav: { path: '/en/ssis-057', title: 'SSIS-057', html: '<h1>SSIS-057 作品</h1>' },
  jable: { path: '/videos/ssis-057/', title: 'Jable', html: '<div class="video-info"><h4>SSIS-057 作品</h4></div>' },
  'jav-guru': { path: '/12345/ssis-057-detail/', title: 'JAV.guru', html: '<h1 class="entry-title">[SSIS-057] 作品</h1>' },
  njav: { path: '/en/v/ssis-057', title: 'NJAV / 123AV', html: '<h1>SSIS-057 作品</h1><dl><dt>Code</dt><dd>SSIS-057</dd></dl>' },
  javtiful: { path: '/video/12345/ssis-057', title: 'JAVtiful', html: '<h1>SSIS-057 作品</h1>' },
  '7mmtv': { path: '/ja/censored_content/12345/SSIS-057.html', title: '7mmtv', html: '<h1>SSIS-057 作品</h1>' },
  javdb: { path: '/v/ABP123', title: 'JavDB', html: '<h2 class="title">作品详情</h2><div class="panel-block"><strong>番號:</strong><span class="value">SSIS-057</span></div>' },
  javbus: { path: '/SSIS-057', title: 'JavBus', html: '<h3>作品详情</h3><div class="info"><p><span class="header">識別碼:</span> SSIS-057</p></div>' },
  javlibrary: { path: '/cn/?v=opaque_123&private=secret#player', source: '/cn/?v=opaque_123', title: 'JavLibrary',
    html: '<div id="video_id"><span class="text">SSIS-057</span></div><div id="video_title">作品详情</div>' },
  jav321: { path: '/video/opaque123', title: 'Jav321', html: '<h3>作品详情</h3><div class="panel-body"><b>品番:</b> SSIS-057<br><b>推荐:</b> ABW-123</div>' },
  avbase: { path: '/works/test-maker:SSIS-057', title: 'AVBase',
    html: '<h1>作品详情</h1><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"work":{"work_id":"SSIS-057","recommendations":[{"work_id":"ABW-123"}]}}}}</script>' },
  javdatabase: { path: '/movies/ssis-057/', title: 'JAVDatabase', html: '<h1>SSIS-057 作品</h1>' },
};

describe('JAV 用户脚本', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  it('实际安装端点生成的脚本在 JavDB 页面读取内联清单并完成添加', async () => {
    const response = await fetch(requiredEnv('PEACH_E2E_ORIGIN') + '/integrations/peach-jav.user.js');
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type') || '', /javascript/);
    const fixture = AUTOMATIC.javdb!;
    const opened = await open(browser, { url: `https://javdb.com${fixture.path}`, title: fixture.title,
      html: fixture.html, script: await response.text() });
    const { page } = opened;
    try {
      await ready(page);
      await page.evaluate(() => { window.javMock.automatic = false; });
      await button(page).click();
      await pending(page, 2);
      const submitted = await page.evaluate(() => JSON.parse(window.javMock.calls[1]!.data!));
      assert.deepEqual(submitted, { code: 'SSIS-057', source_url: 'https://javdb.com/v/ABP123' });
      await reply(page, 1, body('owned'));
      await ready(page, '馆藏已有');
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  for (const site of MANIFEST.sites.filter(site => site.mode === 'automatic')) {
    for (const domain of site.domains) {
      it(`${site.label} 自动识别 ${domain} 的明确作品身份`, async () => {
        const fixture = AUTOMATIC[site.id];
        assert.ok(fixture, `${site.id} 需要作品详情夹具`);
        const opened = await open(browser, { url: `https://${domain}${fixture.path}`,
          title: fixture.title, html: fixture.html + '<aside><h2>推荐 ABW-123</h2><a href="/ABW-123">ABW-123</a></aside>' });
        const { page } = opened;
        try {
          await ready(page);
          const lookup = await page.evaluate(() => window.javMock.calls[0]!);
          assert.equal(new URL(lookup.url).searchParams.get('code'), 'SSIS-057');
          await page.evaluate(() => { window.javMock.automatic = false; });
          await button(page).click();
          await pending(page, 2);
          const submitted = await page.evaluate(() => JSON.parse(window.javMock.calls[1]!.data!));
          assert.deepEqual(submitted, { code: 'SSIS-057', source_url: `https://${domain}${fixture.source || fixture.path}` });
          await reply(page, 1, body('owned'));
          await ready(page, '馆藏已有');
          assert.deepEqual(opened.problems, []);
        } finally { await opened.close(); }
      });
    }
  }

  for (const site of MANIFEST.sites.filter(site => site.mode === 'selection')) {
    for (const domain of site.domains) {
      it(`${site.label} 在 ${domain} 只按主动选中的番号显示添加按钮`, async () => {
        const path = site.id === 'dmm' ? '/av/content/?id=opaque_cid&token=private#player' : '/products/opaque?token=private#player';
        const opened = await open(browser, { url: `https://${domain}${path}`, title: 'SSIS-057 商品详情',
          html: '<h1>商品ID: ssis00057</h1><span id="selected-code">SSIS-057</span><aside>推荐 ABW-123</aside>' });
        const { page } = opened;
        try {
          await page.clock.fastForward(1000);
          assert.equal(await page.locator(ROOT).count(), 0);
          assert.equal(await page.evaluate(() => window.javMock.calls.length), 0);
          await selectCode(page);
          await ready(page);
          assert.deepEqual(await page.evaluate(() => window.javMock.calls.map(call => call.method)), ['GET']);
          await page.evaluate(() => { window.javMock.automatic = false; });
          await button(page).click();
          await pending(page, 2);
          const submitted = await page.evaluate(() => JSON.parse(window.javMock.calls[1]!.data!));
          assert.deepEqual(submitted, { code: 'SSIS-057', source_url: site.id === 'dmm'
            ? `https://${domain}/av/content/?id=opaque_cid` : `https://${domain}/products/opaque` });
          await reply(page, 1, body('owned'));
          await ready(page, '馆藏已有');
          assert.deepEqual(opened.problems, []);
        } finally { await opened.close(); }
      });
    }
  }

  it('选中菜单拒绝完整标题、多番号、编解码器和超长内容', async () => {
    const opened = await open(browser, { url: 'https://dmm.co.jp/av/content/?id=ssis00057',
      title: '商品详情', html: '<span id="selected-code"></span>' });
    const { page } = opened;
    let alerts = 0;
    page.on('dialog', dialog => { alerts++; void dialog.dismiss(); });
    try {
      for (const value of ['', 'SSIS-057 作品标题', 'SSIS-057 ABW-123', 'H264', 'X1234', 'SSIS-057'.repeat(30)]) {
        await page.locator('#selected-code').evaluate((element, value) => { element.textContent = value; }, value);
        await selectCode(page);
      }
      assert.equal(alerts, 6);
      assert.equal(await page.locator(ROOT).count(), 0);
      assert.equal(await page.evaluate(() => window.javMock.calls.length), 0);
    } finally { await opened.close(); }
  });

  for (const raw of ['N1234', 'k1234']) {
    it(`Tokyo-Hot 选中番号 ${raw} 接受小写规范化响应`, async () => {
      const opened = await open(browser, { url: 'https://www.tokyo-hot.com/product/opaque/', title: '商品详情',
        html: `<span id="selected-code">${raw}</span>` });
      const { page } = opened;
      try {
        await selectCode(page);
        await ready(page);
        assert.equal(new URL(await page.evaluate(() => window.javMock.calls[0]!.url)).searchParams.get('code'), raw.toLowerCase());
        await page.evaluate(() => { window.javMock.automatic = false; });
        await button(page).click();
        await pending(page, 2);
        assert.equal(JSON.parse(await page.evaluate(() => window.javMock.calls[1]!.data!)).code, raw.toLowerCase());
        await reply(page, 1, body('owned', raw.toLowerCase()));
        await ready(page, '馆藏已有');
      } finally { await opened.close(); }
    });
  }

  it('JavDB 标题与明确番号字段冲突时不查询或提交', async () => {
    const opened = await open(browser, { url: 'https://javdb.com/v/opaque123', title: 'JavDB',
      html: '<h2 class="title">SSIS-057 作品</h2><div class="panel-block"><strong>番號:</strong><span class="value">ABW-123</span></div>' });
    try {
      await ready(opened.page, '番号存在冲突');
      assert.equal(await button(opened.page).isDisabled(), true);
      assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 0);
    } finally { await opened.close(); }
  });

  it('用户选定番号接管自动冲突，点击提交后切页隔离旧响应', async () => {
    const opened = await open(browser, { url: 'https://javdb.com/v/opaque123', title: 'JavDB',
      html: '<h2 class="title">SSIS-057 作品</h2><div class="panel-block"><strong>番號:</strong><span class="value">ABW-123</span></div><span id="selected-code">SSIS-057</span>' });
    const { page } = opened;
    try {
      await ready(page, '番号存在冲突');
      assert.equal(await page.evaluate(() => window.javMock.calls.length), 0);
      await selectCode(page);
      await ready(page);
      assert.deepEqual(await page.evaluate(() => window.javMock.calls.map(call => call.method)), ['GET']);
      assert.match(await page.locator(ROOT).getByRole('status').innerText(), /SSIS-057/);
      await page.evaluate(() => { window.javMock.automatic = false; });
      await button(page).click();
      await pending(page, 2);
      assert.equal(JSON.parse(await page.evaluate(() => window.javMock.calls[1]!.data!)).code, 'SSIS-057');
      await page.evaluate(() => {
        history.pushState({}, '', '/v/otherOpaque');
        document.querySelector('h2.title')!.textContent = 'ABW-123 作品';
      });
      await pending(page, 3);
      await reply(page, 1, body('owned'));
      await reply(page, 2, body('idle', 'ABW-123'));
      await ready(page);
      assert.match(await page.locator(ROOT).getByRole('status').innerText(), /ABW-123/);
      assert.equal(await button(page).isDisabled(), false);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  for (const url of [
    'https://javdb.com/search?q=SSIS-057', 'https://javlibrary.com/cn/?q=SSIS-057',
    'https://javlibrary.com/cn/?v=one&v=two', 'https://jav321.com/search/SSIS-057',
    'https://javlibrary.com/cn/?v=opaque.with.dot', 'https://javlibrary.com/cn/?v=opaque~tilde',
    'https://jav.guru/category/SSIS-057/', 'https://njav.tv/en/search/SSIS-057',
    'https://javtiful.com/actress/SSIS-057', 'https://avbase.net/works?q=SSIS-057',
    'https://javdatabase.com/idols/SSIS-057/',
  ]) {
    it(`详情适配不扫描列表 ${url}`, async () => {
      const opened = await open(browser, { url, html: '<h1>SSIS-057</h1><h2 class="title">SSIS-057</h2>' });
      try {
        await opened.page.clock.fastForward(1000);
        assert.equal(await opened.page.locator(ROOT).count(), 0);
        assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 0);
      } finally { await opened.close(); }
    });
  }

  for (const domain of ['fc2.com', 'contents.fc2.com', 'other.fc2.com', 'javstore.net', 'r18.com', 'sodprime.com', 'javdb.com.evil.invalid']) {
    it(`未列入清单的域 ${domain} 不接受选中添加`, async () => {
      const opened = await open(browser, { url: `https://${domain}/products/SSIS-057`,
        html: '<h1>SSIS-057</h1><span id="selected-code">SSIS-057</span>' });
      try {
        await selectCode(opened.page);
        await opened.page.clock.fastForward(1000);
        assert.equal(await opened.page.locator(ROOT).count(), 0);
        assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 0);
      } finally { await opened.close(); }
    });
  }

  it('配置存入 GM，提交仅带番号和无查询参数的来源地址，重复点击只创建一项任务', async () => {
    const opened = await open(browser, { configured: false });
    const { page } = opened;
    try {
      await ready(page, '配置 Peach');
      const dialogs = [BASE, TOKEN];
      page.on('dialog', dialog => { void dialog.accept(dialogs.shift()); });
      await button(page).click();
      await ready(page);
      assert.equal(await page.evaluate(() => window.javMock.calls.length), 1);
      await page.evaluate(() => { window.javMock.automatic = false; });
      await button(page).click();
      await page.locator(ROOT).evaluate(root => {
        root.shadowRoot!.querySelector('button')!.click();
        root.shadowRoot!.querySelector('button')!.click();
      });
      await pending(page, 2);
      const calls = await page.evaluate(() => window.javMock.calls);
      assert.equal(calls[0]!.method, 'GET');
      assert.equal(calls[1]!.method, 'POST');
      assert.deepEqual(JSON.parse(calls[1]!.data!), { code: 'SSIS-057', source_url: 'https://missav.ws/en/ssis-057' });
      for (const call of calls) {
        assert.equal(new URL(call.url).origin, BASE);
        assert.equal(call.anonymous, true);
        assert.equal(call.headers.Authorization, `Bearer ${TOKEN}`);
        assert.equal('Cookie' in call.headers, false);
        assert.equal(call.url.includes(TOKEN), false);
        assert.equal(call.data?.includes(TOKEN) || false, false);
      }
      const surface = await page.evaluate(() => ({
        html: document.documentElement.outerHTML,
        panel: document.querySelector('#peach-jav-action')!.shadowRoot!.innerHTML,
        storage: JSON.stringify(localStorage), cookies: document.cookie, url: location.href,
      }));
      assert.equal(JSON.stringify(surface).includes(TOKEN), false);
      await reply(page, 1, body('queued'));
      await page.clock.fastForward(2100);
      await pending(page, 3);
      await reply(page, 2, body('searching'));
      await page.clock.fastForward(2100);
      await pending(page, 4);
      await reply(page, 3, body('submitted'));
      await page.clock.fastForward(2100);
      await pending(page, 5);
      await reply(page, 4, body('ingested'));
      await ready(page, '已入库');
      assert.equal(await button(page).isDisabled(), true);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  for (const example of [
    { url: 'https://jable.tv/videos/fc2-ppv-1234567/', title: 'FC2 PPV 1234567 作品', code: 'FC2-PPV-1234567' },
    { url: 'https://missav.ai/ja/ssni001', title: 'SSNI-001 作品', code: 'SSNI-001' },
    { url: 'https://missav.com/cn/abw-123-uncensored', title: 'ABW-123 作品', code: 'ABW-123' },
  ]) {
    it(`识别详情页 ${example.code}`, async () => {
      const opened = await open(browser, { url: example.url, title: example.title, html: `<h1>${example.title}</h1>` });
      try {
        await ready(opened.page);
        const calls = await opened.page.evaluate(() => window.javMock.calls);
        assert.equal(new URL(calls[0]!.url).searchParams.get('code'), example.code);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }

  for (const example of [
    { slug: 'abp-7', title: 'ABP-0007 作品', extracted: 'ABP-7', canonical: 'ABP-007' },
    { slug: '300mium-0123', title: '300MIUM-123 作品', extracted: '300MIUM-0123', canonical: '300MIUM-123' },
    { slug: '092415_001', title: '092415_001 作品', extracted: '092415_001', canonical: '092415_001' },
    { slug: '092415-001', title: '092415-001 作品', extracted: '092415-001', canonical: '092415-001' },
  ]) {
    it(`接受服务端规范化番号 ${example.canonical}`, async () => {
      const opened = await open(browser, { url: `https://jable.tv/videos/${example.slug}/`,
        title: example.title, html: `<h1>${example.title}</h1>` });
      const { page } = opened;
      try {
        await ready(page);
        await page.evaluate(() => { window.javMock.automatic = false; });
        await button(page).click();
        await pending(page, 2);
        const submitted = await page.evaluate(() => JSON.parse(window.javMock.calls[1]!.data!));
        assert.equal(submitted.code, example.extracted);
        await reply(page, 1, body('owned', example.canonical));
        await ready(page, '馆藏已有');
        assert.equal(await button(page).isDisabled(), true);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }

  for (const example of [
    { url: 'https://jable.tv/videos/092415_001/', title: '092415-001' },
    { url: 'https://jable.tv/videos/fc2-ppv-0123456/', title: 'FC2-PPV-123456' },
  ]) {
    it(`保留番号身份边界 ${example.title}`, async () => {
      const opened = await open(browser, { url: example.url, title: example.title, html: `<h1>${example.title}</h1>` });
      try {
        await ready(opened.page, '番号存在冲突');
        assert.equal(await button(opened.page).isDisabled(), true);
        assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 0);
      } finally { await opened.close(); }
    });
  }

  for (const invalid of [
    { base: 'http://peach.example.invalid', token: TOKEN },
    { base: `${BASE}/abc`, token: TOKEN },
    { base: `${BASE}/?query=1`, token: TOKEN },
    { base: `${BASE}/#fragment`, token: TOKEN },
    { base: 'https://user:password@peach.example.invalid', token: TOKEN },
    { base: BASE, token: 't'.repeat(257) },
    { base: BASE, token: '' },
  ]) {
    it(`拒绝保存无效配置 ${invalid.base} / token 长度 ${invalid.token.length}`, async () => {
      const opened = await open(browser, { configured: false });
      const { page } = opened;
      try {
        await ready(page, '配置 Peach');
        const dialogs = [invalid.base, invalid.token];
        page.on('dialog', dialog => { void dialog.accept(dialogs.shift()); });
        await button(page).click();
        await ready(page, '配置 Peach');
        assert.deepEqual(await page.evaluate(() => window.javMock.values), {});
        assert.equal(await page.evaluate(() => window.javMock.calls.length), 0);
        assert.match(await page.locator(ROOT).getByRole('status').innerText(), /地址或令牌无效/);
      } finally { await opened.close(); }
    });
  }

  for (const base of ['http://localhost:8099', 'http://127.0.0.1:8099', 'http://[::1]:8099']) {
    it(`接受 HTTP 回环配置 ${base}`, async () => {
      const opened = await open(browser, { configured: false });
      const { page } = opened;
      try {
        await ready(page, '配置 Peach');
        const dialogs = [base, TOKEN];
        page.on('dialog', dialog => { void dialog.accept(dialogs.shift()); });
        await button(page).click();
        await ready(page);
        const stored = await page.evaluate(() => window.javMock.values);
        assert.equal(stored['peach.jav.base'], base);
        assert.equal(await page.evaluate(() => window.javMock.calls[0]!.url), `${base}/api/jav-browser?code=SSIS-057`);
      } finally { await opened.close(); }
    });
  }

  for (const url of [
    'https://missav.ws/en', 'https://missav.ws/search/ssis-057',
    'https://jable.tv/categories/ssis-057/', 'https://jable.tv/videos/',
    'https://unrelated.example.invalid/videos/ssis-057/',
  ]) {
    it(`排除列表页与未支持页面 ${new URL(url).pathname}`, async () => {
      const opened = await open(browser, { url, html: '<h1>SSIS-057</h1><article>ABW-123 推荐作品</article>' });
      try {
        await opened.page.clock.fastForward(1000);
        assert.equal(await opened.page.locator(ROOT).count(), 0);
        assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 0);
      } finally { await opened.close(); }
    });
  }

  it('身份字段冲突时禁用，推荐区与查询参数不参与身份', async () => {
    const opened = await open(browser, { title: 'SSIS-057', html: '<h1>SSIS-057</h1><dl><dt>番号：</dt><dd>ABW-123</dd></dl>' });
    try {
      await ready(opened.page, '番号存在冲突');
      assert.equal(await button(opened.page).isDisabled(), true);
      assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 0);
    } finally { await opened.close(); }
    const detail = await open(browser, { html: '<h1>SSIS-057</h1><article><h2>推荐 ABW-123</h2></article>',
      url: 'https://missav.ws/en/ssis-057?related=ABW-123' });
    try { await ready(detail.page); } finally { await detail.close(); }
  });

  it('提交超时只提供查询，查询失败可重试，明确失败后可主动重新加入', async () => {
    const opened = await open(browser);
    const { page } = opened;
    try {
      await ready(page);
      await page.evaluate(() => { window.javMock.automatic = false; });
      await button(page).click();
      await pending(page, 2);
      await page.evaluate(() => window.javMock.pending[1]!.ontimeout());
      await ready(page, '查询 Peach 状态');
      await page.clock.fastForward(30_000);
      assert.equal(await page.evaluate(() => window.javMock.calls.length), 2);
      await button(page).click();
      await pending(page, 3);
      await page.evaluate(() => window.javMock.pending[2]!.onerror());
      await ready(page, '查询 Peach 状态');
      await button(page).click();
      await pending(page, 4);
      await reply(page, 3, body('failed'));
      await ready(page, '重试加入 Peach');
      await button(page).click();
      await pending(page, 5);
      assert.deepEqual(await page.evaluate(() => window.javMock.calls.map(call => call.method)), ['GET', 'POST', 'GET', 'GET', 'POST']);
      await reply(page, 4, body('no_match'));
      await ready(page, '重试加入 Peach');
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  it('状态响应身份不匹配或令牌无效时保留查询入口', async () => {
    const opened = await open(browser);
    const { page } = opened;
    try {
      await ready(page);
      await page.evaluate(() => { window.javMock.automatic = false; });
      await button(page).click();
      await pending(page, 2);
      await reply(page, 1, body('owned', 'ABW-123'));
      await ready(page, '查询 Peach 状态');
      assert.match(await page.locator(ROOT).getByRole('status').innerText(), /状态无效/);
      await button(page).click();
      await pending(page, 3);
      await page.evaluate(() => window.javMock.reply(2, {}, 401));
      await ready(page, '查询 Peach 状态');
      assert.match(await page.locator(ROOT).getByRole('status').innerText(), /令牌无效/);
    } finally { await opened.close(); }
  });

  it('SPA 切页隔离过期响应，列表页移除按钮，重复执行只注入一份', async () => {
    const opened = await open(browser);
    const { page } = opened;
    try {
      await ready(page);
      await page.evaluate(() => { window.javMock.automatic = false; });
      await button(page).click();
      await pending(page, 2);
      await page.evaluate(() => {
        history.pushState({}, '', '/en/abw-123');
        document.title = 'ABW-123';
        document.querySelector('h1')!.textContent = 'ABW-123';
      });
      await pending(page, 3);
      await reply(page, 1, body('owned'));
      await reply(page, 2, body('idle', 'ABW-123'));
      await ready(page);
      assert.match(await page.locator(ROOT).getByRole('status').innerText(), /ABW-123/);
      await page.addScriptTag({ content: SCRIPT });
      await page.clock.fastForward(1000);
      assert.equal(await page.locator(ROOT).count(), 1);
      assert.equal(await page.evaluate(() => window.javMock.calls.length), 3);
      await button(page).click();
      await pending(page, 4);
      await page.evaluate(() => {
        history.pushState({}, '', '/en/ssis-057');
        history.pushState({}, '', '/en/abw-123');
      });
      await page.clock.fastForward(1000);
      await pending(page, 5);
      await reply(page, 3, body('owned', 'ABW-123'));
      await reply(page, 4, body('idle', 'ABW-123'));
      await ready(page);
      await page.evaluate(() => { history.pushState({}, '', '/search/ABW-123'); });
      await page.clock.fastForward(1000);
      assert.equal(await page.locator(ROOT).count(), 0);
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close(); }
  });

  it('后台状态轮询有次数上限且不创建 POST，馆藏已有时不可加入', async () => {
    const opened = await open(browser, { initial: 'queued' });
    try {
      await pending(opened.page, 1);
      for (let index = 0; index < 30; index++) {
        await opened.page.waitForFunction(() => document.querySelector('#peach-jav-action')?.shadowRoot?.querySelector('p')?.textContent?.includes('已排队'));
        await opened.page.clock.fastForward(2100);
        await pending(opened.page, index + 2);
      }
      await opened.page.clock.fastForward(10);
      await ready(opened.page, '查询 Peach 状态');
      const calls = await opened.page.evaluate(() => window.javMock.calls);
      assert.equal(calls.length, 31);
      assert.ok(calls.every(call => call.method === 'GET'));
      await opened.page.clock.fastForward(60_000);
      assert.equal(await opened.page.evaluate(() => window.javMock.calls.length), 31);
    } finally { await opened.close(); }
    const owned = await open(browser, { initial: 'owned' });
    try {
      await ready(owned.page, '馆藏已有');
      assert.equal(await button(owned.page).isDisabled(), true);
    } finally { await owned.close(); }
  });

  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 下按钮可聚焦且不越出视口`, async () => {
      const opened = await open(browser, { viewport });
      try {
        await ready(opened.page);
        await button(opened.page).focus();
        const state = await button(opened.page).evaluate(element => {
          const rect = element.getBoundingClientRect();
          return { left: rect.left, right: rect.right, width: innerWidth,
            focused: element.getRootNode() instanceof ShadowRoot && (element.getRootNode() as ShadowRoot).activeElement === element,
            outline: getComputedStyle(element).outlineStyle };
        });
        assert.equal(state.focused, true);
        assert.notEqual(state.outline, 'none');
        assert.ok(state.left >= 0 && state.right <= state.width);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
  }
});
