/* 客户端导航（ADR-0031「React Router 外壳阶段接管」）：React Router 的 Declarative 模式接管 history。
 * 管理区那几页、播放列表页与关注页、索引页与资料页、目录网格与垃圾队列，以及页面里的页面内组件（首页筛选条、
 * 首页新作行、顶栏搜索下拉）与常驻组件（后台任务、底部批量条、侧栏配色卡、管理区页头、沉浸模式、设置面板、
 * 侧栏、详情舞台）（`managed-routes.tsx`）由这棵树画。每一页、沉浸模式与详情覆盖都由路由按匹配打开
 * （`pages/` 下各元素），壳不接派发：它只订阅历史同步标题与侧栏，换页时写地址。
 *
 * 用底层的 `<Router>`，history 是 `@peach/history` 那一份：应用启动时就要写地址，`<BrowserRouter>`
 * 自己建的 history 只听 `popstate`，看不见壳 push 进去的条目。也不用 `unstable_HistoryRouter`：它的更新
 * 默认包在 `startTransition` 里，接连两次变化会并成一次渲染，后退前进就少开一次。
 *
 * 页面宿主是两组 `<Routes>` 的兄弟，从头活到尾：画着的页面跟着登记的那几条走（`@peach/history` 的
 * `openManagedRoute`，每个容器一条），详情舞台压在上面、地址换成 `/assets/:id` 时它留在原处。
 *
 * 路由分两组：页面组按条目自己记的背景（`usr.backgroundLocation`，启动那一条不读）匹配，详情压在哪一页上就还
 * 匹配那一页，背景页不重挂；覆盖组按真实地址匹配详情与队列那几条（`OVERLAY_PATHS`，`pages/overlay.tsx`）。页面组
 * 里管理区、关注、索引、资料、目录与沉浸各挂按地址打开那一页的元素（`pages/managed.tsx`、`pages/follow.tsx`、
 * `pages/index-entity.tsx`、`pages/catalog.tsx`、`pages/immerse.tsx`）；没有背景的覆盖地址也在页面组里挂一格：
 * 作品与队列补画目录网格，关注详情只让出 `#stats`。`path="*"` 是认不出的地址，什么都不打开。
 *
 * 一处渲染错误只带走抛错的那一面：每一面各套一层错误边界（`SurfaceBoundary`），根上不套，两组 `<Routes>` 不随
 * 某一面卸掉。错误经根的 `onCaughtError` 交给 `reportError`，每次一条。 */
import {
  Component, createContext, memo, useContext, useLayoutEffect, useMemo, useRef, useState, type ReactNode,
  type RefObject,
} from 'react';
import { createPortal, flushSync } from 'react-dom';
import type { RootOptions } from 'react-dom/client';
import { Route, Router, Routes, useLocation, useNavigate, type NavigateFunction } from 'react-router';

import {
  failManagedRoute, listenManagedEntry, managedEntries, navigationBackground, OVERLAY_PATHS, peachHistory,
  PREFERENCE_PATHS, isPreferencePath, settingsBackgroundOf, bootEntry,
  type ManagedEntry, type Navigation,
} from '@peach/history';

import { Providers } from '../providers';
import { LibraryProcessingDock } from '../library-processing/library-processing-dock';
import { SettingsMatch } from './pages/settings';
import {
  BROWSE_ROUTES, CATALOG_PATHS, CATALOG_ROUTES, ENTITY_ROUTES, IMMERSE_ROUTES, INDEX_ROUTES, MANAGED_ROUTES, REDIRECT_ROUTES,
  isManagedPath, isRoutedPath, managedPage,
} from './managed-routes';
import { FollowDetailGround, OverlayMatch, QueueRequests } from './pages/overlay';
import type { ShellActions } from './shell-actions';

/* 页面打开序号（`@peach/history` 的 `openEpoch`）：没人认领的历史变化各领一个，认领的写地址不领。地址没变的
 * `popstate`（同一条目重放）不会让 Router 的 location 变，代次照样变，按它挂 key 的元素照样重开。 */
const OpenEpoch = createContext(0);

/** 当前这一页是第几次打开：页面元素按它挂 key，重开就重挂，认领的写地址（页内 replace 写参数）不重挂。 */
export const useOpenEpoch = (): number => useContext(OpenEpoch);

/** 壳交进来的能力，管理区那几页经它回到壳。 */
export const ShellActionsContext = createContext<ShellActions | null>(null);

