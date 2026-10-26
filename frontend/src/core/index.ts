/* 前端底层：DOM 取元素、请求、转义、格式化与路由常量。
 * Application 与 React 页面直接引用共享源码，LOC、fmtDur 等语义契约只有一份。
 * 本层不引入其他前端模块，避免被所有域引用的基础层反向依赖。
 * route() 留在应用导航域，因为它还要同步标题与界面动作。 */
import { entityLinkId, registerEntityLink, registerResponseEntities } from './entity-links';
import { SOURCE_LABELS } from './media-sources';
import { requestErrorMessage } from './request-error';
import { iconGeometry } from './icon-geometry';
export { registerEntityLink, registerResponseEntities } from './entity-links';
export { requestErrorMessage } from './request-error';
const $=(s:string)=>document.querySelector(s);
/* 方形槽位里塞不进 1.4:1 的字形：按宽度对齐它就矮一截，挨着满格的 Lucide
   图标看就是小一号。这几枚外层 viewBox 跟着 symbol 的比例走，槽位由 CSS 按高定宽。 */
/** 雪碧图字形的 SVG 片段；`cls` 是挂在 `<svg>` 上的类（如 `ui-externalmark`）。 */
const icon=(name:string,cls='')=>{
  const {wide,viewBox}=iconGeometry(name),classes=[wide?'iconwide':'',cls].filter(Boolean).join(' ');
  return `<svg${classes?` class="${classes}"`:''} viewBox="${viewBox}" aria-hidden="true"><use href="#i-${name}"/></svg>`};
/* `signal` 写成显式的一项，不靠 Object.assign 顺带透传：表面切换要能作废上一屏
   还没读完的请求，「这个请求可被取消」得在签名上看得见。 */
/** 遗留层的取数入口：JSON 请求体，失败抛带人话原因的 Error。 */
const api=async(p:string,o?:RequestInit):Promise<unknown>=>{
  if(!o?.method||o.method==='GET'){
    const library=sessionStorage.getItem('peach.library');
    if(library&&/^\/api\/(items|facets)(\?|$)/.test(p))p+=(p.includes('?')?'&':'?')+'library='+encodeURIComponent(library);
  }
  const {signal=null,...rest}=o||{};
  const init:RequestInit={headers:{'Content-Type':'application/json'},...rest};
  if(signal)init.signal=signal;
  let response:Response;
  try{response=await fetch(p,init)}catch(error:any){if(error?.name==='AbortError')throw error;throw new Error(requestErrorMessage(error),{cause:error})}
  let payload:any=null;
  try{payload=await response.json()}catch{}
  if(!response.ok){
    const detail=payload&&(payload.message||payload.detail||payload.error);
    throw new Error(requestErrorMessage(detail,response.status));
  }
  registerResponseEntities(p,payload);
  return payload;
};
/* 取消不是失败。abort 只可能来自表面切换，调用点本来就有一条「已过期」分支要走，
   不该顺手弹一个「请求失败」的错误提示。 */
const isAbort=(error:any):boolean=>error?.name==='AbortError';
/* 有界并发的批量请求。串行发一千次 POST 是实测的卡点（批量标记「已看」要几分钟，
   界面全程按住），而一次全发出去等于自己挤自己：浏览器对同一 host 只有 6 条
   HTTP/1.1 连接，多出来的排在队里，连同一时间的正常浏览请求一起等。所以固定几个
   工人按序取任务。返回值顺序与输入一致；某一项失败只记下原因，不中断整批——
   批量操作里一条失败不该把其余几百条一起放弃。 */
