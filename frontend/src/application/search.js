/* search 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { javImageKind } from './../jav-artwork';
import { preferencesState } from './preferences.js';
import { coverImage } from './../card-art/markup';
import { catalogState, goHome, homePath } from './catalog.js';
import { disposeStage, mediaState, openItem } from './media.js';
import { followSelected, notifyShell, selectMode, selected, state } from './../shell/index';
import { navigationState } from './navigation.js';
import { openEntity } from './entity.js';
import { catalogSuggestions } from './../catalog-onboarding';
import { searchRecommendations } from '../react/search/recommendations';
import { feedNewCoverHtml } from './feed.js';
import { followState, openFollowDetail } from './follow.js';
import { $, api, esc } from '../core';
import { dismissMenu, glideEase, presentMenu, wireHorizontalScroller } from '../ui-kit';
import { openManagedRoute } from './../history/managed';
import { applicationEffects } from './effects';
import { searchMorphFrames } from './search-morph.js';
import { immerseApi, settingsPanelApi, stageApi } from '../react/application-residents';
import { openDrawer } from './layout.js';
import { setSelectMode } from './selection.js';
import { clickPlayerControl, seekVideoBy, toggleVideoPlayback } from './../player/index';
export const searchState = { searchControl: undefined, searchActions: undefined, searchHelpers: undefined, searchMorph: undefined, searchMorphViewport: undefined };

export function hideSearchMenu(){searchState.searchControl?.close()}

export function searchCoverImage(card){
  const kind=card?javImageKind({...card,is_jav:!!card.code},preferencesState.appSettings.javImage):'';
  return kind==='cover'?coverImage(card,'big')
    :kind?`<img class="poster still" src="/poster?id=${card.id}&c=4" width="640" height="360" alt="" loading="lazy" data-drop="self">`
    :'<span class="nopic">无预览</span>';
}

export function finishSearchMorph(){
  searchState.searchMorph?.cancel();searchState.searchMorph=null;
  $('.search').classList.remove('search-morphing');
}

export function setNarrowSearchOpen(open){
  const search=$('.search'),button=$('#searchBtn');
  if(search.classList.contains('open')===open)return;
  const interrupted=!!searchState.searchMorph;
  const current=search.getBoundingClientRect();
  const currentPadding=parseFloat(getComputedStyle(search).paddingLeft);
  finishSearchMorph();
  search.classList.remove('open');
  const anchor=button.getBoundingClientRect();
  search.classList.toggle('open',open);
  $('#searchBtn').setAttribute('aria-expanded',String(open));
  if(innerWidth>760||matchMedia('(prefers-reduced-motion:reduce)').matches)return;
  const expanded=search.getBoundingClientRect(),css=getComputedStyle(search);
  const padding=parseFloat(css.paddingLeft);
  const iconWidth=search.querySelector('svg').getBoundingClientRect().width;
  const compactPadding=Math.max(0,(anchor.width-iconWidth)/2-parseFloat(css.borderLeftWidth));
  const from=interrupted?current:open?anchor:expanded,to=open?expanded:anchor;
  const ease=glideEase();
  search.classList.add('search-morphing');
  const motion=search.animate(searchMorphFrames(from,to,expanded,
    interrupted?currentPadding:open?compactPadding:padding,open?padding:compactPadding,
    ease.easing,innerWidth),{duration:ease.duration,easing:'linear',fill:'both'});
  searchState.searchMorph=motion;
  applicationEffects.handler(motion, 'onfinish', ()=>{if(searchState.searchMorph===motion)finishSearchMorph()});
}

export function activeVideo(){
  if(mediaState.immerseOpen())return immerseApi().activeVideo();
  return stageApi()?.activeVideo()||null;
}

export function isTypingTarget(el){
  return !!el&&(el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.isContentEditable);
}

export function installSearch20() {
searchState.searchControl = null;
searchState.searchActions = {
  search:query=>{
    catalogState.rememberSearchValue();disposeStage(false);
    state.q=query;notifyShell();navigationState.route(state.jav==='1'?homePath():state.q?'/?q='+encodeURIComponent(state.q):'/',true);
  },
  openItem:id=>openItem(id),
  openEntity:(kind,name)=>openEntity(kind,name),
  openPath:path=>navigationState.route(path,false),
  openFollow:(id,media,index)=>{followState.followMediaView=media;void openFollowDetail(id,true,index)},
};
searchState.searchHelpers = {
  pool:()=>catalogSuggestions(state,api),
  recommendations:()=>searchRecommendations(api),
  /* `.pic` 是卡片封面那一格（比例、底色、圆角与模糊垫底都认它），和里面那张图一起由壳给。 */
  coverHtml:card=>`<span class="pic">${card?.thumb_url
    ?`<img src="${esc(card.thumb_url)}" width="640" height="360" alt="" loading="lazy" referrerpolicy="no-referrer" data-drop="self">`
    :card?.sourceWork?feedNewCoverHtml(card):searchCoverImage(card)}</span>`,
  present:menu=>presentMenu(menu),
  dismiss:menu=>dismissMenu(menu),
  wireScroller:row=>wireHorizontalScroller(row),
  /* 字变了：清空时照着上一刻的字留一段残影，敲进新字就收掉正在散的那段。 */
  typed:input=>{
    const next=input.value;
    if(!next&&catalogState.searchValueSnapshot.text)catalogState.clearSearchField(catalogState.searchValueSnapshot);
    else{
      if(next){catalogState.cancelSearchDissolve();catalogState.cancelSearchDissolve=()=>{}}
      catalogState.rememberSearchValue(input);
    }
  },
  composing:()=>{catalogState.cancelSearchDissolve();catalogState.cancelSearchDissolve=()=>{}},
  clearField:input=>catalogState.clearSearchField({text:input.value,scrollLeft:input.scrollLeft||catalogState.searchValueSnapshot.scrollLeft}),
};
void openManagedRoute('search',{
  input:$('#q'),historyLimit:preferencesState.appSettings.searchHistoryLimit,actions:searchState.searchActions,helpers:searchState.searchHelpers,
  expose:control=>{searchState.searchControl=control},
},{container:$('#searchMenu'),isCurrent:()=>true});
applicationEffects.listen($('#q'), 'beforeinput', e=>{if(!e.isComposing)catalogState.rememberSearchValue(e.currentTarget)});
applicationEffects.listen($('#q'), 'scroll', e=>{if(e.currentTarget.value)catalogState.rememberSearchValue(e.currentTarget)});
applicationEffects.listen($('#q'), 'pointerdown', e=>{if(e.currentTarget.value)catalogState.rememberSearchValue(e.currentTarget)});
}

