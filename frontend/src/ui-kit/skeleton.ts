/* 骨架：整块占位、索引占位、配置页占位，落进 DOM 后按实际尺寸补齐，过了显示门槛才露面，
   到货时把占位换成真内容。由共享源码直接引用，随 `peach-app.js` 发出。 */
import { esc } from '../core';

/**
 * 占位换成真内容：旧的那一屏抬成盖在容器上的一层淡出，新内容同时从模糊里清晰起来。
 *
 * `write()` 负责把新内容写进 `container`。容器里没有骨架时直接写，不套这一层——
 * 翻页、筛选这类「内容换内容」不属于这条动效，套上去每换一次筛选整页都糊一下。
 */
export function revealSkeleton(container:any,write:()=>void):void{
  if(!container)return;
  /* 还没到显示门槛就已经取完：直接落内容。隐藏中的占位从未被人看见，不该为了它再
     播一段「骨架退场」；这也让很快的本地请求完全没有骨架闪烁。 */
  if(container.querySelector('.skeleton-awaiting')){write();return}
  const hasSkeleton=container.querySelector('.skeleton,[data-skeleton],.skeletoncard,.countskeleton');
  if(!hasSkeleton||!container.firstChild){write();return}
  const fade=document.createElement('div');
  fade.className='skelfade';
  fade.setAttribute('aria-hidden','true');
  while(container.firstChild)fade.append(container.firstChild);
  write();
  container.classList.add('skelreveal');
  container.prepend(fade);
  container.getBoundingClientRect();
  container.classList.add('revealing');
  const drop=()=>{fade.remove();container.classList.remove('skelreveal','revealing')};
  const done=(event:Event)=>{if(event.target!==fade)return;fade.removeEventListener('transitionend',done);drop()};
  fade.addEventListener('transitionend',done);
  setTimeout(()=>{if(fade.isConnected){fade.removeEventListener('transitionend',done);drop()}},1000);
}

export function configurationSkeletonHtml():string{
  const groups:[string,number][]=[['通用',2],['媒体',2],['下载',2],['网络与访问',3],['维护',3]];
  return `<div class="ui-configpage" data-skeleton="configuration" role="status" aria-label="正在读取配置">${groups.map(([label,count])=>`<h2 class="ui-configgroup" aria-hidden="true">${label}</h2>${Array.from({length:count},()=>`<div class="configfieldset config-skeleton-card" aria-hidden="true"><div class="geist-fieldset-content"><span class="skeleton"></span><span class="skeleton"></span><span class="skeleton"></span></div><footer class="geist-fieldset-footer"><span class="skeleton"></span></footer></div>`).join('')}`).join('')}</div>`;
}

/** Geist Skeleton: reserve a large content region while its structure is loading. */
/* `count` 只对 cards 生效：块数是骨架说出口的结构预告，六块对上的是海报网格，
   而行政界面往往只有两三个大区。多画的块加载完就消失，那不是占位是误报。
   `cardRatio` 是卡片封面的宽高比，交给调用方按真卡的版式给：大图版式的正封是竖的，
   骨架照 16:9 铺的话，内容一到整屏卡片都要被拉高一截。 */
export function skeletonHtml(label='正在读取内容',{className='',variant='panel',count=6,fill=true,gridClass='',gridSize='',cardRatio=0}={}):string{
  const kind=new Set(['panel','cards','dashboard']).has(variant)?variant:'panel';
  const body=kind==='cards'
    ?Array.from({length:Math.max(1,count)},
      ()=>`<span class="skeletoncard"><i></i><s></s><b></b><em></em><u></u></span>`).join('')
    :kind==='dashboard'
      /* 指标带是统计与口味两页真正的第一屏内容，四格的位置和高度都是定死的。
         骨架从大区开始画，等数据到货再从上面挤进一条 96px 的带子，整页往下跳一次。 */
      ?`<span class="ui-skeletondashstrip">${Array.from({length:4},
          ()=>`<span><i></i><b></b><em></em></span>`).join('')}</span>
        <span class="ui-skeletondashhero"><i></i><b></b></span>
        <span class="ui-skeletondashpanel"><i></i><b></b><em></em></span>
        <span class="ui-skeletondashpanel"><i></i><b></b><em></em></span>`
    :`<span class="skeleton" style="width:38%"></span>
      <span class="skeleton" style="width:100%"></span>
      <span class="skeleton" style="width:100%"></span>
      <span class="skeleton" style="width:72%"></span>`;
  /* data-skeleton 是这张骨架的身份。深链启动先画一张、路由到位后各页再画一张，
     整页刷新就会连闪两段动画；调用方拿这个键判断「已经是同一张了」，跳过重画。 */
  return `<div class="skeletonpanel skeleton-${kind}${className?` ${esc(className)}`:''}"
    data-skeleton="${esc(kind)}${className?`/${esc(className)}`:''}"${kind==='cards'&&fill?' data-fill=""':''}
    role="status" aria-label="${esc(label)}"><span class="sr-only">${esc(label)}</span>
    <div${gridClass?` class="${esc(gridClass)}"`:''}${gridSize?` data-size="${esc(gridSize)}"`:''}${
      kind==='cards'&&Number(cardRatio)>0?` style="--skeleton-card-ratio:${Number(cardRatio)}"`:''} aria-hidden="true">${body}</div></div>`;
}

