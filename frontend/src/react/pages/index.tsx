/* 独立页面包的构建入口（`web/dist/peach-pages.js`）。
 *
 * 首启、登录与错误三页共用服务端给的同一张HTML 模板，壳里只有一个挂载点 `#peach-page`：`data-page` 说是
 * 哪一页，其余 `data-*` 是服务端才知道的变量，原样交给那一页。这份产物不依赖主界面的任何模块：
 * 不加载主包，不带 Query 缓存，共用控件（折叠、来源站标）由 `vite.pages.config.ts`
 * 直接打进来。三页都用浏览器原生滚动条。弹出层落在 body 上，body 本身就是 `.peach-react` 容器。
 *
 * 这个包不要会话就能取（ADR-0094）：不得 import 别的产物，不得内嵌数据库、配置或凭据派生的数据。 */
import './pages.css';
import { installErrorReporting } from '../../error-reporting';

import type { ComponentType } from 'react';
import { createRoot } from 'react-dom/client';

import { ErrorPage } from './error/error-page';
import { LoginPage } from './login/login-page';
import type { PageData } from './page-data';
import { SetupPage } from './setup/setup-page';

const PAGES: Readonly<Record<string, ComponentType<{ data: PageData }>>> = {
  setup: SetupPage,
  login: LoginPage,
  error: ErrorPage,
};

installErrorReporting();
const mount = document.getElementById('peach-page');
const Page = mount ? PAGES[mount.dataset.page ?? ''] : undefined;
if (mount && Page) createRoot(mount).render(<Page data={{ ...mount.dataset }} />);
