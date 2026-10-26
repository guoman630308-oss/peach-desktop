/* 索引五页与资料页的路由元素（ADR-0031「索引页与资料页同样由路由树按匹配打开」）：页面组匹配到
 * `INDEX_ROUTES` 或 `ENTITY_ROUTES` 时挂上，从地址读出这一页是谁、带什么参数，再按地址打开那一页。
 *
 * 元素自己什么都不画：页面经 `openManagedRoute` 登记进 `#index`，由 `ManagedSurface` 画。打开前先报
 * `surfaceChanged` 让壳收起别的面、铺开首页那一侧，再铺骨架，首屏取齐那一刻骨架与整页一起换掉。
 *
 * 什么时候打开：
 * - 索引元素按页面打开序号（`useOpenEpoch()`）挂 key：后退前进与不认领的跳转各领一个代次，就重挂、按地址重开；
 *   页内写地址（过滤词、页签）由壳认领，代次不变，不重挂。
 * - 资料页元素按种类与名字挂 key：换一位就重挂；同一位领了新代次（后退前进到另一份筛选）只把地址上的新筛选
 *   推给画着的那一页，那一页没画着才整页重开。
 * - 壳要求按当前地址从头重开（`@peach/shell` 的 `pageOpens` 加一：批量写回之后、点开的正是画着的那一位）时，
 *   两种元素都整页重开。`pageOpens` 还是 0 时壳没开始路由，元素只挂着。
 * - 挂上或领代次那一刻这一页被详情压着（页面组按背景匹配）：不重开。关掉详情时这一页要是没开过、下面也没画着
 *   （后退落到压在别处之上的详情才挂上），按地址整页打开；画着就不动。
 *
 * 打开排进微任务：路由根在历史变化的同一次调用里同步提交，打开时收起上一页与画首帧都要同步提交，在提交阶段里
 * 做会出 flushSync 告警。壳订阅历史早于路由根，它写标题与侧栏的那一轮排在前面。 */
import { useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { useLocation } from 'react-router';

import { appSettingsStore } from '@peach/appearance';
import { isOverlayPath, managedEntry, openManagedRoute, peachHistory, restoreHistoryPage, shellNavigate, updateManagedRoute } from '@peach/history';
import { ROUTE_ENTITIES, entityPath } from '@peach/legacy/core';
import { apiGet } from '../../../api';
import { queryClient } from '../../query';
import type { EntityPageData } from '../../entity-page/entity-page';
import { pageOpens, selectMode, subscribeShell, writeShell } from '@peach/shell';

import { indexParams, paintIndexSkeleton, peopleLayoutOf, type IndexPageKind } from '../../../index-skeleton';
import type { EntityPageProps } from '../../entity-page/entity-page';
import { ShellActionsContext, useOpenEpoch } from '../router';
import type { EntityPageShell, EntityRoutePath, ShellActions } from '../shell-actions';

const readOpens = () => pageOpens;
const readSelectMode = () => selectMode;
const indexHost = () => document.getElementById('index');

/** `usePageOpen` 的两个可选判断。`shown()`：这一页此刻画着没有，没开过的元素在详情关掉时据此决定要不要打开。
 *  `onCovered()`：挂上或领代次那一刻被详情压着时调一次。 */
export interface PageOpenOptions {
  readonly shown?: () => boolean;
  readonly onCovered?: () => void;
}

/** 这一格什么时候按地址打开。`run(fresh, live)`：`fresh` 为真是整页打开（挂上、壳要求重开），为假是同一个
 *  元素领了新代次；`live()` 在元素卸下或又开了一次之后回 false。关注页与播放列表页的元素也用它。 */
export function usePageOpen(
  epoch: number, run: (fresh: boolean, live: () => boolean) => void, options: PageOpenOptions = {},
): void {
  const opens = useSyncExternalStore(subscribeShell, readOpens);
  /* 每次导航都重渲染一次，详情压上来、撤下去都看得见。页面组给的是背景那一份地址，压没压着看真实地址。 */
  useLocation();
  const covered = isOverlayPath(peachHistory.navigation.location.pathname);
  const seen = useRef({ mounted: true, epoch: -1, opens: -1, run: 0, opened: false });
  const latest = useRef({ run, options });
  useLayoutEffect(() => { latest.current = { run, options } });
  useEffect(() => {
    const mark = seen.current;
    mark.mounted = true;
    return () => { mark.mounted = false };
  }, []);
  useEffect(() => {
    if (opens === 0) return;
    queueMicrotask(() => {
      const mark = seen.current;
      if (!mark.mounted) return;
      /* 读这一刻的 `pageOpens`：壳写地址挂上这一格、紧接着要求重开时，两边合成一次打开。 */
      const stale = mark.epoch !== epoch || mark.opens !== pageOpens;
      let fresh = mark.opens !== pageOpens;
      mark.epoch = epoch;
      mark.opens = pageOpens;
      if (isOverlayPath(peachHistory.navigation.location.pathname)) {
        if (stale) latest.current.options.onCovered?.();
        return;
      }
      if (!stale) {
        if (mark.opened || latest.current.options.shown?.()) {
          mark.opened = true;
          return;
        }
        fresh = true;
      }
      mark.opened = true;
      const mine = ++mark.run;
      latest.current.run(fresh, () => mark.mounted && mark.run === mine);
    });
  }, [epoch, opens, covered]);
}

/* ── 索引 ── */

async function openIndex(actions: ShellActions, kind: IndexPageKind, search: string, live: () => boolean) {
  const host = indexHost();
  if (!host) return;
  const path = `/${kind}` as const;
  actions.surfaceChanged('index', path);
  const params = indexParams(kind, search);
  const layout = peopleLayoutOf(appSettingsStore().value.peopleLayout);
  paintIndexSkeleton(host, params, layout);
  /* 页内换档、存版式、退出选择、头像与去处都在 `ShellActions` 里，跟着打开走的只有地址上那四项与此刻的版式、
     选择键和配置权限。 */
  const painted = await openManagedRoute(path,
    { ...params, layout, selectMode, configurable: !!actions.configurable?.() }, { container: host, isCurrent: live });
  if (painted && live()) {
    actions.surfaceShown?.('index', path);
    if (!restoreHistoryPage()) window.scrollTo({ top: 0, behavior: 'instant' });
  }
}

/* `#index` 里画着、露着的是这一页索引。 */
function indexShown(path: string): boolean {
  const host = indexHost();
  const entry = host ? managedEntry(host) : null;
  return !!host && !host.hidden && !!entry && entry.path === path && entry.host.isConnected;
}

function IndexOpen({ epoch }: { epoch: number }) {
  const actions = useContext(ShellActionsContext);
  const location = useLocation();
  const kind = location.pathname.split('/').filter(Boolean)[0] as IndexPageKind;
  usePageOpen(epoch, (_fresh, live) => {
    if (actions) void openIndex(actions, kind, location.search, live);
  }, { shown: () => indexShown(`/${kind}`) });
  return null;
}

/** 索引五页共用的元素。选择键归壳：开关一变就推给画着的标签页，页面关掉时随之清空所选。 */
export function IndexMatch() {
  const epoch = useOpenEpoch();
  const on = useSyncExternalStore(subscribeShell, readSelectMode);
  const last = useRef(on);
  useEffect(() => {
    if (last.current === on) return;
    last.current = on;
    if (window.location.pathname === '/tags') updateManagedRoute(indexHost(), { selectMode: on });
  }, [on]);
  return <IndexOpen key={epoch} epoch={epoch} />;
}

/* ── 资料 ── */

/** 地址上的这一位：先整段解码再按段切开（名字里的斜杠吃掉剩下全部段，空段不算），同壳的 `matchPath`。 */
export function entityTarget(pathname: string): { segment: string; kind: string; name: string } | null {
  let path: string;
  try { path = decodeURIComponent(pathname) } catch { return null }
  const [segment = '', ...rest] = path.split('/').filter(Boolean);
  const kind = Object.hasOwn(ROUTE_ENTITIES, segment) ? ROUTE_ENTITIES[segment] : undefined;
  const name = rest.join('/');
  return kind && name ? { segment, kind, name } : null;
}

/* 卡片的助手与动作就是目录那一份，名册一格的取图同索引页，照片墙多一样：灯箱里定位本地图片的源文件。按壳交进来的
   那一组记一份，每次打开交出去的是同一个对象。 */
const cards = new WeakMap<ShellActions, EntityPageProps['card']>();
function entityCard(actions: ShellActions): EntityPageProps['card'] {
  let card = cards.get(actions);
  if (!card) {
    card = {
      helpers: { ...actions.grid.helpers, personAvatar: actions.personAvatar },
      actions: { ...actions.grid.actions, revealSource: actions.revealSource },
    };
    cards.set(actions, card);
  }
  return card;
}

/* `#index` 里画着的是资料页。 */
function entityShown(): boolean {
  const host = indexHost();
  const entry = host ? managedEntry(host) : null;
  return !!entry && entry.path.endsWith('/*') && entry.host.isConnected;
}

async function openEntity(
  actions: ShellActions, shell: EntityPageShell, target: { segment: string; kind: string; name: string },
  pathname: string, search: string, live: () => boolean, resolvedEntity: EntityPageData,
) {
  const host = indexHost();
  if (!host) return;
  const { segment, kind, name } = target;
  actions.surfaceChanged('entity', pathname);
  await shell.loading(kind, name, live);
  if (!live()) return;
  writeShell({ entityJavLayout: false });
  /* 卡外面依次是交集条与玻璃浮层、新作和正文，顶到底一条线。四块宿主先在文档外排好，首屏取齐那一刻才换掉骨架
     （`place`）：换掉与画出整页落在同一个任务里。资料卡、浮层与正文各带一层 `.peach-react`（React 子树的样式
     范围）；新作那一行是遗留层的卡片，宿主不进这个范围。 */
  const frame = document.createElement('template');
  frame.innerHTML = `<div data-entity-hero><div class="peach-react"></div></div>
    <div data-entity-filter><div class="peach-react"></div></div>
    <section class="feednew" data-feed-new aria-label="未入库的新作" hidden></section>
    <div data-entity-body><div class="peach-react"></div></div>`;
  const parts = [...frame.content.childNodes];
  const hero = frame.content.querySelector('[data-entity-hero]')!;
  const hosts = {
    filter: frame.content.querySelector('[data-entity-filter]>.peach-react')!,
    feed: frame.content.querySelector<HTMLElement>('[data-feed-new]')!,
    body: frame.content.querySelector('[data-entity-body]>.peach-react')!,
  };
  const open = shell.props(kind, name, search);
  /* 这一页是不是 JAV 语境由页面按第一页作品推出来，写回壳的状态：壳的排序项、侧栏取数与卡片版式都读它。 */
  const props: EntityPageProps = {
    ...open, hosts, card: entityCard(actions), resolvedEntity,
    actions: { ...open.actions, javContext: (on) => { writeShell({ entityJavLayout: !!on }) } },
  };
  const painted = await openManagedRoute(`/${segment}/*` as EntityRoutePath, props, {
    container: host, isCurrent: live,
    place: (container) => { container.replaceChildren(...parts); return hero.firstElementChild as HTMLElement },
  });
  if (!painted || !live() || !entityShown()) return;
  actions.surfaceShown?.('entity', pathname);
  if (!restoreHistoryPage()) window.scrollTo({ top: 0, behavior: 'smooth' });
}

function EntityOpen({ target }: { target: { segment: string; kind: string; name: string } }) {
  const actions = useContext(ShellActionsContext);
  const epoch = useOpenEpoch();
  const location = useLocation();
  usePageOpen(epoch, (fresh, live) => {
    const shell = actions?.entity;
    if (!actions || !shell) return;
    void (async () => {
      const params = new URLSearchParams({ kind: target.kind });
      params.set(/^[0-9]+$/.test(target.name) ? 'id' : 'name', target.name);
      const entity = await queryClient.fetchQuery({ queryKey: ['entity-route', target.kind, target.name],
        queryFn: ({ signal }) => apiGet<EntityPageData>(`/api/entity?${params}`, signal), staleTime: 0 })
        .catch(() => null);
      if (!live()) return;
      if (entity?.redirect) {
        shellNavigate(entityPath(entity.redirect.kind, entity.redirect.name, entity.redirect.id) + location.search + location.hash,
          { replace: true, state: peachHistory.navigation.location.state });
        return;
      }
      if (!entity?.id || entity.error) { shell.missing(target.kind); return }
      const name = String(entity.canonical_name || target.name);
      const path = entityPath(target.kind, name, Number(entity.id));
      if (path !== location.pathname) {
        shellNavigate(path + location.search + location.hash, { replace: true, state: peachHistory.navigation.location.state });
        return;
      }
      if (!fresh && shell.refresh(target.kind, name, location.search)) return;
      void openEntity(actions, shell, { ...target, name }, location.pathname, location.search, live, entity);
    })();
  }, { shown: () => entityShown() && !indexHost()?.hidden });
  return null;
}

/** 资料页五个模式共用的元素：按种类与名字挂 key，同一位换筛选不重挂。 */
export function EntityMatch() {
  const target = entityTarget(useLocation().pathname);
  return target ? <EntityOpen key={JSON.stringify([target.kind, target.name])} target={target} /> : null;
}
