/* 滚动那一组：横排的滚轮转向、拖动与橡皮筋、横排自动滚动、选中底板的滑动、Geist Scroller，
   以及按名单批量挂覆盖式滚动条。由共享源码直接引用，随 `peach-app.js` 发出；
   已绑定的横排、自动滚动的那几排与弹簧取值都是模块级状态，只有一份。 */
import { esc } from '../core';

import { attachOverlayScrollbar } from './overlay-scrollbar';

const horizontalControls=new Map<Element,any>();
let horizontalCleanup:MutationObserver|null|undefined;
/* 两次滚轮事件隔多久算下一次手势。触控板的惯性尾巴一格一格地来，间隔在几十毫秒；
   人停下来再滚一次，中间隔的比这长得多。 */
const WHEEL_GESTURE_GAP=240;
/* 一格滚轮至少这么大才平滑着走。鼠标滚轮一格是整整一段（Windows 上 100px 起），直接
   改 `scrollLeft` 会一格一格地跳，而页面自己滚的时候每一格都是一段动画；触控板一次只
   给几个像素、一秒几十次，本来就是连续的，再套动画只会拖慢跟手。 */
const WHEEL_SMOOTH_STEP=50;
/* 平滑那一段由这一排自己逐帧走，不用 `scrollTo({behavior:'smooth'})`：那条动画每接一格
   都从零速重新起步，连续转滚轮时一格一顿，实测每两格之间有两三帧完全停住。这里每帧
   朝终点走剩下距离的一截（时间常数 70ms），新的一格只把终点往前挪，速度连着走，不断档。 */
const WHEEL_GLIDE_MS=70;
/* 横排滚到头还往前推，内容多走一小段再弹回来。transitions.dev 没有这一条配方，越界位移
   借 UIScrollView 那条橡皮筋公式 `(1 - 1/(x·c/d + 1))·d`（c = 0.55，use-gesture 的
   `rubberband` 是同一条）：推得越远越推不动，怎么推也到不了容器那么宽。弹回走站内那条
   采样弹簧 `--spring-pane`，冲过原位再荡回来，和筛选条那块底板一个手感。 */
const RUBBER_BAND=.55;
/* 滚轮没有「手还按着」那一段，推到头的那一下按这一格的力度弹一次：一格滚轮当作手拖了它
   的三成半，放在最多 480px 宽的橡皮筋上算。宽屏上一整排一千多像素，按整排算，一格
   推出去的量会大到像是整排滑脱了。 */
const WHEEL_PULL=.35;
const WHEEL_PULL_SPAN=480;
export function rubberBand(distance:number,dimension:number):number{
  if(!distance||!(dimension>0))return 0;
  return Math.sign(distance)*(1-1/(Math.abs(distance)*RUBBER_BAND/dimension+1))*dimension;
}
/* 位移挂在这一排的子元素上（`.edgepull>*`，读 `--edge-pull`），不挂在容器自己身上：容器
   一挪，贴着它的渐隐遮罩和外面那一层的边界跟着一起走，右移时还会把整页撑出横向滚动条。
   动画只在越界那一下挂类名，平时子元素上什么都不多。 */