const mapLimit=async<T,R>(items:Iterable<T>,limit:number,run:(item:T,index:number)=>Promise<R>)
  :Promise<({ok:true;value:R}|{ok:false;error:unknown})[]>=>{
  const list=[...items],results=new Array<{ok:true;value:R}|{ok:false;error:unknown}>(list.length);
  let next=0;
  const worker=async()=>{
    while(next<list.length){
      const index=next++;
      try{results[index]={ok:true,value:await run(list[index] as T,index)}}
      catch(error){results[index]={ok:false,error}}
    }
  };
  const workers=Math.min(Math.max(1,limit),list.length);
  await Promise.all(Array.from({length:workers},worker));
  return results;
};
const STATE_ROUTES:Record<string,string>={fresh:'/unseen',later:'/watch-later',flagged:'/flagged',ads:'/junk-files'};
const ROUTE_STATES:Record<string,string>=Object.fromEntries(Object.entries(STATE_ROUTES).map(([state,path])=>[path,state]));
const STATE_LABELS:Record<string,string>={fresh:'没看过',later:'稍后看',flagged:'已标记',ads:'垃圾文件'};
const isCatalogPath=(path:string)=>path==='/'||path==='/wall'||Object.prototype.hasOwnProperty.call(ROUTE_STATES,path);
const ENTITY_ROUTES:Record<string,string>={performer:'performers',studio:'studios',creator:'creators',series:'series',agency:'agencies',tag:'tags'};
const ROUTE_ENTITIES:Record<string,string>={performers:'performer',studios:'studio',creators:'creator',series:'series',agencies:'agency',tags:'tag'};
/** 实体资料页使用已取得的稳定身份。姓名地址由资料路由解析为 ID。 */
const entityPath=(kind:string,name:string,id?:number|null)=>{
  if(id)registerEntityLink(kind,name,id);
  const target=id||entityLinkId(kind,name);
  return `/${ENTITY_ROUTES[kind]||kind}/${target||encodeURIComponent(name)}`;
};
const esc=(s:unknown)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'} as Record<string,string>)[c] as string);
/* 外链上写的是站点的短名：事务所和片商平时就被叫作 NAX、T-POWERS、DMM，而数据库里的
   label 是采集时抄下来的全称（`New Actor eXperience`、`DMM 检索`），域名又要人先在
   `OFFICIAL.NAX-PRO.COM` 里认出哪一段是名字。两样都比短名难读，一排链接里尤其。

   按主机名查表，末尾后缀也算（`official.nax-pro.com` 命中 `nax-pro.com`）。表里没有的
   退回数据库 label：这张表覆盖常来常往的那些站，剩下的长尾不值得逐条登记，而 label 至少
   是人写过的。数据库里的 label 不动——它是采集证据，显示是另一件事。 */
const SITE_NAMES:[string,string][]=[['nax-pro.com','NAX'],['t-powers.co.jp','T-POWERS'],['dmm.co.jp','DMM'],
  ['mgstage.com','MGStage'],['av-event.jp','AV-EVENT'],['k-mib.com','K-MIB'],
  ['minnano-av.com','みんなのAV'],
  ['km-produce.com','KMP'],['mousouzoku-av.com','妄想族'],['mines-pro.jp','マインズ'],
  ['lightpro.jp','LIGHT'],['eltra.jp','ELTRA'],['linx.live','LINX'],['bambi.ne.jp','Bambi'],
  ['prestige-av.com','Prestige'],['cmore.jp','C-more'],['so-agent.jp','SO MODEL'],
  ['attractive-llc.net','Attractive'],['krone-web.jp','KRONE'],['actentertainment.jp','ACT'],
  ['crusegroup.net','Cruse'],['prime-recruit.com','Prime'],['life-promotion.com','Life'],
  ['8man.jp','Eightman'],['senzai.tv','Five'],['maxing.jp','MAXING'],['s1s1s1.com','S1'],
  ['ideapocket.com','Idea Pocket'],['faleno.jp','FALENO'],['capsule.bz','Capsule'],
  ['sod.co.jp','SOD'],['tokyo-hot.com','Tokyo-Hot'],['heyzo.com','HEYZO'],
  ['dogma.co.jp','DOGMA'],['naturalhigh.co.jp','Natural High'],['bangbros.com','BangBros'],
  ['dorcelclub.com','Dorcel']];
