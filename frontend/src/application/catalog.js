import { initialCatalogFilters, resetCatalogFilters, homeCatalogState, homePath as pathForCatalog, onlineDefaultLoc as defaultLocations, resolveSort } from '../react/catalog-grid/catalog-state';
import { CatalogController } from '../react/catalog-grid/catalog-controller';
/* catalog 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { censorOn, preferencesState, refreshAll, replaceCatalogAddress, sidebarFacets, wireDrag } from './preferences.js';
import { nextSortState } from './../sort-preferences';
import { $, STATE_ROUTES, api, esc, firstGrapheme, isCatalogPath, leadingGraphemes } from '../core';
import { barsContext, detailReturnBarsContext, notifyShell, pageOpens, runtimeConfigurable, selectMode, selected, state, writeShell } from './../shell/index';
import { receiptsState } from './receipts.js';
import { dropBars, fetchBars, fetchTopsPage } from './../catalog-bars';
import { confirmModal, dissolveValue, fitSkeleton, wireHorizontalScroller } from '../ui-kit';
import { navigationState, paintSidebar, syncNavigation } from './navigation.js';
import { disposeStage, mediaState, openEditions, openItem, openMix, openParts, openTok } from './media.js';
import { buildManageBar, cardLayout, homeLayoutActive, javActive, layoutState, setHomeLayout, setJavLayout, showHomeSurfaces } from './layout.js';
import { selectionState, setSelectMode, toggleSelection } from './selection.js';
import { javDisplayName, javTitleHtml } from '../core/jav-title';
import { tagLabel } from '../core/tags';
import { loadStage, stageApi } from '../react/application-residents';
import { currentBarsContext, entityState, openEntity, pushEntityPage, routeEntityPage, sortKeys } from './entity.js';
import { applicationEffects } from './effects';
import { managedEntry, managedTaken, openManagedRoute, releaseManagedRoute, updateManagedRoute } from './../history/managed';
import { catalogFilterSkeletonHtml } from './../catalog-filter-skeleton';
import { JAV_LAYOUTS, cardRatio, gridLayout, javLayout } from './../appearance/layout';
import { entityFaceImg, facePos, logoUrl } from './../card-art/markup';
import { sidebarHasCatalogContent } from './../sidebar';
import { rememberRepresentatives } from './../card-art/representatives';
import { clearHomeFeed, isFeedNewPath, renderHomeFeed } from './feed.js';
import { loadingState, renderCatalogLoading } from './loading.js';
import { releaseHoverPreviews } from './../card-art/hover';
import { revealRoutedPage } from './follow.js';
import { pageSkeletonHtml } from './../management-placeholder';
import { catalogEmptyHtml } from './../catalog-onboarding';
import { junkRoute } from './../junk-queue';
import { wallAddress } from '../react/video-wall/wall-route';
export const catalogState = { homeHasFeed: undefined, barsRequestSeq: undefined, barsRendered: undefined, catalogFilterProps: undefined, catalogFilterMounting: undefined, catalogTagRows: undefined, catalogTopsPages: undefined, initialParams: undefined, resolveSort: undefined, initialCatalogUrl: undefined, initialParam: undefined, HOME_QUERY_KEYS: undefined, searchValueSnapshot: undefined, cancelSearchDissolve: undefined, rememberSearchValue: undefined, clearSearchField: undefined, mixRelatedCache: undefined, reduceMotion: undefined, tagList: undefined, tagPressed: undefined, withTagToggled: undefined, gridHelpers: undefined, gridActions: undefined, topsQueryParams: undefined, facetCountsSeq: undefined, VIEW_PILLS: undefined, catalogViews: undefined, TAGS_FIRST: undefined, catalogOnScreen: undefined, COMBO_LABELS: undefined, hideCatalogCombo: undefined, junkQueueHelpers: undefined, junkQueueActions: undefined };

export function dropOfflineFromDefaultLoc(){
  if(catalogState.initialParams.has('loc'))return;
  state.loc=onlineDefaultLoc(state.loc);notifyShell();
}

export function onlineDefaultLoc(loc) { return defaultLocations(loc,preferencesState.sourceOnline) }

export function homePath(filters=state) { return pathForCatalog(filters) }

export function resetHomeState() {
    writeShell(homeCatalogState(resetCatalogFilters(state,preferencesState.appSettings,receiptsState.rollSeed)));
    dropBars();
  }

export function goHome(filters=null,scroll=false){
  resetHomeState();
  if(filters){Object.assign(state,filters);notifyShell()}
  navigationState.route(filters?homePath():'/');catalogState.clearSearchField();disposeStage(false);showHomeSurfaces();
  syncNavigation();buildBars();
  if(scroll)window.scrollTo({top:0,behavior:'smooth'});
}

export function openResourceCard(item,anchor=null){
  const id=item.id;
  if(!item.medium||item.medium==='video'){openItem(id,true,null,anchor);return}
  if(item.medium==='image'&&item.location!=='online'){
    window.open('/photo?id='+id,'_blank','noopener');return
  }
  toggleSelection(id);
}

export function mixRelated(seedId){
  if(!catalogState.mixRelatedCache.has(seedId))
    catalogState.mixRelatedCache.set(seedId,api('/api/related?id='+seedId+'&limit=28')
      .then(d=>(d.items||[]).filter(x=>x.id!==seedId))
      .catch(error=>{catalogState.mixRelatedCache.delete(seedId);throw error}));
  return catalogState.mixRelatedCache.get(seedId);
}

export function openGridCard(it,anchor){
  if(stageApi()?.miniplayerTakesCard(it)){stageApi().miniplayerPlay(it.id);return}
  openCardDetails(it,anchor);
}

function openCardDetails(it,anchor){
  if(it.edition_group){openEditions(it.edition_group.seed_id,it.id,true,anchor);return}
  if(it.part_group){openParts(it.part_group.seed_id,it.id,true,anchor);return}
  openItem(it.id,true,null,anchor);
}

export async function toggleWatchLater(it,onChange){
  try{
    const r=await api('/api/watch-later',{method:'POST',body:JSON.stringify({id:it.id})});
    it.watch_later=r.watch_later;onChange(!!r.watch_later);
    receiptsState.actionReceipt(r.watch_later?'已加入稍后看':'已移出稍后看',{undo:async()=>{
      const restored=await api('/api/watch-later',{method:'POST',body:JSON.stringify({id:it.id})});
      it.watch_later=restored.watch_later;onChange(!!restored.watch_later);
    }});
  }catch(error){receiptsState.actionFailure('更新稍后看',error)}
}

export async function runResourceOperation(it,operation){
  try{
    await api('/api/batch',{method:'POST',body:JSON.stringify({ids:[it.id],operation})});
    await loadCatalog();
    const inverse=operation==='restore'?'dispose':'restore';
    receiptsState.actionReceipt(operation==='restore'?'已还原':'已移入回收站',{undo:async()=>{
      await api('/api/batch',{method:'POST',body:JSON.stringify({ids:[it.id],operation:inverse,
        ...(inverse==='dispose'?{disposal_reason:it.disposal_reason??null}:{})})});
      await loadCatalog();
    }});
  }catch(error){receiptsState.actionFailure('操作',error);throw error}
}

export async function getBarsData(context){
  // JAV 模式的顶部三层与筛选面板要跟着收窄，否则会列出只出现在创作者作品里的
  // 女优和厂牌，点进去却是空的。口径进键，换了口径就是另一份。
  const facetParams=new URLSearchParams();
  if(javActive())facetParams.set('jav','1');
  if(context.type==='entity'){
    facetParams.set('scope_kind',context.kind);facetParams.set('scope_name',context.name)
  }else if(context.type==='item')facetParams.set('id',String(context.id));
  // 已标记/稍后看这类状态页也是一个更窄的集合。不传的话，上面那排头像和
  // 标签条走的是全库口径，列出来的人和标签在本页一个作品都没有。
  if(context.type==='home'&&state.state)facetParams.set('state',state.state);
  if(context.type!=='item'){
    const filters=entityState.activeFilterState();
    ['loc','creator','studio','tag','tag_match','len','dur_min','dur_max','orient','region','q','thumb','folder','folder_recursive'].forEach(key=>{
      if(filters[key])facetParams.set(key,filters[key]);
    });
  }
  // 顶部三层跟着「换一批」的同一个种子走，刷新后才真的换人。
  return fetchBars(facetParams.toString(),catalogState.topsQueryParams(context).toString());
}

export function scrollFilteredViewToTop(){
  if(scrollY<=0)return;
  const root=document.documentElement;
  root.classList.add('refiltering');
  const done=()=>{root.classList.remove('refiltering');removeEventListener('scrollend',done)};
  applicationEffects.listen.bind(applicationEffects, window)('scrollend',done);
  applicationEffects.delay(done,1200);
  scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
}

export function applyFilterStateInPlace(filters){
  // 资料页那一条的按下态由页面按地址上的筛选算，新的筛选已经经 `routeEntityPage` 推过去了。
  if(currentBarsContext().type!=='entity')paintCatalogFilter({tags:catalogTags(filters)});
  // 侧栏那几组的按下态与时长两端由侧栏照这一份筛选画，成员不动。
  paintSidebar({filters:{...filters}});
  renderCombo();
}

export async function refreshFacetCounts(context){
  const seq=++catalogState.facetCountsSeq;
  const [facetData]=await getBarsData(context);
  if(seq!==catalogState.facetCountsSeq)return;
  if(context.type==='home')mediaState.facets=facetData;
  // 名单里没有的那几枚由岛记 0；「展开全部」也照这一份摊开。
  paintSidebar({latest:sidebarFacets(facetData,context)});
}

export function commitContextFilter(mutate){
  scrollFilteredViewToTop();
  const context=currentBarsContext();
  if(context.type==='entity'){
    // 标签是作品筛选，点了就回到作品视图：留在照片或名册里既不生效，标签条也会自相矛盾。
    const filters={...context.filters};mutate(filters);
    routeEntityPage(context.kind,context.name,filters);
    applyFilterStateInPlace(filters);refreshFacetCounts(currentBarsContext());return
  }
  if(context.type==='item'){
    // 从详情回到列表是换语境，不是换一条筛选：那几排本来就要照新语境重新画。
    const target=entityState.cloneBarsContext(detailReturnBarsContext);
    disposeStage(false);writeShell({detailReturnBarsContext:null});
    if(target&&target.type==='entity'){
      mutate(target.filters);
      // 详情下面那一页通常还挂着，推新筛选就够；深链进的详情下面没有它，照新地址重开。
      if(routeEntityPage(target.kind,target.name,target.filters))buildBars();
      else writeShell({pageOpens:pageOpens+1});
      return
    }
    mutate(state);writeShell({barsContext:{type:'home',filters:state}});navigationState.route(homePath());showHomeSurfaces();
    buildBars();return
  }
  mutate(state);notifyShell();navigationState.route(homePath());
  applyFilterStateInPlace(state);refreshFacetCounts(barsContext);
}

export function catalogFilterBase(){
  return {tiers:null,empty:false,tags:[],tagFirst:catalogState.TAGS_FIRST,views:catalogState.catalogViews(),state:'',count:null,
    trash:false,sorts:[],layout:null,refreshing:false,offscreen:false,combo:[],comboHidden:true,comboHost:$('#combo'),
    actions:catalogFilterActions(),
    helpers:{wireDrag,wireScroller:row=>{if(row)wireHorizontalScroller(row)}}};
}

export function syncCatalogFilterScreen(){if(catalogState.catalogFilterProps)paintCatalogFilter({})}

export function paintCatalogFilter(patch){
  const host=$('#catalogFilter');
  /* 收起时岛只留两排头像：浮层那两排与资料页、索引页的是同一组控件，藏着一份就是两套同名的，
     等不来的读数微光也会一直挂在页面上。收起时同样不铺静态骨架。 */
  const offscreen=host.style.display==='none'||document.body.matches('.entity-open,.index-open');
  patch={...patch,offscreen};
  catalogState.catalogFilterProps={...(catalogState.catalogFilterProps||catalogFilterBase()),...patch};
  if(catalogState.catalogFilterMounting)return;
  if(managedTaken(host)){updateManagedRoute(host,patch);return}
  if(!host.firstChild&&!offscreen){host.innerHTML=catalogFilterSkeletonHtml(catalogState.catalogViews(),catalogState.catalogFilterProps.state,catalogHeadProps());fitSkeleton(host)}
  /* 常驻：不随换页收，所以不判当前页。 */
  catalogState.catalogFilterMounting=openManagedRoute('catalog-filter',catalogState.catalogFilterProps,{container:host,isCurrent:()=>true})
    .then(()=>updateManagedRoute(host,catalogState.catalogFilterProps))
    .finally(()=>{catalogState.catalogFilterMounting=null});
}

