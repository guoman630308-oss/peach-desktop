import { playUiSound } from './sounds';

interface OpenedMenu { mount: Element; menu: HTMLElement; toggle: HTMLElement; setOpen: (next: boolean) => void }

/* 锚定在触发钮上的菜单：无展开动画，固定在视口内，内容在菜单内滚动。

   优先从触发钮右缘向左展开，下方放不下时改到上方。全站的锚定菜单共用这一份定位与
   开关：菜单在视口边缘的表现最容易各写各的，同一语义留两份实现就只会有一份被修。 */
let openedMenu:OpenedMenu|null=null;
const peachGlobal=globalThis as typeof globalThis & { __peachMenuCloser?: boolean };
if(!peachGlobal.__peachMenuCloser){
  peachGlobal.__peachMenuCloser=true;
  /* 「点到别处就关」里的「别处」不能只按 mount 之外算。mount 是定位用的那一片祖先——
     侧栏配色弹层给的是 `.board-sidebar-foot`，媒体库弹层给的是整个 `#drawer`——设置钮
     和侧栏里其余每一枚控件都在里面，按 mount 算的话点它们全是「内点」，弹层就一直挂着。
     mount 缩到触发钮本身也不行：定位要按它算，菜单开在左边还是右边看的就是这一片。
     所以判据改成「点的是不是这枚菜单自己的东西」：菜单内部与触发钮上算内点，mount 里
     别的可点控件算外点，mount 里的空白仍算内点（点空白本来就什么也不该发生）。 */
  const clickable='a[href],button,input,select,textarea,summary,[role=button],[role=menuitem],[tabindex]';
  document.addEventListener('click',event=>{
    if(!openedMenu)return;
    const target=event.target as Node|null;
    if(openedMenu.menu.contains(target)||openedMenu.toggle.contains(target))return;
    if(openedMenu.mount.contains(target)&&!(target instanceof Element&&target.closest(clickable)))return;
    openedMenu.setOpen(false)},true);
}
export function closeAnchoredMenu(): void {if(openedMenu)openedMenu.setOpen(false)}
/* 菜单面板的开合动效来自 boardui 的 menu-styles.ts（登记在 docs/BOARD_UI.md）：150ms
   ease-out，透明度、scale .95 和 2px 模糊一起进出。进场由 Board 层的 CSS 按 `:not([hidden])`
   起；退场要等动画放完再 hidden，display:none 一落下去动画就被掐掉。哪些面板算菜单由
   CSS 决定：读到的 animation-name 是 none（旧界面、prefers-reduced-motion）就当场藏起来。
   全站的菜单都从这两个口进出，`hidden` 才始终是「看不见了」，不会有一份自己写的 150ms。 */
const leavingMenus=new WeakMap<HTMLElement,()=>void>();
export function presentMenu(menu: HTMLElement): void {
  leavingMenus.delete(menu);menu.classList.remove('leaving');
  if(menu.hidden)playUiSound('whoosh');
  menu.hidden=false;
}
export function dismissMenu(menu: HTMLElement, finish?: () => void): void {
  if(menu.hidden||leavingMenus.has(menu))return;
  const done=()=>{if(leavingMenus.get(menu)!==done)return;
    leavingMenus.delete(menu);menu.classList.remove('leaving');menu.hidden=true;if(finish)finish()};
  leavingMenus.set(menu,done);
  menu.classList.add('leaving');
  if(getComputedStyle(menu).animationName==='none'){done();return}
  menu.addEventListener('animationend',event=>{if(event.target===menu)done()},{once:true});
  // 面板在动画结束前被别的规则藏掉（比如切了页）就收不到 animationend，兜一拍。
  setTimeout(done,240);
}
/* 可用的视口上沿是固定顶栏的下缘。顶栏在每一页都盖着最上面那一条，菜单顶到 8px
   会被它压掉半截，而且看不出是被压住的——只是第一项凭空不见了。 */
const viewportTop=()=>8+(parseFloat(getComputedStyle(document.documentElement)
  .getPropertyValue('--topH'))||0);
/**
 * 这一下滚动会不会把 `anchor` 带走：滚的是整页，或者是装着它的那一层滚动容器。
 * 挂在视口上的浮层（锚定菜单、播放器菜单、提示框）只该在这时收起。别处的滚动挪不动
 * 锚点——首页新作那一排自己横着走，每一帧都发一次 scroll，捕获阶段照样收得到，按「浮层
 * 之外」算的话，浮层刚开就被它收掉。浮层自己的滚动也不装着锚点，自然不算。
 */
export function scrollMovesAnchor(event: Event, anchor: Node): boolean {
  return event.target instanceof Node&&event.target.contains(anchor);
}
/* `align:'start'` 让菜单从触发钮左缘往右开。默认往左开，是因为触发钮多半在一排的
   右端；触发钮紧跟在内容后面时（资料页名字右边那一枚），往左开就盖住了它跟着的那段
   内容和再往左的头像。 */
