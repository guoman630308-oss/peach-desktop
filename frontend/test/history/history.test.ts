/* 全站那一份浏览器历史（`src/history/`）：壳经 `shellNavigate` 写地址，后退前进与 React 子树的写入
 * 都从同一个对象分发。 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import * as sharedHistory from '@peach/history';
import { canGoBack, historyPagePosition, peachHistory, rememberHistoryPages, restoreHistoryPage,
  returnToHistoryPage, routeMetaOf, shellNavigate, type Navigation } from '../../src/history';

let seen: Navigation[] = [];
let stop = () => {};
beforeEach(() => {
  shellNavigate('/', { replace: true });
  seen = [];
  stop = peachHistory.listen((navigation) => { seen.push(navigation) });
});
afterEach(() => { stop(); vi.restoreAllMocks(); vi.unstubAllGlobals() });

/** 浏览器自己的后退前进：地址先变，再派发 `popstate`。 */
function pop(path: string) {
  window.history.replaceState(window.history.state, '', path);
  window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
}

it('共享 history 入口与真实浏览器历史模块使用同一份绑定', () => {
  expect(sharedHistory.peachHistory).toBe(peachHistory);
  expect(sharedHistory.shellNavigate).toBe(shellNavigate);
});

it('push 加一条历史，replace 不加', () => {
  const length = window.history.length;
  shellNavigate('/stats');
  expect([location.pathname, window.history.length]).toEqual(['/stats', length + 1]);
  shellNavigate('/taste', { replace: true });
  expect([location.pathname, window.history.length]).toEqual(['/taste', length + 1]);
  expect(seen.map((n) => n.action)).toEqual(['PUSH', 'REPLACE']);
});

it('后退按历史条目恢复位置与页数，同地址的新条目独立记录', () => {
  shellNavigate('/performers?q=return');
  const original = window.history.state;
  vi.stubGlobal('scrollY', 720);
  rememberHistoryPages(3);
  shellNavigate('/performers/7');
  vi.stubGlobal('scrollY', 0);
  window.history.replaceState(original, '', '/performers?q=return');
  window.dispatchEvent(new PopStateEvent('popstate', { state: original }));
  expect(historyPagePosition()).toMatchObject({ top: 720, pages: 3 });
  const scroll = vi.fn();
  vi.stubGlobal('scrollTo', scroll);
  expect(restoreHistoryPage()).toBe(true);
  expect(scroll).toHaveBeenCalledWith({ left: 0, top: 720, behavior: 'instant' });
  shellNavigate('/performers?q=return');
  expect(restoreHistoryPage()).toBe(false);
});

it('没有站内上一条历史时返回不可用', () => {
  const initial = { key: 'back-test-boot', idx: 0, usr: null };
  window.history.replaceState(initial, '', '/');
  window.dispatchEvent(new PopStateEvent('popstate', { state: initial }));
  expect(canGoBack()).toBe(false);
  shellNavigate('/performers');
  expect(canGoBack()).toBe(true);
});

it('关闭连续播放详情回到原始页面条目，不把中间视频当作上一页', () => {
  shellNavigate('/performers/7?tag=return');
  const original = window.history.state;
  shellNavigate('/assets/11?parts=11');
  shellNavigate('/assets/12?parts=11');
  shellNavigate('/assets/12?parts=11&t=10', { replace: true });
  const go = vi.spyOn(peachHistory, 'go').mockImplementation(() => {});
  const returned = vi.fn(() => peachHistory.navigation.location.pathname);
  expect(returnToHistoryPage('/performers/7?tag=return', { onReturn: returned })).toBe(true);
  expect(returned).not.toHaveBeenCalled();
  expect(go).toHaveBeenCalledExactlyOnceWith(-2);
  const epoch = peachHistory.navigation.openEpoch;
  window.history.replaceState(original, '', '/performers/7?tag=return');
  window.dispatchEvent(new PopStateEvent('popstate', { state: original }));
  expect(peachHistory.navigation).toMatchObject({ action: 'POP', claimed: true, openEpoch: epoch });
  expect(returned).toHaveReturnedWith('/performers/7');
});

