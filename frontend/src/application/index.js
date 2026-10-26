import { indexPath as pathForIndex, personAvatar as avatarForPerson, onlineAuthorRingHtml as avatarForOnline, createIndexController } from '../react/index/index-controller';
import { followManageParams as manageParams, followManagePath as managePath, followManageEntry as manageEntry } from '../react/follow-manage/manage-route';
import { followView, adoptFollowView } from './follow.js';
/* index 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { $, esc, leadingGraphemes } from '../core';
import { faceBoxAttrs, facePos } from './../card-art/markup';
import { followState } from './follow.js';
import { preferencesState } from './preferences.js';
import { navigationState, openRoutedPage, syncNavigation } from './navigation.js';
import { applicationEffects } from './effects';
import { openConfigurationSection } from './playlists.js';
import { peopleLayoutOf } from './../index-skeleton';
import { buildBars, catalogState, clearCatalogGrid, homePath } from './catalog.js';
import { state, writeShell } from './../shell/index';
import { setSelectMode } from './selection.js';
import { showHomeSurfaces } from './layout.js';
export const indexState = { peopleIndexLayout: undefined, personAvatar: undefined };

export function followWorkMark([key,label,,icon,focus]){
  const fallback=esc(leadingGraphemes(label,2));
  return icon?`<img src="/work-icon?work=${encodeURIComponent(key)}" width="128" height="128" alt="" loading="lazy"${facePos(focus)}${faceBoxAttrs(focus)}>`:fallback;
}

export function followListLayout(){return preferencesState.appSettings.followLayout==='table'?'table':'default'}

export function followManageParams(){return manageParams(location.search)}

export function followManagePath(params){return managePath(params)}

export function routeFollowManage(params){navigationState.route(followManagePath(params))}

export function followManageEntry(workspace=''){return manageEntry(location.search,workspace)}

export function personRingHtml(item,kind,big){return avatarForPerson(item,kind,big).html}

export function onlineAuthorRingHtml(item){return avatarForOnline(item)}

export function indexPath(next){return pathForIndex(next)}

export function showIndexContainer(){
  $('#stats').hidden=true;$('#index').hidden=false;clearCatalogGrid();catalogState.hideCatalogCombo();
  $('#count').textContent='';$('#loadSentinel').hidden=true;
}

export function showIndexTags(tags,match){
  writeShell({state:{...state,state:'',tag:tags.join(','),tag_match:match}});
  setSelectMode(false,false);navigationState.route(homePath());showHomeSurfaces();syncNavigation();buildBars();
}

export function openFollowAuthorFromIndex(key){indexState.controller.openFollowAuthor(key)}

export function openFollowTagFromIndex(tag){indexState.controller.openFollowTag(tag)}

export function installIndex16() {
followState.FOLLOW_MANAGE_TABS = ['list','add','feeds'];
applicationEffects.listen(document, 'click', event=>{
  if(event.target.closest?.('[data-empty-settings]')){openConfigurationSection('媒体库与入库');return}
  const link=event.target.closest?.('a[href="/subscriptions?tab=add"]');
  if(!link||event.defaultPrevented||event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();
  navigationState.navigatePath(followManageEntry('add'));
});
indexState.peopleIndexLayout = ()=>peopleLayoutOf(preferencesState.appSettings.peopleLayout);
indexState.personAvatar = avatarForPerson;
}

export function connectIndexController(){indexState.controller=createIndexController({
    route:navigationState.route,followView,adoptFollowView,hideIndex:()=>{$('#index').hidden=true},openPage:openRoutedPage,
  })}