export function catalogFilterActions(){
  return {
    currentHref:()=>{
      const href=new URL(homePath(),location.origin);
      href.searchParams.set('loc',state.loc||'');
      href.searchParams.set('thumb',state.thumb||'0');
      href.searchParams.set('sort',state.sort||'');
      href.searchParams.set('dir',state.dir||'');
      return href.pathname+href.search;
    },
    openEntity:(kind,name)=>openEntity(kind,name),
    /* 按下去当场就推新的按下态，滑动玻璃跟着走，不等这一趟取数。 */
    setView:view=>{
      state.state=view;notifyShell();paintCatalogFilter({state:view});
      navigationState.route(homePath());buildBars();
    },
    toggleTag:tag=>toggleTag(tag),
    clearFilter:key=>commitContextFilter(filters=>{filters[key]=''}),
    clearAll:()=>commitContextFilter(filters=>{filters.tag='';filters.creator='';filters.studio='';filters.owner=''}),
    setSort:key=>{
      const next=nextSortState(key,state.sort,state.dir);
      if(!next)return;
      state.sort=next.sort;state.dir=next.dir;notifyShell();replaceCatalogAddress();
    },
    reshuffle:()=>refreshAll(),
    setLayout:value=>{if(javActive())setJavLayout(value);else setHomeLayout(value)},
    /* 每排各记各的页号：两排的长度不一样，共用一个计数会让先到头的那排替另一排把页翻过去。
       页号跟着这一份名单走，`buildBars` 换名单时从头数起。两排翻到同一页时由 `fetchTopsPage` 共用一次请求。 */
    moreTops:async kind=>{
      const pages=catalogState.catalogTopsPages;if(!pages)return [];
      const rows=(await fetchTopsPage(catalogState.topsQueryParams(pages.context,++pages[kind]).toString()))[kind]||[];
      return kind==='performers'?rows.map(tierPerformer):rows.map(tierStudio);
    },
  };
}

