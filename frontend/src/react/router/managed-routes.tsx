/* 由路由画的那几页、页面里的页面内组件与常驻组件：登记键到首屏取数与画法的对照表，七张；另有旧直达地址的
 * 改写表 `REDIRECT_ROUTES` 和沉浸模式的 `IMMERSE_ROUTES`，只挂元素。
 *
 * `MANAGED_ROUTES` 是管理区那几页（画进 `#stats`），键是精确路径，每条带页面组按匹配挂上的元素；
 * `BROWSE_ROUTES` 是同样画进 `#stats` 的播放列表页与关注页；`INDEX_ROUTES` 是索引页、`ENTITY_ROUTES` 是资料页（都画进 `#index`），资料页按模式登记
 * （`/performers/*`），种类与名字跟着打开走；`CATALOG_ROUTES` 是画进 `#grid` 的目录网格与垃圾队列，按页面
 * 分键；`SURFACE_ROUTES` 是页面里的页面内组件，`RESIDENT_ROUTES` 是不跟某一页走的常驻组件，都按名字登记，不是地址、
 * 不进 `<Routes>`。每次打开交进来的 `open`（管理区由页面元素算，其余由壳算）只带那一次才算得出的值（地址上的分类与页签、只读状态、引导标记、一次性预填、刷新代次），`#grid` 那两页
 * 与页面内组件是壳那一整份 props；回执与换到还归壳的那几屏走 `ShellActions`。
 * 管理区几页之间的跳转走 `go`：落在 `MANAGED_ROUTES` 上的交给 React Router 的 `navigate`，其余交壳。另外
 * 四张表的页面不走 `go`：它们在页内写地址一律由壳认领（`routeIndex`、资料页与关注页的 `actions.route`、
 * 目录的换筛选、垃圾队列的换分类），跨页也交壳，打开次数同壳自己打开。 */
import type { ReactElement } from 'react';
import { settingsPath, type ConfigurationPath } from '@peach/history';

import { javDisplayName, javTitleHtml } from '@peach/legacy/jav-title';
import { tagLabel } from '@peach/legacy/tags';

import { ActivityPage } from '../activity/activity-page';
import { prefetchTasks } from '../activity/tasks';
import { BatchDockSurface } from '../batch-dock/batch-dock-island';
import type { DataCleanupSection, LibraryProcessingProps } from '../bundle';
import { CatalogFilterPage } from '../catalog-filter/catalog-filter-page';
import { prefetchCatalogGrid } from '../catalog-grid/catalog-grid';
import { CatalogGridPage } from '../catalog-grid/catalog-grid-page';
import { prefetchDataCleanup } from '../data-cleanup/data-cleanup';
import { DataCleanupPage } from '../data-cleanup/data-cleanup-page';
import { prefetchEntityPage, type EntityPageProps } from '../entity-page/entity-page';
import { EntityPage } from '../entity-page/entity-page-view';
import { prefetchDuplicates } from '../duplicates/duplicates';
import { DuplicatesPage } from '../duplicates/duplicates-page';
import { prefetchDiagnostics } from '../diagnostics/diagnostics';
import { DiagnosticsPage } from '../diagnostics/diagnostics-page';
import { prefetchFeedNew } from '../feed-new/feed-new';
import { FeedNewPage } from '../feed-new/feed-new-page';
import { prefetchFollowFeed } from '../follow-feed/follow-feed';
import { FollowFeedPage } from '../follow-feed/follow-feed-page';
import { prefetchFollowManage } from '../follow-manage/follow-manage';
import { FollowManagePage } from '../follow-manage/follow-manage-page';
import { GlowPickerSurface } from '../glow-picker/glow-picker-island';
import { ImmerseSurface } from '../immerse/immerse-island';
import { prefetchIndex } from '../index/index-data';
import { IndexPage } from '../index/index-page';
import { JunkQueuePage } from '../junk-queue/junk-queue-page';
import { JavPage } from '../jav/jav-page';
import { prefetchJav } from '../jav/jav';
import { prefetchLibraryProcessing } from '../library-processing/library-processing';
import { LibraryProcessingCard } from '../library-processing/library-processing-card';
import { LibraryProcessingNotice } from '../library-processing/library-processing-notice';
import { ManageHeaderSurface } from '../manage-header/manage-header-island';
import { prefetchPlaylists } from '../playlists/playlists';
import { PlaylistsPage } from '../playlists/playlists-page';
import { prefetchQualityGoals } from '../quality-goals/quality-goals';
import { QualityGoalsPage } from '../quality-goals/quality-goals-page';
import { prefetchReview } from '../review/review';
import { ReviewPage } from '../review/review-page';
import { prefetchScraping } from '../scraping/scraping';
import { ScrapingPage } from '../scraping/scraping-page';
import { SearchPage } from '../search/search-page';
import { prefetchConfiguration } from '../settings/configuration';
import { ConfigurationPage } from '../settings/configuration-page';
import { SettingsPanelSurface } from '../settings-panel/settings-panel';
import { SidebarSurface } from '../sidebar/sidebar-island';
import { StageSurface } from '../stage/stage';
import { prefetchStats } from '../stats/stats';
import { StatsPage } from '../stats/stats-page';
import { DEFAULT_WINDOW, prefetchTaste } from '../taste/taste';
import { TastePage } from '../taste/taste-page';
import { CatalogMatch } from './pages/catalog';
import { FollowMatch, PlaylistsMatch } from './pages/follow';
import { ImmerseMatch } from './pages/immerse';
import { EntityMatch, IndexMatch } from './pages/index-entity';
import { ManagedMatch, ResourceSyncRedirect } from './pages/managed';
import type {
  BrowseOpenProps, BrowseRoutePath, CatalogOpenProps, CatalogPagePath, EntityRoutePath, IndexOpenProps,
  IndexOpenPropsTable, IndexRoutePath, ManagedOpenProps, ManagedPath, ResidentName, ResidentOpenProps, ShellActions,
  SurfaceName, SurfaceOpenProps,
} from './shell-actions';