function edgeBounce(el:HTMLElement){
  let spring:Animation|null=null,pulled=0;
  const idle=()=>typeof el.animate!=='function'||matchMedia('(prefers-reduced-motion:reduce)').matches;
  const settle=()=>{spring=null;if(!pulled)el.classList.remove('edgepull')};
  const play=(frames:Keyframe[],duration:number)=>{
    spring?.cancel();el.classList.add('edgepull');
    spring=el.animate(frames,{duration});
    spring.onfinish=settle;
  };
  return {
    /* 拖动跟手：越界多少就按橡皮筋收成多少，松手 `release` 弹回。 */
    drag(excess:number){
      if(idle())return;
      spring?.cancel();spring=null;
      pulled=-rubberBand(excess,el.clientWidth);
      el.classList.toggle('edgepull',!!pulled);
      if(pulled)el.style.setProperty('--edge-pull',`${pulled}px`);else el.style.removeProperty('--edge-pull');
    },
    release(){
      if(!pulled)return;
      const from=pulled,ease=glideEase();pulled=0;el.style.removeProperty('--edge-pull');
      play([{'--edge-pull':`${from}px`,easing:ease.easing},{'--edge-pull':'0px'}],ease.duration);
    },
    /* 滚轮顶到头：冲出去那一段减速，荡回来那段走弹簧。一次手势只弹一下，调用方判。 */
    kick(delta:number){
      if(idle()||pulled)return;
      const peak=-rubberBand(delta*WHEEL_PULL,Math.min(el.clientWidth,WHEEL_PULL_SPAN));
      if(!peak)return;
      const ease=glideEase();
      play([{'--edge-pull':'0px',easing:'cubic-bezier(.2,.8,.4,1)'},
        {'--edge-pull':`${peak}px`,offset:.3,easing:ease.easing},{'--edge-pull':'0px'}],ease.duration*1.6);
    },
  };
}
/** 同一容器只绑定一次。指针在这一排上时滚轮只滚这一排，滚到头弹一下，不带着整页走。 */
/** 会超宽的横向滚动层：两端按滚动位置渐隐，竖向滚轮转成横向，`drag` 为真时可拖。 */
export function wireHorizontalScroller(el:any,{drag=false,fade=true}={}):any{
  if(!el)return;
  const existing=horizontalControls.get(el);
  if(existing){existing.options.drag ||= drag;existing.options.fade ||= fade;existing.update();return existing}
  const options={drag,fade},abort=new AbortController(),bounce=edgeBounce(el);
  let start:{x:number;left:number}|null=null,moved=0,heldUntil=0,target=0,kicked=false,glide=0,glideAt=0,pos=0,pending=0;
  /* 值没变就不写：自动滚动每一帧都触发一次 `scroll`，同值重写属性照样让样式重算一遍。 */
  const mark=(key:string,value:string)=>{if(el.dataset[key]!==value)el.dataset[key]=value};
  const scrollable=()=>['auto','scroll','hidden'].includes(getComputedStyle(el).overflowX);
  const update=()=>{if(options.fade){const active=scrollable();mark('overflowLeft',String(active&&el.scrollLeft>1));mark('overflowRight',String(active&&el.scrollLeft+el.clientWidth<el.scrollWidth-1))}};
  const listen=(target:EventTarget,event:string,handler:(event:any)=>void,extra={})=>target.addEventListener(event,handler,{...extra,signal:abort.signal});
  listen(el,'scroll',update,{passive:true});
  const kick=(delta:number)=>{if(!kicked){kicked=true;bounce.kick(delta)}};
  const stopGlide=()=>{cancelAnimationFrame(glide);glide=0;pending=0};
  /* 位置自己记一份小数：一帧只走零点几个像素时，`scrollLeft` 读回来是取整后的值。
     推过头的那一下等走到头再弹，半路就弹看起来是没到头先弹了。 */
  const frame=(now:number)=>{
    const dt=Math.min(Math.max(now-glideAt,0),64);glideAt=now;
    pos+=(target-pos)*(1-Math.exp(-dt/WHEEL_GLIDE_MS));
    if(Math.abs(target-pos)<.5)pos=target;
    el.scrollLeft=pos;
    if(pos!==target){glide=requestAnimationFrame(frame);return}
    glide=0;
    if(pending){const delta=pending;pending=0;kick(delta)}
  };
  /* 页面那边开了头的手势拦不住：Chrome 把一串滚轮事件认作同一次手势，第一下没被拦下，
     后面那些就再也拦不住。页面滚着滚着让这一排经过指针底下时，这些原样留给页面。
     在这一排上开头的手势整段都归这一排：还没滚完、滚到头以后，都不把剩下的交给页面；
     要滚整页，指针挪到这一排外面去。推过头就弹一下，一次手势只弹一次：触控板的惯性
     尾巴一格一格地撞在边上，每格都弹就是一串抖动。 */
  listen(el,'wheel',event=>{
    const max=el.scrollWidth-el.clientWidth;
    if(event.defaultPrevented||!event.cancelable||Math.abs(event.deltaY)<=Math.abs(event.deltaX)||max<=0||!scrollable())return;
    event.preventDefault();
    const now=performance.now(),step=Math.abs(event.deltaY);
    if(now>heldUntil)kicked=false;
    heldUntil=now+WHEEL_GESTURE_GAP;
    // 平滑那一段还在半路时，下一格从上一格的终点接着算。
    const from=glide?target:el.scrollLeft,want=from+event.deltaY;
    target=Math.min(max,Math.max(0,want));
    const over=want===target?0:event.deltaY;
    if(step<WHEEL_SMOOTH_STEP||target===from){
      if(target!==from){stopGlide();el.scrollLeft=target}
      if(over&&!glide)kick(over);else if(over)pending=over;
      return;
    }
    if(over)pending=over;
    if(!glide){pos=el.scrollLeft;glideAt=performance.now();glide=requestAnimationFrame(frame)}
  },{passive:false});
  listen(el,'mousedown',event=>{if(!options.drag||event.button!==0||el.scrollWidth-el.clientWidth<=1)return;event.stopPropagation();stopGlide();start={x:event.pageX,left:el.scrollLeft};moved=0;el.style.cursor='grabbing'});
  listen(window,'mousemove',event=>{
    if(!start)return;
    const dx=event.pageX-start.x,want=start.left-dx,max=el.scrollWidth-el.clientWidth;
    moved=Math.max(moved,Math.abs(dx));el.scrollLeft=want;
    bounce.drag(want<0?want:want>max?want-max:0);
    event.preventDefault();
  });
  listen(window,'mouseup',()=>{if(start)bounce.release();start=null;el.style.cursor=''});
  listen(el,'click',event=>{if(moved>6){event.stopPropagation();event.preventDefault();moved=0}},{capture:true});
  const resize=new ResizeObserver(update);
  const observe=()=>{resize.observe(el);for(const child of el.children)resize.observe(child);update()};
  const contents=new MutationObserver(()=>{resize.disconnect();observe()});
  contents.observe(el,{childList:true,characterData:true,subtree:true});observe();
  const control={options,update,destroy(){abort.abort();stopGlide();resize.disconnect();contents.disconnect();horizontalControls.delete(el);el.style.cursor='';if(!horizontalControls.size){horizontalCleanup?.disconnect();horizontalCleanup=null}}};
  horizontalControls.set(el,control);
  if(!horizontalCleanup){horizontalCleanup=new MutationObserver(()=>{for(const [node,item] of horizontalControls)if(!node.isConnected)item.destroy()});horizontalCleanup.observe(document.body,{childList:true,subtree:true})}
  update();return control;
}