export function wireAnchoredMenu(
  mount: HTMLElement, toggle: HTMLElement, menu: HTMLElement,
  { side = false, align = 'end' }: { side?: boolean; align?: 'start' | 'end' } = {},
): { setOpen(next: boolean): void; isOpen(): boolean; dispose(): void } {
  const position=()=>{
    // 宽度读 offsetWidth：进场动画起手是 scale(.95)，getBoundingClientRect 量到的是缩过的框。
    const anchor=toggle.getBoundingClientRect(),width=menu.offsetWidth;
    if(side&&innerWidth>=640){
      menu.dataset.placement='right';
      menu.style.maxHeight=Math.max(0,innerHeight-32)+'px';
      /* 贴着触发钮的右缘开，允许压住侧栏剩下的那一段。按侧栏右缘起算的话，展开态下
         触发钮到侧栏边还有两百来像素，菜单和它点开的那个控件之间隔着一片空白，读不出
         是谁弹出来的。 */
      menu.style.left=Math.max(16,Math.min(anchor.right+8,innerWidth-width-16))+'px';
      menu.style.top=Math.max(16,Math.min(anchor.top,innerHeight-menu.offsetHeight-16))+'px';return;
    }
    const top=viewportTop(),under=innerHeight-8-anchor.bottom-8,over=anchor.top-8-top;
    /* 下方放不下就改到上方；两侧都放不下时取宽的那一侧，并把菜单压到那一侧的高度，
       内容在菜单内滚。不压高度的话它会横跨触发钮盖住自己，点开之后连改的是哪一个
       名字都看不见。 */
    const naturalHeight=menu.scrollHeight+menu.offsetHeight-menu.clientHeight;
    const downward=under>=naturalHeight||under>=over;
    const height=Math.min(naturalHeight,Math.max(downward?under:over,0));
    menu.dataset.placement=downward?'bottom':'top';
    menu.style.maxHeight=height+'px';
    const preferredLeft=align==='start'||menu.classList.contains('context-card')?anchor.left:anchor.right-width;
    menu.style.left=Math.max(8,Math.min(preferredLeft,innerWidth-width-8))+'px';
    menu.style.top=(downward?anchor.bottom+8:anchor.top-8-height)+'px'};
  /* 触发钮被滚走了就关掉：菜单固定在视口里，锚点跟着内容跑，留着就悬在半空。
     菜单自己的滚动不算——它装不下时本来就要在内部滚，滚一下就关等于底下那几项
     根本够不着；别处那一排横滚也不算，判据见 `scrollMovesAnchor`。 */
  const closeFromViewport=(event:Event)=>{if(scrollMovesAnchor(event,toggle))setOpen(false)};
  /* 带 popover 的菜单进顶层。`position:fixed` 只在没有被祖先接管时才相对视口：祖先上
     一个 transform、filter 或 backdrop-filter 就会成为它的包含块，算好的视口坐标于是
     整体偏移，还要被那个祖先的 overflow 裁掉。设置面板的卡片正是这种祖先——入场动画的
     fill-mode 让 transform 一直挂在上面——菜单于是开在看不见的地方，读起来就是「点不开」。 */
  const inTopLayer=menu.hasAttribute('popover');
  /* 开着没开着记在这里，不看 `hidden`：退场那 150ms 里面板还在、hidden 还是 false，
     按 hidden 判会把「正在收」当成「开着」，再点一下触发钮就关了个已经在关的。 */
  let open=false,disposed=false;
  const setOpen=(next:boolean)=>{
    if(disposed)return;
    if(next){
      if(openedMenu&&openedMenu.mount!==mount)openedMenu.setOpen(false);
      open=true;presentMenu(menu);if(inTopLayer&&!menu.matches(':popover-open'))menu.showPopover();position();
      window.addEventListener('resize',position);
      window.addEventListener('scroll',closeFromViewport,{capture:true,passive:true});
    }else{
      open=false;
      window.removeEventListener('resize',position);
      window.removeEventListener('scroll',closeFromViewport,true);
      dismissMenu(menu,()=>{menu.style.left='';menu.style.top='';menu.style.maxHeight='';
        if(inTopLayer&&menu.matches(':popover-open'))menu.hidePopover()});
    }
    toggle.setAttribute('aria-expanded',String(next));
    openedMenu=next?{mount,menu,toggle,setOpen}:(openedMenu&&openedMenu.mount===mount?null:openedMenu)};
  const click=(event:MouseEvent)=>{event.stopPropagation();setOpen(!open)};
  const keydown=(event:KeyboardEvent)=>{
    if(event.key==='Escape'&&open){event.stopPropagation();setOpen(false);toggle.focus()}};
  toggle.addEventListener('click',click);
  mount.addEventListener('keydown',keydown);
  const dispose=()=>{
    if(disposed)return;
    disposed=true;open=false;
    toggle.removeEventListener('click',click);
    mount.removeEventListener('keydown',keydown);
    window.removeEventListener('resize',position);
    window.removeEventListener('scroll',closeFromViewport,true);
    if(openedMenu?.menu===menu)openedMenu=null;
    leavingMenus.delete(menu);menu.classList.remove('leaving');menu.hidden=true;
    if(inTopLayer&&menu.matches(':popover-open'))menu.hidePopover();
    menu.style.left='';menu.style.top='';menu.style.maxHeight='';
    toggle.setAttribute('aria-expanded','false');
  };
  return {setOpen,isOpen:()=>open,dispose};
}