interface ManagedRoute<P> {
  prefetch(open: P, signal: AbortSignal): Promise<void>;
  page(open: P, actions: ShellActions, go: (path: string) => void): ReactElement;
  /** 页面组里这条路由的元素：按地址打开这一页（`pages/`）。没有的那几页仍由壳打开。 */
  element?: ReactElement;
}

type ManagedRouteTable = { [Path in ManagedPath]: ManagedRoute<ManagedOpenProps[Path]> };
type BrowseRouteTable = { [Path in BrowseRoutePath]: ManagedRoute<BrowseOpenProps[Path]> };
type IndexRouteTable = { [Path in IndexRoutePath]: ManagedRoute<IndexOpenPropsTable[Path]> };
type EntityRouteTable = { [Path in EntityRoutePath]: ManagedRoute<EntityPageProps> };
type CatalogRouteTable = { [Path in CatalogPagePath]: ManagedRoute<CatalogOpenProps[Path]> };
type SurfaceRouteTable = { [Name in SurfaceName]: ManagedRoute<SurfaceOpenProps[Name]> };
type ResidentRouteTable = { [Name in ResidentName]: ManagedRoute<ResidentOpenProps[Name]> };
/** 路由树画的全部页面与面：管理区那几页、播放列表页与关注页、索引页与资料页、目录网格，加上页面内组件与常驻组件的名字。 */
export type RoutedPath =
  ManagedPath | BrowseRoutePath | IndexRoutePath | EntityRoutePath | CatalogPagePath | SurfaceName | ResidentName;

/* 数据管理页读数卡的去处里，只有重复文件不按管理区身份找：它报的是数据管理的身份。 */
function openCleanupSection(section: DataCleanupSection, actions: ShellActions, go: (path: string) => void) {
  if (section === 'duplicates') { go('/duplicates'); return }
  const path = actions.managePath(section);
  if (isManagedPath(path)) go(path);
  else actions.openManage(section);
}

/* 第一帧在宿主放进 `#stats` 的同一个任务里同步画完（`router.tsx` 的 `ManagedSurface`）：壳画完这一页
 * 紧接着就读它画出来的结构（配置页按地址里的 `#peachProxy` 滚过去，那一刻选中的页签已经是「网络与访问」）。 */
const configurationRoute = (path: ConfigurationPath): ManagedRoute<ManagedOpenProps['/settings/library']> => ({
  element: <ManagedMatch path={path} />,
  prefetch: async (_open, signal) => {
    await Promise.all([prefetchConfiguration(signal), ...(path === '/settings/diagnostics' ? [prefetchDiagnostics(signal)] : [])]);
  },
  page: (open, actions, go) => <ConfigurationPage receipt={message => actions.receipt(message)}
    reopenTutorial={actions.reopenTutorial} section={path === '/settings/diagnostics' ? '系统诊断' : open.section ?? ''}
    diagnostics={<DiagnosticsPage navigate={go} openItem={actions.openItem} receipt={message => actions.receipt(message)}
      configure={section => { actions.requestConfigurationSection(section); go(settingsPath(section)) }} />} />,
});