export function catalogLayoutProp(){
  if(javActive())return {name:'jav-layout',label:'JAV 卡片版式',value:javLayout(),options:JAV_LAYOUTS};
  if(homeLayoutActive())return {name:'home-layout',label:'首页卡片版式',value:cardLayout(),options:JAV_LAYOUTS};
  return null;
}

export function catalogHeadProps(){
  return {sorts:sortKeys(state.sort,state.dir),layout:catalogLayoutProp(),
    state:state.state||'',trash:state.state==='trash'};
}

export function catalogTags(filters){
  return catalogState.catalogTagRows.map(row=>({...row,selected:catalogState.tagPressed(filters.tag,row.k)}));
}

export function tierPerformer(x){
  return {name:x.k,ringHtml:`<span data-tier-initial>${esc(firstGrapheme(x.k))}</span>${entityFaceImg(
    {id:x.id,hasImage:x.has_image,version:x.image_version,rep:x.has_avatar?x.rep:null,style:facePos(x.avatar_focus),focus:x.avatar_focus,
      standIn:x.avatar_stand_in})}`};
}

export function tierStudio(x){
  return {name:x.k,fallback:leadingGraphemes(x.k,2),
    logo:x.has_logo?logoUrl(x.k,'icon',x.logo_version):''};
}

