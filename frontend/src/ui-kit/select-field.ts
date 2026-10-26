import { esc, icon } from '../core';

import { wireAnchoredMenu } from './anchored-menu';

/** 一项：值、显示文字，可选的站标（雪碧图字形名或内嵌 PNG 的 data URL）。 */
export type SelectOption = readonly [value: string, text: string, mark?: string | undefined];
/** `wireSelectField` 接好的根元素：`value`、`disabled` 按原生 select 的写法读写。 */
export type SelectField = HTMLElement & { value: string; disabled: boolean };

/* Geist Select：站内每一个下拉都是它，没有一个走浏览器自带的 select 控件。

   原生下拉的弹出层由操作系统画，不认站内色板：浅色主题下它要么跟着系统换成另一套灰白，
   要么只能用 `color-scheme` 整个按回深色——设置面板里那七个此前就是被按成深色的，
   白底页面上七块黑。2026-09-04 实测 vercel.com 后台：整站没有一个原生下拉，触发器是
   button，面板是自绘 listbox，面板底色就是页面底色（浅色下纯白 --ds-background-100），
   行高 36px、圆角 6px、行内边距 0 8px，悬停与选中都是 5% 中性填充，选中项没有勾。Peach
   的行高走站内已有的 --control-h（38px），面板与定位复用 .popmenu 和 wireAnchoredMenu。

   选中态只有填充，行内也就没有一枚勾要占位：行首腾出来的那一格正好让每一项的站标和
   触发器里那一枚落在同一列上。面板的左内边距（.popmenu 的 6px 加行的 6px）按触发器的
   12px 凑，两处图标于是对齐到一像素。

   `value`、`disabled` 和 `change` 三样按原生 select 的写法留在根元素上：调用方读写它跟
   读写原生下拉一样，换掉的只是画法。 */
export function selectOptionIconHtml(mark?: string): string {
  return !mark?'':mark.startsWith('data:image/png;base64,')
    ?`<img class="gselectmark" src="${esc(mark)}" alt="" width="16" height="16">`:icon(mark,'gselectmark');
}
export function selectFieldHtml(
  options: readonly SelectOption[], current: string,
  { label = '', attr = '', className = '' }: { label?: string; attr?: string; className?: string } = {},
): string {
  const chosen:SelectOption=options.find(([value])=>String(value)===String(current))||options[0]||['',''];
  const content=([,text,mark]:SelectOption)=>`${selectOptionIconHtml(mark)}${esc(text)}`;
  const rows=options.map(([value,text,mark])=>
    `<button type="button" role="option" data-select-option="${esc(value)}"
      aria-selected="${String(value)===String(chosen[0])}" tabindex="-1"><span data-select-content>${content([value,text,mark])}</span></button>`).join('');
  return `<div class="ui-gselect${className?` ${esc(className)}`:''}" ${attr}>
    <button type="button" class="gselectfield" data-select-trigger aria-haspopup="listbox"
      aria-expanded="false" aria-label="${esc(label)}"><span data-select-label>${content(chosen)}</span>${icon('chevron-down')}</button>
    <div class="popmenu gselectmenu" role="listbox" aria-label="${esc(label)}" popover="manual" data-select-menu hidden>${rows}</div></div>`;
}

/** 接上 selectFieldHtml 画出来的一个下拉；返回的就是根元素，带 value / disabled。 */
export function wireSelectField(root: HTMLElement): SelectField {
  const trigger=root.querySelector<HTMLButtonElement>('[data-select-trigger]')!;
  const menu=root.querySelector<HTMLElement>('[data-select-menu]')!,label=root.querySelector('[data-select-label]')!;
  const options=()=>[...menu.querySelectorAll<HTMLElement>('[data-select-option]')];
  const current=()=>menu.querySelector<HTMLElement>('[aria-selected="true"]');
  /* 面板至少和触发器一样宽。菜单是 fixed 的，宽度不会自己跟着触发器走，而一个比触发器
     还窄的面板看着不像同一个控件。这条要接在 wireAnchoredMenu 之前：它按当前宽度定位。 */
  trigger.addEventListener('click',()=>{
    const width=`${trigger.getBoundingClientRect().width}px`;
    menu.style.minWidth=width;
    if(root.hasAttribute('data-fixed-width'))menu.style.width=width;
  });
  const anchored=wireAnchoredMenu(root,trigger,menu);
  trigger.addEventListener('click',()=>{if(anchored.isOpen())current()?.focus()});
  const choose=(value:string|undefined)=>{
    const picked=options().find(option=>option.dataset.selectOption===String(value));
    if(!picked)return;
    options().forEach(option=>{
      option.setAttribute('aria-selected',String(option===picked));option.tabIndex=option===picked?0:-1});
    label.innerHTML=picked.querySelector('[data-select-content]')!.innerHTML;
  };
  options().forEach(option=>{
    option.onclick=()=>{
      const changed=option.getAttribute('aria-selected')!=='true';
      choose(option.dataset.selectOption);anchored.setOpen(false);trigger.focus();
      if(changed)root.dispatchEvent(new Event('change',{bubbles:true}));
    };
    option.onkeydown=event=>{
      if(event.key!=='ArrowDown'&&event.key!=='ArrowUp')return;
      event.preventDefault();
      const all=options(),at=all.indexOf(option);
      all[(at+(event.key==='ArrowDown'?1:-1)+all.length)%all.length]!.focus();
    };
  });
  if(current())current()!.tabIndex=0;
  Object.defineProperty(root,'value',{configurable:true,
    get:()=>current()?.dataset.selectOption??'',set:(value:string)=>choose(value)});
  Object.defineProperty(root,'disabled',{configurable:true,
    get:()=>trigger.disabled,set:(value:unknown)=>{trigger.disabled=!!value;if(value)anchored.setOpen(false)}});
  return root as SelectField;
}