export const MANAGED_ROUTES: ManagedRouteTable = {
  '/jav': {
    element: <ManagedMatch path="/jav" />,
    prefetch: (open, signal) => prefetchJav(open.tab, signal),
    page: (open, actions) => <JavPage {...open} navigate={actions.navigate} receipt={message => actions.receipt(message)} />,
  },
  '/stats': {
    element: <ManagedMatch path="/stats" />,
    prefetch: (_open, signal) => prefetchStats(signal),
    /* 点一个内容标签是「回目录并按它筛选」，整页换成目录仍归壳。 */
    page: (open, actions, go) => (
      <StatsPage tagLabel={tagLabel} onTag={actions.openTag} configurable={open.configurable}
        openMediaSettings={() => { actions.requestConfigurationSection('媒体库与入库'); go('/settings/library') }} />
    ),
  },
  '/taste': {
    element: <ManagedMatch path="/taste" />,
    /* 首屏取的是「全部时间」那一份：分析范围是组件状态，每次进这一页都从它开始。 */
    prefetch: (_open, signal) => prefetchTaste(DEFAULT_WINDOW, signal),
    /* 总结里的下一步动作按路径走（`taste_history.py` 给的），路由树里没有的交壳的路由表。 */
    page: (open, actions, go) => (
      <TastePage onSignal={actions.openTasteSignal} navigate={go} toast={(message) => actions.receipt(message)}
        onboarding={open.onboarding} />
    ),
  },
  /* ADR-0018 的确定项已由扫描与资料处理任务落库；复核页只读取剩下的判断题。 */
  '/review': {
    element: <ManagedMatch path="/review" />,
    prefetch: (_open, signal) => prefetchReview(signal),
    page: (open, actions) => (
      <ReviewPage {...open} route={actions.routeReview} openItem={actions.openItem} openEntity={actions.openEntity}
        revealSource={actions.revealSource} toast={(message) => actions.receipt(message)} />
    ),
  },
  /* 首屏等顶上那排读数里自己的几份、两张后台任务卡与整理卡；复核、高清版与链接各读各的。 */
  '/data-cleanup': {
    element: <ManagedMatch path="/data-cleanup" />,
    prefetch: (_open, signal) => prefetchDataCleanup(signal),
    page: (_open, actions, go) => (
      <DataCleanupPage failure={actions.failure} open={(section) => openCleanupSection(section, actions, go)}
        toast={(message, { warning = false } = {}) => (
          warning ? actions.toast({ text: message }, { sound: 'warning' }) : actions.receipt(message))} />
    ),
  },
  '/duplicates': {
    element: <ManagedMatch path="/duplicates" />,
    prefetch: (_open, signal) => prefetchDuplicates(signal),
    page: (_open, actions) => (
      <DuplicatesPage openItem={actions.openItem} failure={actions.failure}
        toast={(message, { undo } = {}) => actions.receipt(message, undo ? { undo } : {})} />
    ),
  },
  '/quality-goals': {
    element: <ManagedMatch path="/quality-goals" />,
    prefetch: (_open, signal) => prefetchQualityGoals(signal),
    page: (_open, actions) => (
      <QualityGoalsPage openItem={actions.openItem} javTitleHtml={javTitleHtml} javDisplayName={javDisplayName}
        toast={(message) => actions.receipt(message)} srcBadge={actions.srcBadge} />
    ),
  },
  '/sources': {
    element: <ManagedMatch path="/sources" />,
    prefetch: (_open, signal) => prefetchScraping(signal),
    page: (_open, actions) => <ScrapingPage toast={(message) => actions.toast(message)} />,
  },
  '/settings/library': configurationRoute('/settings/library'),
  '/settings/subscriptions': configurationRoute('/settings/subscriptions'),
  '/settings/downloads': configurationRoute('/settings/downloads'),
  '/settings/network': configurationRoute('/settings/network'),
  '/settings/maintenance': configurationRoute('/settings/maintenance'),
  '/settings/diagnostics': configurationRoute('/settings/diagnostics'),
  '/tasks': {
    element: <ManagedMatch path="/tasks" />,
    prefetch: (_open, signal) => prefetchTasks(signal),
    page: () => <ActivityPage />,
  },
  /* 首屏只取来源清单与凭据状态，地址栏指着「订阅源」时连它一起取。检查更新与查找那两趟
     后台任务的快照不在首屏里：它们常年躺着上一趟的回执，等它们只会让首屏多一个往返。 */
  '/subscriptions': {
    element: <ManagedMatch path="/subscriptions" />,
    prefetch: (open, signal) => prefetchFollowManage(signal, open.tab),
    page: (open, actions) => (
      <FollowManagePage {...open} route={actions.routeFollowManage} savePreference={actions.saveFollowPreference}
        toast={(message) => actions.receipt(message)} openFollow={actions.openFollow}
        openServiceConfiguration={() => actions.navigate('/settings/subscriptions')} />
    ),
  },
};