/** 索引占位复用最终网格、头像和词表的尺寸。 */
/** 索引页的占位：名册网格、字母表或标签云，尺寸取最终那一副。首屏和页内换档共用这一份。 */
export function indexSkeletonHtml({kind,layout='big',mode='alphabet'}:{kind?:string;layout?:string;mode?:string}={}):string{
  const people=kind!=='tags',company=kind==='studios'||kind==='agencies';
  const cell=people
    ?'<span class="icell"><span class="ring skeleton"></span><span class="nm skeleton">&nbsp;</span><span class="n skeleton">&nbsp;</span></span>'
    :'<span class="alphatag"><span class="skeleton"></span><span class="n skeleton"></span></span>';
  const grid=people?`igrid" data-cells="${company?'company':'people'}" data-layout="${esc(layout)}`:'ui-alphalist';
  /* 字母表是一组一张卡：每组两行占位，铺三组，形状同页面落地后开头那几组；
     它不走 `data-fill`——那条补的是单张网格，一组补到视口下沿反而不像。 */
  const body=!people&&mode==='cloud'
    ?`<div class="tagwall index-tags">${Array.from({length:60},(_,i)=>
      `<span class="tg skeleton" style="width:${[92,128,76,108,144][i%5]}px">&nbsp;</span>`).join('')}</div>`
    :people?`<div class="${grid}">${cell.repeat(12)}</div>`
    :`<section class="ui-alphagroup"><span class="indexletterskeleton skeleton"></span><div class="${grid}">${cell.repeat(10)}</div></section>`.repeat(3);
  const label='正在读取索引';
  return `<div class="skeletonpanel index-skeleton" data-skeleton="index/${esc(kind)}/${esc(layout)}/${esc(mode)}"${people?' data-fill=""':''}
    role="status" aria-label="${label}"><span class="sr-only">${label}</span><section aria-hidden="true">${body}</section></div>`;
}

/* 骨架的枚数由容器当下的宽度决定，不写死一个数：横向一行铺到右缘为止，网格补满
   整行。写死的话宽屏最后一行留一截豁口，窄屏和手机端又多出一堆要横滑才看得见的
   占位；算出来就不必再为断点各写一套。宽度序列只是让胶囊长短不一，像真词。 */
const SKELETON_SLOT:Record<string,(width:number)=>string>={
  av:()=>`<span class="av avskeleton"><span class="ring"></span><span class="nm">&nbsp;</span></span>`,
  brandpill:width=>`<span class="brandpill brandskeleton" style="width:${width}px"><span class="mk"></span></span>`,
  pill:width=>`<span class="pill tagskeleton" style="width:${width}px"></span>`,
};
const SKELETON_SLOT_WIDTHS:Record<string,number[]>={
  av:[0],
  brandpill:[132,158,118,146,124,164,138],
  pill:[92,68,104,76,88,64,96,72,100,80,68,92,76,84],
};

/* 低于这一段的请求直接显示内容。Peach 的数据多在本机，立即把占位画出来会让几十毫秒的
   正常读取看成一次闪烁；超过门槛才说明页面确实需要等待。只藏最外层占位，内部结构仍先
   参与布局和 fit 计算，因此真正出现时不会再重排。 */
