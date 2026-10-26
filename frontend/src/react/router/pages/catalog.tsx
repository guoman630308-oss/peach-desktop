/* 目录的路由元素（ADR-0031「目录由路由树按匹配打开」）：页面组匹配到 `CATALOG_PATHS`（首页、三个筛选态、回收站与
 * 垃圾文件）时挂上，按地址重建目录筛选（`@peach/shell` 的 `state`），再交壳收舞台以外的事：搜索框、侧栏与顶栏、
 * 顶部三层和取数（`ShellActions.catalog`）。
 *
 * 元素按页面挂 key：首页、三个筛选态与回收站画的是同一张网格，共用一个元素；垃圾文件是另一页。没有背景的作品与
 * 队列地址（深链、刷新）也挂这个元素。换筛选、后退前进与壳要求重开都不重挂，元素在每一轮变化之后的微任务里按下面的
 * 次序判断做哪一件：
 * - 详情压在上面：条目记了背景（页面组按背景匹配）就什么都不做，下面那一页一直画着；没有背景时补画一次目录网格
 *   （`ShellActions.catalog.fill`），详情下面不留一张永远在读的骨架。
 * - 壳要求按当前地址从头重开（`pageOpens` 加一：启动、深链详情关掉补画来处、从管理条进回收站）：整页打开。
 * - 领了新的页面打开序号（后退前进、不认领的跳转）：整页打开；后退前进从别处回到首页时作废顶部三层的缓存、重掷取样种子，
 *   在目录里换来换去沿用这一粒。地址上写了 `seed` 就用它。
 * - 壳认领写了地址（换筛选、换排序、换一批、回首页）：筛选已经由壳写进 `state`，只重取；关掉详情回到压着的那一份
 *   地址、筛选也没变时不重取。这一页要是还没开过（后退落到压在别处之上的详情，挂上时就被盖着），整页打开。
 * `pageOpens` 还是 0 时壳没开始路由，元素只挂着。挂上那一刻按那一次历史变化认没认领分别算作后两种；路由根晚于
 * 壳开始路由才装上时，挂上那一刻算第一种。
 *
 * `?state=ads` 落在首页上时不打开，改写成垃圾文件的地址（不加条目），由垃圾文件那一页的元素接着打开。卸载时去处
 * 不是目录、详情、沉浸或资料页，就收起 `#grid` 与首页新作行：这些页面离开目录也留着它们。判断、改写与收起
 * 都排进微任务：路由根在历史变化的同一次调用里同步提交，壳订阅历史早于路由根，它写标题与侧栏的那一轮排在前面。 */
import { useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { useLocation } from 'react-router';

import { appSettingsStore } from '@peach/appearance';
import { isOverlayPath, navigationBackground, overlayTarget, peachHistory, shellNavigate } from '@peach/history';
import { cleanTagFilter, isCatalogPath, newSeed, ROUTE_STATES } from '@peach/legacy/core';
import { pageOpens, state, subscribeShell, writeShell, type CatalogFilters } from '@peach/shell';

import { junkPath, junkRoute } from '../../../junk-queue';
import { sortFromAddress } from '../../../sort-preferences';
import { ShellActionsContext, useOpenEpoch } from '../router';
import type { CatalogShell, ShellActions } from '../shell-actions';
import { entityTarget } from './index-entity';
import { JavLibraryHeader } from '../../jav/jav-header';
import { CatalogFolders } from '../../catalog-folders/catalog-folders-page';

const readOpens = () => pageOpens;

/* 每一次历史变化认没认领、变化之前在哪一条路径上。模块一装载就听，排在路由根之前：路由根同步提交那一次读到的
   已经是这一次。`initialSeq` 是装载那一刻的序号，挂上时序号还是它，说明路由根是装上那一下挂的这一页。 */
const initialSeq = peachHistory.navigation.seq;
let epoch = peachHistory.navigation.openEpoch;
let here = peachHistory.navigation.location.pathname;
let last = { seq: initialSeq, claimed: true, from: here };
peachHistory.listen((navigation) => {
  last = { seq: navigation.seq, claimed: navigation.openEpoch === epoch, from: here };
  epoch = navigation.openEpoch;
  here = navigation.location.pathname;
});

/* 首页上的 `?state=ads` 改写成垃圾文件地址那一下：垃圾文件那一页的元素挂上时取走，整页打开。 */
let junkRedirect = false;

/** 元素上一次处理到哪儿：序号、页面打开序号、重开次数，那一刻有没有详情压着、页面组画的是哪一份地址与哪一套筛选，
 *  这一页开过没有。 */
interface Seen {
  seq: number;
  epoch: number;
  opens: number;
  covered: boolean;
  address: string;
  filters: string;
  opened: boolean;
}

function seen(opened: boolean): Seen {
  const { seq, openEpoch, location } = peachHistory.navigation;
  const covered = isOverlayPath(location.pathname);
  const shown = navigationBackground() ?? location;
  const filters = state ?? {};
  return {
    seq, epoch: openEpoch, opens: pageOpens, covered, address: `${shown.pathname}${shown.search}`,
    filters: JSON.stringify(filters, Object.keys(filters).sort()), opened,
  };
}

/** 按地址重建的目录筛选，同壳启动时那一份的写法；回收站只钉死状态、清掉别的筛选。产地与缩略图不随地址重读。 */
function filtersAt(pathname: string, search: string, entering: boolean, shell: CatalogShell): CatalogFilters {
  const previous: CatalogFilters = { ...state };
  if (pathname === '/trash') return { ...previous, creator: '', studio: '', tag: '', orient: '', state: 'trash', q: '' };
  const params = new URLSearchParams(search);
  const { defaultSort, defaultSortDirection } = appSettingsStore().value;
  return {
    ...previous,
    loc: params.get('loc') ?? shell.defaultLoc(),
    creator: params.get('creator') || '', studio: params.get('studio') || '',
    owner: params.get('owner') === 'none' ? 'none' : '',
    tag: cleanTagFilter(params.get('tag')), tag_match: params.get('tag_match') === 'any' ? 'any' : 'all',
    len: params.get('len') || '', dur_min: params.get('dur_min') || '', dur_max: params.get('dur_max') || '',
    orient: params.get('orient') || '',
    state: ROUTE_STATES[pathname] || params.get('state') || '',
    ...sortFromAddress(params.get('sort'), params.get('dir'), defaultSort, defaultSortDirection),
    seed: params.get('seed') || (entering ? newSeed() : previous.seed || newSeed()),
    q: params.get('q') || '', jav: params.get('jav') || '',
    performer: params.get('performer') || '', series: params.get('series') || '',
    browse: params.get('browse') === 'folders' || params.get('folder') ? 'folders' : '',
    folder: params.get('folder') || '', folder_recursive: params.get('folder_recursive') === '0' ? '0' : '',
  };
}

/** 判断这一轮做哪一件，做掉；`mark` 换成这一刻。 */
function settle(mark: Seen, actions: ShellActions): void {
  const was = { ...mark };
  const now = seen(was.opened);
  Object.assign(mark, now);
  const shell = actions.catalog;
  if (!shell || now.opens === 0) return;
  if (now.covered) {
    if (!navigationBackground()) shell.fill();
    return;
  }
  let entering = false;
  if (now.opens === was.opens) {
    if (now.epoch === was.epoch && was.opened) {
      if (now.seq === was.seq || (was.covered && now.address === was.address && now.filters === was.filters)) return;
      shell.load();
      return;
    }
    if (now.epoch !== was.epoch) entering = peachHistory.navigation.action === 'POP' && last.from !== '/';
  }
  const { pathname, search } = peachHistory.navigation.location;
  if (pathname === '/' && new URLSearchParams(search).get('state') === 'ads') {
    const { kind, view } = junkRoute(search);
    junkRedirect = true;
    shellNavigate(junkPath(kind, view), { replace: true });
    return;
  }
  const retitle = junkRedirect;
  junkRedirect = false;
  entering &&= pathname === '/';
  mark.opened = true;
  writeShell({ state: filtersAt(pathname, search, entering, shell) });
  shell.open(pathname, { entering, retitle });
}

/* 去处还画着目录的东西就不收：目录几页之间换页由壳接着画；详情与沉浸压在上面；资料页铺开时只藏起它们。 */
function keepsCatalog(pathname: string): boolean {
  return isCatalogPath(pathname) || pathname === '/trash' || isOverlayPath(pathname) || pathname === '/immerse'
    || !!entityTarget(pathname);
}

function CatalogOpen() {
  const actions = useContext(ShellActionsContext);
  const openEpoch = useOpenEpoch();
  const opens = useSyncExternalStore(subscribeShell, readOpens);
  const location = useLocation();
  const mark = useRef<Seen | null>(null);
  const live = useRef({ mounted: true, queued: false });
  useEffect(() => {
    const own = live.current;
    own.mounted = true;
    const now = seen(!isOverlayPath(peachHistory.navigation.location.pathname));
    /* 挂上这一刻就记下：之后壳在同一轮里要求重开（`pageOpens` 加一），微任务里比得出来。 */
    if (!now.covered && now.opens > 0) {
      if (junkRedirect || last.seq === initialSeq) now.opens = -1;
      else if (!last.claimed) now.epoch = -1;
      else now.seq = -1;
    }
    mark.current = now;
    return () => {
      own.mounted = false;
      queueMicrotask(() => {
        if (actions?.catalog && !keepsCatalog(peachHistory.navigation.location.pathname)) actions.catalog.release();
      });
    };
    // 每次挂上只登记一次；之后的变化由下面那一格排进微任务。
  }, []);
  useEffect(() => {
    const own = live.current;
    if (own.queued || !actions) return;
    own.queued = true;
    queueMicrotask(() => {
      own.queued = false;
      if (own.mounted && mark.current) settle(mark.current, actions);
    });
  }, [openEpoch, opens, location, actions]);
  return null;
}

/** 目录六条路径共用的元素：首页、三个筛选态与回收站一个 key，垃圾文件另一个；页面组按背景匹配时取背景那一页。
 *  覆盖地址的参数不是数字（`/assets/abc`）时什么都不挂：那不是一条详情，下面也不补画。 */
export function CatalogMatch() {
  const { pathname, search } = useLocation();
  useLayoutEffect(() => {
    if (pathname !== '/wall') return;
    document.body.setAttribute('data-wall-open', '');
    document.documentElement.removeAttribute('data-wall-entry');
    const chrome = Array.from(document.querySelectorAll<HTMLElement>('header.top,#drawer,#main,#catalogFilter,#count,#javLibraryHeader,#feedNew'));
    const inert = chrome.map(node => node.inert);
    chrome.forEach(node => { node.inert = true });
    return () => {
      document.body.removeAttribute('data-wall-open');
      chrome.forEach((node, index) => { node.inert = inert[index] || false });
    };
  }, [pathname]);
  const actions = useContext(ShellActionsContext);
  const host = document.getElementById('javLibraryHeader');
  const jav = isCatalogPath(pathname) && new URLSearchParams(search).get('jav') === '1';
  useLayoutEffect(() => {
    if (!host) return;
    host.hidden = !jav;
    return () => { host.hidden = true };
  }, [host, jav]);
  if (isOverlayPath(pathname) && !overlayTarget(pathname)) return null;
  return <>
    <CatalogOpen key={pathname === '/junk-files' ? '/junk-files' : '/'} />
    {isCatalogPath(pathname) ? <CatalogFolders /> : null}
    {jav && host && actions ? createPortal(<JavLibraryHeader navigate={actions.navigate}
      receipt={message => actions.receipt(message)} />, host) : null}
  </>;
}
