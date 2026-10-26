/* layout 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { DIRECT_MANAGE_NAV, MANAGE_MENU_SECTIONS, MANAGE_SECTIONS, NAV_CATALOG, OPTIONAL_SIDEBAR_ITEMS, SIDEBAR_ITEMS, getManageMenuSections } from './navigation-items';
import { FOLLOW_INITIAL_RANGE_OPTIONS } from './initial-follow-ranges';
import { noteHtml, revealTexts } from '../ui-kit';
import { $, ROUTE_ENTITIES, STATE_LABELS, STATE_ROUTES, api, isCatalogPath } from '../core';
import { buildBars, catalogLayoutProp, catalogState, clearCatalogGrid, commitContextFilter, emptyTrash, goHome, homePath, loadCatalog, paintCatalogFilter, repaintCatalogGrid, syncCatalogFilterScreen } from './catalog.js';
import { navigationState, openRoutedPage, paintSidebar, syncNavigation } from './navigation.js';
import { paintManagementPlaceholder } from './../management-placeholder';
import { hideDiscoveryBars } from './loading.js';
import { releaseManagedRoute } from './../history/managed';
import { isFeedNewPath } from './feed.js';
import { tagLabel } from '../core/tags';
import { followFeedLive, followState, followView, followViewPath, routeFollowFeed } from './follow.js';
import { applicationEffects } from './effects';
import { entityJavLayout, notifyShell, pageOpens, runtimeConfigurable, selectMode, selectSurface, state, writeShell } from './../shell/index';
import { censorOn, preferencesState, setCensored, setHighContrast } from './preferences.js';
import { playUiSound } from '../ui-kit/sounds';
import { THEME_OPTIONS } from './../appearance/theme';
import { JAV_LAYOUTS, cardLayoutFor, storeHomeLayout, storeJavLayout, storeVideoLayout } from './../appearance/layout';
import { receiptsState } from './receipts.js';
import { applySyncedSettings } from './../appearance/settings';
import { loadManageHeader, loadSettingsPanel, loadSidebar, manageHeaderApi, sidebarApi } from '../react/application-residents';
import { sidebarSkeletonHtml } from './../sidebar-skeleton';
import { selectionState, setSelectMode } from './selection.js';
import { manageHeaderSkeletonHtml, manageHeaderView } from './../manage-header';
import { junkPath } from './../junk-queue';
import { JAV_RELEASE_SORT, SORTS } from './../sort-preferences';
import { dropBars } from './../catalog-bars';
import { entityState, pushEntityPage, syncPhotoWalls } from './entity.js';
import { routeMetaOf } from './../history/route-meta';
import { peachHistory, shellNavigate, isPreferencePath, preferenceSection, settingsPath, settingsBackgroundOf } from '../history';
import { filterScrollState } from './filter-scroll.js';
import { releaseHoverPreviews } from './../card-art/hover';
export const layoutState = { sidebarProps: undefined, sidebarSurface: undefined, sidebarCatalog: undefined, sidebarContentSeq: undefined, SIDEBAR_ITEMS: undefined, MANAGE_SECTIONS: undefined, MANAGE_MENU_SECTIONS: undefined, manageMenuSections: undefined, OPTIONAL_SIDEBAR_ITEMS: undefined, NAV_CATALOG: undefined, DIRECT_MANAGE_NAV: undefined, settingsHost: undefined, lastManagePageLabel: undefined, manageHeaderSkeleton: undefined, manageHeaderRoot: undefined, scrollT: undefined, stickyFrame: undefined, mobileFilterScroll: undefined, mobileFilterHost: undefined, mobileFilterPath: undefined };

export function ledgerGateNote(runtime,message,actionLabel,actionHref){
  return noteHtml(message,{variant:runtime?.ledger_sync==='conflict'?'warning':'secondary',
    className:'runtimegate',actionLabel:actionHref?actionLabel:'',actionHref});
}

export function showManagementBody({manage=true,placeholder=''}={}){
  $('#stats').hidden=false;$('#index').hidden=true;clearCatalogGrid();
  $('#count').textContent='';$('#loadSentinel').hidden=true;
  if(manage)buildManageBar();
  else{paintManageHeader('');syncNavigation()}
  // 屏幕上已经是同一张骨架就不重画（`paintManagementPlaceholder`）。
  if(placeholder)paintManagementPlaceholder($('#stats'),placeholder);
}

export function enterManagementSurface(){
  catalogState.hideCatalogCombo();
  hideDiscoveryBars();
  document.body.classList.remove('entity-open','index-open');
}

export function showHomeSurfaces(){
  // 两个类都要清：只清 entity-open 会让从索引页回首页时顶栏一直空着，
  // 而且下面那一行 style.display='' 恢复不了被 class 隐藏的元素。
  document.body.classList.remove('entity-open','index-open');
  /* 索引页和资料页都由路由树画进 #index，两条路都先经过这里再铺骨架：直接盖掉的话，上一页的
     React 子树就没人卸，留着一棵管着已经不在页面上的节点的根。这里只收 `#index` 那一页，管理区
     `#stats` 那一页不动（资料页压在它上面时它藏着继续活）。 */
  releaseManagedRoute($('#index'));
  $('#stats').hidden=true;$('#index').hidden=true;
  if(!isFeedNewPath(location.pathname))$('#feedNew').hidden=true;
  $('#catalogFilter').style.display='';syncCatalogFilterScreen();
  buildManageBar();paintListTitle();   // 放在最后：管理区要盖掉上面刚恢复的首页横条
}