const bareHost=(host:unknown)=>String(host).replace(/^www\./,'').toLowerCase();
const linkHost=(url:unknown)=>{try{return bareHost(new URL(url as string).hostname)}catch{return ''}};
const siteName=(url:unknown)=>{
  const host=linkHost(url);
  return host&&SITE_NAMES.find(([domain])=>host===domain||host.endsWith('.'+domain))?.[1]||'';
};
/* 服务端处理过的链接图标：单色字形的 favicon 会被做成「品牌色底 + 白色主体」，
   做不了就把原图按 32 px 转出来。放服务端有三个理由：它要读别人站点的图、要缓存，
   而且这样浏览器不再直接向对方站点发请求（也就不泄露正在看谁的资料页）。

   传的是链接 id 而不是地址。跟 `/follow-stream` 同一条规矩：服务端只取数据库里已有的
   地址，绝不去取前端递过来的任意 URL——那等于开一个任意地址抓取的口子。 */
const linkMarkUrl=(link:{link_id?:number|null})=>`/link-mark?id=${encodeURIComponent(link.link_id ?? '')}`;
/* 采集来源和口味排行里那些站点的圆标，同一条规矩的另一个入口：递的是服务端已经
   知道的键——采集来源键，或口味域名白名单里的那条后缀——不是地址。 */
const siteMarkUrl=(params:{source:string}|{domain:string})=>`/site-mark?${new URLSearchParams(params)}`;
/* 图标库里有的常见社媒走内联品牌标记，连 favicon 都不取。

   favicon 是别人服务器上的一张小位图：X 和 Instagram 直接挡掉爬取，资料页上只剩一只
   地球；取得到的也多是 16×16，放进 32 px 的圆里必然糊。站点自己给的大图又是另一种坏
   法——Threads、TikTok 给的是 iOS 应用图标那种圆角方图，四边一圈高光照着方角画，裁成圆
   之后沿圆周露出一道白，而那圈高光是人家设计的一部分，`/link-mark` 抠不掉。内联 SVG
   两样都没有，还省一次跨站请求，场色是品牌自己的那块、不跟着主题走。
   名单按图标库的覆盖面定，不按链接条数：Phosphor 有这七家的字形，一家一枚配齐；库里
   没有的（livedoor、ameblo、lit.link、pub.linx.live 这类）继续走 `/link-mark`，那边按
   站点自己的图标合成品牌色圆底。 */
const BRAND_ICONS:[string[],string][]=[[['x.com','twitter.com'],'brand-x'],[['instagram.com'],'brand-instagram'],
  [['threads.com','threads.net'],'brand-threads'],[['tiktok.com'],'brand-tiktok'],
  [['youtube.com','youtu.be'],'brand-youtube'],[['facebook.com','fb.com'],'brand-facebook'],
  [['linktr.ee','linktree.com'],'brand-linktree']];
const brandIcon=(url:unknown)=>{
  const host=linkHost(url);
  return host&&BRAND_ICONS.find(([hosts])=>hosts.some(d=>host===d||host.endsWith('.'+d)))?.[1]||'';
};
/** 名字比对用的折叠：NFKC、去首尾空白、小写。 */
const foldName=(s:unknown)=>String(s??'').normalize('NFKC').trim().toLocaleLowerCase();
/* 官网链接上那行字。写站点短名（`siteName`），表里没有就用数据库 label。

   厂牌页和事务所页上指回自家站的写「官方网站」：页头已经是公司名，链接再写一遍 S1、
   LIGHT 只是重复。人物页照旧写名字，那里的名字说的是她归哪一家，正是要看的信息。名字是
   别家的（Jackson 页上链到 Prestige 的名录页）也保留，那说的是另一家。自家的判据是
   短名或 label 与规范名、别名互相包含：`C-more` 之于 `C-more Entertainment`，短名是缩写
   时看 label（`NAX` 那条的 label 是 `New Actor eXperience`）。label 只是这条链接自己的
   主机名（`www.ran-maru.com`）等于没起名，也算自家。

   存档快照（`web.archive.org`）一律照写 label：公司关门后官网只剩这一份，label 里写着
   它是哪一年的存档，改写成「官方网站」就读不出点过去看到的是旧页面。 */
