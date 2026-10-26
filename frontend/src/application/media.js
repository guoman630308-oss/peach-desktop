/* media 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { loadSourceStatus, preferencesState, wireDrag } from './preferences.js';
import { receiptsState } from './receipts.js';
import { followState, followViewPath, openFollowDetail } from './follow.js';
import { immerseApi, loadImmerse, loadStage, stageApi } from '../react/application-residents';
import { cancelQueueRequest, navigationState, openRoutedPage, requestOverlay } from './navigation.js';
import { activeQueue, barsContext, detailOriginAbove, detailOriginAnchor, detailReturnBarsContext, detailReturnNeedsRestore, detailReturnPath, pageOpens, pendingQueueRoute, presentedItem, queueOpens, selectMode, selected, state, writeShell } from './../shell/index';
import { scheduleStickySurfaces } from './layout.js';
import { clearOverlayBackground, holdOverlayBackground, overlayState, takeOverlayReturn } from './../history/overlay';
import { peachHistory, shellNavigate } from './../history/index';
import { $, DURATION_TAGS } from '../core';
import { apiGet } from '../api';
import { javDisplayName, javTitleHtml } from '../core/jav-title';
import { tagLabel } from '../core/tags';
import { pageSkeletonHtml } from './../management-placeholder';
import { buildBars, catalogGridLayout, catalogState, commitContextFilter, goHome, loadCatalog, mixRelated } from './catalog.js';
import { wireDragReorder } from '../ui-kit';
import { currentBarsContext, entityState, openEntity, revealForIsland, syncForIsland } from './entity.js';
import { openAddToPlaylist, rereadPlaylists, saveMixAsPlaylist } from './playlists.js';
import { releaseHoverPreviews } from './../card-art/hover';
import { applicationEffects } from './effects';
export const mediaState = { total: undefined, facets: undefined, stageHost: undefined, stageOpen: undefined, itemDetailHelpers: undefined, itemDetailActions: undefined, immerseHost: undefined, immerseOpen: undefined, requestedImmerseId: undefined };

export function urlResume(){
  const seconds=Number(new URLSearchParams(location.search).get('t'));
  return Number.isFinite(seconds)&&seconds>0?{time:seconds,autoplay:false}:null;
}

export function stageExit(){return stageApi()?.exit()||Promise.resolve()}

export function disposeStage(push=false,preserveInlineOrigin=false,{miniplayer=true,preserveQueueRequest=null}={}){
  if(navigationState.queueOpenRequest!==preserveQueueRequest)cancelQueueRequest();
  stageApi()?.dispose({miniplayer});
  writeShell({activeQueue:null,pendingQueueRoute:null,presentedItem:null});
  if(!preserveInlineOrigin){
    writeShell({detailOriginAnchor:null,detailOriginAbove:false,detailReturnNeedsRestore:false});
  }
  scheduleStickySurfaces();
  if(push)navigationState.route(detailReturnPath||'/');
}

export function repaintDetailPoster(){stageApi()?.repaintPoster()}

export function openQueue(kind,key,itemId,push,anchor=null,options={}){
  key=+key;
  const same=activeQueue?.kind===kind&&(kind==='playlist'?activeQueue.playlistId:activeQueue.seedId)===key;
  /* 同一个队列里换条，来处保持打开队列那一刻的那一页：播放列表关掉回列表页并重读。 */
  if(push&&!same)writeShell({detailReturnPath:location.pathname+location.search});
  /* 队列地址取完数才推（`present`），背景按此刻记；同队列换条在详情地址上，沿用上一条的背景。 */
  if(push)holdOverlayBackground();
  const queue=kind==='playlist'?{kind,playlistId:key,fresh:true}:{kind,seedId:key,fresh:!same};
  if(options.autoplay)queue.autoplay=true;
  if(push){
    cancelQueueRequest();
    const request=navigationState.queueOpenRequest={seq:peachHistory.navigation.seq,cancelled:false};
    navigationState.requestedQueue=()=>openItem(itemId==null?null:+itemId,false,queue,anchor,true,request);
    writeShell({queueOpens:queueOpens+1});
    return;
  }
  return openItem(itemId==null?null:+itemId,false,queue,anchor,push);
}

export function openMix(seedId,itemId=seedId,push=true,anchor=null){return openQueue('mix',seedId,itemId,push,anchor)}

export function openEditions(seedId,itemId=seedId,push=true,anchor=null){return openQueue('editions',seedId,itemId,push,anchor)}

export function openParts(seedId,itemId=seedId,push=true,anchor=null){return openQueue('parts',seedId,itemId,push,anchor)}