export function closeStats(){navigationState.route('/');showHomeSurfaces()}

export function renderFollowDrawer({tags=[],providers=[],duration=false}){
  const content=tags.length||providers.length||duration?{kind:'follow',
    tags:tags.map(([tag,n])=>({value:tag,label:tagLabel(tag),n})),selected:[...followState.followTags],
    providers:providers.map(([value,label,src])=>({value,label,n:null,...(src?{dot:{kind:'image',src}}:{})})),
    duration}:null;
  paintSidebar({content,filters:followState.followSidebarFilters()});
}

export function routeFollowFromSidebar(patch){
  routeFollowFeed({...followView(),...patch});
  paintSidebar({filters:followState.followSidebarFilters()});
}

export function narrowShell(){return matchMedia('(max-width:760px)').matches}

export function openDrawer(v){const drawer=$('#drawer'),restore=!v&&drawer.contains(document.activeElement);
  drawer.inert=!v&&narrowShell();
  drawer.classList.toggle('open',v);$('#scrim').classList.toggle('on',v);
  document.body.classList.toggle('drawer-open',!!v);document.dispatchEvent(new Event('board:sidebar'));
  $('#filterBtn').setAttribute('aria-expanded',String(!!v));$('#filterBtn').setAttribute('aria-controls','drawer');$('#filterBtn').setAttribute('aria-label',v?'收起侧栏':'展开侧栏');
  if(restore)$('#filterBtn').focus();sessionStorage.setItem('board.sidebar',v?'open':'closed')}

export function closeDrawerAfterNav(){if(narrowShell())openDrawer(false)}

export function probeConfigurable(){
  if(runtimeConfigurable!==null)return;
  writeShell({runtimeConfigurable:false});
  api('/healthz').then(runtime=>{
    writeShell({runtimeConfigurable:!!runtime.configurable});
    if(runtimeConfigurable&&manageSection())buildManageBar();
  }).catch(()=>{});
}

export function openSettings(section='',fromRoute=false){
  if(!fromRoute&&!isPreferencePath(location.pathname)){
    const navigation=peachHistory.navigation;
    peachHistory.push(settingsPath(section),{settingsBackground:{pathname:location.pathname,search:location.search,state:navigation.location.state,seq:navigation.seq}});
  }else if(!fromRoute&&section){
    shellNavigate(settingsPath(section),{replace:true,state:peachHistory.navigation.location.state});
  }
  section=section||preferenceSection(location.pathname);
  return loadSettingsPanel(layoutState.settingsHost()).then(panel=>{panel.open(section);return panel});
}

