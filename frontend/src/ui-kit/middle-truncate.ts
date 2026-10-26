/* Geist MiddleTruncate 的 Peach 原生适配层。

   只复用已验证的行为：按容器宽度保留首尾、使用单个省略号、宽度变化后重算、复制与无障碍名称
   仍提供完整原文。调用方只需给文件名、路径、URL 或 ID 加 data-middle-truncate；标题和说明继续
   使用末尾省略。随 `peach-app.js` 发出，由 Application 启动与清理全文档的唯一观察者，
   原生宿主与 React 子树写的属性都归它管。 */

interface TruncateState { full: string; rendered: string; raf: number }

const ELLIPSIS='…';
const segmenter=typeof Intl.Segmenter==='function'
  ? new Intl.Segmenter(undefined,{granularity:'grapheme'})
  : null;
const states=new WeakMap<Element,TruncateState>();
let observed=new WeakSet<Element>();
let resizeObserver:ResizeObserver|null=null;
let disposeTruncation:(()=>void)|null=null;
let tracked=new Set<Element>();

const graphemes=(value:unknown)=>segmenter
  ? [...segmenter.segment(String(value??''))].map(part=>part.segment)
  : Array.from(String(value??''));

/* 纯算法边界单独导出，Node 可直接实测，不需要伪造 DOM。fits(candidate) 由浏览器
   测量层注入；二分寻找能放下的最长首尾组合，奇数位优先保留开头。 */
function middleTruncateText(value:unknown,fits:(candidate:string)=>boolean):string{
  const parts=graphemes(value);
  if(!parts.length||fits(value as string))return String(value??'');
  let low=0,high=Math.max(0,parts.length-1),best=ELLIPSIS;
  while(low<=high){
    const kept=(low+high)>>1;
    const head=Math.ceil(kept/2),tail=Math.floor(kept/2);
    const candidate=parts.slice(0,head).join('')+ELLIPSIS+(tail?parts.slice(-tail).join(''):'');
    if(fits(candidate)){best=candidate;low=kept+1}else high=kept-1;
  }
  return best;
}

