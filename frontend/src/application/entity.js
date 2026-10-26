import { createRevealSourceAction } from './source-action';
import { emptyEntityFilters, parseEntityFilters, entityFilterSearch, parseMediaView, entityViewSearch } from '../react/entity-page/entity-route';
import { photoViewActive as hasPhotoView } from '../react/entity-page/photo-surface';
import { createEntityApplication } from '../react/entity-page/entity-application';
import { waitEntityShapes } from './loading.js';
/* entity 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { catalogGridLayout, catalogSkeletonHtml, catalogState, comboItems, commitContextFilter, toggleTag } from './catalog.js';
import { nextSortState, sortDirWord } from './../sort-preferences';
import { barsContext, notifyShell, selectMode, selected, state, writeShell } from './../shell/index';
import { $, ENTITY_ROUTES, api, esc, icon } from '../core';
import { managedEntry, releaseManagedRoute, updateManagedRoute } from './../history/managed';
import { javActive, scheduleStickySurfaces, setJavLayout, sortOptions } from './layout.js';
import { navigationState, openRoutedPage } from './navigation.js';
import { releaseHoverPreviews } from './../card-art/hover';
import { preferencesState, wireDrag } from './preferences.js';
import { fitSkeleton, formModal, indexSkeletonHtml, wireHorizontalScroller } from '../ui-kit';
import { feedRowHtml, feedState, loadEntityShapes, wireFeedNewRow } from './feed.js';
import { receiptsState } from './receipts.js';
import { JAV_LAYOUTS, PHOTO_LAYOUTS, javLayout, photoLayout, photoSize, storePhotoLayout, storePhotoSize } from './../appearance/layout';
import { dropBars } from './../catalog-bars';
import { indexState, openFollowAuthorFromIndex } from './index.js';
import { followState, pushFollowFeed } from './follow.js';
import { applyDensity, paintPhotoSizeButton } from './../appearance/density';
import { applicationEffects } from './effects';
import { entitySkeletonHtml } from './../entity-skeleton';
import { INDEX_TITLES } from './../index-skeleton';
import { filterControlsSkeletonHtml } from './../filter-controls-skeleton';
import { cardRatio, gridLayout } from './../appearance/layout';
import { pageSkeletonHtml } from './../management-placeholder';
import { islandShortcutsEntry } from './../island-skeleton';
export const entityState = { ENTITY_FILTER_KEYS: undefined, emptyEntityFilters: undefined, parseEntityFilters: undefined, entityFilterSearch: undefined, cloneBarsContext: undefined, activeFilterState: undefined, entityPageView: undefined, entityPageWall: undefined, entityPageRevision: undefined, entityPageCurrent: undefined, entityPageLive: undefined, EMPTY_ENTITY_MEDIA: undefined, parseMediaView: undefined, entityViewSearch: undefined, entityBodyCanLoadMore: undefined, entityBodySkeleton: undefined, entityPageHelpers: undefined, entityFollowActions: undefined, SOURCE_HINTS: undefined, sourceHint: undefined, syncedText: undefined, sourceToolButtons: undefined, application: undefined };

export function entityPageEntry(){
  const entry=managedEntry($('#index'));
  return entry&&entry.path.endsWith('/*')&&entry.host.isConnected?entry:null;
}

export function pushEntityPage(patch){if(entityState.entityPageCurrent())updateManagedRoute($('#index'),patch)}

export function currentBarsContext(){
  if(barsContext.type==='item')return barsContext;
  const entry=entityPageEntry();
  if(entry){const {kind,name,filters}=entry.props;return {type:'entity',kind,name,filters:{...filters}}}
  return barsContext;
}

export function sortKeys(current,dir,jav=javActive()){
  return sortOptions(jav).map(([key,label])=>{
    const pressed=current===key,next=nextSortState(key,current,dir);
    return {key,label,pressed,dir:pressed&&sortDirWord(key,dir)?(dir==='asc'?'asc':'desc'):'',
      ariaLabel:next?`按${label}${next.dir?sortDirWord(next.sort,next.dir):''}排序`:''}});
}

export function routeEntityPage(kind,name,filters,media=entityState.EMPTY_ENTITY_MEDIA,options={}){
  return entityState.application.route(kind,name,filters,media,options);
}

export function entityPageActions(kind,name){return entityState.application.actions(kind,name)}

export function entityPageProps(kind,name,filters,media){return entityState.application.props(kind,name,filters,media)}

export function photoViewActive(){return hasPhotoView({entityWall:entityState.entityPageWall,
    entityCurrent:entityState.entityPageCurrent(),indexVisible:!$('#index').hidden,pathname:location.pathname,
    followMedia:followState.followMediaView,statsVisible:!$('#stats').hidden},
    ()=>[...document.querySelectorAll('.followphotowall')].some(wall=>wall.getClientRects().length>0))}

export function syncPhotoWalls(){entityState.photos.sync()}

export function syncDensityControl(){
  if(photoViewActive())paintPhotoSizeButton(photoSize());else applyDensity();
}

export function setPhotoSize(value){entityState.photos.setSize(value)}

export const revealSource = createRevealSourceAction({
  request:id=>api('/api/reveal',{method:'POST',body:JSON.stringify({id})}),
  success:()=>receiptsState.toast({text:'已在资源管理器中显示'}),
  failureText:error=>entityState.sourceHint(error instanceof Error?error.message:String(error)),
});

export async function revealForIsland(id){const status={textContent:''};await revealSource(id,status);return status.textContent}

export async function syncForIsland(id){
  const status={textContent:''};let removed=[];
  await syncMissing(id,status,result=>{if(result.items)removed=result.items.map(item=>item.id)});
  return {text:status.textContent,removed};
}

export async function syncMissing(id,status,done){
  status.textContent='正在核对目录…';
  try{
    const r=await api('/api/purge-missing',{method:'POST',body:JSON.stringify({id})});
    if(r.ok===false){status.textContent=entityState.sourceHint(r.error);return}
    status.textContent=r.removed
      ? `${entityState.syncedText(r)}（核对 ${r.checked} 项${r.unreadable?`，${r.unreadable} 项未能读取`:''}）`
      : r.unreadable
        ? `目录有 ${r.unreadable} 项暂时无法读取，本次未改动`
        : `目录内 ${r.checked} 项都还在，无需改动`;
    if(r.removed){
      const ids=(r.items||[]).filter(item=>item.disposal!=='reattached').map(item=>item.id);
      if(done)done(r);
      receiptsState.actionReceipt(entityState.syncedText(r),{undo:ids.length?async()=>{
        await api('/api/batch',{method:'POST',body:JSON.stringify({ids,operation:'restore'})});
        if(done)done({removed:0,restored:ids.length});
      }:null});
    }else receiptsState.actionReceipt('目录核对完成，无需改动');
  }catch(e){status.textContent=entityState.sourceHint(e.message);receiptsState.actionFailure('核对目录',e)}
}

export function sourceTools(id){return `<div class="srctools">${entityState.sourceToolButtons(id)}
    <span class="srcstate" aria-live="polite"></span></div>`}

export function wireSourceTools(root,done){
  const status=root.querySelector('.srcstate');
  if(!status)return;
  const reveal=root.querySelector('[data-reveal]');
  const sync=root.querySelector('[data-sync]');
  if(reveal)applicationEffects.handler(reveal, 'onclick', ()=>revealSource(Number(reveal.dataset.reveal),status,{button:reveal}));
  if(sync)applicationEffects.handler(sync, 'onclick', ()=>syncMissing(Number(sync.dataset.sync),status,done));
}

export async function entityAliasForm(mine,write){
  const form=formModal({
    title:'添加别名',
    description:'图库按名字存图，补充名字写法能找到更多备选图片；这里添加的名字也能设为统称。',
    body:`<label class="modalfield"><span>别名</span>
        <input class="geist-input" name="alias" maxlength="80" autocomplete="off"
          placeholder="另一种写法，或另一个艺名"></label>`
      +(mine.length?`<div class="modalfield"><span>自己添过的</span>
        <div class="aliaschips">${mine.map(one=>`<span class="aliaschip">${esc(one)}
          <button type="button" data-alias-drop="${esc(one)}"
            aria-label="撤销别名 ${esc(one)}">${icon('x')}</button></span>`).join('')}</div></div>`:''),
    confirmLabel:'添加别名',
    confirmDisabled:true,
    onConfirm:()=>write({alias:field.value.trim()})});
  const field=form.dialog.querySelector('[name=alias]');
  applicationEffects.handler(field, 'oninput', ()=>{form.confirmButton.disabled=!field.value.trim()});
  /* 撤销只认自己添的那几个：刮削和合并留下的别名是这条实体当初被认成这个人的依据，
     一次点击删不得。服务端按来源守这条线，这里只列它报回来的那几个。 */
  form.dialog.querySelectorAll('[data-alias-drop]').forEach(chip=>applicationEffects.handler(chip, 'onclick', async()=>{
    const gone=chip.dataset.aliasDrop;
    form.close();
    try{await write({alias:gone,remove:true})}
    catch(error){receiptsState.actionFailure('撤销别名',error);return}
    receiptsState.actionReceipt(`已撤销别名 ${gone}`,{undo:async()=>{await write({alias:gone})}});
  }));
  const {confirmed,result}=await form.done;
  if(!confirmed)return;
  if(result?.added)receiptsState.actionReceipt(`已添加别名 ${result.alias}`,{undo:async()=>{
    await write({alias:result.alias,remove:true})}});
  else receiptsState.actionReceipt(`${result?.alias} 已经是这份档案中的名字`);
}

