/* 原地换态的一组辅助：字形互换、文字换行、读数错峰、清空输入框、计数徽标与标题逐行揭示。
   由共享源码直接引用，随 `peach-app.js` 发出；计数徽标上一次的值是模块级状态，只有一份。

   形状和 CSS 在同目录的 `motion.css`（文字换行那一条在 `web/css/25-motion.css`），那里也写着每一条的
   来源与取值。这一层只负责
   「什么时候换」：动画本身一律交给 CSS，JS 不读也不写具体的毫秒数。 */
import { esc, icon } from '../core';

/**
 * 两枚字形叠在一格里，换态只改容器上的 `data-icon-state`。
 *
 * 直接 `innerHTML=icon(...)` 的地方都可以换成它：重写 innerHTML 会把旧字形连同它正在
 * 走的动画一起丢掉，新字形也没有起点可走，读出来是一次硬切。
 */
export function iconSwapHtml(a:string,b:string,state:'a'|'b'|(string&{})='a',{className='',iconClass='',label=''}={}):string{
  return `<span class="iconswap${className?' '+esc(className):''}" data-icon-swap data-icon-state="${state==='b'?'b':'a'}"${
    label?` aria-label="${esc(label)}"`:''}><span data-icon="a">${icon(a,iconClass)}</span><span data-icon="b">${icon(b,iconClass)}</span></span>`;
}
/** 把 `iconSwapHtml` 画出来的那一枚切到 a 或 b；`root` 可以是它本身或它的祖先。 */
export function setIconSwap(root:any,state:'a'|'b'|boolean|(string&{})):any{
  const el=root&&(root.matches?.('[data-icon-swap]')?root:root.querySelector?.('[data-icon-swap]'));
  if(el)el.dataset.iconState=state==='b'||state===true?'b':'a';
  return el;
}

/**
 * 一行字换成另一行字：旧字往上糊掉，新字从下方回到原位。
 *
 * 值没变就什么也不做——同一次重绘里把同样的字再写一遍是常态，跟着抖一下会让页面看起来
 * 一直在变。首次写入（这一格还空着）也不放动画：那是内容第一次出现，归骨架那一段。
 */
export function swapText(el:any,text:unknown,{html=false}={}):void{
  if(!el)return;
  const next=String(text??''),write=()=>{if(html)el.innerHTML=next;else el.textContent=next};
  const previous=el.dataset.swapText;
  if(previous===next&&(el.textContent||'').length)return;
  el.dataset.swapText=next;
  el.classList.add('textswap');
  if(previous===undefined||previous===next){write();return}
  el.classList.remove('entering');
  el.classList.add('leaving');
  const enter=()=>{
    el.classList.remove('leaving');
    write();
    el.classList.add('entering');
    el.getBoundingClientRect();
    el.classList.remove('entering');
  };
  const done=(event:Event)=>{if(event.target!==el)return;el.removeEventListener('transitionend',done);enter()};
  el.addEventListener('transitionend',done);
  /* 兜底：`--motion-swap` 归零或元素此刻不可见时 `transitionend` 不会来，
     不兜的话这一行就永远停在透明。 */
  setTimeout(()=>{if(el.classList.contains('leaving')){el.removeEventListener('transitionend',done);enter()}},400);
}

/**
 * 读数按位错峰长出来。把 `text` 拆成一个个字符，数字各占一档延迟，分隔符跟着前一档。
 *
 * 和 `swapText` 一样只在值真变时触发，首次写入不放动画。
 */
export function popCount(el:any,text:unknown):void{
  if(!el)return;
  const next=String(text??''),previous=el.dataset.popCount;
  if(previous===next&&el.firstElementChild)return;
  el.dataset.popCount=next;
  let at=-1;
  el.innerHTML=`<span class="digits">${[...next].map(ch=>{
    if(/\d/.test(ch))at+=1;
    return `<span style="--digit-at:${Math.max(at,0)}">${esc(ch)}</span>`;
  }).join('')}</span>`;
  /* 上一个值没登记过（这一格第一次出现）或压根没变，就只把字写上去。调用点整块重绘时
     把上一次的读数写回 `dataset.popCount` 再调，这一格才知道自己是换了值还是刚建出来。 */
  if(previous===undefined||previous===next)return;
  const group=el.firstElementChild;
  group.getBoundingClientRect();
  group.classList.add('popping');
}

/**
 * 清空输入框，框里那段字往上飘着糊掉。
 *
 * 输入框的 value 是一瞬间没的，动画只能挂在别处：这里照着它此刻的位置和字体摆一份同样
 * 的字，让那一份去飘，真正的输入框当场就空了，光标和输入法一刻也不等。`host` 缺省取
 * 输入框的父元素，它必须是定位祖先，否则这一层会飘到页面左上角去。
 */