export function renderBarsLoading(filterState){
  if(catalogState.catalogFilterProps?.tiers)return;
  catalogState.barsRendered='';
  paintCatalogFilter({tiers:null,state:filterState.state||''});
}

export async function buildBars(){
  const requestSeq=++catalogState.barsRequestSeq;
  paintSidebar();
  if(!sidebarHasCatalogContent(location.pathname))return;
  /* 详情浮窗是盖住整页的模态：两排头像、标签条和抽屉在它开着的时候一格都看不见。
     为它们另取一趟这一部作品口径的聚合，换来的只是把列表那份缓存挤掉——关掉详情时
     整排头像连 `<img>` 一起重建，人看到的就是「点进去又退出来，页面自己刷新了一次」。
     所以详情不碰表面的条，列表的口径和那份缓存原样留着等他回来。 */
  if(barsContext.type==='item')return;
  const context=currentBarsContext(),filterState=entityState.activeFilterState();
  const signature=JSON.stringify([context,filterState,state.state||'',state.seed||'',javActive()]);
  renderBarsLoading(filterState);
  // 两个聚合查询互不依赖。冷启动各需约 1 秒，串行会让手机首屏白等；
  // 并行取回后再一次性绘制顶部与抽屉。
  const [facetData,tops]=await getBarsData(context);
  if(requestSeq!==catalogState.barsRequestSeq)return;
  /* 口径和数据都和上一次一样时，画出来的是同一串 HTML。照样赋一次 innerHTML 只换来
     整排头像连 `<img>` 一起重建、重解一遍码，屏幕上就是白闪一下——这一排每一个都是
     一张图。比数据不比时间：详情看上十分钟再回来，取回的多半还是同一份。 */
  const rendered=signature+'\n'+JSON.stringify([facetData,tops]);
  /* 顶上那几排原样留着；侧栏在中途换过页面（详情开了又关）时已收回到只剩导航，同一份分组推回去。 */
  if(rendered===catalogState.barsRendered){
    if(layoutState.sidebarCatalog)paintSidebar({content:layoutState.sidebarCatalog,filters:{...filterState}});
    return;
  }
  catalogState.barsRendered=rendered;
  if(context.type==='home')mediaState.facets=facetData;
  const topTags=facetData.tags||[];

  /* 顶部三层：女优圆头像 / 厂牌 / 内容标签。代表作表只收真能取到头像的那些，筛法见
     `frontend/src/card-art/representatives.ts`。 */
  rememberRepresentatives(tops.performers);
  rememberRepresentatives(tops.studios);
  // 「两排都空」现在只剩全库真的一个人都没有这一种：窄集合已经由 loadTops 退回全库口径。
  const emptyHome=context.type==='home'&&!javActive()&&!state.state&&!state.q&&!facetData.locations.some(row=>row.n>0);
  catalogState.catalogTopsPages={context,performers:0,studios:0};
  /* 加上去的那几枚排在最前面，按加的先后。它们不一定在抽出来的这一批里，也可能压根不
     在榜上——人是从卡片或详情页点进来的。这一排横着滚，一枚生效的标签落在第三十位跟没
     画出来是一回事：要撤掉刚加的那一条，得先把整排推过去把它找回来。
     第一屏其余的位置由那一批抽样填——「换一批」换的就是这批成员。续上去的是这一批之外
     剩下的，照数量从多到少读下来：抽样只管开头露谁，后面的顺序不归它管。
     标签后面带上这个标签下有多少，跟资料页那条筛选条同一个口径；生效的标签不在这一批里
     时不印数字（`n` 为空）。 */
  const appliedKeys=catalogState.tagList(filterState.tag);
  const byTagKey=new Map(topTags.map(row=>[row.k,row]));
  const appliedTags=appliedKeys.map(k=>byTagKey.get(k)||{k});
  const tagPool=topTags.filter(row=>!appliedKeys.includes(row.k));
  const pickedTags=receiptsState.seededSample(tagPool,catalogState.TAGS_FIRST,`tags:${state.seed||''}`);
  const pickedKeys=new Set(pickedTags.map(row=>row.k));
  catalogState.catalogTagRows=appliedTags.concat(pickedTags,tagPool.filter(row=>!pickedKeys.has(row.k)))
    .map(row=>({k:row.k,label:tagLabel(row.k),n:row.n??null}));
  paintCatalogFilter({
    tiers:{key:String(catalogState.barsRequestSeq),performers:tops.performers.map(tierPerformer),studios:tops.studios.map(tierStudio)},
    empty:emptyHome,tags:catalogTags(filterState),tagFirst:appliedTags.length+pickedTags.length,
    views:catalogState.catalogViews(),state:filterState.state||'',
  });
  renderCombo();
  /* 侧栏那几组换成这一份聚合：`key` 换了，岛按新的按下态重定各组开合，计数徽标按上一次的值判断弹不弹。 */
  layoutState.sidebarCatalog={kind:'catalog',key:String(++layoutState.sidebarContentSeq),facets:sidebarFacets(facetData,context)};
  paintSidebar({content:layoutState.sidebarCatalog,filters:{...filterState},latest:null});
}

