/* 可操作的那几样共用控件：动作忙态、Geist Switch、自绘拉条、补充信息卡与拖动排序。
   由共享源码直接引用，随 `peach-app.js` 发出；已接过忙态拦截的根是模块级状态，只有一份。 */
import { esc, icon } from '../core';

import { wireAnchoredMenu } from './anchored-menu';

/**
 * Geist loading action: visually unavailable and inert without using native
 * `disabled`, so the trigger keeps keyboard focus while its request is running.
 */
export function setActionBusy(control:Element|null|undefined,busy=true):void{
  if(!control)return;
  if(busy){
    control.setAttribute('aria-busy','true');
    control.setAttribute('aria-disabled','true');
  }else{
    control.removeAttribute('aria-busy');
    control.removeAttribute('aria-disabled');
  }
}

const busyActionRoots=new WeakSet<Node>();

/** Block repeat pointer and keyboard activation for every shared busy action. */
export function wireBusyActions(root:Node=document):void{
  if(busyActionRoots.has(root))return;
  busyActionRoots.add(root);
  root.addEventListener('click',event=>{
    const control=(event.target as Element).closest?.('button[aria-busy="true"],[role="button"][aria-busy="true"]');
    if(!control||!root.contains(control))return;
    event.preventDefault();
    event.stopImmediatePropagation();
  },true);
}

/**
 * Geist Switch：2–3 个互斥视图用共享 name 的一组 radio，不用 Toggle。
 *
 * JAV 卡片版式和关注列表版式是同一个控件——只有 name、选项和当前值不同，所以模板
 * 与 `.iconswitch` 样式共用一份；调用方各自的摆放位置仍由自己的类负责。
 */
export function iconSwitchHtml(name:string,legend:string,options:readonly (readonly any[])[],current:unknown,{attr='',className='',text=false}={}):string{
  const items=options.map(([value,label,symbol])=>
    `<label title="${esc(label)}"><input type="radio" name="${esc(name)}" value="${esc(value)}" ${attr}
      ${value===current?'checked':''}><span aria-hidden="true">${text?(symbol?icon(symbol):'')+esc(label):icon(symbol)}</span><span class="sr-only">${esc(label)}</span></label>`).join('');
  return `<fieldset class="iconswitch${text?' iconswitch-text':''}${className?` ${esc(className)}`:''}"><legend class="sr-only">${esc(legend)}</legend>${items}</fieldset>`;
}

/** 把一组 iconSwitchHtml 画出来的 radio 接到 apply(value) 上。 */
export function wireIconSwitch(root:ParentNode|null|undefined,attr:string,apply:(value:string)=>void):void{
  root?.querySelectorAll<HTMLInputElement>(`[${attr}]`).forEach(input=>{
    input.onchange=()=>{if(input.checked)apply(input.value)};
  });
}

/* 自绘单值拉条。原生 `input[type=range]` 的轨道、抓手和刻度分散在三套带厂商前缀的伪
   元素里，拿不到当前值也接不上悬停才显形的刻度；换成一个 `role="slider"` 的容器之后，
   几何、状态和键盘全在一处。形状取自 feralui.dev/gradients 的 `.dial-slider`，实测记在
   `docs/reference-snapshots/feralui-studio-boardui-accent-measured.md`：30px 高、20px 轨道、
   每 10% 一根刻度、3×20px 的圆头抓手，刻度与抓手默认透明，悬停或拖动时才出现。
   颜色一律由 `--ink` 经 color-mix 得到，焦点环仍走站内的 `--tungsten`。
   当前位置写成 `--dial-at` 交给 CSS：填充宽度和抓手位置是同一个数，分两处写就会错开。 */
const DIAL_TICKS=9;
export function dialSliderHtml({value=0,min=0,max=100,step=1,label='',suffix='%',attr='',className=''}={}):string{
  const at=max>min?(value-min)/(max-min)*100:0;
  const text=`${value}${suffix}`;
  return `<div class="ui-dial${className?` ${esc(className)}`:''}" ${attr}>
    <div class="ui-dial-slider" data-dial-slider role="slider" tabindex="0" aria-label="${esc(label)}"
      aria-valuemin="${min}" aria-valuemax="${max}" aria-valuenow="${value}" aria-valuetext="${esc(text)}"
      data-dial-step="${step}" style="--dial-at:${at}%">
      <span class="ui-dial-track" aria-hidden="true"><span class="ui-dial-fill"></span></span>
      <span class="ui-dial-ticks" aria-hidden="true">${'<span></span>'.repeat(DIAL_TICKS)}</span>
      <span class="ui-dial-handle" aria-hidden="true"></span>
    </div><b class="ui-dial-value mono" data-dial-value>${esc(text)}</b></div>`;
}

