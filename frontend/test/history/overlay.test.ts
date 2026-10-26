/* 详情类历史条目的背景（`src/history/overlay.ts`）：壳决定打开详情时记下背景，push 详情地址时带进 `usr`；
 * 详情之间换条沿用上一条的背景，关掉详情与别的地址都不带。这里照壳的调用顺序走一遍：
 * `holdOverlayBackground()` → `shellNavigate(详情地址, {state: overlayState(种类)})`。
 * 后退前进落到详情条目上时壳先清、再 `adoptOverlayState(usr)`，打开详情那一处 `takeOverlayReturn()` 取来处；
 * 启动那一次不调 `adoptOverlayState`，覆盖元素什么时候接背景见 `test/react/pages/overlay.test.tsx`。 */
import { beforeEach, expect, it } from 'vitest';

import * as overlay from '../../src/history/overlay';
import {
  adoptOverlayState, clearOverlayBackground, holdOverlayBackground, isOverlayPath, overlayState, peachHistory,
  shellNavigate, takeOverlayReturn,
} from '../../src/history';

beforeEach(() => {
  shellNavigate('/', { replace: true, state: null });
  clearOverlayBackground();
});

const usr = () => peachHistory.navigation.location.state;
const length = () => window.history.length;

/** 壳打开一条详情：决定那一刻记背景，再 push 详情地址。 */
function open(path: string, overlay: 'item' | 'follow' = 'item') {
  holdOverlayBackground();
  shellNavigate(path, { state: overlayState(overlay) });
}

/** 浏览器后退 `steps` 条，等这一次历史变化分发完。 */
function back(steps = 1) {
  return new Promise<void>((resolve) => {
    const stop = peachHistory.listen(() => { stop(); resolve() });
    peachHistory.go(-steps);
  });
}

/** 壳的 `restoreRoute('history')` 落到这一条上：先清，再接条目自己记的背景。 */
function revisit() {
  clearOverlayBackground();
  adoptOverlayState(usr());
}

it('共享 history 入口与覆盖背景模块使用同一份绑定', () => {
  expect(overlay.holdOverlayBackground).toBe(holdOverlayBackground);
  expect(overlay.overlayState).toBe(overlayState);
  expect(overlay.adoptOverlayState).toBe(adoptOverlayState);
  expect(overlay.takeOverlayReturn).toBe(takeOverlayReturn);
});

it('覆盖层地址是作品详情、四种队列与关注详情，页面与沉浸不算', () => {
  for (const path of ['/assets/7', '/assets/5?mix=4', '/assets/2?parts=1', '/assets/3?editions=3', '/assets/9?playlist=2', '/posts/3']) {
    expect([path, isOverlayPath(path)]).toEqual([path, true]);
  }
  for (const path of ['/', '/playlists', '/follow', '/performers/x', '/immerse', '/trash', '/stats']) {
    expect([path, isOverlayPath(path)]).toEqual([path, false]);
  }
});

it('从页面点进详情：push 带上打开那一刻的地址与查询串', () => {
  shellNavigate('/performers/x?sort=new');
  const before = length();
  open('/assets/7');
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/performers/x', search: '?sort=new' }, overlay: 'item' });
  expect(length()).toBe(before + 1);
});

it('队列地址取完数才推：背景是决定那一刻的地址，不是推的那一刻', () => {
  shellNavigate('/?tag=a');
  holdOverlayBackground();
  shellNavigate('/stats', { replace: true });
  shellNavigate('/assets/5?mix=4', { state: overlayState('item') });
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/', search: '?tag=a' }, overlay: 'item' });
});

it('同队列换条沿用上一条的背景，不把上一条详情当背景', () => {
  shellNavigate('/playlists');
  open('/assets/9?playlist=2');
  open('/assets/10?playlist=2');
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/playlists', search: '' }, overlay: 'item' });
});

it('关注详情组内换条沿用列表那一页（带筛选）', () => {
  shellNavigate('/follow?view=saved');
  open('/posts/3', 'follow');
  open('/posts/4', 'follow');
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/follow', search: '?view=saved' }, overlay: 'follow' });
});

it('关掉详情的 push 与别的地址都不带状态；清掉之后再进详情重新记', () => {
  shellNavigate('/trash');
  open('/assets/7');
  clearOverlayBackground();
  shellNavigate('/trash');
  expect(usr()).toBeNull();
  shellNavigate('/immerse');
  expect(usr()).toBeNull();
  open('/assets/8');
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/immerse', search: '' }, overlay: 'item' });
});

it('页面上从不接着旧背景：没清也按这一页重新记', () => {
  shellNavigate('/');
  open('/assets/7');
  shellNavigate('/stats');
  open('/assets/8');
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/stats', search: '' }, overlay: 'item' });
});

it('落在详情地址上又没有背景（深链、后退进来）：再开一条不带状态，不把详情地址当背景', () => {
  shellNavigate('/assets/7');
  open('/assets/8');
  expect(usr()).toBeNull();
});

it('资产跳到关注条目时替换当前条目并保留背景', () => {
  shellNavigate('/performers/x');
  open('/assets/7');
  const before = length();
  shellNavigate('/posts/9', { replace: true, state: overlayState('follow') });
  expect([location.pathname, length()]).toEqual(['/posts/9', before]);
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/performers/x', search: '' }, overlay: 'follow' });
});

it('后退回到从资料页打开的那一条：来处是条目记的资料页，不是之后点开别的详情时的那一页', async () => {
  shellNavigate('/performers/x?sort=new');
  open('/assets/7');
  clearOverlayBackground();
  shellNavigate('/performers/x?sort=new');
  shellNavigate('/');
  open('/assets/8');
  await back(3);
  expect(location.pathname).toBe('/assets/7');
  revisit();
  expect(takeOverlayReturn()).toBe('/performers/x?sort=new');
});

it('来处取一次就清；条目没记背景（页面条目、深链）时给 null，壳用自己的缺省', () => {
  adoptOverlayState({ backgroundLocation: { pathname: '/trash', search: '' }, overlay: 'item' });
  expect([takeOverlayReturn(), takeOverlayReturn()]).toEqual(['/trash', null]);
  for (const state of [null, undefined, { from: 'router' }, { backgroundLocation: { search: '?q=1' } }]) {
    revisit();
    adoptOverlayState(state);
    expect([state, takeOverlayReturn(), overlayState('item')]).toEqual([state, null, undefined]);
  }
});

it('清掉背景时连同没取走的来处一起清', () => {
  adoptOverlayState({ backgroundLocation: { pathname: '/stats', search: '' }, overlay: 'item' });
  clearOverlayBackground();
  expect([takeOverlayReturn(), overlayState('item')]).toEqual([null, undefined]);
});

it('关注详情：后退回到详情，来处保住列表的筛选', async () => {
  shellNavigate('/follow?view=saved');
  open('/posts/3', 'follow');
  clearOverlayBackground();
  shellNavigate('/follow?view=saved');
  await back();
  expect(location.pathname).toBe('/posts/3');
  revisit();
  expect(takeOverlayReturn()).toBe('/follow?view=saved');
});

it('后退进详情之后再换条：新条目接着带同一个背景', async () => {
  shellNavigate('/playlists');
  open('/assets/9?playlist=2');
  open('/assets/10?playlist=2');
  shellNavigate('/stats');
  await back();
  expect(location.pathname + location.search).toBe('/assets/10?playlist=2');
  revisit();
  takeOverlayReturn();
  open('/assets/11?playlist=2');
  expect(usr()).toEqual({ backgroundLocation: { pathname: '/playlists', search: '' }, overlay: 'item' });
});