/** 管理区那几页之间的跳转判据：只认 `MANAGED_ROUTES` 的精确路径。索引页不算：它们换 search 要由壳
 *  认领，跨进来也是壳写地址再自己打开。 */
export const isManagedPath = (path: string): path is ManagedPath => Object.hasOwn(MANAGED_ROUTES, path);

/** 旧直达地址：页面组按匹配挂上元素，元素把地址改写到别的页面上，自己不画。 */
export const REDIRECT_ROUTES: Readonly<Record<string, { element: ReactElement }>> = {
  '/resource-sync': { element: <ResourceSyncRedirect /> },
};

/** 沉浸模式：页面组按匹配挂上元素（`pages/immerse.tsx`），元素交壳打开常驻组件 `immerse`，自己不画。 */
export const IMMERSE_ROUTES: Readonly<Record<string, { element: ReactElement }>> = {
  '/immerse': { element: <ImmerseMatch /> },
};

/* 两页都由元素按匹配打开（`pages/follow.tsx`）。播放列表页：首屏每次都向服务端重取（首页刚存的 Mix 进来就要
   看得到）。停在这一页时壳要求重读，元素经 `updateManagedRoute` 推一个新的 `revision`，页面只重取、不重挂。点开
   一份进舞台、翻页门槛与回执都归壳。
   关注页：首屏是列表第一页与凭据两趟并行，挂上就是最终样子；之后换筛选、换一批、选择模式与照片版式都由
   壳经 `updateManagedRoute` 推进来，页面按新键只重取列表、不重挂。 */
export const BROWSE_ROUTES: BrowseRouteTable = {
  '/playlists': {
    element: <PlaylistsMatch />,
    prefetch: (_open, signal) => prefetchPlaylists(signal),
    page: (open, actions) => (
      <PlaylistsPage revision={open.revision} openPlaylist={actions.openPlaylist} openEntity={actions.openEntity}
        canFlip={actions.canFlip} toast={(message, { undo } = {}) => actions.receipt(message, undo ? { undo } : {})} />
    ),
  },
  '/follow': {
    element: <FollowMatch />,
    prefetch: (open, signal) => prefetchFollowFeed(open, signal),
    page: (open) => <FollowFeedPage {...open} />,
  },
};
const isBrowsePath = (path: string): path is BrowseRoutePath => Object.hasOwn(BROWSE_ROUTES, path);

/* 索引页：地址栏上的四项由元素从地址读出、跟着这一次打开交进来；页内换档经 `routeIndex` 由壳写地址并认领，
   不重挂（后退前进到同一页的另一份 search 时领新的页面打开序号，元素按地址重开一次）。首屏只取第一页。 */
const indexRoute: ManagedRoute<IndexOpenProps> = {
  element: <IndexMatch />,
  prefetch: (open, signal) => prefetchIndex(open, signal),
  page: (open, actions) => (
    <IndexPage {...open} route={actions.routeIndex} savePreference={actions.savePeopleLayout}
      exitSelectMode={actions.exitSelectMode} personAvatar={actions.personAvatar} authorAvatar={actions.authorAvatar}
      tagLabel={tagLabel} openEntity={actions.openEntity} showTags={actions.showIndexTags}
      openFollowAuthor={actions.openFollowAuthor} openFollowTag={actions.openFollowTag} />
  ),
};
export const INDEX_ROUTES: IndexRouteTable = {
  '/performers': indexRoute, '/creators': indexRoute, '/studios': indexRoute, '/agencies': indexRoute, '/tags': indexRoute,
};