export const SKELETON_REVEAL_DELAY=180;
const SKELETON_REVEAL_SELECTOR='[data-skeleton],[data-skeleton-tier],.countskeleton';
function armSkeletonReveal(root:any){
  if(!root)return;
  const all:HTMLElement[]=[...(root.matches?.(SKELETON_REVEAL_SELECTOR)?[root]:[]),
    ...root.querySelectorAll(SKELETON_REVEAL_SELECTOR)];
  const targets=all.filter(node=>!all.some(parent=>parent!==node&&parent.contains(node)));
  for(const target of targets){
    /* 已经武装过的跳过这一枚就好。写 `return` 会让同一批里排在它后面的占位
       一个都拿不到延迟标记，补进来的那半屏骨架直接闪出来。 */
    if(target.dataset.skeletonReveal)continue;
    target.dataset.skeletonReveal='pending';
    target.classList.add('skeleton-awaiting');
    setTimeout(()=>{
      if(!target.isConnected||target.dataset.skeletonReveal!=='pending')return;
      target.dataset.skeletonReveal='shown';
      target.classList.remove('skeleton-awaiting');
    },SKELETON_REVEAL_DELAY);
  }
}

const SKELETON_TIER_LIMIT=64;
/* 横向骨架铺到溢出容器右缘为止。每量一次宽度就是一次整页布局，逐枚追加、逐枚量的话一排
   要布局十几二十次，冷启动实测一排一百多毫秒。所以按轮来：先铺一轮宽度序列，量一次，按
   量到的平均宽度把缺的那截一次补齐，再量一次确认。几排走同一轮，先全量再全写，合起来也
   只布局这几次。上限只是死循环的护栏。 */
function fillSkeletonTiers(jobs:any[]){
  /* 同一排会再补一次（名单到了、容器改宽）：已经越过右缘的那排不再追加。空排不必先量。 */
  jobs=jobs.filter(job=>job.row&&SKELETON_SLOT[job.kind]&&(!job.row.children.length||job.row.scrollWidth<=job.row.clientWidth))
    .map(job=>({...job,start:job.row.children.length,added:0}));
  const add=(job:any,count:number)=>{
    const slot=SKELETON_SLOT[job.kind]!,widths=SKELETON_SLOT_WIDTHS[job.kind]!;
    let html='';
    for(const end=Math.min(SKELETON_TIER_LIMIT,job.added+count);job.added<end;job.added++)html+=slot(widths[job.added%widths.length]!);
    job.row.insertAdjacentHTML('beforeend',html);
  };
  for(const job of jobs)add(job,SKELETON_SLOT_WIDTHS[job.kind]!.length);
  for(let round=0;round<4;round++){
    const short=jobs.filter(job=>job.added<SKELETON_TIER_LIMIT&&job.row.scrollWidth<=job.row.clientWidth).map(job=>{
      const first=job.row.children[job.start].getBoundingClientRect(),last=job.row.lastElementChild.getBoundingClientRect();
      const each=Math.max(1,(last.right-first.left)/job.added);
      return [job,Math.ceil((job.row.getBoundingClientRect().right-last.right)/each)+1];
    });
    if(!short.length)break;
    for(const [job,count] of short)add(job,Math.max(1,count));
  }
  for(const job of jobs)armSkeletonReveal(job.row);
}
export function fillSkeletonTier(row:Element|null,kind:string):void{fillSkeletonTiers([{row,kind}])}

/** 骨架落进 DOM 之后按实际尺寸补齐：横向一行铺满，卡片网格补到整行且盖住视口余量。 */
export function fitSkeleton(root:any):void{
  if(!root)return;
  const scoped=(selector:string):HTMLElement[]=>[...(root.matches?.(selector)?[root]:[]),...root.querySelectorAll(selector)];
  fillSkeletonTiers(scoped('[data-skeleton-tier]').map(row=>({row,kind:row.dataset.skeletonTier})));
  for(const grid of scoped('.skeletonpanel[data-fill]>div,.index-skeleton[data-fill]>section>div')){
    const first=grid.firstElementChild,style=getComputedStyle(grid);
    /* 横排的推荐行不是网格，列数无从谈起，按整行补会把它裁成一张。 */
    if(!first||style.display!=='grid')continue;
    const columns=style.gridTemplateColumns.split(' ').filter(Boolean).length;
    const rowGap=parseFloat(style.rowGap)||0,cardHeight=first.getBoundingClientRect().height;
    if(!columns||!cardHeight)continue;
    /* 骨架说的是「这块地方等下会被填满」，所以铺到视口下沿；四行是护栏，
       再多也是一屏之外看不见的占位，白占动画。 */
    const room=window.innerHeight-grid.getBoundingClientRect().top;
    const rows=Math.max(1,Math.min(4,Math.ceil((room+rowGap)/(cardHeight+rowGap))));
    const want=columns*rows;
    while(grid.children.length>want)grid.lastElementChild!.remove();
    while(grid.children.length<want)grid.appendChild(first.cloneNode(true));
  }
  armSkeletonReveal(root);
}