export function showEntityLoading(kind,name){
  const jav=['performer','studio'].includes(kind)&&feedState.hasEntityPart(kind,name,'jav');
  const head=`<div data-filter-row="bottom" class="flex min-w-0 items-center gap-3 px-3 py-2 min-h-12"><h3 data-entity-readout><span data-skeleton="count" class="relative inline-block h-3.5 w-24 rounded-md align-middle skeleton-sheen"></span></h3>${islandShortcutsEntry()}${kind==='agency'?'':filterControlsSkeletonHtml(sortKeys(state.sort,state.dir,jav),jav?{name:'jav-layout',label:'JAV 卡片版式',value:javLayout(),options:JAV_LAYOUTS}:null)}</div>`;
  const top=kind==='agency'?'':`<div data-entity-media><button type="button" data-media-view="videos" aria-label="视频" aria-pressed="true" disabled data-skeleton-action>${icon('play')}</button><button type="button" data-media-view="photos" aria-label="照片" disabled data-skeleton-action>${icon('pics')}</button></div><span data-entity-sep></span><div data-entity-scroll><div data-entity-states>${catalogState.catalogViews().map(view=>`<button type="button" data-entity-state="${esc(view.k)}" aria-pressed="${state.state===view.k}" disabled data-skeleton-action>${esc(view.label)}</button>`).join('')}</div>`;
  const body=kind==='agency'
    ?indexSkeletonHtml({kind:'performers',layout:indexState.peopleIndexLayout()})
    :pageSkeletonHtml('正在读取作品',{cards:true,className:'catalog-skeleton postercard-skeleton',cardRatio:cardRatio(gridLayout({active:jav,home:false,portrait:false}))});
  const placeholder=entitySkeletonHtml(kind,head,body,top);
  if($('#index').firstElementChild?.dataset.skeleton!==`entity/${kind}`){
    $('#index').innerHTML=placeholder;
    syncEntitySkeletonParts(kind,name);
    fitSkeleton($('#index'));
  }else syncEntitySkeletonParts(kind,name);
}