export function openPlaylist(playlistId,itemId=null,push=true){return openQueue('playlist',playlistId,itemId,push)}

export function hasReturnSurface(){
  return !!$('#grid').querySelector('[data-id],[data-mix-seed]')
    ||!$('#index').hidden||!$('#stats').hidden;
}

export async function openItem(id,push=true,queue=null,anchor=null,queuePush=false,queueRequest=null){
  if(queueRequest?.cancelled)return;
  if(!queueRequest)cancelQueueRequest();
  if(push&&!queue){
    writeShell({detailReturnPath:location.pathname+location.search});
    requestOverlay('/assets/'+(+id),'item',()=>openItem(id,false,null,anchor));
    return;
  }
  releaseHoverPreviews();
  id=id==null?null:+id;
  const origin=anchor?.isConnected?anchor:(detailOriginAnchor?.isConnected?detailOriginAnchor:null);
  const above=anchor?.isConnected
    ? anchor.getBoundingClientRect().top+anchor.getBoundingClientRect().height/2>window.innerHeight/2
    : detailOriginAbove;
  const returnSurfaceReady=hasReturnSurface();
  const needsReturnRestore=detailReturnNeedsRestore||(!push&&!returnSurfaceReady);
  const returnBars=barsContext.type==='item'?detailReturnBarsContext:entityState.cloneBarsContext(currentBarsContext());
  /* 点进来记当前这一页；后退前进进来按条目记的来处，条目没记（冷启动、深链）就沿用上一次记的。
     队列从详情里换条也以 push=false 进来（`queuePush`），来处已由 `openQueue` 定好，不在这里取。 */
  if(push)writeShell({detailReturnPath:location.pathname+location.search});
  else if(!queuePush)writeShell({detailReturnPath:takeOverlayReturn()||detailReturnPath});
  // 条目的背景另记：从页面点进来记这一页，从详情里点开另一条沿用上一条的背景，不嵌套。
  if(push)holdOverlayBackground();
  /* 换详情不进小窗；小窗里放着别的条目也让位（舞台岛判），两个播放器不同时出声。作品详情开着时
     （队列换卷、相关作品）舞台原地换条，不拆。 */
  if(stageApi()?.showing()!=='item')disposeStage(false,true,{miniplayer:false,preserveQueueRequest:queueRequest});
  writeShell({detailOriginAnchor:origin,detailOriginAbove:above,detailReturnNeedsRestore:needsReturnRestore});
  writeShell({detailReturnBarsContext:returnBars});
  writeShell({activeQueue:queue&&{kind:queue.kind,seedId:queue.seedId,playlistId:queue.playlistId}});
  writeShell({pendingQueueRoute:queue&&queuePush
    ? `${queue.kind}=${queue.kind==='playlist'?queue.playlistId:queue.seedId}`:null});
  if(push&&!queue)navigationState.route('/assets/'+id,false,overlayState('item'));
  const stage=await loadStage(mediaState.stageHost);
  if(queueRequest?.cancelled)return;
  const actions=queueRequest?{...mediaState.itemDetailActions,
    present:(item,queue)=>{if(!queueRequest.cancelled)mediaState.itemDetailActions.present(item,queue)},
    redirect:to=>{if(!queueRequest.cancelled)mediaState.itemDetailActions.redirect(to)},
  }:mediaState.itemDetailActions;
  await stage.open({kind:'item',
    id,queue,relatedLimit:preferencesState.appSettings.relatedLimit>0?+preferencesState.appSettings.relatedLimit:0,
    helpers:mediaState.itemDetailHelpers,actions,
    grid:{helpers:catalogState.gridHelpers,actions:catalogState.gridActions},
    layout:catalogGridLayout(),selectMode,selected:new Set(selected),seekSeconds:preferencesState.appSettings.seekSeconds,
    resume:push||id==null?null:urlResume(),
  });
  scheduleStickySurfaces();
}

export async function closeItemDetail(){
  const restore=entityState.cloneBarsContext(detailReturnBarsContext);
  const returnPath=detailReturnPath||'/',restoreSurface=detailReturnNeedsRestore;
  await stageExit();
  disposeStage(false,false,{miniplayer:false});writeShell({detailReturnBarsContext:null});clearOverlayBackground();
  writeShell({barsContext:restore||{type:'home',filters:state}});
  /* 下面没有那一屏、要照地址重建时，管理区不认领写地址、领一个页面打开序号重开，其余认领写地址后要路由树从头重开。 */
  if(restoreSurface&&navigationState.managedPagePath(new URL(returnPath,location.href).pathname)){navigationState.navigatePath(returnPath);return}
  navigationState.route(returnPath);
  if(restoreSurface)writeShell({pageOpens:pageOpens+1});
  // 播放列表页回来就从头重开、取最新的那一份（详情里可能刚加进或移出了一条）。
  else{buildBars();if(location.pathname==='/playlists')writeShell({pageOpens:pageOpens+1})}
}