export function closeSettingsRoute(){
  if(!isPreferencePath(location.pathname))return;
  if(settingsBackgroundOf(peachHistory.navigation.location.state))peachHistory.go(-1);
  else navigationState.navigatePath('/',true);
}

export function sidebarHost(){
  return {
    scroll:$('#drawerScroll'),store:preferencesState.settingsStore,navCatalog:layoutState.NAV_CATALOG,
    navOn,navTo,
    toggleChip:(key,value,multi)=>commitContextFilter(filters=>{
      if(multi){const cur=String(filters[key]||'').split(',').filter(Boolean);
        const i=cur.indexOf(value);if(i>=0)cur.splice(i,1);else cur.push(value);filters[key]=cur.join(',')}
      else filters[key]=filters[key]===value?'':value
    }),
    setDuration:(lo,hi)=>commitContextFilter(filters=>{
      filters.len='';filters.dur_min=lo?String(lo*60):'';filters.dur_max=hi<180?String(hi*60):''}),
    openFollowTag:tag=>{
      followState.followAuthors=new Set();followState.followProviders=new Set();followState.followMediaView='videos';followState.followFilter='saved';
      followState.followDurMin=followState.followDurMax=0;followState.followTags=new Set([tag]);openDrawer(false);openRoutedPage(followViewPath())},
    selectFollowTag:tag=>{followState.followTags=new Set([tag]);openDrawer(false);openRoutedPage(followViewPath())},
    selectFollowProvider:provider=>routeFollowFromSidebar({provider:followState.followProviders.has(provider)?'':provider}),
    setFollowDuration:(lo,hi)=>routeFollowFromSidebar({durMin:lo?lo*60:0,durMax:hi<180?hi*60:0}),
    attached:()=>{preferencesState.placeSidebarHead();preferencesState.syncGlassOptics()},
  };
}

export function mountSidebar(){
  const scroll=$('#drawerScroll');
  scroll.innerHTML=sidebarSkeletonHtml(preferencesState.appSettings.sidebarOrder,layoutState.NAV_CATALOG,navOn);
  applicationEffects.listen(scroll, 'click', event=>{
    if(sidebarApi())return;
    const button=event.target.closest?.('[data-nav]');
    if(button)navTo(button.dataset.nav);
  });
  loadSidebar(sidebarHost()).then(sidebar=>sidebar.render(layoutState.sidebarProps)).catch(()=>{});
}

export async function loadSyncedSettings(){
  const effects=applicationEffects;
  let remote=null;
  try{remote=await api('/api/settings',{signal:effects.signal})}catch{return}
  if(!effects.isActive)return;
  applySyncedSettings(remote,effect=>preferencesState.settingsEffects[effect]?.());
}

export function manageSection(){
  const hit=navigationState.routeMeta(decodeURIComponent(location.pathname));
  return hit?.route.section||(state.state==='ads'?'cleanup':'');
}

export function buildManageBar(){
  const current=manageSection();
  probeConfigurable();
  // 管理区是行政界面，不该顶着首页的人物/厂牌横条和标签筛选。
  // 隐藏 tagbar 的同时同步 count 栏的吸顶偏移：它默认按「顶栏+筛选条」留位，
  // 筛选条不在时那个偏移会留出一条 58px 的缝，滚动内容从缝里穿出来。
  if(current)hideDiscoveryBars();
  $('#count').classList.toggle('no-tagbar',!!current);
  syncNavigation();     // 顶层高亮跟随管理区；否则从首页进来时仍停在「首页」上
  /* 回收站读数只在这一次进页里有效：离开回收站就忘掉，下次进来说明行先铺同形占位，
     读数到了再落；同页重画（筛选、判完一批、翻页）沿用手上这份，不再铺占位。 */
  if(current!=='trash')selectionState.trashCount=null;
  paintManageHeader(current);
}