export function syncEntitySkeletonParts(kind,name){
  const skeleton=$('#index').firstElementChild;
  if(skeleton?.dataset.skeleton!==`entity/${kind}`)return;
  const sync=(present,wanted,insert)=>{if(wanted&&!present)insert();else if(!wanted&&present)present.remove()};
  sync(skeleton.querySelector('.entityfoot'),kind!=='agency'&&feedState.hasEntityPart(kind,name,'costars'),
    ()=>skeleton.querySelector('.entityhero')?.insertAdjacentHTML('beforeend',feedState.costarSkeletonFoot()));
  sync(skeleton.querySelector('.feednew'),feedState.hasEntityPart(kind,name,'feed'),
    ()=>skeleton.querySelector('.entitysection')?.insertAdjacentHTML('beforebegin',feedState.feedNewSkeletonSection()));
  sync(skeleton.querySelector('.entityfacts'),kind==='performer'&&feedState.hasEntityPart(kind,name,'facts'),
    ()=>skeleton.querySelector('.entityprofile')?.insertAdjacentHTML('beforeend',feedState.factsSkeleton()));
}

export function showEntityMissing(kind){
  const index=ENTITY_ROUTES[kind]||kind,title=INDEX_TITLES[index]||'条目';
  const actions=INDEX_TITLES[index]?`<a class="geist-button primary" href="/${index}">返回${title}列表</a>`:'';
  $('#index').innerHTML=preferencesState.emptyState('search-x',`找不到这个${title}`,'名字可能拼错了，或者已经合并到别的名字下；回列表里重新找。',{actions});
}

