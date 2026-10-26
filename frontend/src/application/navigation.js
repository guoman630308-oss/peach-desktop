/* navigation 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { ROUTE_META, routeMetaOf } from './../history/route-meta';
import { $, isCatalogPath } from '../core';
import { clearOverlayBackground, holdOverlayBackground, isOverlayPath, overlayState } from './../history/overlay';
import { openTok } from './media.js';
import { immerseStartId } from './preferences.js';
import { enterFollow } from './follow.js';
import { setSelectMode } from './selection.js';
import { followManageEntry, indexPath } from './index.js';
import { sidebarApi } from '../react/application-residents';
import { layoutState, navOn, paintListTitle, syncHeaderActions } from './layout.js';
import { clearHomeFeed, isFeedNewPath } from './feed.js';
import { releaseManagedRoute } from './../history/managed';
import { catalogState, clearCatalogGrid } from './catalog.js';
import { peachHistory, shellNavigate } from './../history/index';
import { loadingState } from './loading.js';
import { syncPostSetupTutorial } from './tutorial.js';
import { pageOpens, writeShell } from './../shell/index';
export const navigationState = { routeMeta: undefined, routePathOf: undefined, catalogPage: undefined, managedPagePath: undefined, openRoutePath: undefined, surfaceEpoch: undefined, surfacePath: undefined, surfaceRequests: undefined, surfaceToken: undefined, surfaceCurrent: undefined, claimSurface: undefined, route: undefined, navigatePath: undefined, requestedOverlay: undefined, requestedQueue: undefined, queueOpenRequest: undefined, initialOverlayOpened: undefined };

export function paintNav(){
  const sidebar=sidebarApi();
  if(sidebar){sidebar.navChanged();return}
  $('#drawerScroll')?.querySelectorAll('[data-nav]')
    .forEach(b=>b.setAttribute('aria-pressed',String(navOn(b.dataset.nav))));
}

export function paintSidebar(patch={}){
  const surface=navigationState.surfacePath();
  if(surface!==layoutState.sidebarSurface){layoutState.sidebarSurface=surface;layoutState.sidebarProps={...layoutState.sidebarProps,content:null,latest:null}}
  layoutState.sidebarProps={...layoutState.sidebarProps,...patch};
  sidebarApi()?.render(layoutState.sidebarProps);
}

export function syncNavigation(){paintSidebar();paintNav();syncHeaderActions()}

export function cancelQueueRequest(){
  if(navigationState.queueOpenRequest)navigationState.queueOpenRequest.cancelled=true;
  navigationState.queueOpenRequest=null;navigationState.requestedQueue=null;
  writeShell({pendingQueueRoute:null});
}

export function requestOverlay(path,kind,open){
  cancelQueueRequest();
  holdOverlayBackground();
  navigationState.requestedOverlay={path,open};
  navigationState.route(path,false,overlayState(kind));
}

export function syncRouteChrome(){
  navigationState.surfaceEpoch++;
  catalogState.barsRequestSeq++;
  loadingState.syncPageTitle(location.href);
  paintSidebar();
  clearOverlayBackground();
  void syncPostSetupTutorial();
}

export function openRoutedPage(path){
  navigationState.route(path);writeShell({pageOpens:pageOpens+1});
}

export function installNavigation2() {
navigationState.routeMeta = path=>{const meta=routeMetaOf(path);return meta?{route:meta}:null};
navigationState.routePathOf = (key,value)=>Object.keys(ROUTE_META).find(path=>ROUTE_META[path][key]===value);
navigationState.catalogPage = path=>isCatalogPath(path)||path==='/trash';
navigationState.managedPagePath = path=>{
  const meta=routeMetaOf(path);
  return !!meta&&meta.reload!=='reopen'&&!navigationState.catalogPage(path)&&!isOverlayPath(path)&&path!=='/immerse';
};
navigationState.openRoutePath = path=>{
  if(path==='/immerse'){void openTok(immerseStartId());return}
  if(path==='/follow'){enterFollow();return}
  if(path==='/playlists'||navigationState.catalogPage(path)){openRoutedPage(path);return}
  if(!navigationState.managedPagePath(path)){
    setSelectMode(false,true);
    openRoutedPage(indexPath({kind:path.slice(1),q:'',scope:'local',view:'alphabet',category:'all'}));
    return;
  }
  navigationState.navigatePath(path==='/subscriptions'?followManageEntry():path);
};
}

export function installNavigation4() {
navigationState.surfaceEpoch = 0;
navigationState.surfacePath = ()=>decodeURIComponent(location.pathname);
navigationState.surfaceRequests = null;
navigationState.surfaceToken = path=>({epoch:navigationState.surfaceEpoch,path,signal:navigationState.surfaceRequests?.signal});
navigationState.surfaceCurrent = token=>token.epoch===navigationState.surfaceEpoch&&navigationState.surfacePath()===token.path;
navigationState.claimSurface = path=>{
  /* 那条横幅讲的是库里那趟后台任务的下场，跟当前看的是哪一份名单无关。跟着每次取数
     卸了再挂，换一条筛选就会让它塌一下再撑回来——实测那一下底下整块先往上跳 62px，
     二十来毫秒后落回原处，比它要说的那句话显眼得多。目录页之间它一直挂着，自己在轮询
     库那边的进度；离开目录页才收起，那些页面本来就不该有它。 */
  /* 首页那一行新作同样只属于目录页。管理区的入口不经过 `showHomeSurfaces`，离开目录页时
     在这里收起并清空，连同它的自动滚动一起停掉。 */
  if(!isFeedNewPath(path))clearHomeFeed();
  /* 管理区正文与索引页、资料页的容器每次换页都经过这里，所以收起也落在这里。由路由树画进 `#stats`
     与 `#index` 的那几页（`openManagedRoute`）自己管取数：不收掉，离开之后页面还活着，有轮询的照着
     原节律继续敲库。没画着页面的容器是空操作，逐页判断反而会漏掉新迁过来的那一页。详情舞台压在
     它们上面时不经过这里，页面留着。`#grid` 不在其中，容器逐个点名。 */
  releaseManagedRoute($('#stats'),$('#index'));
  /* 目录网格不跟着换页收：目录页与回收站之间它一直画着，换筛选只是换查询；去别的页面才由
     `clearCatalogGrid` 收掉，那些页面接着会往 `#grid` 里写自己的东西。 */
  if(!isCatalogPath(path)&&path!=='/trash')clearCatalogGrid();
  navigationState.surfaceRequests?.abort();
  navigationState.surfaceRequests=new AbortController();
  navigationState.surfaceEpoch++;return navigationState.surfaceToken(path)};
navigationState.route = (path,replace=false,state)=>{
  navigationState.surfaceEpoch++;
  catalogState.barsRequestSeq++;
  shellNavigate(path,{replace,state});loadingState.syncPageTitle(path);
  queueMicrotask(()=>{syncHeaderActions();paintListTitle();paintSidebar();void syncPostSetupTutorial()});
};
navigationState.navigatePath = (path,replace=false,state)=>{
  if(replace)peachHistory.replace(path,state);else peachHistory.push(path,state);
  queueMicrotask(()=>{syncHeaderActions();paintListTitle()});
};
navigationState.requestedOverlay = null;
navigationState.requestedQueue = null;
navigationState.queueOpenRequest = null;
navigationState.initialOverlayOpened = false;
}