/* 资料页：资料卡、筛选浮层、新作那一行与正文是同一页的四块。框架由元素排好（`openManagedRoute` 的
   `place`），页面画进资料卡那一格，再经 portal 画进另外三块。首屏把资料（连同新作与头几张封面）、
   作品第一页与照片取齐再画；换头像的候选不在首屏里，资料页每进一次就打一遍图库的话，多数时候没人
   点开它。壳交进来的筛选、视图与展示设置之后经 `updateManagedRoute` 推进来，页面按新键重取、不重挂。卡片与
   遗留层拼的 HTML（头像、新作那一行、源文件键）都是壳的那一份，跟着打开走。 */
const entityRoute: ManagedRoute<EntityPageProps> = {
  element: <EntityMatch />,
  prefetch: (open) => prefetchEntityPage(open),
  page: (open) => <EntityPage {...open} />,
};
export const ENTITY_ROUTES: EntityRouteTable = {
  '/performers/*': entityRoute, '/studios/*': entityRoute, '/creators/*': entityRoute, '/series/*': entityRoute,
  '/agencies/*': entityRoute, '/tags/*': entityRoute,
};

/* 画进 `#grid` 的两页。目录网格：首页、四个筛选态里除垃圾文件外的三个，和回收站，画的都是这一张网格，
   筛选是壳那一份 `state`；垃圾队列：垃圾文件那一屏，分类与视图只从地址读。表按页面分键：壳用 `/` 打开
   网格、用 `/junk-files` 打开队列，槽里记的路径说的是画着哪一页、不是地址。按地址分键会在三处出错：网格在
   `/trash` 打开之后去 `/` 是就地推，槽里的路径不跟着变；`?state=ads` 落在 `/` 上，画的是垃圾队列；冷启动
   落在详情地址上时，网格在 `/assets/:id` 底下补画。
   网格首屏取第一页再画；队列不在打开前等，画上就铺自己那份骨架（`junk-queue-page.tsx` 开头）。之后换
   筛选或分类、换版式、选择模式与刷新代次都由壳经 `updateManagedRoute` 推进来，页面按新键重取、自己铺
   骨架，不重挂。 */
export const CATALOG_ROUTES: CatalogRouteTable = {
  '/': {
    element: <CatalogMatch />,
    prefetch: (open, signal) => prefetchCatalogGrid(open, signal),
    page: (open) => <CatalogGridPage {...open} />,
  },
  '/junk-files': {
    element: <CatalogMatch />,
    prefetch: async () => {},
    page: (open) => <JunkQueuePage {...open} />,
  },
};
const isCatalogPage = (path: string): path is CatalogPagePath => Object.hasOwn(CATALOG_ROUTES, path);

/* 扫描与采集提供数据管理卡片和紧凑任务面。两边读同一个
 * `queryKey`，所以同时挂着时它们看的是同一份快照，Query 也只发一份轮询。 */
const LibraryProcessing = (props: LibraryProcessingProps) => (
  props.mode === 'notice' ? <LibraryProcessingNotice {...props} /> : <LibraryProcessingCard {...props} />
);

/* 页面里的页面内组件，按名字登记：壳每次打开交进来整份 props，之后经 `updateManagedRoute` 推补丁，页面就地
   重渲染。首页筛选条与搜索下拉不在打开前等数据（筛选条的读数与头像由壳推进来，搜索下拉的记录与推荐聚焦
   时才取）；首页新作行连头几张封面一起等，再一次换掉骨架；任务面先取一份进度快照。 */
export const SURFACE_ROUTES: SurfaceRouteTable = {
  'catalog-filter': {
    prefetch: async () => {},
    page: (open) => <CatalogFilterPage {...open} />,
  },
  'feed-new': {
    prefetch: (open) => prefetchFeedNew(open),
    page: (open) => <FeedNewPage {...open} />,
  },
  'library-processing': {
    prefetch: (_open, signal) => prefetchLibraryProcessing(signal),
    page: (open) => <LibraryProcessing {...open} />,
  },
  /* 输入框是壳的，下拉只接它的事件、画下拉栏里的内容。 */
  search: {
    prefetch: async () => {},
    page: (open) => <SearchPage {...open} />,
  },
};
const isSurfaceName = (key: string): key is SurfaceName => Object.hasOwn(SURFACE_ROUTES, key);