export function openEntity(kind,name){openRoutedPage(entityState.application.fresh(kind,name))}

export function installEntity9() {
entityState.ENTITY_FILTER_KEYS = ['loc','creator','tag','state','dur_min','dur_max','orient','sort','dir'];
entityState.emptyEntityFilters = emptyEntityFilters;
entityState.parseEntityFilters = search=>parseEntityFilters(search,{sort:preferencesState.appSettings.defaultSort,dir:preferencesState.appSettings.defaultSortDirection});
entityState.entityFilterSearch = entityFilterSearch;
entityState.cloneBarsContext = context=>context&&context.type==='entity'
  ? {...context,filters:{...context.filters}}:context;
entityState.activeFilterState = ()=>{const context=currentBarsContext();return context.type==='home'?state:context.filters};
$('#q').value=state.q;
catalogState.rememberSearchValue();
}

export function installEntity17() {
entityState.entityPageView = '';
entityState.entityPageWall = false;
entityState.entityPageRevision = 0;
entityState.entityPageCurrent = ()=>!!entityPageEntry();
entityState.entityPageLive = (kind,name)=>{const entry=entityPageEntry();
  return !!entry&&!$('#index').hidden&&entry.props.kind===kind&&entry.props.name===name};
entityState.EMPTY_ENTITY_MEDIA = {media:'videos',set:0};
entityState.parseMediaView = parseMediaView;
entityState.entityViewSearch = entityViewSearch;
entityState.entityBodyCanLoadMore = ()=>!$('#index').hidden&&$('#stats').hidden;
entityState.entityBodySkeleton = ()=>catalogSkeletonHtml();
entityState.entityPageHelpers = {
  wireDrag:row=>{if(row)wireDrag(row)},
  wireScroller:row=>{if(row)wireHorizontalScroller(row)},
  wireFeedRow:row=>wireFeedNewRow(row),
  feedRowHtml,
  receipt:(message,options)=>receiptsState.actionReceipt(message,options),
  failure:(label,error)=>receiptsState.actionFailure(label,error),
  aliasForm:(mine,write)=>entityAliasForm(mine,write),
  sourceToolsHtml:id=>sourceTools(id),
  wireSourceTools:(root,done)=>wireSourceTools(root,done),
  comboItems:filters=>comboItems(filters),
  sortKeys:(sort,dir,jav)=>sortKeys(sort,dir,jav),
};
entityState.entityFollowActions = {...followState.followFeedActions,
  route:()=>{},shuffle:()=>{},loaded:()=>{},toggleSelection:()=>{}};
entityState.SOURCE_HINTS = {
  'source offline':'来源不在线，这一次不对账；接上这个来源后重试',
  'source not mapped':'本机没有映射这个来源的盘符',
  'file missing':'找不到源文件；点击右侧的扫描按钮，重新检查目录并更新数据库记录',
  'unsupported platform':'当前服务端系统不支持直接定位文件',
  'reveal failed':'打开文件管理器失败，请重试',
};
entityState.sourceHint = message=>entityState.SOURCE_HINTS[message]||message;
entityState.syncedText = r=>[r.trashed?`已把 ${r.trashed} 项移入回收站`:'',
  r.vanished?`${r.vanished} 项带个人记录，已标为已消失`:'',
  r.reattached?`${r.reattached} 项的记录已接到库里的另一个版本`:''].filter(Boolean).join('，');
entityState.sourceToolButtons = id=>`
    <button type="button" data-reveal="${id}" title="在文件管理器里打开源文件所在目录"
      aria-label="定位源文件">${icon('folder-open')}</button>
    <button type="button" data-sync="${id}" title="核对该目录：磁盘上已删除的移入 Peach 回收站，带个人记录的标为已消失"
      aria-label="同步删除">${icon('folder-sync')}</button>`;
}

