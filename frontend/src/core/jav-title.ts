/* JAV 标题：从 item 计算番号、版本徽章与标题。
 * 纯字符串逻辑，只依赖 core 的 esc，不碰 DOM、不读 state。
 * Application 与 React 页面直接引用本模块，标题规则只有一份。
 * 后缀只对 is_jav 剥离；官方标题优先取含日文的一条，再按字段顺序回落。 */

import { esc } from './index';

/** 算标题要读的那几个字段。 */
export interface JavTitleSource {
  name?: string | null;
  code?: string | null;
  display_code?: string | null;
  is_jav?: boolean | number | null;
  catalog_title?: string | null;
  original_title?: string | null;
  display_title?: string | null;
  edition_badges?: string[] | null;
  width?: number | null;
  height?: number | null;
}
type Item = JavTitleSource | null | undefined;

const JAV_MEDIA_SUFFIX=/\.(?:mp4|mkv|avi|wmv|mov|m4v|webm|ts|m2ts|mts|mpg|mpeg|flv|rm|rmvb|iso)$/i;

export const javFileDisplayName=(it:Item,value=it?.name)=>{
  const name=String(value||'').trim();
  return it?.is_jav?name.replace(JAV_MEDIA_SUFFIX,''):name;
};

export const hasJapaneseText=(value:unknown)=>/[぀-ヿ㐀-鿿]/.test(String(value||''));

export const javPreferredTitle=(it:Item)=>{
  const titles=[it?.catalog_title,it?.original_title].map(value=>String(value||'').trim()).filter(Boolean);
  return titles.find(hasJapaneseText)||titles[0]||'';
};

export function javTitleParts(it:Item,value=it?.name):{code:string;title:string;badges?:string[]}{
  const name=javFileDisplayName(it,value),code=String(it?.code||'').trim().toUpperCase();
  if(!it?.is_jav||!code)return {code:'',title:name};
  const displayCode=String(it?.display_code||code).trim().toUpperCase();
  const upper=name.toUpperCase(),hasPrefix=upper===code||upper===displayCode
    ||(upper.startsWith(code)&&/^[\s._\-[\]]/.test(name.slice(code.length)))
    ||(upper.startsWith(displayCode)&&/^[\s._\-[\]]/.test(name.slice(displayCode.length)));
  const prefixLength=upper.startsWith(displayCode)?displayCode.length:code.length;
  const filenameTitle=(hasPrefix?name.slice(prefixLength):name).replace(/^[\s._-]+/,'').trim();
  const officialTitle=javPreferredTitle(it);
  // API 显式返回空 display_title 也是有意义的“清洁后无标题”，不能再回退到脏文件名。
  const cleanFallback=Object.prototype.hasOwnProperty.call(it||{},'display_title')
    ?String(it.display_title||'').trim():filenameTitle;
  const title=officialTitle||cleanFallback;
  const badges=Array.isArray(it?.edition_badges)?it.edition_badges.filter(
    label=>['中字','无码','无码破解'].includes(label)):[];
  const dimensions=[Number(it?.width)||0,Number(it?.height)||0];
  if(Math.min(...dimensions)>=2160&&Math.max(...dimensions)>=3840)badges.push('4K');
  return {code:displayCode,title,badges};
}

/** 同一条目的纯文本形态，用于无障碍名称。 */
export const javDisplayName=(it:Item,value=it?.name)=>{
  const {code,title,badges=[]}=javTitleParts(it,value);
  return code?[code,...badges,title].filter(Boolean).join(' '):title;
};

/** 番号 + 版次徽章 + 标题的 HTML。非 JAV 条目退化成转义后的文件名。 */
export function javTitleHtml(it:Item,value=it?.name){
  const {code,title,badges=[]}=javTitleParts(it,value);
  if(!code)return esc(title);
  const edition=badges.map(label=>`<small class="javedition ${label==='中字'?'subtitle':label==='无码'?'uncensored':label==='无码破解'?'cracked':'quality'}">${esc(label)}</small>`).join('');
  return `<span class="ui-javidentity"><strong class="ui-javcode">${esc(code)}</strong>${edition}</span>${title?` <span class="ui-javtitle">${esc(title)}</span>`:''}`;
}