export function installSearch22() {
searchState.searchMorph = null;
searchState.searchMorphViewport = innerWidth;
applicationEffects.listen.bind(applicationEffects, window)('resize',()=>{
  if(innerWidth===searchState.searchMorphViewport)return;
  searchState.searchMorphViewport=innerWidth;finishSearchMorph();
},{passive:true});
applicationEffects.listen(matchMedia('(prefers-reduced-motion:reduce)'), 'change', finishSearchMorph);
applicationEffects.handler($('#searchBtn'), 'onclick', ()=>{setNarrowSearchOpen(true);$('#q').focus({preventScroll:true})});
applicationEffects.handler($('#searchBack'), 'onclick', ()=>{
  setNarrowSearchOpen(false);
  hideSearchMenu();
  $('#q').blur();
  $('#searchBtn').focus({preventScroll:true});
});
const dismissOutsideSearch=event=>{
  if(event.target.closest('.search'))return;
  // 输入法收起只把焦点交回页面；建议仍可继续选择。
  if(event.type==='focusin'&&(event.target===document.body||event.target===document.documentElement))return;
  if(!$('#q').value)setNarrowSearchOpen(false);
  hideSearchMenu();
};
applicationEffects.listen(document, 'pointerdown', dismissOutsideSearch, true);
applicationEffects.listen(document, 'focusin', dismissOutsideSearch);
applicationEffects.handler($('#brandHome'), 'onclick', e=>{e.preventDefault();goHome(null,true)});
applicationEffects.listen(document, 'keydown', e=>{
  if(e.key==='Escape'){
    const settings=settingsPanelApi();
    if(settings?.isOpen()){settings.close();return}
    if(!$('#searchMenu').hidden){hideSearchMenu();return}
    if(mediaState.immerseOpen()){immerseApi().close();return}
    /* 详情开着：Escape 归舞台。焦点在浮窗里时舞台自己已经收了这一下；里层弹层（标签搜索框、右键
       菜单）先收掉的会 `preventDefault`，那一下只关弹层。焦点落在浮窗外（body）时在这里转给舞台。 */
    if(mediaState.stageOpen()){if(!e.defaultPrevented){e.preventDefault();stageApi().requestClose()}return}
    if($('#drawer').classList.contains('open')){openDrawer(false);return}
    if(selectMode||selected.size||followSelected.size){setSelectMode(false,true);return}
    return;
  }
  // 输入态不抢键：搜索框、标签弹窗和任何可编辑区域里的按键归它们自己处理。
  if(isTypingTarget(e.target)||e.ctrlKey||e.metaKey||e.altKey)return;
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
    const imageStep=document.querySelector(`#stage[open] [data-media-carousel]:not([data-carousel-video-active]) [data-follow-image-step="${e.key==='ArrowRight'?1:-1}"]`);
    if(imageStep){e.preventDefault();imageStep.click();return}
  }
  const video=activeVideo();
  if(video){
    if(e.key==='t'||e.key==='T'){
      // 影院模式是详情舞台的版式，小窗里没有这个东西可切。
      e.preventDefault();stageApi()?.toggleTheater();return;
    }
    if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
      e.preventDefault();
      seekVideoBy(video,preferencesState.appSettings.seekSeconds*(e.key==='ArrowRight'?1:-1));
      return;
    }
    if(e.key===' '||e.key==='k'||e.key==='K'){
      e.preventDefault();          // 不加这句空格会把页面滚下去
      toggleVideoPlayback(video);
      return;
    }
    if(e.key==='m'||e.key==='M'){e.preventDefault();clickPlayerControl(video,'.vjs-mute-control');return}
    if(e.key==='f'||e.key==='F'){e.preventDefault();clickPlayerControl(video,'.vjs-fullscreen-control');return}
    if(e.key==='i'||e.key==='I'){e.preventDefault();stageApi()?.toggleMiniplayer();return}
  }
});
}