/** 库外作品在同一个详情舞台打开，来处与关闭行为跟随覆盖条目。 */
export async function openJavMovie(code,push=true){
  if(push){
    writeShell({detailReturnPath:location.pathname+location.search});
    requestOverlay('/works/'+encodeURIComponent(code),'jav',()=>openJavMovie(code,false));
    return;
  }
  const seq=peachHistory.navigation.seq;
  const restore=entityState.cloneBarsContext(currentBarsContext());
  const needsRestore=!hasReturnSurface();
  const returnPath=takeOverlayReturn()||(needsRestore?'/jav?tab=discover':detailReturnPath)||'/jav?tab=discover';
  if(stageApi()?.showing()!=='jav')disposeStage(false,true,{miniplayer:false});
  writeShell({detailReturnPath:returnPath,detailReturnBarsContext:restore,detailReturnNeedsRestore:needsRestore});
  releaseHoverPreviews();
  const [stage,runtime]=await Promise.all([loadStage(mediaState.stageHost),apiGet('/healthz')]);
  if(peachHistory.navigation.seq!==seq)return;
  await stage.open({kind:'jav',code,readOnly:!!runtime.ledger_read_only,
    actions:{close:()=>void closeItemDetail(),navigate:path=>navigationState.navigatePath(path),
      canonicalCode:canonical=>{
        if(peachHistory.navigation.seq!==seq||!canonical||canonical===code)return;
        shellNavigate('/works/'+encodeURIComponent(canonical)+location.search+location.hash,
          {replace:true,state:peachHistory.navigation.location.state});
      },
      openTag:tag=>{commitContextFilter(filters=>{filters.tag=tag});window.scrollTo({top:0,behavior:'smooth'})},
      receipt:message=>receiptsState.toast({text:message})}});
  scheduleStickySurfaces();
}

export async function openTok(startId=null,push=true){
  if(push){mediaState.requestedImmerseId=startId;navigationState.route('/immerse');return}
  stageApi()?.closeMiniplayer();
  await (await loadImmerse(mediaState.immerseHost)).open(startId);
}

export function installMedia10() {
mediaState.total = 0;
mediaState.facets = null;
mediaState.stageHost = {
  player:{
    settings:()=>preferencesState.appSettings,saveSettings:()=>preferencesState.saveSettings(),
    toast:(text,options)=>receiptsState.toast({text},options),
    loadSourceStatus:()=>loadSourceStatus(),offlineReason:key=>preferencesState.offlineReason(key),
  },
  sourceOffline:key=>preferencesState.sourceOffline(key),
  /* 小窗里的「展开」：同一个播放器搬回这一条的详情，地址与来处照点卡片进来的那一条走。 */
  expand:(kind,id,mediaIndex)=>{if(kind==='follow')void openFollowDetail(id,true,mediaIndex);else void openItem(id,true)},
  openItem:id=>void openItem(id),
};
mediaState.stageOpen = ()=>!!stageApi()?.isOpen();
}