const ARCHIVE_HOSTS=['web.archive.org'];
const isArchiveLink=(url:unknown)=>ARCHIVE_HOSTS.includes(linkHost(url));
const officialLinkText=(link:{url?:string;label?:string},kind:string,names:readonly string[]=[])=>{
  const text=siteName(link.url)||link.label||'';
  if((kind!=='studio'&&kind!=='agency')||isArchiveLink(link.url))return text;
  const said=[text,link.label].map(foldName).filter(Boolean);
  const own=!said.length||said.includes('官方网站')||said.some(shown=>
    bareHost(shown)===linkHost(link.url)||names.some(name=>{
      const folded=foldName(name);
      return Boolean(folded)&&(folded.includes(shown)||shown.includes(folded));
    }));
  return own?'官方网站':text;
};
/* 什么才算一个真时长——只有这一处说了算。

   数据库里 `-1` 是 probe 的「硬失败」哨兵（见 scripts/probe.py：抽帧的 duration>2 门槛
   会让失败条目永远卡住，所以硬失败写成 -1），它不是时长。而 `!s` 这种真值判断挡不住
   负数：`fmtDur(-1)` 会算出 `0:-1`，喂进播放器更糟——Video.js 的 duration() setter 里写着
   `parseFloat(e)<0 ? Infinity : e`，随后 `=== Infinity` 就 `addClass("vjs-live")`，
   于是一部本地影片被标成「直播」，总时长显示 NaN。

   所以判据是「有限且大于零」，不是「非空」。 */
const realDuration=(value:unknown)=>{const n=Number(value);return Number.isFinite(n)&&n>0?n:0};
/** 秒数格式化成 `h:mm:ss`／`m:ss`，48 小时起写成 `N 天 N 小时`；`0`、负数与非有限值都是 `—`
 *  （probe 的硬失败哨兵）。 */
const fmtDur:(seconds:number|null|undefined)=>string=(s:any)=>{s=realDuration(s);if(!s)return'—';s=Math.round(s);
  if(s>=48*3600){const d=Math.floor(s/86400),hours=Math.floor((s%86400)/3600);return hours?`${d} 天 ${hours} 小时`:`${d} 天`}
  const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;
  return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`};
/** 秒数格式化成播放器时钟 `h:mm:ss`／`m:ss`；非数值当 0。 */
const fmtClock:(seconds:unknown)=>string=(s:any)=>{s=Math.max(0,Math.floor(Number(s)||0));const h=s/3600|0,m=(s%3600)/60|0,x=s%60;
  return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`};
/* 网盘报的容量可以到 PB：PikPak 报 10 PiB，写成 TB 是「10240.00 TB」。不到 1 MB 写 KB，
   非零的最少写 1 KB，免得 1 字节读成「0」。缺值、NaN 与负数是「大小未知」；0 是真的 0——
   可回收空间、已下载量这类读数会是 0，单个文件的 0 字节由调用方判成未知。 */
const fmtSize:(bytes:number|null|undefined)=>string=(value:any)=>{
  if(value===null||value===undefined||value==='')return '大小未知';
  const b=Number(value);
  if(!Number.isFinite(b)||b<0)return '大小未知';
  if(b===0)return '0 B';
  if(b<1048576)return `${Math.max(1,Math.round(b/1024))} KB`;
  return b>=1125899906842624?(b/1125899906842624).toFixed(2)+' PB':b>=1099511627776?(b/1099511627776).toFixed(2)+' TB':b>=1073741824?(b/1073741824).toFixed(1)+' GB':Math.floor(b/1048576)+' MB'};
