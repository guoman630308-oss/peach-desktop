/* 设置面板（`settings-panel` 岛）的桩与入口：`settings-panel.test.ts` 与设计决定用例（`design-*.test.ts`）共用。
 *
 * 跟数据库走的 `/api/settings` 与两份机器状态（`/api/follow/schedule`、`/api/thumbnail-jobs`）各给一份
 * 内存桩：读写都由它接住，好看写进去的是什么，也让「刷新后保持」不依赖临时服务上的状态。 */
import type { Browser, Page, Request } from 'playwright-core';

import { settle, visit, VIEWPORTS, type Viewport, type Visit } from './harness.ts';

export const PANEL = '#settingsPanel';
export const ORDER = ['', 'follow', 'jav', 'performers', 'tags', 'studios', 'flagged', 'manage'];
const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;

export interface Server {
  settings: Record<string, unknown>;
  schedule: Record<string, unknown>;
  thumbnails: Record<string, unknown>;
  /** 三个写接口收到的请求体，按到达顺序。 */
  posts: { path: string; body: Record<string, unknown> }[];
}

const body = (request: Request) => JSON.parse(request.postData() || '{}') as Record<string, unknown>;

export async function stubServer(page: Page): Promise<Server> {
  const server: Server = {
    settings: {
      sidebarOrder: [...ORDER], metadataRefreshDays: 30, followInitialDays: 30, postSetupTutorialDone: true,
      organizeTemplates: {}, feedHideGroupCompilations: true, feedHideSoloCompilations: false, feedHideExcerpts: true,
      searchHistoryLimit: 10,
    },
    schedule: {
      available: true, enabled: true, interval_minutes: 180, running: false, last_error: null,
      last_finished_at: '2026-09-30T08:00:00Z', last_added: 3, next_run_at: null,
    },
    thumbnails: { mode: 'precise', status: 'idle' },
    posts: [],
  };
  const route = (path: string, merge: (sent: Record<string, unknown>) => Record<string, unknown>) =>
    page.route((url) => url.pathname === path, (handled) => {
      const request = handled.request();
      if (request.method() === 'GET') return handled.fulfill({ json: merge({}) });
      const sent = body(request);
      server.posts.push({ path, body: sent });
      return handled.fulfill({ json: merge(sent) });
    });
  await route('/api/settings', (sent) => {
    Object.assign(server.settings, sent);
    return { ok: true, ...server.settings };
  });
  await route('/api/follow/schedule', (sent) => Object.assign(server.schedule, sent));
  await route('/api/thumbnail-jobs', (sent) => Object.assign(server.thumbnails, sent));
  return server;
}

/** 齿轮打开面板并停在 `section` 那一格。窄屏下齿轮收在抽屉底部，直接派发点击，两种宽度走同一条入口。 */
export async function openPanel(page: Page, section = '外观与导航'): Promise<void> {
  await page.locator('#settingsBtn').evaluate((button: HTMLElement) => button.click());
  await page.locator(`${PANEL}:not([hidden])`).waitFor({ timeout: 10_000 });
  if (section !== '外观与导航') await page.locator(PANEL).getByRole('tab', { name: section, exact: true }).click();
  await page.locator(PANEL).getByRole('tab', { name: section, exact: true })
    .and(page.locator('[aria-selected="true"]')).waitFor({ timeout: 5_000 });
}

/** 首页挂上桩之后重载一次，桩从第一次取设置起就接住。 */
export async function openHome(browser: Browser, viewport: Viewport = DESKTOP): Promise<Visit & { server: Server }> {
  const opened = await visit(browser, '/', viewport);
  const server = await stubServer(opened.page);
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('#settingsBtn').waitFor({ state: 'attached', timeout: 15_000 });
  await settle(opened.page);
  return { ...opened, server };
}