/**
 * 接上 dialSliderHtml 画出来的一条拉条。
 *
 * `onInput` 在拖动和键盘的每一步都发，调用方自己合并到一帧里；`onChange` 只在松手、
 * 键盘落键和触摸取消时发一次，落盘归它。两者分开是这条拉条唯一的性能约定：拖动中
 * 每一步都写 localStorage 的话，一次 60 步的拖动就是 60 次同步序列化。
 */
export function wireDialSlider(root:ParentNode,{onInput=()=>{},onChange=()=>{},suffix='%'}:{
  onInput?: (value:number)=>void; onChange?: (value:number)=>void; suffix?: string;
}={}):{readonly value:number;set(next:number):void}{
  const slider:any=root.querySelector('[data-dial-slider]'),readout=root.querySelector('[data-dial-value]');
  const track:any=root.querySelector('.ui-dial-track');
  const min=+slider.getAttribute('aria-valuemin'),max=+slider.getAttribute('aria-valuemax');
  const step=+slider.dataset.dialStep||1;
  let value=+slider.getAttribute('aria-valuenow');
  const clamp=(raw:number)=>Math.min(max,Math.max(min,Math.round(raw/step)*step));
  const paint=()=>{
    const text=`${value}${suffix}`;
    slider.style.setProperty('--dial-at',`${max>min?(value-min)/(max-min)*100:0}%`);
    slider.setAttribute('aria-valuenow',String(value));
    slider.setAttribute('aria-valuetext',text);
    if(readout)readout.textContent=text;
  };
  const set=(raw:number,notify:boolean)=>{
    const next=clamp(raw);
    if(next===value)return false;
    value=next;paint();if(notify)onInput(value);return true;
  };
  /* 轨道几何在按下那一刻量一次就够，之后每次 pointermove 都读缓存。指针捕获期间这条
     轨道不会跑，而 `getBoundingClientRect()` 会强制同步一次样式与布局——放在每一个
     pointermove 里，一次拖动就是上百次强制布局，实测占掉这条链路一大半时间。 */
  let trackBox:DOMRect|null=null;
  const fromPointer=(event:PointerEvent)=>{
    const rect=trackBox||track.getBoundingClientRect();
    const ratio=rect.width?(event.clientX-rect.left)/rect.width:0;
    set(min+(max-min)*Math.min(1,Math.max(0,ratio)),true);
  };
  slider.addEventListener('pointerdown',(event:PointerEvent)=>{
    if(event.pointerType==='mouse'&&event.button!==0)return;
    event.preventDefault();slider.focus();
    trackBox=track.getBoundingClientRect();
    slider.dataset.dragging='true';
    /* 指针捕获拿不到就算了：一次合成出来的 pointerdown（自动化、辅助技术）带的 id
       不对应任何活动指针，`setPointerCapture` 会抛，抛出去整条拖动就断在第一步。 */
    try{slider.setPointerCapture(event.pointerId)}catch{}
    fromPointer(event);
  });
  slider.addEventListener('pointermove',(event:PointerEvent)=>{if(slider.dataset.dragging==='true')fromPointer(event)});
  const release=(event:PointerEvent)=>{
    if(slider.dataset.dragging!=='true')return;
    delete slider.dataset.dragging;trackBox=null;
    try{if(slider.hasPointerCapture(event.pointerId))slider.releasePointerCapture(event.pointerId)}catch{}
    onChange(value);
  };
  slider.addEventListener('pointerup',release);
  slider.addEventListener('pointercancel',release);
  /* 键盘一档就是一个 step，按住 Shift 走 10。Home／End 直接到两端：一条 0–100 的拉条
     用方向键从一头走到另一头要按一百下，那不叫可用。 */
  slider.addEventListener('keydown',(event:KeyboardEvent)=>{
    const span=event.shiftKey?10:step;
    const moves:Record<string,number>={ArrowLeft:-span,ArrowDown:-span,ArrowRight:span,ArrowUp:span};
    let next:number|null=null;
    if(event.key in moves)next=value+moves[event.key]!;
    else if(event.key==='Home')next=min;
    else if(event.key==='End')next=max;
    if(next===null)return;
    event.preventDefault();
    if(set(next,true))onChange(value);
  });
  return {get value(){return value},set(next:number){set(next,false)}};
}

