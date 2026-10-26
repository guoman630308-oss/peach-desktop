/* loading 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { followState } from './follow.js';
import { managementSkeletonHtml, pageSkeletonHtml, skeletonKeyOf } from './../management-placeholder';
import { photoSize } from './../appearance/layout';
import { $, ROUTE_ENTITIES } from '../core';
import { fitSkeleton, loadingDotsHtml, revealSkeleton } from '../ui-kit';
import { junkCountSkeletonHtml, junkRoute } from './../junk-queue';
import { state } from './../shell/index';
import { catalogCardRatio, catalogHeadProps, catalogSkeletonHtml, clearCatalogGrid, gridTaken, paintCatalogFilter, syncCatalogFilterScreen } from './catalog.js';
import { followListLayout, followManageParams, indexState, showIndexContainer } from './index.js';
import { applicationEffects } from './effects';
import { feedState, loadEntityShapes } from './feed.js';
import { navigationState, paintNav } from './navigation.js';
import { indexParams, paintIndexSkeleton } from './../index-skeleton';
import { showEntityLoading } from './entity.js';
import { routeMetaOf } from './../history/route-meta';
import { javTab } from '../react/jav/jav-navigation';
export const loadingState = { setGridCards: undefined, gridCards: undefined, managementPlaceholder: undefined, wantsDiscoveryBars: undefined, entityShapesReady: undefined, ENTITY_SHAPES_WAIT: undefined, syncPageTitle: undefined };

export function renderCatalogLoading(label='正在读取作品'){
  if(location.pathname==='/wall'){$('#loadSentinel').hidden=true;return}
  const count=$('#count');
  /* 垃圾文件那一屏的计数行是自己的：一块摘要面加一条分类切换，没有排序也没有换批。
     这一版是垃圾队列等数据时那一版的静态副本，队列画上就换掉它；骨架里的
     分类链接是真的 `<a href>`，React 包没到时点下去照常换页。目录与回收站的读数在筛选条里，
     这一行留空。 */
  const junk=decodeURIComponent(location.pathname)==='/junk-files';
  count.classList.toggle('manage-static',junk);
  count.classList.toggle('junkcount',junk);
  if(junk){
    count.setAttribute('aria-busy','true');count.setAttribute('aria-label',label);
    count.innerHTML=junkCountSkeletonHtml(junkRoute(location.search));
    fitSkeleton(count);
  }else{
    count.removeAttribute('aria-busy');count.removeAttribute('aria-label');count.textContent='';
    if(state)paintCatalogFilter({count:null,...catalogHeadProps()});
  }
  /* 骨架一铺上去就得把底部那颗 Loading Dots 收掉。骨架说的是「等下会出现几张什么
     形状的卡」，dots 说的是「上面已经有内容，还在往下接」；两段同时在场时一屏里
     铺着两种等待动画，而实际只有一次请求在跑。哨兵的可见性由数据落地后的
     `has_more` 重新决定，所以这里只管收，不必记住原值。 */
  $('#loadSentinel').hidden=true;
  /* 网格已经画着（或首屏在途）时骨架归它自己铺：`#grid` 是它的容器，壳往里写会把页面冲掉。 */
  if(gridTaken())return;
  const grid=$('#grid'),placeholder=catalogSkeletonHtml(label);
  const skeleton=grid.querySelector('.catalog-skeleton');
  if(skeleton?.dataset.skeleton===skeletonKeyOf(placeholder)
    &&Number(skeleton.querySelector('[style]')?.style.getPropertyValue('--skeleton-card-ratio'))===catalogCardRatio())return;
  if(skeleton)grid.innerHTML=`<div class="grid">${placeholder}</div>`;
  else loadingState.setGridCards(placeholder);
  fitSkeleton($('#grid'));
}

export function hideDiscoveryBars(){$('#catalogFilter').style.display='none';syncCatalogFilterScreen()}

export function waitEntityShapes(){
  const deadline=new Promise(resolve=>{
    let left=loadingState.ENTITY_SHAPES_WAIT,last=performance.now();
    const tick=()=>{
      const now=performance.now();
      left-=Math.min(now-last,50);last=now;
      if(left>0)applicationEffects.delay(tick,Math.min(left,50));else resolve();
    };
    applicationEffects.delay(tick,50);
  });
  return Promise.race([loadingState.entityShapesReady||=loadEntityShapes(),deadline]);
}

