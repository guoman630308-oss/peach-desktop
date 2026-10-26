/* preferences 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { loadMediaSources } from './../query/media-sources';
import { buildBars, dropOfflineFromDefaultLoc, homePath, loadCatalog, paintCatalogFilter, repaintCatalogGrid } from './catalog.js';
import { appSettingsStore } from './../appearance/settings';
import { playUiSound, setUiSoundsEnabled, wireUiSounds } from '../ui-kit/sounds';
import { applyTheme, watchSystemTheme } from './../appearance/theme';
import { applyAccent, applyGlassFaces, applyHomeGlow, paintHomeGlowNow } from './../appearance/glow';
import { navigationState } from './navigation.js';
import { $, LOC, esc, icon, isCatalogPath } from '../core';
import { peachHistory, shellNavigate } from './../history/index';
import { notifyShell, selectMode, selected, state } from './../shell/index';
import { preferredDirection } from './../sort-preferences';
import { releaseHoverPreviews, setHoverState } from './../card-art/hover';
import { reloadCurrentSurface } from './layout.js';
import { refreshFeedRows, syncFeedAutoScroll } from './feed.js';
import { stageApi } from '../react/application-residents';
import { syncJavImages } from './../jav-artwork';
import { coverAnchor } from './../card-art/framing';
import { mediaState, repaintDetailPoster } from './media.js';
import { updateManagedRoute } from './../history/managed';
import { MEDIA_SOURCE_ICONS, emptyStateHtml, wireHorizontalScroller } from '../ui-kit';
import { tagLabel } from '../core/tags';
import { rereadPlaylists } from './playlists.js';
import { receiptsState } from './receipts.js';
import { dropBars } from './../catalog-bars';
import { applicationEffects } from './effects';
export const preferencesState = { sourceOnline: undefined, sourceProblems: undefined, sourceOffline: undefined, OFFLINE_HINT: undefined, OFFLINE_REASON: undefined, offlineReason: undefined, settingsStore: undefined, appSettings: undefined, saveSettings: undefined, catalogLoadWaiters: undefined, nextCatalogLoad: undefined, handOff: undefined, settingsEffects: undefined, syncGlassOptics: undefined, placeSidebarHead: undefined, SRCICON: undefined, srcBadge: undefined, emptyState: undefined, CENSOR_KEY: undefined };

export async function loadSourceStatus(){
  const effects=applicationEffects;
  try{
    const d=await loadMediaSources(effects.signal);
    if(!effects.isActive)return preferencesState.sourceOnline;
    preferencesState.sourceOnline=Object.fromEntries((d.sources||[]).map(s=>[s.location,s.online]));
    preferencesState.sourceProblems=Object.fromEntries((d.sources||[]).map(s=>[s.location,s.message||'']));
  }catch{if(!effects.isActive)return preferencesState.sourceOnline;preferencesState.sourceOnline={};preferencesState.sourceProblems={}}
  document.body.classList.toggle('offline-source',Object.values(preferencesState.sourceOnline).includes(false));
  dropOfflineFromDefaultLoc();
  return preferencesState.sourceOnline;
}

export function replaceCatalogAddress(){
  const path=navigationState.surfacePath();
  if(!isCatalogPath(path)||path==='/junk-files')return loadCatalog();
  const loading=preferencesState.nextCatalogLoad();
  shellNavigate(homePath(),{replace:true,state:peachHistory.navigation.location.state});
  return loading;
}

export function sidebarDot(loc){
  if(loc==='local')return {kind:'glyph',name:'hard-drive'};
  if(loc==='online')return {kind:'glyph',name:'rss'};
  const mark=MEDIA_SOURCE_ICONS[loc];
  if(mark)return mark.startsWith('data:')?{kind:'image',src:mark}:{kind:'glyph',name:mark};
  // 已登记的计费来源使用自己的标识。
  return {kind:'cost',cost:'free'};
}

export function sidebarFacets(facetData,context){
  const rows=(items,named=row=>row.label||tagLabel(row.k))=>(items||[]).map(row=>({value:String(row.k),label:named(row),n:row.n??null}));
  const creators=context.type==='entity'&&context.kind==='creator'
    ?(facetData.creators||[]).filter(row=>row.k!==context.name):facetData.creators;
  return {
    locations:(facetData.locations||[]).map(row=>({value:row.k,label:LOC[row.k]||row.k,n:row.n??null,dot:sidebarDot(row.k),
      ...(preferencesState.sourceOffline(row.k)?{offline:preferencesState.OFFLINE_HINT}:{})})),
    regions:rows(facetData.regions),orientations:rows(facetData.orientations),
    creators:rows(creators),tags:rows(facetData.tags),tech:rows(facetData.tech),
    followTags:rows(facetData.follow_tags,row=>tagLabel(row.k)),
    duration:!!facetData.stats?.duration,
  };
}

export async function refreshAll(automatic=false){
  if(automatic&&(document.hidden||!isCatalogPath(decodeURIComponent(location.pathname))||
      mediaState.stageOpen()||mediaState.immerseOpen()||selectMode||selected.size||document.activeElement===$('#q')))return false;
  if(!$('#stats').hidden){
    /* 管理区的换批行为写在路由元数据的 `refresh` 上：`reopen` 重开自己，
       `skip` 不参与（追更页重画要联网，只能由它自己的按钮触发），
       没写的（统计、数据管理、资源同步）落到统计页。播放列表页画着就只重读（`rereadPlaylists`）；别的页原地
       改写一次不认领的地址，领一个新的页面打开序号，页面元素换一次 key 重开，历史条数不变。 */
    const path=decodeURIComponent(location.pathname),refresh=navigationState.routeMeta(path)?.route.refresh;
    if(refresh==='skip')return;
    if(path==='/playlists'){rereadPlaylists();return}
    const target=refresh==='reopen'?path:'/stats';
    if(target===path)navigationState.navigatePath(location.pathname+location.search+location.hash,true,peachHistory.navigation.location.state);
    else navigationState.navigatePath(target);
    return;
  }
  if(!$('#index').hidden){return}
  state.sort='seed';state.dir='';state.seed=receiptsState.rollSeed();notifyShell();
  // 顶部三层（女优头像、厂牌、标签）有 30 秒会话缓存，而 refreshAll 只重载网格：
  // 不清掉这两个缓存，「换一批」之后上面还是同一批人。
  dropBars();
  /* 网格和顶部三层一起换，两边耗时不一样，所以转圈归这一层管：换批键转到这个 Promise
     落定，网格先到时标签条还在等的那段时间里它不停。顶部三层与标签条不铺骨架——它们此刻
     有内容在屏幕上，撕成灰条再填回去比直接换掉更晃眼；只铺一层微光，骨架留给从无到有的首屏。 */
  paintCatalogFilter({refreshing:true});
  /* 目录元素在改写地址排下的那个微任务里发起取数；让过这一拍，网格先认领表面、铺好等待态，顶部三层再取。 */
  try{const loading=replaceCatalogAddress();await null;await Promise.all([loading,buildBars()])}
  finally{paintCatalogFilter({refreshing:false})}
  if(!automatic)window.scrollTo({top:0,behavior:'smooth'});
  return true;
}

