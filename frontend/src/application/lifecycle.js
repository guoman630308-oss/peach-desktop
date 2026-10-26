/* lifecycle 域控制器：本域状态与动作，启动由 Application 的生命周期统一安装。 */
import { initBoardControls } from './../board-controls';
import { attachOverlayScrollbar, wireBusyActions, wireOverlayScrollbars } from '../ui-kit';
import { $ } from '../core';
import { applicationEffects } from './effects';
import { wireImageFallbacks } from './../card-art/image-fallback';
import { catalogState } from './catalog.js';
import { layoutState } from './layout.js';
import { followState } from './follow.js';
export const lifecycleState = { overlayScan: undefined };



export function installLifecycle1() {
initBoardControls();
wireBusyActions(document);
attachOverlayScrollbar(document.documentElement,{variant:'page'});
attachOverlayScrollbar($('#drawerScroll'));
lifecycleState.overlayScan = 0;
applicationEffects.mutation(()=>{
  if(lifecycleState.overlayScan)return;
  lifecycleState.overlayScan=applicationEffects.delay(()=>{lifecycleState.overlayScan=0;wireOverlayScrollbars()});
}).observe(document.body,{childList:true,subtree:true});
wireOverlayScrollbars();
wireImageFallbacks(document.body);
catalogState.homeHasFeed = null;
catalogState.barsRequestSeq = 0;
catalogState.barsRendered = '';
catalogState.catalogFilterProps = null;
catalogState.catalogFilterMounting = null;
catalogState.catalogTagRows = [];
catalogState.catalogTopsPages = null;
layoutState.sidebarProps = {content:null,filters:{},latest:null};
layoutState.sidebarSurface = '';
layoutState.sidebarCatalog = null;
layoutState.sidebarContentSeq = 0;
followState.followFilter = '';
followState.followAuthors = new Set();
followState.followProviders = new Set();
followState.followTags = new Set();
followState.followWorks = new Set();
followState.followMediaView = 'videos';
followState.FOLLOW_FEED_SORTS = [['new','更新时间'],['hot','热度'],['dur','时长']];
followState.FOLLOW_RANDOM_SORT = 'rand';
followState.followSort = 'new';
followState.followDir = 'desc';
followState.followSeed = 0;
followState.followDurMin = 0;
followState.followDurMax = 0;
}