export function paintCatalogCount(nextTotal,n){
  mediaState.total=nextTotal;
  /* 回收站的读数写在页头的说明行里（右端挂「清空回收站」），由页头岛画。 */
  if(state.state==='trash')selectionState.trashCount={total: mediaState.total,shown:n};
  buildManageBar();
  paintCatalogFilter({count:{total: mediaState.total,shown:n}});
}

export function emptyTrash(){
  return confirmModal({title:'清空回收站',body:'回收站中的全部文件和馆藏记录将永久删除，无法恢复。',confirmLabel:'清空回收站',danger:true,onConfirm:async()=>{
    try{
      const r=await api('/api/trash/empty',{method:'POST'});
      /* 删不掉的文件（占用中、网盘离线）会连同数据库行一起留在回收站，必须说出来，
         否则用户看到条目还在会以为清空又没生效。 */
      if(r.blocked&&r.blocked.length)throw new Error(`已永久删除 ${r.purged} 项；${r.blocked.length} 项未能删除，仍在回收站：\n`
        +r.blocked.slice(0,5).map(x=>`${x.path}（${x.reason}）`).join('\n'));
      receiptsState.actionReceipt(`已永久删除 ${r.purged} 项`);
    }finally{await loadCatalog()}
  }});
}

export function toggleTag(t){commitContextFilter(filters=>{filters.tag=t?catalogState.withTagToggled(filters.tag,t):''})}

export function comboItems(filters){
  const items=[];
  if(filters.creator)items.push({kind:'clear',key:'creator',label:`${catalogState.COMBO_LABELS.creator} ${filters.creator}`});
  if(filters.studio)items.push({kind:'clear',key:'studio',label:`${catalogState.COMBO_LABELS.studio} ${filters.studio}`});
  if(filters.owner==='none')items.push({kind:'clear',key:'owner',label:`${catalogState.COMBO_LABELS.owner} 未归属`});
  catalogState.tagList(filters.tag).forEach(t=>items.push({kind:'untag',key:t,label:tagLabel(t)}));
  return items;
}

export function renderCombo(){
  paintCatalogFilter({combo:comboItems(state),comboHidden:!catalogState.catalogOnScreen()});
}