/* 一排横卡自己缓缓往前走，走到头停一下再往回走。一秒 24px 是看得出在动、又不催人读的
   速度：一张 168px 的卡七秒走过去。两端各停两秒，最后一张和第一张都看得清。 */
const AUTO_SCROLL_SPEED=24;
const AUTO_SCROLL_DWELL=2000;
/* 人手动过之后隔多久再接着走：刚滚到想看的那张，这一排马上又自己挪走，等于跟人抢。 */
const AUTO_SCROLL_RESUME=3000;
const autoScrollers=new Map<Element,()=>void>();
const autoScrollWakeups=new Set<()=>void>();
let autoScrollVisibility:MutationObserver|null=null;
/**
 * 横排自动滚动。指针停在上面、焦点在里面、这一排不在屏幕上、页面切到后台时都停；
 * 人滚过或拖过之后从人停下的位置接着走。系统要求减少动态效果时不动。
 */
export function wireAutoScroll(el:any):void{
  if(!el||autoScrollers.has(el))return;
  if(matchMedia('(prefers-reduced-motion:reduce)').matches)return;
  const abort=new AbortController();
  let pos=el.scrollLeft,dir=1,last=0,frame=0,hovered=false,holdUntil=0;
  const listen=(target:EventTarget,type:string,fn:()=>void)=>target.addEventListener(type,fn,{passive:true,signal:abort.signal});
  /* 在不在屏幕上每帧量一次，不另挂观察器：页面观察器只留给「载入更多」那一份。
     滚出屏幕后循环停下，页面再滚动时由下面的捕获监听叫醒。 */
  const onscreen=()=>{const r=el.getBoundingClientRect();return r.width>0&&r.bottom>0&&r.top<innerHeight};
  const running=()=>el.isConnected&&!hovered&&!document.hidden&&
    (!document.body.hasAttribute('data-detail-open')||!!el.closest('#stage'))&&!el.matches(':focus-within')&&onscreen();
  const tick=(now:number)=>{
    frame=0;
    if(!el.isConnected){stop();return}
    if(!running())return;
    frame=requestAnimationFrame(tick);
    const dt=last?Math.min(now-last,64):0;last=now;
    const max=el.scrollWidth-el.clientWidth;
    if(now<holdUntil||max<=1)return;
    /* 位置自己记一份小数：一帧只走零点几个像素，`scrollLeft` 读回来是取整后的值，
       拿它接着加会一直原地不动。 */
    pos=Math.min(max,Math.max(0,pos+dir*AUTO_SCROLL_SPEED*dt/1000));
    el.scrollLeft=pos;
    if(pos>=max||pos<=0){dir=pos>=max?-1:1;holdUntil=now+AUTO_SCROLL_DWELL}
  };
  // 重画换掉了这一排时，挂在 document 上的监听要在下一次叫醒时跟着撤掉。
  const wake=()=>{if(!el.isConnected)stop();else if(!frame&&running()){last=0;frame=requestAnimationFrame(tick)}};
  const hold=()=>{holdUntil=performance.now()+AUTO_SCROLL_RESUME};
  // 读回来和自己记的差不到一个像素就是自己那一帧；差得多是人滚的，从人停下的地方接着走。
  listen(el,'scroll',()=>{if(Math.abs(el.scrollLeft-pos)>1){pos=el.scrollLeft;hold()}});
  listen(el,'pointerenter',()=>{hovered=true});
  listen(el,'pointerleave',()=>{hovered=false;hold();wake()});
  listen(el,'focusout',()=>setTimeout(wake,0));
  listen(document,'visibilitychange',wake);
  document.addEventListener('scroll',wake,{capture:true,passive:true,signal:abort.signal});
  wake();
  function stop(){cancelAnimationFrame(frame);frame=0;abort.abort();autoScrollers.delete(el);autoScrollWakeups.delete(wake);
    if(!autoScrollers.size){autoScrollVisibility?.disconnect();autoScrollVisibility=null}}
  autoScrollers.set(el,stop);
  autoScrollWakeups.add(wake);
  if(!autoScrollVisibility){autoScrollVisibility=new MutationObserver(()=>autoScrollWakeups.forEach(resume=>resume()));
    autoScrollVisibility.observe(document.body,{attributes:true,attributeFilter:['data-detail-open']})}
}
export function stopAutoScroll(el:Element):void{autoScrollers.get(el)?.()}