export function manageHeaderProps(section){
  return {section,path:decodeURIComponent(location.pathname),sections:layoutState.MANAGE_SECTIONS,
    menu:section?layoutState.manageMenuSections():[],trash:selectionState.trashCount};
}

export function paintManageHeader(section=manageSection()){
  paintListTitle();
  const props=manageHeaderProps(section),header=manageHeaderApi();
  if(header)header.render(props);
  else{
    const html=manageHeaderSkeletonHtml(props);
    if(html!==layoutState.manageHeaderSkeleton){layoutState.manageHeaderSkeleton=html;layoutState.manageHeaderRoot().innerHTML=html}
  }
  /* 换了页才揭示一遍。同一页里的每一次重画（筛选、判完一批、翻页）走的也是这里，
     不比一下标题的话，页面标题会跟着每一次取数再飘一次。骨架阶段与路由树接手之后是同一处。 */
  const label=manageHeaderView(props)?.title||'';
  if(label===layoutState.lastManagePageLabel)return;
  layoutState.lastManagePageLabel=label;
  if(label)revealTexts(layoutState.manageHeaderRoot(),'[data-manage-title],[data-manage-lede]');
}

export function mountManageHeader(){
  const root=layoutState.manageHeaderRoot();
  applicationEffects.listen(root, 'click', event=>{
    if(manageHeaderApi())return;
    const tab=event.target.closest?.('[data-manage]');
    if(tab){openManage(tab.dataset.manage);return}
    if(event.target.closest?.('[data-manage-crumb] a[href]')){
      if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button)return;
      event.preventDefault();navigationState.navigatePath('/data-cleanup');return;
    }
    if(event.target.closest?.('[data-empty-trash]'))emptyTrash();
  });
  loadManageHeader({root,openManage,openDataCleanup:()=>navigationState.navigatePath('/data-cleanup'),emptyTrash})
    .then(()=>paintManageHeader()).catch(()=>{});
}

export function paintListTitle(){
  const el=$('#listTitle');if(!el)return;
  const path=decodeURIComponent(location.pathname);
  const label=!manageSection()&&isCatalogPath(path)?STATE_LABELS[state.state]||'':'';
  el.hidden=!label;if(label)el.textContent=label;
  $('#catalogPageHeading').hidden=!!manageSection()||!isCatalogPath(path)||!!label||state.jav==='1';
}

export function openManage(section='stats'){
  const path=navigationState.routePathOf('section',section);
  if(path){navigationState.openRoutePath(path);return}
  /* 认不出的 section 一律落到垃圾文件：统计页那颗「查看垃圾文件」传的就是 `ads`，
     而垃圾文件是目录页的一个筛选态，没有自己的 section。 */
  state.orient='';state.state='ads';notifyShell();navigationState.route(junkPath());
  showHomeSurfaces();syncNavigation();buildBars();
}

export function javActive(){
  const path=decodeURIComponent(location.pathname);
  if(path==='/')return state.jav==='1';
  if(path.startsWith('/performers/')||path.startsWith('/studios/'))
    return state.jav==='1'||entityJavLayout;
  return false;
}

export function sortOptions(jav=javActive()){
  const ordered=SORTS.filter(([key])=>key!=='seed');
  return jav?[JAV_RELEASE_SORT,...ordered]:ordered;
}

export function homeLayoutActive(){
  return decodeURIComponent(location.pathname)==='/'&&state.jav!=='1';
}

export function cardLayout(){return cardLayoutFor(homeLayoutActive())}

export function setHomeLayout(value){
  storeHomeLayout(value);
  syncVideoLayoutSetting();
  if(!$('#grid').hidden)repaintCatalogGrid();
}