export async function loadCatalog(){
  const surface=navigationState.claimSurface(navigationState.surfacePath());
  /* 新作那一行只在目录路径上出现：管理页、回收站这些页面回答的是别的问题，一行「外面出了
     什么」摆在那里只是噪音。离开目录时要显式收起——它是 `#main` 的固定子节点，没人收就
     一直挂在那儿。 */
  writeShell({barsContext:{type:'home',filters:state},detailReturnBarsContext:null});disposeStage(false);
  if(state.state==='ads')return loadJunk(surface);
  // 卸掉垃圾队列要赶在铺骨架之前：它的计数行也在 `#count` 里，先铺就把它挂着的那一格冲掉了。
  if(catalogState.controller.page==='/junk-files')clearCatalogGrid();
  renderCatalogLoading();
  showHomeSurfaces();
  if(isFeedNewPath(location.pathname)&&state.browse!=='folders'){
    await loadingState.entityShapesReady;
    if(!navigationState.surfaceCurrent(surface))return;
    renderHomeFeed();
  }else clearHomeFeed();
  renderCombo();
  $('#count').classList.remove('manage-static','junkcount');
  return paintCatalogGrid(surface);
}

export function settleCatalog(revision) { catalogState.controller.settle(revision) }

export function clearCatalogGrid() {
    const grid=$('#grid');releaseHoverPreviews(grid);releaseManagedRoute(grid);
    catalogState.controller.clear();grid.innerHTML='';
  }

export function paintCatalogGrid(surface){return paintGridPage('/',catalogGridProps,surface,{place:revealRoutedPage})}

export function paintJunkQueue(surface){return paintGridPage('/junk-files',junkQueueProps,surface)}

export function gridPainted(){return !!managedEntry($('#grid'))}

export function gridTaken() { return catalogState.controller.taken(gridPainted()) }

export function pushGridPage(patch){updateManagedRoute($('#grid'),patch)}

export function paintGridPage(page,propsFor,surface,options={}) {
    const grid=$('#grid');
    if(catalogState.controller.page&&catalogState.controller.page!==page)clearCatalogGrid();
    const render=catalogState.controller.begin(page,gridPainted());
    const props=propsFor();
    if(!render.mount){pushGridPage(props);return render.settled}
    releaseHoverPreviews(grid);
    // 取消信号撤销实际 managed 预取，不能只让晚到回调不绘制。
    const abort=()=>releaseManagedRoute(grid);
    render.signal.addEventListener('abort',abort,{once:true});
    const painting=openManagedRoute(page,props,{...options,container:grid,isCurrent:()=>navigationState.surfaceCurrent(surface)&&render.isCurrent()});
    painting.catch(error=>console.error(error)).finally(()=>{
      render.signal.removeEventListener('abort',abort);render.finish(gridPainted());
    });
    return render.settled;
  }

export function catalogGridLayout(){
  const home=homeLayoutActive();
  return gridLayout({active:javActive()||home,home,portrait:state.orient==='竖屏'});
}

export function catalogSkeletonHtml(label='正在读取作品'){
  return pageSkeletonHtml(label,{cards:true,className:'catalog-skeleton postercard-skeleton',cardRatio:catalogCardRatio()});
}

export function catalogCardRatio(){
  if(!state)return 16/9;
  return cardRatio(catalogGridLayout());
}

export function repaintCatalogGrid(){
  if(gridTaken()){
    releaseHoverPreviews($('#grid'));
    pushGridPage({layout:catalogGridLayout(),seekSeconds:preferencesState.appSettings.seekSeconds});
  }
  if(!entityState.entityPageCurrent())return;
  releaseHoverPreviews($('#index [data-entity-hero]'));
  pushEntityPage({layout:catalogGridLayout(),seekSeconds:preferencesState.appSettings.seekSeconds});
}

export function catalogGridProps(){
  const path=decodeURIComponent(location.pathname),home=isCatalogPath(path),trash=state.state==='trash';
  return {
    mode:'catalog',helpers:catalogState.gridHelpers,actions:catalogState.gridActions,layout:catalogGridLayout(),
    wall:path==='/wall',
    selectMode,selected:new Set(selected),seekSeconds:preferencesState.appSettings.seekSeconds,revision:catalogState.controller.revision,
    wireDrag,settled:settleCatalog,
    skeletonHtml:()=>catalogSkeletonHtml(),
    filters:{...state},batchSize:preferencesState.appSettings.batchSize,groupCollapse:preferencesState.appSettings.groupCollapse,
    /* 只有首页默认列表排除竖屏——那里另有独立的竖屏带承接它们。搜索必须能搜到竖屏作品，
       否则按名字找一条竖屏视频会得到 0 结果。JAV 模式恒不含竖屏：番号发行物本身就是横版。 */
    excludeVertical:(home&&path!=='/wall'&&!state.q&&!state.orient&&state.browse!=='folders')||state.jav==='1',
    mix:home&&path!=='/wall'&&!trash&&state.browse!=='folders',
    /* JAV 模式不插竖屏带：主列表的 exclude_vertical 管不到它，它是独立请求、独立插入的。 */
    shorts:home&&path!=='/wall'&&!javActive()&&state.orient!=='竖屏'&&!trash&&state.browse!=='folders',
    countRow:$('#count'),onCount:paintCatalogCount,onCountFailed:()=>paintCatalogFilter({count:false}),
    emptyHtml:({trash:inTrash,libraryEmpty})=>inTrash
      ?preferencesState.emptyState('trash','回收站是空的','删掉的内容会先到这里；确认不再需要后再清空。')
      :catalogEmptyHtml({jav:javActive()&&!libraryEmpty,configurable:runtimeConfigurable,filtered:!libraryEmpty}),
    canLoadMore:()=>$('#stats').hidden&&$('#index').hidden,
  };
}

