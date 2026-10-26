/* 真浏览器冒烟的夹具：打开页面、收集运行期问题、测布局几何。
 *
 * 只用 playwright-core 驱动本机已装的 Chrome，不下载浏览器；路径由
 * `tests/test_web_e2e.py` 探测后经 PEACH_E2E_CHROME 传进来。服务、数据库和演示库
 * 也归 Python 那一侧准备，这里只认 PEACH_E2E_ORIGIN。
 *
 * 用例只写语义断言（没有横向溢出、等待态会结束、控制台无错误），不做截图比对：
 * 单人部署里维护基线图的成本高于它能拦住的问题。 */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

import { chromium, type Browser, type BrowserContext, type Locator, type Page } from 'playwright-core';

export interface Viewport {
  name: string;
  width: number;
  height: number;
  mobile: boolean;
}

export const VIEWPORTS: readonly Viewport[] = [
  { name: 'desktop', width: 1280, height: 800, mobile: false },
  { name: 'mobile', width: 390, height: 844, mobile: true },
];

export interface Visit {
  page: Page;
  /** 页面异常、console.error、同源 4xx/5xx 与失败请求，按发生顺序。 */
  problems: string[];
  close(): Promise<void>;
}

export interface Layout {
  viewportWidth: number;
  scrollWidth: number;
  /** 右边缘越出视口、且没有被任何祖先横向裁切的可见元素，最多列 5 个。 */
  offenders: string[];
}

export function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} 未设置：用 \`tests/test_web_e2e.py\` 起服务后再跑`);
  return value;
}

export async function launch(): Promise<Browser> {
  const args: string[] = [];
  const networkLogs = process.env.PEACH_E2E_NET_LOG_DIR;
  if (networkLogs) {
    mkdirSync(networkLogs, { recursive: true });
    const filename = `network-${process.pid}-${randomUUID()}.json`;
    args.push(`--log-net-log=${join(networkLogs, filename)}`, '--net-log-capture-mode=HeavilyRedacted');
    console.info(`浏览器网络事件日志：${filename}`);
  }
  return chromium.launch({
    headless: true,
    executablePath: requiredEnv('PEACH_E2E_CHROME'),
    args,
    // Windows 首次加载浏览器与系统组件使用独立的启动预算，页面等待由各用例限定。
    timeout: 60_000,
  });
}

/** 等待态以 `aria-busy="true"` 与 `data-skeleton` 为准；超时就是等待态卡住了。
 * 两个标记由页面自己写：island、遗留模板和 React 子树都得发出，这里才等得到。
 *
 * 它只证明「没有东西在等」，证明不了页面画出来了：主体完全没渲染时同样立刻成立。
 * 所以调用方先等到目标页面自己的主体出现，再调它。 */
export async function settle(page: Page, scope = 'body'): Promise<void> {
  /* 活动页和馆藏页会持续轮询任务状态，本来就不保证进入 `networkidle`。页面导航由
     `visit()` 等到 load，主体由调用方的 `expectBody()` 验证；这里只等 Peach 自己公开的
     等待态结束。 */
  try {
    await page.waitForFunction(
      (selector) => {
        const root = document.querySelector(selector);
        return !!root && !root.matches('[aria-busy="true"],[data-skeleton]')
          && !root.querySelector('[aria-busy="true"],[data-skeleton]');
      },
      scope, { timeout: 15_000 });
  } catch (error) {
    const pending = await page.evaluate((selector) => {
      const root = document.querySelector(selector);
      if (!root) return { missing_scope: selector };
      const nodes = [...root.querySelectorAll('[aria-busy="true"],[data-skeleton]')];
      if (root.matches('[aria-busy="true"],[data-skeleton]')) nodes.unshift(root);
      return nodes.slice(0, 12).map(node => {
        const box = node.getBoundingClientRect();
        return { tag: node.tagName, id: node.id, skeleton: node.getAttribute('data-skeleton'),
          label: node.getAttribute('aria-label'), scope: node.closest('dialog')?.id || 'page',
          width: box.width, height: box.height };
      });
    }, scope).catch(() => '未取得');
    throw new Error(`页面等待态未结束：${JSON.stringify(pending)}\n${(error as Error).message}`);
  }
}