/** 路由根在历史变化的同一调用里同步提交（`flushSync`）：`shellNavigate` 与后退前进返回时，两组 `<Routes>`
 *  的匹配已经换成新地址，上一页的元素已经卸掉，壳接着往同一个容器里写骨架不会撕掉 React 还管着的节点。
 *  元素的打开不在这一次提交里跑，各自排进提交之后的微任务。 */
export function PeachRouter({ children }: { children: ReactNode }) {
  const [navigation, setNavigation] = useState<Navigation>(() => peachHistory.navigation);
  useLayoutEffect(() => {
    /* 从读初值到这里订阅之间有过变化的话，补上最新那一次。 */
    setNavigation(peachHistory.navigation);
    return peachHistory.listen((next) => { flushSync(() => setNavigation(next)) });
  }, []);
  return (
    <OpenEpoch.Provider value={navigation.openEpoch}>
      <Router location={navigation.location} navigationType={navigation.action} navigator={peachHistory}>
        {children}
      </Router>
    </OpenEpoch.Provider>
  );
}

/* 跟着地址重渲染的只有这一格：它把当前的 `navigate` 交给管理区宿主，宿主与页面不随导航重渲染。 */
function NavigateInto({ target }: { target: RefObject<NavigateFunction | null> }) {
  const navigate = useNavigate();
  useLayoutEffect(() => { target.current = navigate });
  return null;
}

/** 管理区页面里的跳转：落在管理区那几页上的交给 React Router，别的路径交 `actions.navigate`。两条路都不认领，
 * 各领一个页面打开序号，打开次数与后退前进相同。 */
export function managedGo(path: string, actions: ShellActions, navigate: NavigateFunction): void {
  const target = new URL(path, window.location.href);
  if (!isManagedPath(target.pathname)) { actions.navigate(path); return }
  void navigate(`${target.pathname}${target.search}${target.hash}`);
}

/** 页面宿主：画壳在各个容器里登记的那一页（`#stats`、`#index`、`#grid` 与各页面内组件的容器各一页，互不相收）。
 *
 * 换页与收起都由 `@peach/history` 同步通知，这里用 `flushSync` 当场画完：壳在同一个任务里刚换上宿主
 * （或刚撤掉它），晚一拍画就是一帧空白。每次打开领一个代次，页面按代次重挂，首屏读的是刚取回的缓存。
 * 就地更新（`updateManagedRoute`）只换那一页的 props、代次不变，照常排进下一次渲染：壳的开关可能就是从
 * 页面自己的 effect 里推过来的，那里不能 `flushSync`。 */
function ManagedSurface() {
  const actions = useContext(ShellActionsContext);
  const [entries, setEntries] = useState<readonly ManagedEntry[]>(managedEntries);
  const navigate = useRef<NavigateFunction | null>(null);
  useLayoutEffect(() => {
    setEntries(managedEntries());
    return listenManagedEntry((next, sync) => {
      if (sync) flushSync(() => setEntries(next));
      else setEntries(next);
    });
  }, []);
  const go = useMemo(() => (path: string) => {
    if (actions && navigate.current) managedGo(path, actions, navigate.current);
  }, [actions]);
  return (
    <>
      <NavigateInto target={navigate} />
      {actions ? entries.map((entry) => (
        <SurfaceBoundary key={entry.revision} entry={entry}>
          <ManagedPortal entry={entry} actions={actions} go={go} />
        </SurfaceBoundary>)) : null}
    </>
  );
}

/** 一面一层的错误边界。接住之后这一面画成空，并撤掉它的登记与宿主（`failManagedRoute`），壳下次打开时重开；
 *  按代次挂 key，重开就是一个新的边界实例。上报不在这里：根的 `onCaughtError` 已经报过一次。 */
class SurfaceBoundary extends Component<{ entry: ManagedEntry; children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(): void {
    failManagedRoute(this.props.entry.container, this.props.entry.revision);
  }

