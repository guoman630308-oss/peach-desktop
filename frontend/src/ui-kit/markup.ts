/* 只出 HTML 字符串的那几样 Geist 控件：集合页头、Note、项目横幅、Gauge、Progress、Spinner、
 * Search Input、Loading Dots、Board Tabs、Empty State、徽标与勾选框。
 *
 * 由共享源码直接引用，随 `peach-app.js` 发出；island 里用 `dangerouslySetInnerHTML` 插入。
 * 它们内部已经对文本做转义，且是 Peach 唯一那份 Geist 控件实现——在 island 里另画一遍空态或 Note
 * 就是同一语义的第二份实现，`peach-web-ui` 的门槛不允许。 */
import { esc, icon, requestErrorMessage } from '../core';
import { islandShortcutsEntry } from '../island-skeleton';

const NOTE_VARIANTS=new Set(['secondary','warning','error','success']);

/* `filterRow` 给骨架里那块两排浮层用：槽位标记跟着 HTML 一起生成，否则这一条会自己画成
   第二块浮层。 */
export function collectionHeaderHtml({readout='',controls='',before='',className='',loading=false,filterRow=''}={}){
  return `<div class="entitycollectionhead${className?' '+esc(className):''}"${filterRow?` data-filter-row="${esc(filterRow)}"`:''}>${before}<h3${loading?' class="skeleton"':''}>${readout}</h3>${loading && filterRow === 'bottom' ? islandShortcutsEntry() : ''}${controls}</div>`;
}

/* 逐条明细收在 Note 自己的 <details> 里，默认折叠：一句话的结论后面跟着上千行清单时，
   下面的内容全被推走，而那句结论本身才是要先读到的东西。每条给标题、说明和路径三段：
   只给一个「查看视频」链接的话，是哪个文件得逐个点开才知道。 */
interface NoteDetailItem { label?: string; href?: string; note?: string; hint?: string }
interface NoteDetails { label?: string; items?: NoteDetailItem[]; footnote?: string }
function noteDetailsHtml({label='',items=[],footnote=''}:NoteDetails={}){
  const line=(item:NoteDetailItem)=>`<li>${item.href?`<a href="${esc(item.href)}">${esc(item.label||'')}</a>`
    :`<b>${esc(item.label||'')}</b>`}<span>${esc(item.note||'')}</span>${
    item.hint?`<code>${esc(item.hint)}</code>`:''}</li>`;
  return `<details class="geist-note-details"><summary>${esc(label)}</summary>
    <ul>${items.map(line).join('')}</ul>${footnote?`<p>${esc(footnote)}</p>`:''}</details>`;
}

/* Inline, persistent context beside the field/card/section it describes.
   恢复动作有两种形态：留在本页执行的走按钮，要离开本页才做得成的走链接。
   两者占同一个格子、同一枚 `data-note-action`，Note 的版式不因为它是 <a> 改变。 */
/** Geist Note：字段、卡片、分区旁的持久反馈。 */
export function noteHtml(message:any,{variant='secondary',label='',className='',size='medium',filled=false,actionLabel='',actionHref='',details=null}:{
  variant?: 'secondary' | 'warning' | 'error' | 'success' | (string & {});
  label?: string;
  className?: string;
  size?: 'small' | 'medium' | (string & {});
  actionLabel?: string; filled?: boolean;
  /** 恢复动作要离开本页才做得成时给出目标地址，动作由按钮换成同格子的链接。 */
  actionHref?: string;
  /** 逐条明细，收在 Note 里默认折叠的 details；`hint` 放路径这类次要标注。 */
  details?: NoteDetails | null;
}={}):string{
  const kind=NOTE_VARIANTS.has(variant)?variant:'secondary';
  const symbol=kind==='secondary'?'info':kind==='success'?'check':'alert';
  const role=kind==='error'?' role="alert"':' role="note"';
  const action=!actionLabel?''
    :actionHref?`<a class="geist-button" href="${esc(actionHref)}" data-note-action>${esc(actionLabel)}</a>`
    :`<button type="button" class="geist-button primary" data-note-action>${esc(actionLabel)}</button>`;
  return `<div class="geist-note geist-note-${kind}${className?` ${esc(className)}`:''}${size==='small'?' geist-note-small':''}${filled?' geist-note-filled':''}"${role}>
    ${icon(symbol)}<p>${label?`<b>${esc(label)}</b>`:''}<span>${esc(kind==='error'?requestErrorMessage(message):message)}</span></p>${action}${details?noteDetailsHtml(details):''}</div>`;
}