/* 名字的第一个字素：头像垫底、首字母圆框都取它。按 UTF-16 码元取会把 emoji 劈成半个代理对，
   画出来是「�」；肤色、ZWJ 组合与国旗也要整枚取。空名用 `fallback`。 */
const graphemeSegmenter=typeof Intl!=='undefined'&&typeof Intl.Segmenter==='function'
  ? new Intl.Segmenter(undefined,{granularity:'grapheme'})
  : null;
const graphemesOf=(value:string)=>graphemeSegmenter
  ? Array.from(graphemeSegmenter.segment(value),part=>part.segment)
  : Array.from(value);
/** 开头 `count` 个字素（不加省略号）：厂牌没有标识时垫的两个字用它。 */
const leadingGraphemes=(value:unknown,count:number)=>{
  const text=String(value??'').trim(),parts:string[]=[];
  if(!text||count<=0)return '';
  if(!graphemeSegmenter)return Array.from(text).slice(0,count).join('');
  for(const part of graphemeSegmenter.segment(text)){parts.push(part.segment);if(parts.length>=count)break}
  return parts.join('');
};
const firstGrapheme=(value:unknown,fallback='?')=>leadingGraphemes(value,1)||fallback;
/** 按字素截断：超过 `max` 个字素时留前 `max - 1` 个再接「…」。 */
const clipGraphemes=(value:unknown,max:number)=>{
  const parts=graphemesOf(String(value??''));
  return parts.length>max?`${parts.slice(0,Math.max(0,max-1)).join('')}…`:parts.join('');
};
/** 来源代号到界面名称：`local`→`本地`、`115`→`115`、`pikpak`→`PikPak`、`online`→`在线`。 */
const LOC:Record<string,string>={...SOURCE_LABELS,local:'本地',online:'在线'};
/* 种子随机：FNV-1a 把「种子 + 键」压成一个 32 位数当排序键。同一个种子下顺序稳定，
   换种子就是另一套顺序，客户端不必存 PRNG 状态，也不必让后端多带一个参数。首页的
   抽样和关注页岛的两排共用这一份，同一粒种子两边取到的是同一批。 */
const seededRank=(seed:number|string,value:string)=>{
  let hash=2166136261>>>0;
  for(const char of `${seed}\u0000${value}`){hash^=char.codePointAt(0) as number;hash=Math.imul(hash,16777619)>>>0}
  return hash;
};
/* 掷一粒取样种子。异或结果是有符号 32 位，先转无符号再取模：种子要写进地址，后端只认非负整数。 */
const newSeed=()=>String(((Date.now()^(Math.random()*1e9|0))>>>0)%99991);
/* 时长四档是筛选条上的时长段，不是内容标签：目录的标签筛选里不收它们，地址上带来的也摘掉。 */
const DURATION_TAGS:ReadonlySet<string>=new Set(['短片-2分内','中片-10分内','长片-30分内','超长片-30分上']);
const cleanTagFilter=(value:unknown)=>String(value||'').split(',').filter(tag=>tag&&!DURATION_TAGS.has(tag)).join(',');

export {
  $,
  seededRank,
  newSeed,
  DURATION_TAGS,
  cleanTagFilter,
  realDuration,
  icon,
  api,
  isAbort,
  mapLimit,
  STATE_ROUTES,
  ROUTE_STATES,
  STATE_LABELS,
  isCatalogPath,
  ENTITY_ROUTES,
  ROUTE_ENTITIES,
  entityPath,
  esc,
  brandIcon,
  siteName,
  linkMarkUrl,
  siteMarkUrl,
  foldName,
  officialLinkText,
  fmtDur,
  fmtClock,
  fmtSize,
  firstGrapheme,
  leadingGraphemes,
  clipGraphemes,
  LOC,
};