export function connectPhotoController(){
  entityState.application=createEntityApplication({
    controller:{
      context:()=>{const context=currentBarsContext();return context.type==='entity'?context:null},
      live:(kind,name)=>entityState.entityPageLive(kind,name),current:()=>entityState.entityPageCurrent(),
      route:(path,replace=false)=>navigationState.route(path,replace),suffix:()=>location.search+location.hash,
      restoreHomeContext:()=>writeShell({barsContext:{type:'home',filters:state}}),
      releaseBodyPreviews:()=>releaseHoverPreviews($('#index [data-entity-body]')),push:pushEntityPage,
      state:()=>({seed:String(state.seed||''),jav:state.jav==='1'}),
      preferences:()=>({revision:entityState.entityPageRevision,feedRevision:feedState.feedRevision,
        photoSize:photoSize(),photoLayout:photoLayout(),photoLayouts:PHOTO_LAYOUTS,
        followImagesOnly:!!preferencesState.appSettings.followImagesOnly,
        javLayout:javLayout(),javLayouts:JAV_LAYOUTS,states:catalogState.VIEW_PILLS,peopleLayout:indexState.peopleIndexLayout(),
        layout:catalogGridLayout(),selectMode,selected:new Set(selected),seekSeconds:preferencesState.appSettings.seekSeconds,
        groupCollapse:preferencesState.appSettings.groupCollapse,wireDrag,skeletonHtml:entityState.entityBodySkeleton,
        canLoadMore:entityState.entityBodyCanLoadMore,
        follow:{helpers:followState.followFeedHelpers,actions:entityState.entityFollowActions}}),
      changeFilter:commitContextFilter,rollSeed:()=>receiptsState.rollSeed(),
      writeSeed:seed=>{state.seed=seed;notifyShell()},
      setJavLayout,javLayout,setPhotoLayout:storePhotoLayout,syncPhotoWalls,openEntity,photoViewActive,
      recordPainted:(view,wall)=>{entityState.entityPageView=view;entityState.entityPageWall=wall},
      syncDensity:syncDensityControl,scheduleSticky:scheduleStickySurfaces,
      releasePage:()=>releaseManagedRoute($('#index')),showMissing:showEntityMissing,dropBars,
    },
    loading:{
      shapesReady:(kind,name)=>feedState.hasEntityShape(kind,name),waitShapes:waitEntityShapes,paintSkeleton:showEntityLoading,
      resetView:()=>{writeShell({detailReturnBarsContext:null});entityState.entityPageView='';entityState.entityPageWall=false},
      loadShapes:loadEntityShapes,syncParts:syncEntitySkeletonParts,
    },
    photos:{
      preferences:()=>({size:photoSize(),layout:photoLayout(),imagesOnly:!!preferencesState.appSettings.followImagesOnly}),
      walls:()=>document.querySelectorAll('.followphotowall'),pushEntity:pushEntityPage,pushFollow:pushFollowFeed,
      syncDensity:syncDensityControl,storeSize:storePhotoSize,
    },
    helpers:entityState.entityPageHelpers,
    actions:{toggleTag,openFollowAuthor:openFollowAuthorFromIndex,javContext:on=>writeShell({entityJavLayout:!!on})},
    sortPreference:()=>({sort:preferencesState.appSettings.defaultSort,dir:preferencesState.appSettings.defaultSortDirection}),
  });
  entityState.photos=entityState.application.photos;
  entityState.entityPageHelpers=entityState.application.helpers;
}
