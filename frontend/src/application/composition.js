/* composition 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { loadingState, renderInitialSurfaceLoading } from './loading.js';
import { clearHomeFeed, installEntityShapeBootstrap, loadEntityShapes } from './feed.js';
import { buildManageBar, closeStats, enterManagementSurface, loadSyncedSettings, mountManageHeader, mountSidebar, openManage, openSettings, paintListTitle, scheduleStickySurfaces, showHomeSurfaces, showManagementBody, syncHeaderActions } from './layout.js';
import { disposeStage, mediaState, openItem, openJavMovie, openPlaylist, openQueue, openTok } from './media.js';
import { currentBarsContext, entityState, openEntity, revealForIsland, routeEntityPage, showEntityMissing } from './entity.js';
import { applyFilterStateInPlace, buildBars, catalogState, clearCatalogGrid, gridTaken, loadCatalog, onlineDefaultLoc, paintCatalogGrid, refreshFacetCounts, syncCatalogFilterScreen, toggleTag } from './catalog.js';
import { openTasteSignal, routeReview } from './playlists.js';
import { navigationState, syncNavigation, syncRouteChrome } from './navigation.js';
import { enterFollow, followState, openFollowDetail } from './follow.js';
import { receiptsState } from './receipts.js';
import { reopenPostSetupTutorial } from './tutorial.js';
import { runtimeConfigurable, selectMode, state, writeShell } from './../shell/index';
import { indexPath, indexState, onlineAuthorRingHtml, openFollowAuthorFromIndex, openFollowTagFromIndex, routeFollowManage, showIndexContainer, showIndexTags } from './index.js';
import { censorOn, loadSourceStatus, preferencesState, wireAllDrag } from './preferences.js';
import { mountBatchDock, setSelectMode } from './selection.js';
import { releaseHoverPreviews } from './../card-art/hover';
import { bootEntry, peachHistory } from './../history/index';
import { $ } from '../core';
import { dropBars } from './../catalog-bars';
import { isOverlayPath } from './../history/overlay';
import { loadRouter, settingsPanelApi } from '../react/application-residents';
import { applicationEffects } from './effects';
export const compositionState = { shellActions: undefined, routing: undefined };



export function installComposition24() {
const effects=applicationEffects;
installEntityShapeBootstrap();
loadingState.entityShapesReady=loadEntityShapes();
renderInitialSurfaceLoading();
mountSidebar();
compositionState.shellActions = {
  openSettings:section=>openSettings(section,true),
  closeSettings:()=>settingsPanelApi()?.close(false),
  openItem:id=>void openItem(id),
  openEntity:(kind,name)=>void openEntity(kind,name),
  /* 点一个内容标签是「回目录并按它筛选」：整页换成目录仍归壳，页面只说点了哪个键。 */
  openTag:key=>{closeStats();toggleTag(key)},
  openTasteSignal,
  navigate:path=>{
    const movie=/^\/works\/([A-Za-z0-9_-]{2,64})$/.exec(path);
    const item=/^\/assets\/(\d+)$/.exec(path);
    if(movie)void openJavMovie(movie[1]);else if(item)void openItem(+item[1]);else navigationState.navigatePath(path);
  },
  openManage,
  managePath:section=>navigationState.routePathOf('section',section)||'',
  openFollow:()=>enterFollow(),
  receipt:(message,options)=>receiptsState.actionReceipt(message,options),
  toast:(message,options)=>receiptsState.toast(message,options),
  failure:receiptsState.actionFailure,
  revealSource:revealForIsland,
  reopenTutorial:reopenPostSetupTutorial,
  requestConfigurationSection:section=>{writeShell({configurationRequestedSection:section})},
  routeReview,
  routeFollowManage,
  saveFollowPreference:patch=>{
    if(patch.pageSize!==undefined)preferencesState.appSettings.followPageSize=patch.pageSize;
    if(patch.layout!==undefined)preferencesState.appSettings.followLayout=patch.layout;
    preferencesState.saveSettings();
  },
  srcBadge:(location,cost)=>preferencesState.srcBadge(location,cost),
  /* 索引页：同一页换 search 由壳认领、不重挂；后退前进到另一份 search 时路由树的索引元素按地址重开。 */
  routeIndex:(next,{replace=false}={})=>navigationState.route(indexPath(next),replace),
  savePeopleLayout:({layout})=>{preferencesState.appSettings.peopleLayout=layout;preferencesState.saveSettings()},
  exitSelectMode:()=>setSelectMode(false,false),
  personAvatar:(item,entityKind,big)=>indexState.personAvatar(item,entityKind,big),
  authorAvatar:author=>onlineAuthorRingHtml(author),
  showIndexTags,
  openFollowAuthor:openFollowAuthorFromIndex,
  openFollowTag:openFollowTagFromIndex,
  openPlaylist:(id,resume)=>void openPlaylist(id,resume,true),
  canFlip:()=>!selectMode&&!censorOn()&&!window.__scrolling&&!catalogState.reduceMotion(),
  /* 页面元素挂上、开始取数之前报一次：收起上一页留下的面，再铺开这一页那一侧。 */
  surfaceChanged:(kind,path)=>{
    /* 索引页与资料页画进 `#index`。`showHomeSurfaces` 会清掉 `index-open`／`entity-open` 并恢复顶部横条，
       这两个类要在它之后加。资料页的语境由画着的那一页推（`currentBarsContext`），变量只记首页那一份。 */
    if(kind==='entity'){
      releaseHoverPreviews();writeShell({barsContext:{type:'home',filters:state}});showHomeSurfaces();
      document.body.classList.add('entity-open');syncCatalogFilterScreen();showIndexContainer();return;
    }
    if(kind==='index'){
      releaseHoverPreviews();document.body.classList.remove('entity-open');navigationState.claimSurface(path);showHomeSurfaces();
      document.body.classList.add('index-open');syncCatalogFilterScreen();showIndexContainer();return;
    }
    releaseHoverPreviews();navigationState.claimSurface(path);
    if(kind==='catalog'){showHomeSurfaces();return}
    enterManagementSurface();showManagementBody({manage:kind==='management'});
  },
  clearSearch:()=>catalogState.clearSearchField(),
  openImmerse:id=>{
    const start=mediaState.requestedImmerseId===undefined?id:mediaState.requestedImmerseId;
    mediaState.requestedImmerseId=undefined;void openTok(start,false);
  },
  openQueueRequest:()=>{
    const open=navigationState.requestedQueue;navigationState.requestedQueue=null;
    if(open)void open();
  },
  /* 覆盖地址要打开的那一条（作品详情、关注详情、四种队列），地址栏已经是它。 */
  openOverlay:target=>{
    const requested=navigationState.requestedOverlay;
    navigationState.requestedOverlay=null;
    if(peachHistory.navigation.claimed&&(!bootEntry()||navigationState.initialOverlayOpened)){
      if(requested?.path===location.pathname)void requested.open();
      return;
    }
    if(bootEntry())navigationState.initialOverlayOpened=true;
    if(target.kind==='item')void openItem(target.id,false);
    else if(target.kind==='jav')void openJavMovie(target.code,false);
    else if(target.kind==='follow')void openFollowDetail(target.id,false);
    else void openQueue(target.queue,target.key,target.item,false);
  },
  closeStage:()=>{if(mediaState.stageOpen())disposeStage(false)},
  grid:{helpers:catalogState.gridHelpers,actions:catalogState.gridActions},

  // 索引页与资料页的元素用。
  configurable:()=>!!runtimeConfigurable,
  /* 整页画上之后：索引页重读侧栏按下态、重排吸顶；资料页重拉侧栏与顶部横条。 */
  surfaceShown:kind=>{if(kind==='index'){syncNavigation();scheduleStickySurfaces()}else buildBars()},
  entity:{
    missing:kind=>{compositionState.shellActions.surfaceChanged('entity',location.pathname);showEntityMissing(kind)},
    /* 名单启动时就在取；深链直接落在资料页时它可能还在路上，稍等一下再画骨架，画出来就是最终的形状。等不到
       就先画，名单到了再补那两块。名单每进一页重取一遍，下一页用的就是服务端的现状。 */
    loading:(kind,name,current)=>entityState.application.loading(kind,name,current),
    props:(kind,name,search)=>entityState.application.addressProps(kind,name,search),
    /* 后退前进落在同一位的另一份筛选上：这一页还画着就只把地址上的新筛选推过去。 */
    refresh:(kind,name,search)=>{
      const filters=entityState.parseEntityFilters(search);if(kind==='creator')filters.creator='';
      const shown=currentBarsContext();
      if(shown.type!=='entity'||shown.kind!==kind||shown.name!==name)return false;
      if(!routeEntityPage(kind,name,filters,entityState.parseMediaView(search),{push:false}))return false;
      applyFilterStateInPlace(filters);refreshFacetCounts(currentBarsContext());return true;
    },
  },

  // 关注与播放列表用。
  follow:followState.followFeedShell,

  // 目录用。
  catalog:{
    /* 地址上没写来源时的缺省：本地加 115，离线的那一处摘掉。 */
    defaultLoc:()=>onlineDefaultLoc('local,115'),
    /* 整页打开：筛选已由目录元素照地址写进 `state`。`retitle` 是首页 `?state=ads` 改写成垃圾文件地址的那一下：
       改写认领了，标题、侧栏与教程状态在这里补；`entering` 是从别处回到首页，顶部三层的缓存作废。
       回收站清掉搜索框，别的页把地址上的搜索词摆回框里。 */
    open:(path,{entering=false,retitle=false}={})=>{
      if(retitle){syncRouteChrome();syncHeaderActions();paintListTitle()}
      if(entering)dropBars();
      if(path==='/trash'){catalogState.clearSearchField();showHomeSurfaces()}
      else{$('#q').value=state.q;catalogState.rememberSearchValue()}
      syncNavigation();buildBars();preferencesState.handOff(loadCatalog());
    },
    /* 壳认领写了地址、筛选已在 `state` 里：只重取。 */
    load:()=>{preferencesState.handOff(loadCatalog())},
    /* 没有背景的作品与队列地址（深链、刷新）：详情下面那份列表一次请求都没发过，补发一次，不留一张写着在读、
       其实没有请求在跑的骨架。走 `paintCatalogGrid` 直接挂网格：`loadCatalog` 开头就 `disposeStage()`，会把这一屏
       详情一起收掉。网格已经画上（哪怕还在取第一页）就不再补发；不是启动那一条时只接着那张骨架。 */
    fill:()=>{
      if(gridTaken())return;
      if(!bootEntry()&&!$('#grid').querySelector('.catalog-skeleton'))return;
      const count=$('#count');count.removeAttribute('aria-busy');count.removeAttribute('aria-label');
      void paintCatalogGrid(navigationState.surfaceToken(navigationState.surfacePath()));
    },
    /* 离开目录：收起网格与首页新作行。 */
    release:()=>{clearCatalogGrid();clearHomeFeed()},
  },
};
compositionState.routing = false;
applicationEffects.own(peachHistory.listen(navigation=>{
  /* 取齐前地址仍在源页：任何非覆盖历史变化（包括同地址 POP）都撤回这一趟，晚到的包或响应不能再开舞台。 */
  if(navigationState.queueOpenRequest&&navigation.seq!==navigationState.queueOpenRequest.seq&&!isOverlayPath(navigation.location.pathname))disposeStage(false);
  // 历史通知内先同步页面代次，再由路由元素的微任务领取取数代次。
  if(compositionState.routing&&!navigation.claimed)syncRouteChrome();
}));
loadRouter(compositionState.shellActions).catch(error=>console.error('客户端导航装载失败',error));
mountManageHeader();
mountBatchDock();
buildManageBar();
Promise.all([loadSourceStatus(),loadSyncedSettings(),loadingState.entityShapesReady])
  .then(()=>effects.isActive&&loadingState.wantsDiscoveryBars()?buildBars():null)
  .then(()=>{
    if(!effects.isActive)return;
    syncNavigation();wireAllDrag();
    // 路由树里按匹配打开的页面等这一下才开始打开：壳的状态到这时才齐。
    compositionState.routing=true;writeShell({pageOpens:1});syncRouteChrome();
    scheduleStickySurfaces();
  }).catch(error=>{if(effects.isActive)console.error('应用启动失败',error)});
}