export function setJavLayout(value){
  storeJavLayout(value);
  syncVideoLayoutSetting();
  // 只重画卡片，不重新请求：版式是纯展示层的事。资料页保留已经载入的分页。
  repaintCatalogGrid();
}

export function syncVideoLayoutSetting(){
  document.querySelectorAll('[data-video-layout]').forEach(input=>{input.checked=input.value===cardLayout()});
  if(catalogState.catalogFilterProps)paintCatalogFilter({layout:catalogLayoutProp()});
}

export function setVideoLayout(value){
  storeVideoLayout(value);
  syncVideoLayoutSetting();
  repaintCatalogGrid();
}

export function toggleJavMode(){
  state.jav=state.jav==='1'?'':'1';
  if(state.jav!=='1'&&state.sort==='release'){state.sort='seed';state.dir=''}
  state.state='';state.orient='';notifyShell();
  navigationState.route(homePath());
  showHomeSurfaces();syncNavigation();buildBars();
}

export async function reloadAfterWrite(){
  dropBars();catalogState.mixRelatedCache.clear();
  await Promise.all([reloadCurrentSurface(),buildBars()]);
}

export async function reloadCurrentSurface(){
  // 资料页换一个代次，岛按地址上的筛选重取作品；照片与名册不受批量操作影响。
  if(entityState.entityPageCurrent()&&!$('#index').hidden){pushEntityPage({revision:++entityState.entityPageRevision});return}
  const path=decodeURIComponent(location.pathname);
  // 路由树按匹配打开的索引页与还没画上的资料页：要路由树按地址从头重开。
  if(routeMetaOf(path)?.reload==='reopen'){writeShell({pageOpens:pageOpens+1});return}
  // 关注页：路由树的关注元素推一个新代次，列表重取、不重挂。
  if(path==='/follow'){writeShell({pageOpens:pageOpens+1});return}
  const hit=navigationState.routeMeta(path);
  if(hit?.route.reload){await hit.route.reload();return}
  await loadCatalog();
}

export function navOn(k){
  const path=decodeURIComponent(location.pathname);
  const nav=navigationState.routeMeta(path)?.route.nav||'';
  const directSection=layoutState.DIRECT_MANAGE_NAV[k];
  if(directSection)return manageSection()===directSection;
  if(k==='manage'){
    const current=manageSection();
    return !!current&&!preferencesState.appSettings.sidebarOrder.some(key=>layoutState.DIRECT_MANAGE_NAV[key]===current);
  }
  // JAV 分区及馆藏 JAV 筛选共用导航状态；竖屏由内存里的筛选开关判定。
  if(k==='jav')return path==='/jav'||javActive();
  if(k==='shorts')return state.orient==='竖屏';
  // 首页只在真的停在首页列表上时亮：管理区、索引页、实体页都不算，
  // 否则它会和当前所在的入口同时高亮。
  if(k==='')return path==='/'&&!manageSection()&&!state.state&&!javActive()&&state.orient!=='竖屏';
  // 目录页的四个筛选态共用一屏，竖屏是压在它们之上的另一层筛选。
  if(STATE_ROUTES[k])return nav===k&&state.orient!=='竖屏';
  if(nav)return nav===k;
  return path==='/'&&state.state===k&&state.orient!=='竖屏';
}

export function navTo(k){
  closeDrawerAfterNav();                 // 点了就收起抽屉，且短暂禁止悬停把它立刻弹回
  if(layoutState.DIRECT_MANAGE_NAV[k]){openManage(layoutState.DIRECT_MANAGE_NAV[k]);return}
  if(k==='manage'){openManage();return}
  if(k==='jav'){navigationState.navigatePath('/jav');return}
  if(k===''){goHome();return}
  // 有自己路径的入口（追更、播放列表、沉浸模式、索引页）按路由元数据的 `nav` 找路径进。
  const path=STATE_ROUTES[k]?null:navigationState.routePathOf('nav',k);
  if(path){navigationState.openRoutePath(path);return}
  if(k==='shorts'){state.orient='竖屏';state.state=''}else{state.orient='';state.state=k}
  notifyShell();
  navigationState.route(homePath());
  showHomeSurfaces();
  syncNavigation();buildBars();
}