export function renderInitialSurfaceLoading(){
  const path=decodeURIComponent(location.pathname);
  /* 骨架画的就是这个表面，所以先把 `data-surface` 写上：深链冷启动时壳开始路由排在这一步后面，
     等它写的话骨架会先按默认版式铺一遍，数据到货再跳成分栏。 */
  document.body.dataset.surface=location.pathname;
  if(path==='/wall'){
    document.body.setAttribute('data-wall-open','');hideDiscoveryBars();return;
  }
  if(/^\/assets\/\d+$/.test(path)){
    /* 详情的骨架归舞台岛：路由到位后 `openItem` 取回 React 包就画。下面那份列表由路由树的目录元素补发。 */
    hideDiscoveryBars();return;
  }
  if(path==='/settings'||/^\/settings\/(appearance|playback|jav|search|content)$/.test(path)||path.startsWith('/posts/')||path.startsWith('/works/')){
    /* 关注详情同样只等舞台浮窗里那份骨架，页面里不先铺一份；列表等关掉详情才画，不补发。 */
    hideDiscoveryBars();return;
  }
  if(path==='/junk-files'){
    /* 垃圾文件是一屏同质卡片，等的是内容结构不是后台进度：Loading Dots 说的是
       「还在跑」，这里要说的是「等下会出现几张什么形状的卡」，所以用目录骨架。 */
    renderCatalogLoading('正在读取垃圾文件');
    return;
  }
  if(path==='/jav'&&javTab(new URLSearchParams(location.search).get('tab'))==='library'){
    renderCatalogLoading('正在读取馆藏');return;
  }
  /* 管理区、播放列表页与关注页的占位由各页面接手。 */
  if(navigationState.managedPagePath(path)){
    hideDiscoveryBars();
    const stats=$('#stats');stats.hidden=false;clearCatalogGrid();
    stats.innerHTML=path.startsWith('/follow')&&path!=='/subscriptions'
      ?followState.followSkeletonHtml('正在读取关注内容')
      :loadingState.managementPlaceholder(path);
    fitSkeleton(stats);
    return;
  }
  if(/^\/(performers|creators|studios|agencies|tags)$/.test(path)){
    hideDiscoveryBars();showIndexContainer();
    paintIndexSkeleton($('#index'),indexParams(path.slice(1),location.search),indexState.peopleIndexLayout());
    return;
  }
  if(/^\/(?:performers|creators|studios|agencies|series|tags)\//.test(path)){
    hideDiscoveryBars();
    $('#index').hidden=false;clearCatalogGrid();
    const kind=ROUTE_ENTITIES[path.split('/')[1]],name=path.split('/').slice(2).join('/');
    const ready=feedState.hasEntityShape(kind,name)?Promise.resolve():waitEntityShapes();
    void ready.then(()=>{
      if($('#index').firstElementChild||decodeURIComponent(location.pathname)!==path)return;
      showEntityLoading(kind,name);
      fitSkeleton($('#index'));
    });
    return;
  }
  /* 未匹配的地址没有随后的读取，不能留一张永远不会被替换的目录骨架。合法的目录、
     回收站和沉浸模式都有路由，才进入各自真实请求对应的等待态。 */
  if(!navigationState.routeMeta(path)){
    clearCatalogGrid();$('#count').textContent='';$('#loadSentinel').hidden=true;
    return;
  }
  renderCatalogLoading();
}

export function installLoading3() {
followState.followContentSkeletonHtml = (media=new URLSearchParams(location.search).get('media')==='images'?'images':'videos',
  label='正在读取关注内容')=>pageSkeletonHtml(label,{cards:true,className:'follow-content-skeleton postercard-skeleton',
  gridClass:media==='images'?'followlist followphotowall':'',gridSize:photoSize()});
followState.followSkeletonHtml = (label='正在读取关注内容')=>`<div class="peach-react"><div class="follow" data-follow-feed>
  <div data-follow-head><h2 data-follow-title>关注</h2></div>
  <div class="tier followauthors" data-skeleton-tier="av"></div>
  <div class="tier followworks" data-skeleton-tier="brandpill"></div>
  <div class="board-filter-frame" data-filter-frame>
    <div class="tagbar followfilters" data-filter-row="top" data-skeleton-tier="pill"></div>
    <div class="count followcount" data-filter-row="bottom"><span class="mono"><span class="countskeleton"></span></span></div></div>
  ${followState.followContentSkeletonHtml(undefined,label)}</div></div>`;
$('#loadSentinel').innerHTML=loadingDotsHtml('继续载入中…');
loadingState.setGridCards = html=>revealSkeleton($('#grid'),()=>{$('#grid').innerHTML=`<div class="grid">${html}</div>`});
loadingState.gridCards = ()=>document.querySelectorAll(
    ':is(#grid,#index [data-entity-grid]) :is([data-media-grid] > [data-media-card][data-id],[data-catalog-row][data-id])');
loadingState.managementPlaceholder = path=>managementSkeletonHtml(path,{followLayout:followListLayout(),
  ...(({sort,dir,tab})=>({followSort:sort,followDir:dir,followTab:tab}))(followManageParams())});
loadingState.wantsDiscoveryBars = ()=>$('#catalogFilter').style.display!=='none';
loadingState.entityShapesReady = null;
loadingState.ENTITY_SHAPES_WAIT = 400;
loadingState.syncPageTitle = path=>{
  const url=new URL(path,location.origin);
  const pathname=decodeURIComponent(url.pathname);
  const label=routeMetaOf(pathname)?.title;
  document.title=label?`${label} · Peach`:'Peach · 蜜桃';
  document.body.dataset.surface=url.pathname;
  paintNav();
};
}