/* 一排里标出「当前是哪一个」的那块底板，全站只有这一种动法：滑过去、冲过落点、荡回来。
   筛选条上那块玻璃、抽屉那一列、分段控件里那块白底，在人眼里是同一件事，各写一段就会
   各自漂移成三种手感。
   弹簧曲线和它的时长都写在 `board.css` 的 `--spring-pane` 上，这里只读一次：那串数是一次
   弹簧模拟的采样结果，抄第二份就没人再改得动它。 */
let paneSpring:{easing:string;duration:number}|null=null;
export function glideEase():{easing:string;duration:number}{
  if(!paneSpring){
    const css=getComputedStyle(document.documentElement);
    paneSpring={easing:css.getPropertyValue('--spring-pane').trim()||'ease',
      duration:parseFloat(css.getPropertyValue('--spring-pane-ms'))||300};
  }
  return paneSpring;
}
/* 位移走 `translate`、形变走 `scale`，两个独立属性各挂一段动画，不挤进同一条
   `transform`：一条属性上只放得下一段，而这两下的时间形状不是同一条曲线——位移冲过
   落点再荡回来，抻开是中途最大、两头归一。分开写，两段仍然都在合成线程上。
   都不碰 `width`：宽度是布局属性，逐帧改它等于让整份文档重新排版一遍，合成线程碰不
   到它，主线程一忙这块底板就跟着卡住。
   `from` 给 null 就只落位不动画：第一次出现的那块不该从别处飞进来。 */
export function moveGlidePane(
  pane:HTMLElement,
  from:{x:number;y:number;w:number;h:number}|null,
  box:{x:number;y:number;w:number;h:number},
  axis:'x'|'y'='x',
):void{
  /* 原地重量也要撤掉还在跑的位移与形变，动画不能继续覆盖新落点。 */
  pane.getAnimations().forEach(a=>a.cancel());
  pane.style.width=`${box.w}px`;pane.style.height=`${box.h}px`;
  const span=axis==='y'?'h':'w',head=axis==='y'?'y':'x';
  const settled=`${box.x}px ${box.y}px`;
  if(from&&from[head]!==box[head]&&!matchMedia('(prefers-reduced-motion:reduce)').matches){
    const ease=glideEase();
    pane.animate([{translate:axis==='y'?`${box.x}px ${from.y}px`:`${from.x}px ${box.y}px`},
      {translate:settled}],{duration:ease.duration,easing:ease.easing,fill:'none'});
    /* 一块被拽着走的软东西，跑起来在跑的方向上抻开，停下来收回去。抻多少按这一跳跨了
       自己几个身位算，封在一个半身位：再远也不该更长，那时候读起来不是被拉长的同一
       块，是换上来的另一块。峰值压在前三成——加速那一段才拉得动它。
       形变只在这一排排布的方向上：另一根轴的尺寸是这一排给定的，在那儿拉扯会让它看
       起来不是这一排里的东西。 */
    const reach=Math.min(Math.abs(box[head]-from[head])/box[span],1.5),grow=1+reach*.12;
    pane.animate([{scale:'1 1',offset:0},
      {scale:axis==='y'?`1 ${grow}`:`${grow} 1`,offset:.3},{scale:'1 1',offset:1}],
      {duration:ease.duration,easing:'ease-in-out',fill:'none'});
  }
  pane.style.translate=settled;
}