export function syncHeaderActions(){
  const path=decodeURIComponent(location.pathname),parts=path.split('/').filter(Boolean);
  if(selectMode&&selectSurface!==selectionState.currentSelectSurface())
    setSelectMode(false,true);
  const entity=parts.length>1&&Object.prototype.hasOwnProperty.call(ROUTE_ENTITIES,parts[0]);
  const catalog=isCatalogPath(path)||path==='/trash';
  const canSelect=catalog||entity||path==='/tags'||path==='/follow';
  const canDensity=catalog||entity||path==='/follow';
  $('#selectMode').hidden=!canSelect;$('#density').hidden=!canDensity;
  syncPhotoWalls();
  if(!canSelect&&selectMode)setSelectMode(false,true);
}

export function updateMobileFilterScroll(){
  const frames=[...document.querySelectorAll('[data-filter-frame]')];
  const active=frames.find(frame=>frame.offsetParent!==null);
  if(active!==layoutState.mobileFilterHost||location.pathname!==layoutState.mobileFilterPath)layoutState.mobileFilterScroll=null;
  layoutState.mobileFilterHost=active;layoutState.mobileFilterPath=location.pathname;
  const y=Math.max(0,Math.min(scrollY,document.documentElement.scrollHeight-innerHeight));
  const hold=!!active?.querySelector(':focus-visible,input:focus,select:focus,textarea:focus,[aria-expanded="true"]');
  layoutState.mobileFilterScroll=filterScrollState(layoutState.mobileFilterScroll,y,innerWidth<=760,hold);
  for(const frame of frames){
    const free=frame===active&&layoutState.mobileFilterScroll.free;
    if(free)frame.style.setProperty('--filter-free-top',`${-frame.offsetHeight-12}px`);
    /* 标记写成属性不写类：资料页那块浮层是 React 画的，className 归它管。 */
    if(free===frame.hasAttribute('data-filter-free'))continue;
    /* `top` 一步到位，再用 translate 从量到的起点滑到落点：逐帧改 `top` 每一帧都要重排，滚动中主线程一忙
       就一顿一顿，translate 走合成线程。起点量的是连着上一段滑动的实际位置，半路反向也接得上。 */
    const from=frame.getBoundingClientRect().top;
    frame.getAnimations().forEach(a=>a.id==='filter-slide'&&a.cancel());
    frame.toggleAttribute('data-filter-free',free);
    const shift=from-frame.getBoundingClientRect().top;
    const [duration,easing]=getComputedStyle(document.documentElement).getPropertyValue('--board-motion').trim().split(' ');
    if(Math.abs(shift)>=1&&parseFloat(duration)>0)
      frame.animate([{translate:`0 ${shift}px`},{translate:'0 0'}],{id:'filter-slide',duration:parseFloat(duration)*1000,easing});
  }
}

export function updateStickySurfaces(){
  updateMobileFilterScroll();
  ['.board-filter-frame','#count'].forEach(selector=>{
    const el=$(selector),css=el&&getComputedStyle(el),top=css?parseFloat(css.top):NaN;
    const stuck=!!el&&css.position==='sticky'&&el.offsetParent!==null&&window.scrollY>0&&
      Number.isFinite(top)&&el.getBoundingClientRect().top-(parseFloat(css.translate.split(' ')[1])||0)<=top+1;
    if(el)el.classList.toggle('is-stuck',stuck);
    if(el?.matches('.board-filter-frame'))el.classList.toggle('board-is-stuck',stuck);
  });
}

export function scheduleStickySurfaces(){
  if(layoutState.stickyFrame)return;
  layoutState.stickyFrame=applicationEffects.frame(()=>{layoutState.stickyFrame=0;updateStickySurfaces()});
}