/** 复杂补充信息复用顶层浮层和视口避让，正文可聚焦并独立滚动。
 *  `panel` 进顶层、锚在 `trigger` 旁，悬停 150ms 或点一下打开，`mount` 里按 Escape 收起。 */
export function wireContextCard(mount:HTMLElement,trigger:HTMLElement,panel:HTMLElement):{
  setOpen(open:boolean):void; isOpen():boolean; hide():void;
}{
  panel.classList.add('context-card');panel.setAttribute('popover','manual');
  panel.setAttribute('role','dialog');panel.tabIndex=-1;
  trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-controls',panel.id);
  const floating=wireAnchoredMenu(mount,trigger,panel);
  let timer:ReturnType<typeof setTimeout>|undefined;
  const hide=()=>{clearTimeout(timer);floating.setOpen(false)};
  const enter=()=>{clearTimeout(timer);timer=setTimeout(()=>{if(trigger.isConnected)floating.setOpen(true)},150)};
  const leave=()=>{clearTimeout(timer);timer=setTimeout(()=>{if(!panel.matches(':hover')&&!trigger.matches(':hover')&&!panel.contains(document.activeElement)&&document.activeElement!==trigger)hide()},150)};
  trigger.addEventListener('pointerenter',enter);trigger.addEventListener('pointerleave',leave);
  panel.addEventListener('pointerenter',()=>clearTimeout(timer));panel.addEventListener('pointerleave',leave);
  trigger.addEventListener('focus',enter);trigger.addEventListener('blur',leave);
  trigger.addEventListener('click',()=>clearTimeout(timer));
  mount.addEventListener('keydown',event=>{if(event.key==='Escape')hide()});
  panel.addEventListener('focusout',leave);
  trigger.addEventListener('keydown',event=>{if(event.key==='ArrowDown'&&floating.isOpen()){event.preventDefault();(panel.querySelector<HTMLElement>('a,button,[tabindex="0"]')||panel).focus()}});
  return {...floating,hide};
}

/* 拖动排序：一列带 key 的行，拖到哪一行的上半截或下半截就插到那里。

   站内此前有两份几乎一样的实现（侧栏顺序、导航按钮），第三处再抄一遍，落点判据、
   拖动中的减淡和那条插入线就会各演化一份。这里只收「一列行」这一种：拖动中的行加
   `dragging`，落点行加 `drop-before` / `drop-after`，样式由调用方那一侧的类名给。

   `onMove(key,target,after)` 拿到的是两个 key 和一个方位，重排由调用方自己做——
   顺序存在哪、存完刷什么，各处本来就不一样。 */
export function wireDragReorder(root:ParentNode,{selector,attribute,onMove}:{
  selector?: any; attribute?: any; onMove?: any;
}={}):void{
  const rows=():HTMLElement[]=>[...root.querySelectorAll<HTMLElement>(selector)];
  const clear=()=>rows().forEach(row=>row.classList.remove('dragging','drop-before','drop-after'));
  let dragging:string|null=null;
  rows().forEach(row=>{
    const key=row.getAttribute(attribute);
    row.draggable=true;
    row.addEventListener('dragstart',event=>{
      dragging=key;row.classList.add('dragging');
      event.dataTransfer!.effectAllowed='move';
      // 不写 dataTransfer 的话 Firefox 根本不认这是一次拖动；首页那一项的 key 是空串，
      // 空串等于没写，所以给它一个占位。落点判据只看 key 本身，不看这里写的字。
      event.dataTransfer!.setData('text/plain',key||'peach-row');
    });
    row.addEventListener('dragover',event=>{
      if(dragging===null||dragging===key)return;
      event.preventDefault();event.dataTransfer!.dropEffect='move';
      const after=event.clientY>row.getBoundingClientRect().top+row.offsetHeight/2;
      rows().forEach(item=>item.classList.remove('drop-before','drop-after'));
      row.classList.add(after?'drop-after':'drop-before');
    });
    row.addEventListener('drop',event=>{
      event.preventDefault();
      const after=row.classList.contains('drop-after'),from=dragging;
      dragging=null;clear();
      if(from!==null&&from!==key)onMove(from,key,after);
    });
    row.addEventListener('dragend',()=>{dragging=null;clear()});
  });
}