/* 常驻组件，按名字登记：壳启动时经 `openResidentSurface` 各开一次，一直算当前页，没有首屏取数，宿主就是壳的
   那个节点。从不收，只有错误边界会卸它的组件，宿主照旧留在文档里。打开不带 props：壳经命令式句柄推内容，
   组件订阅自己模块里的 store，句柄里的绘制同步做完。管理区页头与侧栏的宿主里先有壳的启动骨架，打开时由
   `place` 在画出首帧的同一个任务里清掉；沉浸与舞台的宿主不在壳的页面里，第一次打开时新建，由 `place` 在画出首帧
   （沉浸是藏着的外框，舞台是小窗节点）的同一个任务里挂到 body 末尾；设置面板的宿主在第一次打开时才有，由 `place`
   在同一个任务里放进 body 末尾。 */
export const RESIDENT_ROUTES: ResidentRouteTable = {
  'batch-dock': {
    prefetch: async () => {},
    page: () => <BatchDockSurface />,
  },
  'glow-picker': {
    prefetch: async () => {},
    page: () => <GlowPickerSurface />,
  },
  'manage-header': {
    prefetch: async () => {},
    page: () => <ManageHeaderSurface />,
  },
  immerse: {
    prefetch: async () => {},
    page: () => <ImmerseSurface />,
  },
  'settings-panel': {
    prefetch: async () => {},
    page: () => <SettingsPanelSurface />,
  },
  sidebar: {
    prefetch: async () => {},
    page: () => <SidebarSurface />,
  },
  stage: {
    prefetch: async () => {},
    page: () => <StageSurface />,
  },
};
const isResidentName = (key: string): key is ResidentName => Object.hasOwn(RESIDENT_ROUTES, key);
/** 画着 `#grid` 那两页时地址可能落在的路径：首页、三个筛选态、回收站与垃圾文件。只进 `<Routes>` 的声明，
 *  不当槽里的键。 */
export const CATALOG_PATHS = ['/', '/wall', '/unseen', '/watch-later', '/flagged', '/trash', '/junk-files'] as const;

/** `<Routes>` 声明的路径：前四张表的键，加上 `#grid` 那两页画着时地址可能落在的路径。 */
export const ROUTED_PATHS: readonly string[] = [
  ...Object.keys(MANAGED_ROUTES), ...Object.keys(BROWSE_ROUTES), ...Object.keys(INDEX_ROUTES),
  ...Object.keys(ENTITY_ROUTES), ...CATALOG_PATHS,
];
/** 路由树画得了这一页吗：首屏取数与画页按槽里记的登记键查，`#grid` 那两页只认页面键，页面内组件与常驻组件只认名字。 */
export const isRoutedPath = (path: string): path is RoutedPath => (
  isManagedPath(path) || isBrowsePath(path) || Object.hasOwn(INDEX_ROUTES, path) || Object.hasOwn(ENTITY_ROUTES, path)
  || isCatalogPage(path) || isSurfaceName(path) || isResidentName(path));

function routeOf(path: RoutedPath): ManagedRoute<object> {
  if (isManagedPath(path)) return MANAGED_ROUTES[path] as ManagedRoute<object>;
  if (isBrowsePath(path)) return BROWSE_ROUTES[path] as ManagedRoute<object>;
  if (isCatalogPage(path)) return CATALOG_ROUTES[path] as ManagedRoute<object>;
  if (isSurfaceName(path)) return SURFACE_ROUTES[path] as ManagedRoute<object>;
  if (isResidentName(path)) return RESIDENT_ROUTES[path] as ManagedRoute<object>;
  if (Object.hasOwn(INDEX_ROUTES, path)) return INDEX_ROUTES[path as IndexRoutePath] as ManagedRoute<object>;
  return ENTITY_ROUTES[path as EntityRoutePath] as ManagedRoute<object>;
}

/** 首屏取数（`openManagedRoute` 经 `connectManagedRoutes` 调它）。 */
export function prefetchManagedRoute(path: string, open: object, signal: AbortSignal): Promise<void> {
  if (!isRoutedPath(path)) return Promise.reject(new Error(`路由树里没有这一页：${path}`));
  return routeOf(path).prefetch(open, signal);
}

/** 画这一页。 */
export function managedPage(path: RoutedPath, open: object, actions: ShellActions, go: (path: string) => void) {
  return routeOf(path).page(open, actions, go);
}
