/* 覆盖组的元素（ADR-0031「详情条目记下压在哪一页上」）：作品详情、四种队列与关注详情各一条路径
 * （`OVERLAY_PATHS`），覆盖组按真实地址匹配，挂上就把那一条送进舞台，卸下去处不是覆盖地址就收起舞台。
 *
 * 什么时候打开：
 * - 启动（刷新、新标签页、深链）：壳开始路由（`@peach/shell` 的 `pageOpens` 从 0 变成 1）那一刻打开；路由根晚于
 *   壳开始路由才装上、地址还停在启动那一条上时，挂上就打开。启动那一条不读背景，关掉回各自的缺省来处。
 * - 没人认领的历史变化（后退前进、React 子树与壳经 `peachHistory` 直接写的地址）：接上条目记的背景
 *   （`adoptOverlayState`），再打开。元素按导航序号挂 key，地址没变的后退（同一条目重放、`history.state` 为空）
 *   也领一个新代次，重挂、再开一次。
 * - 壳认领写的地址：点卡片把一次性打开请求交给覆盖元素，挂上后取走；队列取齐后写地址与转换详情种类只改条目。
 * 打开与收起都交壳（`ShellActions.openOverlay`、`closeStage`）：取数、来处与舞台句柄都还在壳里。
 *
 * 页面组那一侧（`router.tsx`）：有背景时按背景匹配，背景页不重挂；没有背景的覆盖地址也挂一格，作品与队列补画
 * 目录网格（目录元素），关注详情只让出 `#stats`（`FollowDetailGround`），不画列表。
 *
 * 打开、收起与让位都排进微任务：路由根在历史变化的同一次调用里同步提交，壳打开舞台要同步画，在提交阶段里画会
 * 出 flushSync 告警。历史通知同步更新壳的标题与侧栏，页面取数随后领取当前代次。 */
import { useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore, type ReactNode } from 'react';

import {
  adoptOverlayState, bootEntry, clearOverlayBackground, isOverlayPath, overlayTarget, peachHistory, type OverlayPath,
  settingsBackgroundOf, isPreferencePath,
} from '@peach/history';
import { pageOpens, queueOpens, subscribeShell } from '@peach/shell';

import { ShellActionsContext, useOpenEpoch } from '../router';

const readOpens = () => pageOpens;
const readQueueOpens = () => queueOpens;

/** 队列尚未定下条目时的打开意图：路由树接手，等待首屏确定实际地址；不在历史里放临时条目。 */
export function QueueRequests(): ReactNode {
  const actions = useContext(ShellActionsContext);
  const requests = useSyncExternalStore(subscribeShell, readQueueOpens);
  const opens = useSyncExternalStore(subscribeShell, readOpens);
  useEffect(() => {
    if (!requests || !opens) return;
    let live = true;
    queueMicrotask(() => { if (live) actions?.openQueueRequest?.() });
    return () => { live = false };
  }, [requests, opens, actions]);
  return null;
}

/** 挂上的这一格要不要按地址打开、什么时候打开。`run(boot)` 只跑一次：`boot` 为真是启动那一次（壳开始路由时，
 *  或路由根晚装上时地址还是启动那一条），为假是一次历史变化。`claimedOpens` 允许认领写地址的打开请求。
 *  覆盖元素按导航序号挂 key；沉浸元素按页面打开序号挂 key。 */
export function useRouteOpen(run: (boot: boolean) => void, claimedOpens = false): void {
  const opens = useSyncExternalStore(subscribeShell, readOpens);
  const pending = useRef<'boot' | 'history' | null | undefined>(undefined);
  if (pending.current === undefined) {
    const { claimed } = peachHistory.navigation;
    pending.current = pageOpens === 0 || bootEntry() ? 'boot' : claimed && !claimedOpens ? null : 'history';
  }
  const latest = useRef(run);
  useEffect(() => { latest.current = run });
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false };
  }, []);
  useEffect(() => {
    const origin = pending.current;
    if (!origin || opens === 0) return;
    pending.current = null;
    queueMicrotask(() => { if (live.current) latest.current(origin === 'boot') });
  }, [opens]);
}

/* 一条覆盖地址的元素：挂上按地址打开那一条，卸下去处不再是覆盖地址就收起舞台（正在放的视频照旧进小窗）。 */
function OverlayOpen() {
  const actions = useContext(ShellActionsContext);
  const latestActions = useRef(actions);
  useLayoutEffect(() => { latestActions.current = actions }, [actions]);
  useRouteOpen((boot) => {
    const location = peachHistory.navigation.location;
    const { pathname, search } = isPreferencePath(location.pathname) ? settingsBackgroundOf(location.state) || location : location;
    const target = overlayTarget(pathname, search);
    if (!actions || !target) return;
    clearOverlayBackground();
    if (!boot) adoptOverlayState(peachHistory.navigation.location.state);
    actions.openOverlay(target);
  }, true);
  useEffect(() => () => {
    queueMicrotask(() => {
      if (!isOverlayPath(peachHistory.navigation.location.pathname)) latestActions.current?.closeStage();
    });
  }, []);
  return null;
}

/** 覆盖组一条路径的元素：每次导航各挂一次，同一条目重放也能再打开。 */
export function OverlayMatch({ path }: { path: OverlayPath }): ReactNode {
  useOpenEpoch();
  const { location, seq, action } = peachHistory.navigation;
  const previous = isPreferencePath(location.pathname) ? settingsBackgroundOf(location.state) : null;
  const identity = (previous || location).pathname + (previous || location).search;
  const held = useRef({ identity, seq: previous?.seq ?? seq, navigationSeq: seq, settings: !!previous });
  if (previous) { held.current.settings = true; held.current.navigationSeq = seq }
  else if (held.current.settings && action === 'POP' && held.current.identity === identity) {
    held.current.settings = false; held.current.navigationSeq = seq;
  } else if (held.current.navigationSeq !== seq) held.current = { identity, seq, navigationSeq: seq, settings: false };
  return <OverlayOpen key={`${path} ${held.current.seq}`} />;
}

/* 页面组里没有背景的关注详情（深链、刷新与没记背景的条目）：不画列表，只让出 `#stats`；列表等关掉详情才打开。 */
function FollowGroundOpen() {
  const actions = useContext(ShellActionsContext);
  useRouteOpen(() => { actions?.follow?.ground() });
  return null;
}

/** 页面组 `/posts/:id` 的元素，按页面打开序号挂 key。 */
export function FollowDetailGround(): ReactNode {
  return <FollowGroundOpen key={useOpenEpoch()} />;
}