/** 等目标页面自己的主体出现：每个定位器都要可见，之后才轮到 `settle`。
 * 等不到就带上此刻 `#main` 的无障碍树失败：CI 上只有这段输出，看得出停在骨架、
 * 空态换了文案还是整块没画。 */
export async function expectBody(page: Page, where: string, body: readonly Locator[], problems: readonly string[] = []): Promise<void> {
  for (const locator of body) {
    try {
      await locator.waitFor({ state: 'visible', timeout: 15_000 });
    } catch (error) {
      const snapshot = await page.locator('#main').ariaSnapshot({ timeout: 5_000 }).catch(() => '未取得');
      assert.fail(`${where}：页面主体没有出现，等不到 ${locator}\n${(error as Error).message}\n`
        + `页面：${page.url()}；已关闭：${page.isClosed()}；运行期问题：${JSON.stringify(problems)}\n`
        + `#main 当时的无障碍树：\n${snapshot.slice(0, 3000)}`);
    }
  }
}

/** Peach 配置页的主体：标题与默认的媒体库分区。 */
export function configurationBody(page: Page): Locator[] {
  return [
    page.locator('#main').getByRole('heading', { name: 'Peach 配置', exact: true }),
    page.locator('#stats').getByRole('tab', { name: '媒体库与入库' }),
  ];
}

export async function visit(browser: Browser, path: string, viewport: Viewport,
  prepare?: (page: Page) => Promise<void>): Promise<Visit> {
  const origin = requiredEnv('PEACH_E2E_ORIGIN');
  const context: BrowserContext = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: viewport.mobile,
    hasTouch: viewport.mobile,
    reducedMotion: 'reduce',
  });
  // 详情页默认自动播放；这台机器旁边有人，冒烟不出声。
  await context.addInitScript(() => {
    localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false }));
  });
  const page = await context.newPage();
  const problems: string[] = [];
  page.on('pageerror', (error) => problems.push(`pageerror ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console ${message.text()}`);
  });
  page.on('response', (response) => {
    if (response.url().startsWith(origin) && response.status() >= 400) {
      problems.push(`${response.status()} ${response.url().slice(origin.length)}`);
    }
  });
  page.on('requestfailed', (request) => {
    const reason = request.failure()?.errorText ?? '';
    if (request.url().startsWith(origin) && !reason.includes('ERR_ABORTED')) {
      problems.push(`failed ${request.url().slice(origin.length)} ${reason}`);
    }
  });
  // 只打开到 load：等哪块主体、何时 settle 由用例决定，见 `settle` 的说明。
  try {
    await prepare?.(page);
    await page.goto(origin + path, { waitUntil: 'load' });
  } catch (error) {
    await context.close();
    throw error;
  }
  return { page, problems, close: () => context.close() };
}

/* 越界名单只报根因：页面一旦被撑宽，移动视口跟着变宽，`fixed` 的顶栏、Toast 会一起
 * 伸出去，按文档顺序截前几个就只剩这些症状。所以跳过 `fixed` 及其后代，并且只留
 * 父元素自己没越界的那一层。 */
export async function layout(page: Page): Promise<Layout> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const excused = (element: Element): boolean => {
      if (getComputedStyle(element).position === 'fixed') return true;
      for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        if (style.overflowX !== 'visible' || style.position === 'fixed') return true;
      }
      return false;
    };
    const label = (element: Element): string => {
      const id = element.id ? `#${element.id}` : '';
      const classes = [...element.classList].slice(0, 2).map((name) => `.${name}`).join('');
      return `${element.tagName.toLowerCase()}${id}${classes}`;
    };
    const overflowing = new Set<Element>();
    for (const element of document.body.querySelectorAll('*')) {
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0 || box.right <= width + 1) continue;
      const style = getComputedStyle(element);
      if (style.visibility === 'hidden' || style.display === 'none' || excused(element)) continue;
      overflowing.add(element);
    }
    const offenders = [...overflowing]
      .filter((element) => !element.parentElement || !overflowing.has(element.parentElement))
      .slice(0, 5)
      .map((element) => `${label(element)} right=${Math.round(element.getBoundingClientRect().right)}`);
    return {
      viewportWidth: width,
      scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
      offenders,
    };
  });
}