/** Geist Scroller: one-axis overflow with edge fades as the scroll affordance. */
export function scrollerHtml(content:string,{className='',label='可滚动内容',overflow='y'}={}){
  const axis=new Set(['x','y','both']).has(overflow)?overflow:'y';
  return `<div class="ui-geist-scroller${className?` ${esc(className)}`:''}" data-geist-scroller>
    <div class="ui-geist-scroller-overlay" aria-hidden="true"></div>
    <div class="ui-geist-scroller-container" data-overflow="${axis}" tabindex="0" aria-label="${esc(label)}">${content}</div>
  </div>`;
}

function updateScroller(wrapper:Element){
  const container=wrapper.querySelector(':scope > .ui-geist-scroller-container');
  const overlay=wrapper.querySelector(':scope > .ui-geist-scroller-overlay');
  if(!container||!overlay)return;
  overlay.classList.toggle('ui-can-scroll-top',container.scrollTop>1);
  overlay.classList.toggle('ui-can-scroll-bottom',container.scrollTop+container.clientHeight<container.scrollHeight-1);
  overlay.classList.toggle('can-scroll-left',container.scrollLeft>1);
  overlay.classList.toggle('can-scroll-right',container.scrollLeft+container.clientWidth<container.scrollWidth-1);
}

/** Wire newly rendered scrollers without duplicating listeners after a rerender. */
export function wireScrollers(root:ParentNode=document):void{
  root.querySelectorAll('[data-geist-scroller]').forEach(wrapper=>{
    const container=wrapper.querySelector<HTMLElement>(':scope > .ui-geist-scroller-container');
    if(!container)return;
    if(!container.dataset.scrollerWired){
      container.dataset.scrollerWired='true';
      container.addEventListener('scroll',()=>updateScroller(wrapper),{passive:true});
      container.addEventListener('load',()=>updateScroller(wrapper),true);
    }
    requestAnimationFrame(()=>updateScroller(wrapper));
  });
}

/** 挂覆盖式滚动条的滚动容器。列表只写在这一处，样式那边认的是挂上之后的属性。
 *
 * `[data-stage-scroll]` 是详情浮窗里所有内容的外层。窄屏下整块内容自己纵向滚，滚的得是它而不是
 * `<dialog data-stage>` 本身：轨道必须是滚动容器的兄弟，而 dialog 在顶层，挂在它
 * 父级上的轨道会落到遮罩底下。宽屏下它不溢出，组件自己量得出来，轨道不显示。 */
const OVERLAY_SCROLLERS=[
  '[data-stage-side-content]','[data-stage-scroll]','.tagpickbody','[data-mix-list]','.playlistpicklist',
  '[data-player-stats]',
  '.vjs-peach-settings-menu','.ui-geist-scroller-container','.metricstrip','.ui-tastesummaries',
  '.ui-skeletondashstrip','.followpagination',
  '.reviewtabs','.ftablewrap','.ui-board-local-nav','[data-manage-menu]',
  '.follow-workspace-switch','.fmanagenav','[role="listbox"]',
].join(',');
/* Board 层里会超宽的横向滚动层：两端按滚动位置渐隐说明「那边还有」，鼠标停在上面时竖向
   滚轮转成横向。边线留给外层框，渐隐只落在这一层。
   这里登记的都是 `frontend/` 或 board.css 画出来的层，它们不经过 `wireAllDrag` 那份按 id
   点名的清单，漏登记就是「看得见、够不着」：一排分区在 390px 下溢出两百多像素，
   却既没有渐隐也不接滚轮。组件自己量溢出，不溢出的宽度上登记等于空转，所以按可能
   溢出的层登记，不按某一个断点登记。React 档的页面自己用 `overflow-x-auto`，不进这份清单。 */
const BOARD_EDGE_SCROLLERS='.reviewtabs,.ftablewrap,.ui-board-local-nav,[data-manage-menu],'
  +'.follow-workspace-switch,.fmanagenav';

/** 把这一批 DOM 里所有该有覆盖式滚动条的容器接上；重复调用只接新出现的那些。 */
export function wireOverlayScrollbars(root:ParentNode=document):void{
  root.querySelectorAll<HTMLElement>(OVERLAY_SCROLLERS).forEach(el=>{
    if(el.matches(BOARD_EDGE_SCROLLERS)){
      wireHorizontalScroller(el);return;
    }
    attachOverlayScrollbar(el);
  });
}