const PROJECT_BANNER_CLASSES:Record<string,string>={gray:'project-banner-gray',success:'ui-project-banner-success',warning:'ui-project-banner-warning',error:'ui-project-banner-error'};
export function projectBannerHtml(message:unknown,{variant='gray',href,label,value,max}:{
  variant?: string; href?: string; label?: string; value?: unknown; max?: unknown;
}={}):string{
  const kind=['gray','success','warning','error'].includes(variant)?variant:'gray';
  return `<aside class="ui-project-banner ${PROJECT_BANNER_CLASSES[kind]}" role="${kind==='error'?'alert':'status'}"><div>${Number(max)>0?gaugeHtml('任务完成率',value,max):icon(kind==='error'||kind==='warning'?'alert':'info')}<p>${esc(message)}</p></div><a href="${esc(href)}">${esc(label)}</a></aside>`;
}

export function gaugeHtml(label:string,value:unknown,max:unknown=100,{usage=false,compact=false}={}):string{
  const ceiling=Number(max), current=Number(value);
  if(!Number.isFinite(ceiling)||ceiling<=0||!Number.isFinite(current))return `<span>${esc(label)}：未取得</span>`;
  const percent=Math.max(0,Math.min(100,current/ceiling*100));
  const level=usage?(percent>=95?'error':percent>=80?'warning':'normal'):'normal';
  const status=usage?(level==='error'?'空间即将用满':level==='warning'?'空间使用偏高':'空间充足'):'';
  return `<span class="ui-geist-gauge" data-level="${level}" role="progressbar" aria-label="${esc(label+(status?'：'+status:''))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="13"/><circle cx="16" cy="16" r="13" pathLength="100" stroke-dasharray="${percent} 100"/></svg></span>${status&&!compact?`<span class="ui-gauge-status">${status}</span>`:''}`;
}

/** Determinate progress only. Callers supply real units instead of a decorative width. */
export function progressHtml(label:string,value:unknown,max:unknown=100,{variant='active',stops=[]}:{
  variant?: 'active' | 'warning' | 'error' | (string & {}); stops?: { value: unknown; label: string }[];
}={}):string{
  const ceiling=Math.max(0,Number(max)||0);
  const current=Math.max(0,Math.min(Number(value)||0,ceiling));
  const percent=ceiling?current/ceiling*100:0;
  return `<div class="ui-geist-progress" role="progressbar" aria-label="${esc(label)}"
    aria-valuemin="0" aria-valuemax="${ceiling}" aria-valuenow="${current}"
    style="--progress-value:${percent}%;--progress-color:var(${variant==='error'?'--drop':variant==='warning'?'--meter':'--feedback-success'})"><i></i>${stops.filter(stop=>Number(stop.value)>0&&Number(stop.value)<ceiling&&stop.label).map(stop=>`<span class="ui-geist-progress-stop" style="left:${Number(stop.value)/ceiling*100}%" role="img" aria-label="${esc(stop.label)}"></span>`).join('')}</div>`;
}

/**
 * Geist Spinner: immediate feedback for a user-triggered action. Geist names
 * "inline icon refresh" as one of its three cases, so an icon-only key that
 * swaps its glyph for these ten bars is the prescribed shape, not a fallback.
 *
 * `label` names the work in flight (`正在换一批`), never the action the button
 * already carries. This span is a `role=status` region sitting inside a button
 * that has its own accessible name: repeating that name announces the same
 * words twice and tells the reader nothing changed.
 */
export function spinnerHtml(label='加载中'):string{
  const bars=Array.from({length:10},(_,index)=>
    `<i aria-hidden="true" style="--spinner-angle:${index*36}deg;--spinner-delay:${index*100-900}ms"></i>`).join('');
  return `<span class="ui-geist-spinner" role="status" aria-label="${esc(label)}">${bars}</span>`;
}

/**
 * Geist Search Input: search icon as a prefix, swapped in place for a Spinner
 * while the query runs, and the input geometry never changes. Read-only queries
 * carry no submit button, so the accessible name lives in `aria-label` — a
 * placeholder is not a label, it disappears the moment there is text to read.
 */