export function dissolveValue(input:any,host:any=input&&input.parentElement,
  {text=input&&input.value,scrollLeft=input&&input.scrollLeft||0}:{text?:string;scrollLeft?:number}={}):()=>void{
  if(!input||!host)return ()=>{};
  host.querySelectorAll(':scope > .cleardissolve').forEach((node:Element)=>node.remove());
  input.classList.remove('dissolving');
  input.value='';
  if(!text||!input.isConnected)return ()=>{};
  const box=input.getBoundingClientRect(),frame=host.getBoundingClientRect();
  if(!box.width)return ()=>{};
  const ghost=document.createElement('span');
  ghost.className='cleardissolve';
  ghost.setAttribute('aria-hidden','true');
  const value=document.createElement('span');
  value.dataset.dissolveValue='';
  value.textContent=text;
  value.style.transform=`translateX(-${Math.max(0,scrollLeft)}px)`;
  ghost.append(value);
  const style=getComputedStyle(input);
  Object.assign(ghost.style,{left:`${box.left-frame.left-host.clientLeft}px`,
    top:`${box.top-frame.top-host.clientTop}px`,width:`${box.width}px`,height:`${box.height}px`,
    boxSizing:'border-box',padding:style.padding,font:style.font,lineHeight:style.lineHeight,
    letterSpacing:style.letterSpacing,textAlign:style.textAlign,direction:style.direction});
  input.classList.add('dissolving');
  host.append(ghost);
  let active=true,timer:ReturnType<typeof setTimeout>|null=null;
  const drop=()=>{
    if(!active)return;
    active=false;
    if(timer!==null)clearTimeout(timer);
    ghost.removeEventListener('animationend',drop);
    ghost.remove();
    if(!host.querySelector(':scope > .cleardissolve'))input.classList.remove('dissolving');
  };
  ghost.addEventListener('animationend',drop);
  /* 兜底：`--motion-reveal` 归零或这一刻元素不可见时 `animationend` 不会来，
     不兜的话这份复制品就永远盖在输入框上。 */
  timer=setTimeout(drop,1000);
  return drop;
}

/* 计数徽标上一次是多少。徽标所在的那一排每次筛选都整块重画，节点本身留不住上一个值，
   所以记在这里，键由调用方给的范围加徽标自己的键拼成。 */
const badgeCounts=new Map<string,string>();
/**
 * 一排计数徽标里值真变了的那几枚弹一下，其余的不动。
 *
 * 首次见到某一枚不弹：那是它第一次出现在这个范围里，整排一起弹是一屏烟花，而它要说的
 * 是「这一枚刚变了」。`scope` 区分同一个键在不同排里的计数（顶部筛选条和抽屉各有一份）。
 */
export function popBadges(root:any,scope=''):void{
  if(!root)return;
  root.querySelectorAll('[data-count-badge]').forEach((el:HTMLElement)=>{
    const key=`${scope}\u0000${el.dataset.countBadge}`,next=el.textContent||'';
    const previous=badgeCounts.get(key);
    badgeCounts.set(key,next);
    el.classList.add('countbadge');
    if(previous===undefined||previous===next)return;
    el.classList.add('popped');
    el.getBoundingClientRect();
    el.classList.remove('popped');
  });
}

/**
 * 一段标题从模糊里逐行揭示出来。
 *
 * 按行不按词：逐词要把整句拆成一串 `<span>`，复制出来的文字会跟着散架，而这几处标题
 * 正是最常被复制走的那几段字。走完把类名和行序一起摘掉，终点帧不留下 `filter`。
 */
export function revealTexts(root:any,selector='[data-reveal-line]'):void{
  if(!root)return;
  const lines:HTMLElement[]=[...root.querySelectorAll(selector)];
  if(!lines.length)return;
  /* 起止两个状态都挂在行自己身上，不挂在外面那一层：这几处标题里有一对是 `<body>` 的
     直接子元素（页面标题和它的说明行），要共同的祖先就只剩 body 本身。 */
  lines.forEach((el,i)=>{el.classList.remove('revealing');el.classList.add('revealline');
    el.style.setProperty('--reveal-at',String(i))});
  lines[0]!.getBoundingClientRect();
  lines.forEach(el=>el.classList.add('revealing'));
  const drop=()=>lines.forEach(el=>{el.classList.remove('revealline','revealing');
    el.style.removeProperty('--reveal-at')});
  const last=lines[lines.length-1]!;
  const done=(event:Event)=>{if(event.target!==last)return;last.removeEventListener('transitionend',done);drop()};
  last.addEventListener('transitionend',done);
  setTimeout(()=>{if(last.classList.contains('revealline')){last.removeEventListener('transitionend',done);drop()}},1200);
}