export function censorOn(){return document.body.classList.contains('censor')}

export function applyCensor(on){
  document.body.classList.toggle('censor',on);
  preferencesState.settingsStore.notify();
}

export function setCensored(on){
  localStorage.setItem(preferencesState.CENSOR_KEY,on?'1':'0');
  applyCensor(on);
  if(on)releaseHoverPreviews();
}

export function setHighContrast(on){
  localStorage.setItem('peach.high-contrast',String(on));
  document.documentElement.classList.toggle('board-high-contrast',on);
  preferencesState.settingsStore.notify();
}

export function wireDrag(el){return wireHorizontalScroller(el,{drag:true})}

export function wireAllDrag(){['#nrow','#count'].forEach(s=>wireDrag($(s)));
  document.querySelectorAll('.tier,.srow').forEach(wireDrag)}

export function immerseStartId(){
  const id=new URLSearchParams(location.search).get('id');
  return /^\d+$/.test(id||'')?Number(id):undefined;
}

export function installPreferences5() {
preferencesState.sourceOnline = {};
preferencesState.sourceProblems = {};
preferencesState.sourceOffline = key=>preferencesState.sourceOnline[key]===false;
preferencesState.OFFLINE_HINT = '媒体目录无法访问，请检查目录、读取权限和网盘挂载状态';
preferencesState.OFFLINE_REASON = {local:'无法读取媒体目录，请检查目录是否存在及读取权限。',
  '115':'无法读取 115 媒体目录，请检查目录权限及网盘挂载工具是否正常运行。',
  pikpak:'无法读取 PikPak 媒体目录，请检查目录权限及网盘挂载工具是否正常运行。'};
preferencesState.offlineReason = key=>preferencesState.sourceProblems[key]||preferencesState.OFFLINE_REASON[key]||'无法读取这个来源的媒体目录，请检查目录权限及挂载状态。';
preferencesState.settingsStore = appSettingsStore();
preferencesState.appSettings = preferencesState.settingsStore.value;
preferencesState.saveSettings = ()=>preferencesState.settingsStore.save();
document.documentElement.style.setProperty('--hover-delay',`${preferencesState.appSettings.hoverDelaySeconds}s`);
setUiSoundsEnabled(preferencesState.appSettings.uiSounds);
wireUiSounds();
applyTheme();
watchSystemTheme();
paintHomeGlowNow();
applyGlassFaces();
applyAccent();
preferencesState.catalogLoadWaiters = [];
preferencesState.nextCatalogLoad = ()=>new Promise(resolve=>preferencesState.catalogLoadWaiters.push(resolve));
preferencesState.handOff = loading=>{preferencesState.catalogLoadWaiters.splice(0).forEach(resolve=>resolve(loading));return loading};
preferencesState.settingsEffects = {
  theme:()=>applyTheme(),
  /* 开这一格时立刻响一声开关音，人才知道它开了。关的那一声由 document 上的 change 监听
     发出：捕获阶段排在这里前面，那时开关还没关掉，最后一声还能响出来。 */
  uiSounds:()=>{setUiSoundsEnabled(preferencesState.appSettings.uiSounds);if(preferencesState.appSettings.uiSounds)playUiSound('toggle-on')},
  glowFrame:()=>applyHomeGlow(),
  glow:()=>{applyHomeGlow();applyGlassFaces()},
  batchSize:()=>{if(location.pathname==='/')loadCatalog()},
  defaultSort:()=>{
    state.sort=preferencesState.appSettings.defaultSort;
    state.dir=preferredDirection(state.sort,preferencesState.appSettings.defaultSort,preferencesState.appSettings.defaultSortDirection);
    notifyShell();
    if(location.pathname==='/')replaceCatalogAddress();
  },
  sortDirection:()=>{
    state.dir=preferredDirection(state.sort,preferencesState.appSettings.defaultSort,preferencesState.appSettings.defaultSortDirection);
    notifyShell();
    if(location.pathname==='/')replaceCatalogAddress();
  },
  hoverDelay:()=>{
    if(!preferencesState.appSettings.hoverDelaySeconds)document.querySelectorAll('[data-previewing],[data-longhover]')
      .forEach(el=>{setHoverState(el,'previewing',false);setHoverState(el,'longhover',false)});
    document.documentElement.style.setProperty('--hover-delay',`${preferencesState.appSettings.hoverDelaySeconds}s`);
  },
  seekSeconds:()=>repaintCatalogGrid(),
  /* 折叠是在渲染时做的，不重取的话已经被跳过的那些卡不会自己冒出来。 */
  groupCollapse:()=>reloadCurrentSurface(),
  feedAutoScroll:()=>syncFeedAutoScroll(),
  feedCompilations:()=>refreshFeedRows(),
  /* 设置说的是「离开详情不再进小窗」，已经开着的那个小窗留着反而像没生效。 */
  miniplayer:()=>{if(!preferencesState.appSettings.miniplayer)stageApi()?.closeMiniplayer()},
  javImage:()=>{
    syncJavImages(document,preferencesState.appSettings.javImage);repaintCatalogGrid();
    document.querySelectorAll('img[data-jav-image].cover').forEach(coverAnchor);
    repaintDetailPoster();
  },
  searchHistoryLimit:()=>updateManagedRoute($('#searchMenu'),{historyLimit:preferencesState.appSettings.searchHistoryLimit}),
};
preferencesState.syncGlassOptics = ()=>{};
preferencesState.placeSidebarHead = ()=>{};
preferencesState.SRCICON = {
  ...Object.fromEntries(Object.entries(MEDIA_SOURCE_ICONS).map(([location,mark])=>
    [location,mark.startsWith('data:')?`<img class="source-icon" src="${mark}" alt="">`:icon(mark)])),
  online:icon('rss'),
};
preferencesState.srcBadge = (loc,cost,cls)=>{const label=`${LOC[loc]||loc}${cost==='metered'?' · 计费':''}`;
  return `<span class="${cls||'src'} ${cost==='metered'?'metered':'free'}" title="${esc(label)}" aria-label="${esc(label)}">`
    +(preferencesState.SRCICON[loc]||'')+'</span>'};
preferencesState.emptyState = emptyStateHtml;
}

export function installPreferences23() {
preferencesState.CENSOR_KEY = 'peach-censor';
applyCensor(localStorage.getItem(preferencesState.CENSOR_KEY)==='1');
}