it('关闭详情只回到紧邻的已知背景，不穿过别的列表或跨越刷新前的未知条目', () => {
  shellNavigate('/performers');
  shellNavigate('/tags');
  shellNavigate('/assets/11');
  const go = vi.spyOn(peachHistory, 'go').mockImplementation(() => {});
  expect(returnToHistoryPage('/performers')).toBe(false);
  expect(returnToHistoryPage('https://example.com/tags')).toBe(false);
  const unknown = { key: 'unknown-history', idx: 10_000, usr: null };
  window.history.replaceState(unknown, '', '/assets/11');
  window.dispatchEvent(new PopStateEvent('popstate', { state: unknown }));
  expect(returnToHistoryPage('/tags')).toBe(false);
  expect(go).not.toHaveBeenCalled();
});

it('路径照 pushState 的规则按当前地址解析：只带查询串或只带锚点时其余部分不丢', () => {
  shellNavigate('/performers?q=a');
  shellNavigate('?q=b');
  expect(location.pathname + location.search).toBe('/performers?q=b');
  shellNavigate('#top');
  expect(location.pathname + location.search + location.hash).toBe('/performers?q=b#top');
  shellNavigate(`${location.origin}/tags`);
  expect(location.pathname).toBe('/tags');
});

it('清理地址时把当前条目的状态原样带过去', () => {
  peachHistory.push('/follow', { from: 'router' });
  const state = peachHistory.navigation.location.state;
  shellNavigate('/follow?onboarding=1', { replace: true, state });
  expect(peachHistory.navigation.location.state).toEqual({ from: 'router' });
  shellNavigate('/data-cleanup#libraryProcessing', { replace: true, state: null });
  expect(peachHistory.navigation.location.state).toBeNull();
});

it('每一次变化领一个递增序号，后退前进也算', () => {
  shellNavigate('/stats');
  pop('/');
  peachHistory.push('/tags');
  expect(seen.map((n) => n.action)).toEqual(['PUSH', 'POP', 'PUSH']);
  const [first, second, third] = seen.map((n) => n.seq);
  expect([second! - first!, third! - second!]).toEqual([1, 1]);
  expect(peachHistory.navigation).toBe(seen.at(-1));
  expect(seen[1]!.location.pathname).toBe('/');
});

it('页面打开序号只在没人认领的变化上递增：壳认领的写地址不领，后退前进、路由树 push 与 replace 各领一个', () => {
  const start = peachHistory.navigation.openEpoch;
  shellNavigate('/stats');
  shellNavigate('/stats?tab=1', { replace: true });
  pop('/');
  peachHistory.push('/tags');
  peachHistory.replace('/follow');
  expect(seen.map((n) => n.openEpoch - start)).toEqual([0, 0, 1, 2, 3]);
});

it('顶栏换一批的行为登记在路由元数据上：关注页与关注管理页跳过、口味页与播放列表页重开、统计页不登记', () => {
  expect(routeMetaOf('/follow')?.refresh).toBe('skip');
  expect(routeMetaOf('/subscriptions')?.refresh).toBe('skip');
  expect(routeMetaOf('/playlists')?.refresh).toBe('reopen');
  expect(routeMetaOf('/taste')?.refresh).toBe('reopen');
  expect(routeMetaOf('/stats')?.refresh).toBeUndefined();
});

it('对象式导航使用规范路径并保留查询片段与状态', () => {
  const state = { from: 'react' };
  peachHistory.push({ pathname: '/item/12', search: '?t=93', hash: '#media' }, state);
  expect(location.pathname + location.search + location.hash).toBe('/assets/12?t=93#media');
  expect(peachHistory.navigation.location.state).toEqual(state);
  peachHistory.replace({ pathname: '/configuration', search: '?tab=source' }, state);
  expect(location.pathname + location.search).toBe('/settings/library?tab=source');
});

it('后退前进的兼容地址只通知一次且保留原历史条目', () => {
  const state = { ...window.history.state, usr: { backgroundLocation: { pathname: '/jav', search: '?scope=library' }, overlay: 'item' } };
  const epoch = peachHistory.navigation.openEpoch;
  window.history.replaceState(state, '', '/parts/7/12?t=93#media');
  window.dispatchEvent(new PopStateEvent('popstate', { state }));
  expect(location.pathname + location.search + location.hash).toBe('/assets/12?t=93&parts=7#media');
  expect(window.history.state).toEqual(state);
  expect(seen).toHaveLength(1);
  expect(seen[0]!.action).toBe('POP');
  expect(seen[0]!.openEpoch).toBe(epoch + 1);
});