  override render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

const ManagedPortal = memo(function ManagedPortal(
  { entry, actions, go }: { entry: ManagedEntry; actions: ShellActions; go: (path: string) => void },
) {
  if (!isRoutedPath(entry.path)) return null;
  return createPortal(managedPage(entry.path, entry.props, actions, go), entry.host, String(entry.revision));
});

/* 页面组的 `location` 一直给（没有背景就给当前地址）：给与不给之间 `<Routes>` 会多包一层 `LocationContext`，
 * 来回切换就会把页面元素拆掉重挂。背景读的是当前条目（`navigationBackground`），启动那一条当作没有。
 * 两组按页面分段，段与段之间隔开几行。 */
function RouteGroups({ children }: { children?: ReactNode }) {
  const location = useLocation();
  // 同一条目重放时地址不变、`useLocation` 不触发重渲染，代次照样变：启动那一条重放之后要开始读背景。
  useOpenEpoch();
  const background = navigationBackground();
  const settingsBackground = isPreferencePath(location.pathname) && !bootEntry() ? settingsBackgroundOf(location.state) : null;
  return (
    <>
      <Routes location={background ?? location}>
        {/* ── 管理区 ── */}
        {Object.entries(MANAGED_ROUTES).map(([path, route]) => <Route key={path} path={path} element={route.element} />)}
        {/* 旧直达地址：元素把它改写成 `/data-cleanup#resource-sync`，由数据管理页的元素打开。 */}
        {Object.entries(REDIRECT_ROUTES).map(([path, route]) => <Route key={path} path={path} element={route.element} />)}



        {/* ── 关注 ── */}
        {Object.entries(BROWSE_ROUTES).map(([path, route]) => <Route key={path} path={path} element={route.element} />)}



        {/* ── 索引 ── */}
        {Object.entries(INDEX_ROUTES).map(([path, route]) => <Route key={path} path={path} element={route.element} />)}



        {/* ── 资料 ── */}
        {Object.entries(ENTITY_ROUTES).map(([path, route]) => <Route key={path} path={path} element={route.element} />)}



        {/* ── 目录 ── 六条路径挂同一个元素（`pages/catalog.tsx`），换路径不重挂，元素按页面自己分 key。 */}
        {CATALOG_PATHS.map((path) => <Route key={path} path={path} element={CATALOG_ROUTES['/'].element} />)}



        {/* ── 沉浸 ── */}
        {Object.entries(IMMERSE_ROUTES).map(([path, route]) => <Route key={path} path={path} element={route.element} />)}



        {/* ── 没有背景的覆盖地址 ── 作品与队列挂目录元素，补画网格；关注详情只让出 `#stats`。 */}
        {OVERLAY_PATHS.map((path) => (
          <Route key={path} path={path} element={path === '/works/:code' ? null
            : path === '/posts/:id' ? <FollowDetailGround /> : CATALOG_ROUTES['/'].element} />
        ))}
        {PREFERENCE_PATHS.map(path => <Route key={path} path={path} element={null} />)}



        {/* 认不出的地址。`children` 只给测试用。 */}
        <Route path="*" element={children} />
      </Routes>



      {/* ── 覆盖 ── */}
      <Routes location={settingsBackground ?? location}>
        {OVERLAY_PATHS.map((path) => <Route key={path} path={path} element={<OverlayMatch path={path} />} />)}
        <Route path="*" element={null} />
      </Routes>
      <Routes>
        {PREFERENCE_PATHS.map(path => <Route key={path} path={path} element={<SettingsMatch />} />)}
        <Route path="*" element={null} />
      </Routes>
    </>
  );
}

/** 整棵路由树。`children` 画在页面组 `path="*"` 的元素里面，只给测试用：跟着它挂一次就说明元素没被重挂。 */
export function RouterRoot({ children, actions = null, background, processing = false }:
  { children?: ReactNode; actions?: ShellActions | null; background?: ReactNode; processing?: boolean }) {
  return (
    <Providers>
      {background}
      <ShellActionsContext.Provider value={actions}>
        <PeachRouter>
          <ManagedSurface />
          <QueueRequests />
          {processing && actions ? <LibraryProcessingDock toast={actions.toast} /> : null}
          <RouteGroups>{children}</RouteGroups>
        </PeachRouter>
      </ShellActionsContext.Provider>
    </Providers>
  );
}

/** 路由树那棵根的选项。错误边界接住的错误交给全局 `reportError`，和没人接住时 React 的上报同一条路，
 *  控制台与 window 的 `error` 事件各见一次。运行时才取 `reportError`：测试环境里可能没有它。 */
export const ROUTER_ROOT_OPTIONS: RootOptions = {
  onCaughtError: (error) => { globalThis.reportError?.(error) },
};