export function installLayout18() {
followState.followSidebarFilters = ()=>({provider:[...followState.followProviders][0]||'',
  dur_min:followState.followDurMin||'',dur_max:followState.followDurMax||''});
applicationEffects.handler($('#filterBtn'), 'onclick', ()=>openDrawer(!$('#drawer').classList.contains('open')));
layoutState.SIDEBAR_ITEMS = SIDEBAR_ITEMS;
layoutState.MANAGE_SECTIONS = MANAGE_SECTIONS;
layoutState.MANAGE_MENU_SECTIONS = MANAGE_MENU_SECTIONS;
layoutState.manageMenuSections = ()=>getManageMenuSections(runtimeConfigurable===true);
layoutState.OPTIONAL_SIDEBAR_ITEMS = OPTIONAL_SIDEBAR_ITEMS;
layoutState.NAV_CATALOG = NAV_CATALOG;
layoutState.DIRECT_MANAGE_NAV = DIRECT_MANAGE_NAV;
layoutState.settingsHost = ()=>({
  selectSection:section=>shellNavigate(settingsPath(section),{replace:true,state:peachHistory.navigation.location.state}),
  closed:closeSettingsRoute,
  store:preferencesState.settingsStore,
  changed:effect=>preferencesState.settingsEffects[effect]?.(),
  sound:name=>playUiSound(name),
  navCatalog:layoutState.NAV_CATALOG,themeOptions:THEME_OPTIONS,videoLayouts:JAV_LAYOUTS,
  followInitialRanges:FOLLOW_INITIAL_RANGE_OPTIONS,
  videoLayout:()=>cardLayout(),setVideoLayout,
  censored:censorOn,setCensored,
  highContrast:()=>document.documentElement.classList.contains('board-high-contrast'),setHighContrast,
  receipt:message=>receiptsState.actionReceipt(message),
  failure:receiptsState.actionFailure,
  syncRemote:remote=>applySyncedSettings(remote,effect=>preferencesState.settingsEffects[effect]?.()),
  openConfiguration:()=>navigationState.navigatePath('/settings/library'),
  attached:()=>preferencesState.syncGlassOptics(),
  /* 「这台电脑」那一格的判据：服务由托盘管、已完成配置、请求来自本机三条同时成立。 */
  configurable:()=>api('/healthz')
    .then(runtime=>{writeShell({runtimeConfigurable:!!runtime.configurable});return runtimeConfigurable})
    .catch(()=>null),
});
applicationEffects.handler($('#settingsBtn'), 'onclick', ()=>void openSettings());
layoutState.lastManagePageLabel = '';
layoutState.manageHeaderSkeleton = '';
layoutState.manageHeaderRoot = ()=>$('[data-manage-header]');
window.__scrolling=false;
layoutState.scrollT = null;
layoutState.stickyFrame = 0;
layoutState.mobileFilterScroll = null;
layoutState.mobileFilterHost = null;
layoutState.mobileFilterPath = '';
applicationEffects.listen(document, 'focusin', scheduleStickySurfaces);
applicationEffects.listen(document, 'focusout', scheduleStickySurfaces);
applicationEffects.listen(window, 'scroll', ()=>{
  scheduleStickySurfaces();
  if(location.pathname==='/follow'&&followFeedLive())writeShell({followScrollY:scrollY});
  window.__scrolling=true;
  // 滚动中挂起悬停预览：内容从鼠标下滑过会连续触发 mouseenter，
  // 每次新建 video 并发 /stream，几十个并发直接把页面拖垮
  releaseHoverPreviews();
  clearTimeout(layoutState.scrollT); layoutState.scrollT=applicationEffects.delay(()=>{window.__scrolling=false},180);
}, {passive:true});
applicationEffects.listen(window, 'resize', scheduleStickySurfaces, {passive:true});
applicationEffects.handler($('#scrim'), 'onclick', ()=>openDrawer(false));
}