export function loadJunk(surface){
  const count=$('#count');
  const live=catalogState.controller.page==='/junk-files'&&gridPainted()&&!catalogState.controller.pending
    &&count.querySelector(':scope > .peach-react:not([data-junk-count-skeleton])');
  if(!live){clearCatalogGrid();renderCatalogLoading('正在读取垃圾文件')}
  showHomeSurfaces();
  renderCombo();
  // 垃圾文件是逐项处置队列，计数只是当前队列说明，不是需要跟随浏览的排序工具。
  count.classList.add('manage-static','junkcount');
  count.classList.remove('is-stuck');
  return paintJunkQueue(surface);
}

export async function runJunkOperation(it,operation){
  const ids=[it.id],disposed=operation==='dispose',reconsidered=operation==='reconsider-junk';
  try{
    await api('/api/batch',{method:'POST',body:JSON.stringify({ids,operation,
      ...(disposed?{disposal_reason:'junk'}:{})})});
    await loadCatalog();
    const inverse=disposed?'restore':reconsidered?'dismiss-junk':'reconsider-junk';
    receiptsState.actionReceipt(disposed?'已移入回收站':reconsidered?'已重新加入垃圾判断':'已标记为不是垃圾',{undo:async()=>{
      await api('/api/batch',{method:'POST',body:JSON.stringify({ids,operation:inverse})});
      await loadCatalog();
    }});
  }catch(error){receiptsState.actionFailure('操作',error);throw error}
}

export function junkQueueProps(){
  return {
    ...junkRoute(location.search),helpers:catalogState.junkQueueHelpers,actions:catalogState.junkQueueActions,
    batchSize:preferencesState.appSettings.batchSize,revision:catalogState.controller.revision,selectMode,selected:new Set(selected),
    countRow:$('#count'),settled:settleCatalog,
    skeletonHtml:()=>pageSkeletonHtml('正在读取垃圾文件',{cards:true,className:'catalog-skeleton postercard-skeleton'}),
    canLoadMore:()=>$('#stats').hidden&&$('#index').hidden,
  };
}

export function installCatalog7() {
    catalogState.initialParams = new URLSearchParams(location.search);
    catalogState.resolveSort = (sort,dir,fallback=preferencesState.appSettings.defaultSort)=>resolveSort(sort,dir,preferencesState.appSettings,fallback);
    writeShell(homeCatalogState(initialCatalogFilters(location,preferencesState.appSettings,receiptsState.rollSeed)));
    catalogState.searchValueSnapshot={text:'',scrollLeft:0};
    catalogState.cancelSearchDissolve=()=>{};
    catalogState.rememberSearchValue=(input=$('#q'))=>{catalogState.searchValueSnapshot={text:input?.value||'',scrollLeft:input?.scrollLeft||0}};
    catalogState.clearSearchField=(snapshot=null)=>{
      const input=$('#q');if(!input)return;
      const live={text:input.value,scrollLeft:input.scrollLeft||0};
      const previous=snapshot||(live.text?live:catalogState.searchValueSnapshot);
      catalogState.cancelSearchDissolve();
      catalogState.cancelSearchDissolve=dissolveValue(input,input.parentElement,previous);
      catalogState.searchValueSnapshot={text:'',scrollLeft:0};
    };
  }