export function searchInputHtml({label,id='',name='',value='',placeholder='',attrs=''}:{
  label?: string; id?: string; name?: string; value?: unknown; placeholder?: string; attrs?: string;
}={}):string{
  const parts=[
    'type="search" enterkeyhint="search"',
    id?`id="${esc(id)}"`:'',
    name?`name="${esc(name)}"`:'',
    placeholder?`placeholder="${esc(placeholder)}"`:'',
    `value="${esc(value)}"`,
    `aria-label="${esc(label)}"`,
    'spellcheck="false" autocomplete="off"',
    attrs,
  ].filter(Boolean).join(' ');
  return `<div class="geist-search" data-search-input>
    <span class="geist-search-prefix" data-search-prefix>${icon('search')}</span>
    <input ${parts}></div>`;
}

/** Geist Loading Dots: indeterminate work continuing in the background. */
export function loadingDotsHtml(label='正在处理', {className=''}={}):string{
  return `<span class="ui-geist-loading${className?` ${esc(className)}`:''}" role="status">
    <span class="ui-geist-loading-dots" aria-hidden="true"><i></i><i></i><i></i></span>
    <span>${esc(label)}</span></span>`;
}

/**
 * 页面分区 Tabs：一排互斥的「这一页现在摆的是哪一组东西」。
 *
 * 索引页用它切厂牌／事务所与本地／在线两套词表：两档各是一条地址。它回答的是页面层级的
 * 「在哪一页」，不是给当前这批加一条筛选——筛选归玻璃条上的药丸。
 * 每一枚都是 `role=tab`，当前项写 `aria-selected`；选中态由 `ui-board-local-nav`
 * 的中性填充样式提供，这里只出 DOM。计数是可选的尾随徽标，口径由调用方
 * 给：Tabs 自己不算数。前置字形也是可选的，只在它指向对象（厂牌、事务所、本地、订阅源）
 * 时出现。
 */
export function boardTabsHtml(items:{value:unknown;label:unknown;count?:unknown;symbol?:string}[],
  {active='',attr='data-tab',label='页面视图',className='',panel=''}:{
    active?: unknown; attr?: string; label?: string; className?: string; panel?: string;
  }={}):string{
  const tabs=items.map(({value,label:text,count,symbol})=>{
    const selected=String(value)===String(active);
    const badge=count==null?'':`<span class="ui-board-tab-count">${esc(Number(count).toLocaleString())}</span>`;
    return `<button type="button" role="tab" ${attr}="${esc(value)}" aria-selected="${selected}"${
      panel?` aria-controls="${esc(panel)}"`:''}>${symbol?icon(symbol):''}${esc(text)}${badge}</button>`;
  }).join('');
  return `<div class="ui-board-local-nav ui-board-tabs${className?` ${esc(className)}`:''}" role="tablist" aria-label="${esc(label)}">${tabs}</div>`;
}

/** Geist Empty State: icon tile, title and explanatory copy stay one semantic unit. */
/** Geist Empty State：图标、标题与说明同处一个组件内。 */
export function emptyStateHtml(iconName:string,title:unknown,description:unknown,{className='',actions=''}={}):string{
  return `<div class="ui-emptystate${className?` ${esc(className)}`:''}" data-geist-empty-state role="status">
    <div class="ui-es-icon" aria-hidden="true">${icon(iconName)}</div>
    <div class="ui-es-copy"><h3>${esc(title)}</h3><p>${esc(description)}</p></div>
    ${actions?`<div class="ui-es-actions">${actions}</div>`:''}
  </div>`;
}

/**
 * 共用勾选框。原生 checkbox 在暗色下由浏览器自绘，跟站内别的控件不是同一套语言；
 * `accent-color` 也只能改选中色，未选中态连悬停反馈都给不了。所以自绘一份，关注
 * 列表、来源筛选、候选清单、标签匹配和设置项共用它。
 */
export function badgeHtml(text:unknown):string{return `<span class="ui-geist-badge">${esc(text)}</span>`}

export function checkboxHtml(inputAttrs=''):string{
  return `<span class="ui-pcheck"><input type="checkbox" ${inputAttrs}><span aria-hidden="true">${icon('check')}</span></span>`;
}