const measuredWidth=Object.assign((element:Element,value:string)=>{
  const style=getComputedStyle(element);
  const canvas=measuredWidth.canvas||(measuredWidth.canvas=document.createElement('canvas'));
  const context=canvas.getContext('2d');
  if(!context)return Number.POSITIVE_INFINITY;
  /* Chromium 会在 font-variant-numeric 等长数字开启时把计算后的 font 简写返回为空串；
     直接赋值会让 Canvas 静默退回 10px sans-serif，进而低估中文文件名宽度。 */
  context.font=style.font||`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  let width=context.measureText(value).width;
  const letterSpacing=parseFloat(style.letterSpacing);
  if(Number.isFinite(letterSpacing))width+=Math.max(0,graphemes(value).length-1)*letterSpacing;
  const wordSpacing=parseFloat(style.wordSpacing);
  if(Number.isFinite(wordSpacing))width+=(value.match(/\s/g)||[]).length*wordSpacing;
  return width;
},{canvas:null as HTMLCanvasElement|null});

/* 可用宽度默认取元素自己的盒子。带 data-middle-truncate-within 的元素按内容收缩（后面紧跟着
   别的东西，比如文件名后面的「最大」标记），自己的盒子就是当前文字的宽度——拿它当上限，
   clientWidth 取整少掉的那不到一像素每一轮都让全文「放不下」，越截越短。这类元素改按父级
   量：父级的内容宽减去所有兄弟和列间距，剩下的才是文字能占的。 */
const availableWidth=(element:HTMLElement)=>{
  if(element.dataset.middleTruncateWithin===undefined)return element.clientWidth;
  const box=element.parentElement;if(!box)return element.clientWidth;
  const style=getComputedStyle(box);
  const gap=parseFloat(style.columnGap)||0;
  let used=0,shown=0;
  for(const child of box.children){
    const width=child.getBoundingClientRect().width;
    if(child===element){shown++;continue}
    if(width>0){used+=width;shown++}
  }
  return box.getBoundingClientRect().width-(parseFloat(style.paddingLeft)||0)-(parseFloat(style.paddingRight)||0)
    -used-gap*Math.max(0,shown-1);
};

const paint=(element:HTMLElement)=>{
  const state=states.get(element);if(!state||!element.isConnected)return;
  const available=availableWidth(element);
  const rendered=available>0
    ? middleTruncateText(state.full,candidate=>measuredWidth(element,candidate)<=available)
    : state.full;
  state.rendered=rendered;
  if(element.textContent!==rendered)element.textContent=rendered;
  const truncated=rendered!==state.full;
  element.classList.toggle('middle-truncated',truncated);
  element.setAttribute('aria-label',state.full);
  if(!element.hasAttribute('title')||element.dataset.middleTitle==='true'){
    element.title=state.full;element.dataset.middleTitle='true';
  }
};

const schedule=(element:Element)=>{
  const state=states.get(element);if(!state||state.raf)return;
  state.raf=requestAnimationFrame(()=>{state.raf=0;paint(element as HTMLElement)});
};

const bind=(element:Element)=>{
  if(!(element instanceof HTMLElement))return;
  if(!states.has(element))states.set(element,{full:element.textContent||'',rendered:element.textContent||'',raf:0});
  if(!observed.has(element)){
    observed.add(element);
    tracked.add(element);
    /* 按父级量的元素跟着父级的尺寸重算：它自己的盒子只会随文字变，不会随窗口变。 */
    if(element.dataset.middleTruncateWithin!==undefined&&element.parentElement)resizeObserver?.observe(element.parentElement);
    else resizeObserver?.observe(element);
  }
  schedule(element);
};

const scan=(node:Node)=>{
  if(node.nodeType!==Node.ELEMENT_NODE)return;
  const element=node as Element;
  if(element.matches('[data-middle-truncate]'))bind(element);
  element.querySelectorAll('[data-middle-truncate]').forEach(bind);
};

function initMiddleTruncate(root:Document|Element=document):()=>void{
  disposeTruncation?.();
  observed=new WeakSet<Element>();tracked=new Set<Element>();
  const elements=tracked;
  let live=true;
  resizeObserver=new ResizeObserver(entries=>entries.forEach(entry=>{
    if(!live)return;
    schedule(entry.target);
    entry.target.querySelectorAll?.(':scope>[data-middle-truncate-within]').forEach(schedule);
  }));
  const resize=resizeObserver;
  scan((root as Document).documentElement||root);
  const mutations=new MutationObserver(records=>records.forEach(record=>{
    if(!live)return;
    record.addedNodes.forEach(scan);
    const element=record.target.nodeType===Node.TEXT_NODE?record.target.parentElement:record.target as Element;
    const target=element?.closest?.('[data-middle-truncate]') as HTMLElement|null|undefined;
    if(!target)return;
    const state=states.get(target);
    if(state&&target.textContent!==state.rendered){
      state.full=target.textContent||'';
      if(target.dataset.middleTitle==='true')target.removeAttribute('title');
      schedule(target);
    }
  }));
  mutations.observe((root as Document).body||root,{childList:true,characterData:true,subtree:true});
  document.fonts?.ready?.then(()=>{if(live)root.querySelectorAll('[data-middle-truncate]').forEach(schedule)});
  const copy:EventListener=event=>{
    const selection=document.getSelection();
    if(!selection||selection.isCollapsed)return;
    const anchor=selection.anchorNode?.nodeType===Node.ELEMENT_NODE?selection.anchorNode as Element:selection.anchorNode?.parentElement;
    const target=anchor?.closest?.('[data-middle-truncate]');
    const state=target&&states.get(target);
    if(!state||!target.contains(selection.focusNode)||!(event as ClipboardEvent).clipboardData)return;
    (event as ClipboardEvent).clipboardData?.setData('text/plain',state.full);event.preventDefault();
  };
  root.addEventListener('copy',copy);
  const dispose=()=>{
    if(!live)return;live=false;
    resize.disconnect();mutations.disconnect();root.removeEventListener('copy',copy);
    for(const element of elements){const state=states.get(element);if(state?.raf){cancelAnimationFrame(state.raf);state.raf=0}}
    elements.clear();
    if(disposeTruncation===dispose){disposeTruncation=null;resizeObserver=null}
  };
  disposeTruncation=dispose;
  return dispose;
}

export { initMiddleTruncate, middleTruncateText };