export function installCatalog12() {
applicationEffects.handler($('#wallBtn'), 'onclick', ()=>{
  setSelectMode(false,true);
  const context=currentBarsContext(),filters={...entityState.activeFilterState(),jav:javActive()?'1':''};
  if(context.type==='entity'&&context.kind!=='tag')filters[context.kind]=context.name;
  navigationState.navigatePath(wallAddress(undefined,filters));
});
catalogState.mixRelatedCache = new Map();
catalogState.reduceMotion = ()=>matchMedia('(prefers-reduced-motion:reduce)').matches;
catalogState.tagList = (value=state.tag)=>String(value||'').split(',').filter(Boolean);
catalogState.tagPressed = (value,tag)=>catalogState.tagList(value).includes(String(tag));
catalogState.withTagToggled = (value,tag)=>{const cur=catalogState.tagList(value);const index=cur.indexOf(tag);
  if(index>=0)cur.splice(index,1);else cur.push(tag);return cur.join(',')};
catalogState.gridHelpers = {
  badgeHtml:(location,cost)=>preferencesState.srcBadge(location,cost),
  titleHtml:(it,raw)=>javTitleHtml(it,raw),
  displayName:(it,raw)=>javDisplayName(it,raw),
  tagLabel:tag=>tagLabel(tag),
};
catalogState.gridActions = {
  wallPlayer:async(it,frame,signal)=>{
    const stage=await loadStage(mediaState.stageHost);
    signal.throwIfAborted();
    return stage.mountWallPlayer(it,frame,signal);
  },
  wallDetails:openCardDetails,
  open:(it,anchor)=>openGridCard(it,anchor),
  openResource:(it,anchor)=>stageApi()?.miniplayerTakesCard(it)?stageApi().miniplayerPlay(it.id):openResourceCard(it,anchor),
  openShort:it=>stageApi()?.miniplayerTakesCard(it)?stageApi().miniplayerPlay(it.id):openTok(it.id),
  openShorts:()=>openTok(),
  openMix:(seedId,anchor)=>openMix(seedId,seedId,true,anchor),
  openEntity:(kind,name)=>openEntity(kind,name),
  openUnowned:()=>goHome({owner:'none'},true),
  /* 卡片上的标签是「只看这个标签」，已经在筛它就取消。在哪一屏点就在哪一屏生效。 */
  toggleTag:tag=>{
    commitContextFilter(filters=>{filters.tag=catalogState.tagPressed(filters.tag,tag)?'':tag});
    window.scrollTo({top:0,behavior:'smooth'});
  },
  toggleSelection:(id,range)=>toggleSelection(id,range),
  watchLater:(it,onChange)=>toggleWatchLater(it,onChange),
  resourceOperation:(it,operation)=>runResourceOperation(it,operation),
  mixRelated:seedId=>mixRelated(seedId),
  canFlip:()=>!selectMode&&!censorOn()&&!window.__scrolling&&!catalogState.reduceMotion(),
};
catalogState.topsQueryParams = (context,page=0)=>{
  const params=new URLSearchParams({n:'60',seed:state.seed||''});
  if(page)params.set('page',String(page));
  if(javActive())params.set('jav','1');
  if(context.type==='home'&&state.state)params.set('state',state.state);
  if(context.type==='home'&&state.folder){params.set('folder',state.folder);if(state.folder_recursive)params.set('folder_recursive',state.folder_recursive)}
  return params;
};
catalogState.facetCountsSeq = 0;
catalogState.VIEW_PILLS = [{k:'',label:'全部'},{k:'fresh',label:'没看过'},
                  {k:'later',label:'稍后看'},{k:'flagged',label:'已标记'}];
catalogState.catalogViews = ()=>catalogState.VIEW_PILLS.map(v=>({...v,href:v.k?STATE_ROUTES[v.k]:'/'}));
catalogState.TAGS_FIRST = 26;
applicationEffects.listen($('#catalogFilter'), 'click', event=>{
  const view=event.target.closest?.('[data-catalog-view]');
  if(!view||event.defaultPrevented)return;
  event.preventDefault();catalogFilterActions().setView(view.dataset.catalogView);
});
catalogState.catalogOnScreen = ()=>$('#index').hidden&&$('#stats').hidden;
catalogState.COMBO_LABELS = {creator:'创作者',studio:'厂牌',owner:'归属'};
catalogState.hideCatalogCombo = ()=>{if(catalogState.catalogFilterProps)paintCatalogFilter({comboHidden:true})};
}

export function installCatalog19() {
catalogState.junkQueueHelpers = {badgeHtml:(location,cost)=>preferencesState.srcBadge(location,cost)};
catalogState.junkQueueActions = {
  /* 换分类、换视图：先收起多选，再改地址，由目录元素重读。 */
  navigate:path=>{if(selectMode)setSelectMode(false,true);navigationState.route(path)},
  toggleSelection:(id,range)=>toggleSelection(id,range),
  open:(it,anchor)=>it.junk_kind==='image'
    ?window.open('/photo?id='+it.id,'_blank','noopener'):openItem(it.id,true,null,anchor),
  /* 定位成功由 Toast 报，卡上状态行留空；失败把原因写回状态行。 */
  reveal:async it=>{
    try{await api('/api/reveal',{method:'POST',body:JSON.stringify({id:it.id})});receiptsState.toast({text:'已在资源管理器中显示'});return ''}
    catch(error){return entityState.sourceHint(error.message)}
  },
  operate:(it,operation)=>runJunkOperation(it,operation),
};
}

catalogState.controller = new CatalogController();