export function installMedia21() {
mediaState.itemDetailHelpers = {
  badgeHtml:(location,cost,cls)=>preferencesState.srcBadge(location,cost,cls),
  titleHtml:it=>javTitleHtml(it),
  displayName:it=>javDisplayName(it),
  javImage:()=>preferencesState.appSettings.javImage,
  tagLabel:tag=>tagLabel(tag),
  isDurationTag:tag=>DURATION_TAGS.has(tag),
  tagCandidates:()=>(mediaState.facets&&mediaState.facets.tags)||[],
  sourceOffline:key=>preferencesState.sourceOffline(key),
  offlineReason:key=>preferencesState.offlineReason(key),
  relatedSkeletonHtml:()=>pageSkeletonHtml('正在读取推荐',{cards:true,className:'related-skeleton'}),
  mixRelated:seedId=>mixRelated(seedId),
  wireDrag:el=>wireDrag(el),
  wireDragReorder:(root,options)=>wireDragReorder(root,options),
};
mediaState.itemDetailActions = {
  close:()=>closeItemDetail(),
  /* 顶栏的实体上下文跟着画出来的这一条走；队列的地址也在这时推，停在哪一条要等岛定下来。 */
  present:item=>{
    writeShell({presentedItem:item});
    const returnBars=detailReturnBarsContext;
    writeShell({barsContext:{type:'item',id:item.id,filters:returnBars?.type==='entity'
      ? {...returnBars.filters}:entityState.emptyEntityFilters()}});
    if(pendingQueueRoute){navigationState.queueOpenRequest=null;navigationState.route(`/assets/${item.id}?${pendingQueueRoute}`,false,overlayState('item'));writeShell({pendingQueueRoute:null})}
    buildBars();
  },
  /* 取数时发现要换去别处：保存过的在线作品转关注详情，队列取不到退回普通详情，播放列表空了
     回列表页，条目已不在就收起舞台。壳一换舞台，岛这一次挂载就作废。 */
  redirect:to=>{
    const push=!!pendingQueueRoute;
    if(to.kind==='follow'){
      navigationState.queueOpenRequest=null;
      writeShell({pendingQueueRoute:null,followDetailReturnPath:detailReturnPath||'/'});
      navigationState.route(`/posts/${to.id}`,!push,overlayState('follow'));
      void openFollowDetail(to.id,false,null,true);return;
    }
    if(to.kind==='item'){void openItem(to.id,true);return}
    // 队列地址还没推、人就停在播放列表页上：只重读那一页。
    if(to.kind==='playlists'){if(push||location.pathname!=='/playlists')openRoutedPage('/playlists');else rereadPlaylists();return}
    disposeStage(false);
  },
  openQueueItem:(queue,id,push=true)=>void openQueue(queue.kind,queue.kind==='playlist'?queue.playlistId:queue.seedId,id,push,null,{autoplay:queue.autoplay}),
  // 盘回来了就按正常路径重开，不在半路挂播放器；开着的队列跟着留下。
  reopen:()=>{
    const it=presentedItem;
    if(!it)return;
    const queue=activeQueue;
    if(queue)void openQueue(queue.kind,queue.kind==='playlist'?queue.playlistId:queue.seedId,it.id,false);
    else void openItem(it.id,false);
  },
  checkSource:async key=>(await loadSourceStatus())[key]!==false,
  /* 直接进「已保存」这一档。关注页的筛选照地址推导，所以状态要先写进地址，光设全局会被推回未看。 */
  openSavedFollow:()=>{
    followState.followAuthors=new Set();followState.followProviders=new Set();followState.followTags=new Set();followState.followWorks=new Set();followState.followMediaView='videos';
    followState.followDurMin=followState.followDurMax=0;followState.followFilter='saved';openRoutedPage(followViewPath())},
  openEntity:(kind,name)=>openEntity(kind,name),
  openUnowned:()=>goHome({owner:'none'},true),
  openRegion:region=>goHome({region:region||'none'},true),
  openTag:tag=>{commitContextFilter(filters=>{filters.tag=tag});window.scrollTo({top:0,behavior:'smooth'})},
  addToPlaylist:item=>openAddToPlaylist(item),
  saveMix:options=>saveMixAsPlaylist(options),
  editPlaylist:()=>openRoutedPage('/playlists'),
  openPlaylists:()=>openRoutedPage('/playlists'),
  reveal:id=>revealForIsland(id),
  sync:id=>syncForIsland(id),
  /* 「垃圾文件」那一档（`state=ads`）按回收站状态列：移进回收站的这一条要从列表里消失，撤销
     任何一次反馈之后列表也重读一遍。别的列表不受影响。 */
  trashChanged:async(disposal,undo)=>{
    if(state.state!=='ads')return;
    if(!undo&&disposal!=='trash')return;
    if(!undo){clearOverlayBackground();disposeStage(true,false,{miniplayer:false});}
    await loadCatalog();
  },
  toast:(message,{undo}={})=>receiptsState.actionReceipt(message,{undo}),
  failure:(action,error)=>receiptsState.actionFailure(action,error),
};
mediaState.immerseHost = {
  filters:()=>state,
  seekSeconds:()=>preferencesState.appSettings.seekSeconds,
  sourceOffline:key=>preferencesState.sourceOffline(key),
  displayName:it=>javDisplayName(it),
  /* 每换一条用 replace 写地址：每划一下都往历史里塞一条，后退键就废了。 */
  route:id=>navigationState.route('/immerse?id='+id,true),
  closed:()=>goHome(),
  openItem:id=>void openItem(id),
  openEntity:(kind,name)=>openEntity(kind,name),
  openUnowned:()=>goHome({owner:'none'},true),
  toast:(message,{undo}={})=>receiptsState.actionReceipt(message,{undo}),
  warn:message=>receiptsState.toast({text:message},{sound:'warning'}),
  failure:(action,error)=>receiptsState.actionFailure(action,error),
};
mediaState.immerseOpen = ()=>!!immerseApi()?.isOpen();
applicationEffects.handler($('#immerseBtn'), 'onclick', ()=>openTok());
}
