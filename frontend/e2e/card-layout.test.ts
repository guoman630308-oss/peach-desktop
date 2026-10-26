import assert from 'node:assert/strict';
import {after,before,describe,it} from 'node:test';
import type {Browser,Page} from 'playwright-core';
import {launch,requiredEnv,VIEWPORTS} from './harness.ts';

const FRAMES='#grid [data-media-grid] > [data-media-card] [data-media-pic], #grid [data-mix-cover]';

/** 首页铺 20 张纯白预览图的作品，`size` 是首页版式；`patch` 按序号改其中几张。 */
async function openHome(page:Page,size:string,patch:(i:number)=>Record<string,unknown>=()=>({})){
 await page.addInitScript(size=>localStorage.setItem('peach.settings.v1',JSON.stringify({detailAutoplay:false,homeLayout:size})),size);
 await page.route('**/api/items?*',route=>route.fulfill({json:{items:Array.from({length:20},(_,i)=>({
  id:91000+i,name:`示例作品 ${i}`,creator:'示例创作者',is_jav:i%2===0,has_thumb:true,has_cover:false,
  width:1600,height:900,location:'local',duration:120,...patch(i),
 })),total:20,has_more:false}}));
 await page.route('**/poster?*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900"><rect width="1600" height="900" fill="white"/></svg>'}));
 await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/',{waitUntil:'load'});
 await page.locator('#grid [data-mix-card]').waitFor();
}

/** 截图里一块的最暗通道值，用页面自己的 canvas 解码 PNG。 */
async function darkest(page:Page,clip:{x:number,y:number,width:number,height:number}){
 const png=(await page.screenshot({clip})).toString('base64');
 return page.evaluate(async png=>{
  const img=new Image();
  img.src=`data:image/png;base64,${png}`;
  await img.decode();
  const canvas=document.createElement('canvas');
  canvas.width=img.width;canvas.height=img.height;
  const context=canvas.getContext('2d')!;
  context.drawImage(img,0,0);
  const data=context.getImageData(0,0,canvas.width,canvas.height).data;
  let min=255;
  for(let i=0;i<data.length;i+=4)min=Math.min(min,data[i],data[i+1],data[i+2]);
  return min;
 },png);
}

describe('混排作品的画面框',()=>{
 let browser:Browser;
 before(async()=>{browser=await launch()});
 after(async()=>{await browser?.close()});
 for(const viewport of VIEWPORTS)for(const size of ['big','small']){
  it(`${viewport.name} ${size} 作品与 Mix 等高并保留横图`,async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height}});
   try{
    const page=await context.newPage();
    await openHome(page,size);
    const frames=await page.locator(FRAMES).evaluateAll(nodes=>nodes.map(node=>{
     const box=node.getBoundingClientRect(),img=node.querySelector('img')!;
     return {ratio:box.width/box.height,fit:getComputedStyle(img).objectFit,background:getComputedStyle(img).backgroundColor};
    }));
    assert.ok(frames.length>=20);
    for(const frame of frames){
     assert.ok(Math.abs(frame.ratio-(size==='big'?3/4:16/9))<.01,JSON.stringify(frame));
     assert.equal(frame.fit,'contain');
     assert.equal(frame.background,'rgb(0, 0, 0)');
    }
    if(viewport.mobile)for(const density of ['roomy','dense']){
     await page.evaluate(value=>{document.body.dataset.density=value},density);
     const meta=await page.locator('#grid [data-media-card] [data-media-meta]').first().evaluate(node=>{
      const box=node.getBoundingClientRect();
      const content=Math.max(...[...node.children].map(child=>child.getBoundingClientRect().height));
      return {height:box.height,content};
     });
     assert.ok(meta.height<=meta.content+17,JSON.stringify({density,...meta}));
    }
    if(size==='small')for(const density of ['roomy','dense']){
     await page.evaluate(value=>{document.body.dataset.density=value},density);
     const row=await page.locator('#grid [data-mix-card]').evaluate(mix=>{
      const mixCover=mix.querySelector('[data-mix-cover]')!.getBoundingClientRect();
      const peer=mix.parentElement!.querySelector(':scope > [data-media-card]')!;
      const peerCover=peer.querySelector('[data-media-pic]')!.getBoundingClientRect();
      return {mixBottom:mixCover.bottom-mix.getBoundingClientRect().top,peerBottom:peerCover.bottom-peer.getBoundingClientRect().top,
       mixTop:mixCover.top-mix.getBoundingClientRect().top,peerTop:peerCover.top-peer.getBoundingClientRect().top};
     });
     assert.ok(Math.abs(row.mixBottom-row.peerBottom)<1,JSON.stringify(row));
     assert.ok(Math.abs(row.mixTop-row.peerTop)<1,JSON.stringify(row));
    }
   }finally{await context.close()}
  });
 }
 /* 白图铺满 16:9 格子、浅色页面：四角的抗锯齿只该在白图和浅色页面之间过渡。格子里垫的黑若被逐层按圆角
  * 裁，角上会压出 150–190 的灰黑点。每角只量贴角 6px 见方的一块，圆弧中段在里面，角标都离边更远。 */
 for(const scale of [1,2]){
  it(`浅色页面上白封面的四个圆角不透黑（${scale} 倍像素）`,async()=>{
   const context=await browser.newContext({viewport:{width:1280,height:800},deviceScaleFactor:scale,colorScheme:'light'});
   try{
    const page=await context.newPage();
    await openHome(page,'small');
    await page.evaluate(()=>{document.documentElement.dataset.theme='light';document.documentElement.classList.remove('dark')});
    const corners=[];
    for(const frame of ['#grid [data-media-grid] > [data-media-card] [data-media-pic]','#grid [data-mix-cover]']){
     // 这一格滚到吸顶的顶栏与排序条下面，等它的图到货；角内侧那一点打到的得是这张卡自己，没被别的东西压着。
     const box=await page.locator(frame).first().evaluate(async node=>{
      scrollBy(0,node.getBoundingClientRect().top-240);
      const img=node.querySelector('img')!;
      if(!img.complete)await new Promise(done=>img.addEventListener('load',done,{once:true}));
      await new Promise(requestAnimationFrame);
      const box=node.getBoundingClientRect(),card=node.closest('[data-media-card],[data-mix-card]');
      const covers=[[box.left+8,box.top+8],[box.right-8,box.top+8],[box.left+8,box.bottom-8],[box.right-8,box.bottom-8]]
       .map(([x,y])=>document.elementFromPoint(x,y)).filter(hit=>!hit||!card?.contains(hit))
       .map(hit=>hit?`${hit.tagName} ${[...hit.attributes].map(a=>a.name).join(' ')}`:'视口外');
      return {...box.toJSON(),covers};
     });
     assert.ok(!box.covers.length&&box.bottom<=800,`${frame} 的四角没有露出来：${JSON.stringify(box)}`);
     await page.mouse.move(0,0);
     for(const [x,y] of [[box.left,box.top],[box.right-6,box.top],[box.left,box.bottom-6],[box.right-6,box.bottom-6]]){
      corners.push({frame,x:Math.round(x),y:Math.round(y),min:await darkest(page,{x,y,width:6,height:6})});
     }
    }
    // 高分屏的像素对齐和抗锯齿允许浅灰边缘；灰黑渗透的 150–190 区间必须失败。
    const dark=corners.filter(corner=>corner.min<230);
    assert.deepEqual(dark,[],`${corners.length} 个角里有 ${dark.length} 个透出了黑`);
   }finally{await context.close()}
  });
 }
 /* 大图档一格的模块宽 336px，比 320 的手机屏还宽：网格的最小列宽要让给视口，整张卡留在屏内。 */
 for(const size of ['big','small']){
  it(`320 宽 ${size} 版式的卡片网格不越出视口`,async()=>{
   const context=await browser.newContext({viewport:{width:320,height:640}});
   try{
    const page=await context.newPage();
    await openHome(page,size);
    const box=await page.locator('#grid [data-media-grid]').first().evaluate(node=>({
     right:Math.round(node.getBoundingClientRect().right),
     card:Math.round(node.querySelector('[data-media-card]')!.getBoundingClientRect().right),
     page:document.documentElement.scrollWidth,
     view:document.documentElement.clientWidth,
     overflow:[...document.querySelectorAll('body *')].filter(element=>{
      const rect=element.getBoundingClientRect();
      if(rect.right<=document.documentElement.clientWidth||rect.width===0)return false;
      return true;
     }).slice(0,30).map(element=>({tag:element.tagName,attrs:[...element.attributes].map(a=>`${a.name}=${a.value}`).join(' '),right:Math.round(element.getBoundingClientRect().right)})),
    }));
    assert.ok(box.right<=box.view&&box.card<=box.view&&box.page<=box.view,JSON.stringify(box));
   }finally{await context.close()}
  });
 }
 it('多选态的勾选圆不压分卷徽标；番号带三枚版本徽标时整组留在标题框内',async()=>{
  const context=await browser.newContext({viewport:{width:320,height:720}});
  try{
   const page=await context.newPage();
   await openHome(page,'small',i=>i===0?{
    name:'FC2-PPV-4728193 夏の終わり',code:'FC2-PPV-4728193',display_title:'夏の終わり',edition_badges:['中字','无码','无码破解'],
    part_group:{key:'p',seed_id:91000,count:128},
   }:{});
   const first=page.locator('#grid [data-media-grid] > [data-media-card]').first();
   await first.click({modifiers:['Control']});
   await first.locator('[data-media-check]').waitFor();
   const box=await first.evaluate(card=>{
    const rect=(selector:string)=>card.querySelector(selector)!.getBoundingClientRect();
    const title=rect('[data-media-title]');
    return {
     group:rect('[data-media-group]').right,check:rect('[data-media-check]').left,
     count:card.querySelectorAll('[data-media-title] .javedition').length,title:title.right,badges:Math.max(...[...card.querySelectorAll('[data-media-title] .javedition')].map(node=>node.getBoundingClientRect().right)),
    };
   });
   assert.equal(box.count,3);
   assert.ok(box.group<=box.check,`勾选圆压住了分卷徽标：${JSON.stringify(box)}`);
   assert.ok(box.badges<=box.title+0.5,`版本徽标越出标题框：${JSON.stringify(box)}`);
  }finally{await context.close()}
 });
 it('十二位共演都没有头像图：网格卡五枚首字母都露在外面，竖屏卡只放两枚，标题照样有宽度',async()=>{
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  try{
   const page=await context.newPage();
   const names=['七海ひな','桜井まい','白石りん','水瀬ゆい','藤原あや','J','K','L','M','N','O','P'];
   await openHome(page,'small',()=>({creator:'',performers:names,performer_total:12}));
   const short=page.locator('[data-media-card][data-variant="short"]').first();
   await short.waitFor();
   /* 命中测试只认视口里、没被吸顶栏压着的点：先把头像那一排即时滚到视口中间，等滚动落定再量。 */
   const measure=async(card:Element)=>{
    card.querySelector('[data-media-avatars]')!.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    for(let frame=0;frame<2;frame+=1)await new Promise(requestAnimationFrame);
    const avatars=[...card.querySelectorAll('[data-media-avatars] > [data-media-avatar]')]
     .filter(node=>getComputedStyle(node).display!=='none');
    const covered=avatars.filter(node=>{
     const box=node.getBoundingClientRect();
     return document.elementFromPoint(box.left+box.width/2,box.top+box.height/2)?.closest('[data-media-avatar]')!==node;
    }).length;
    return {shown:avatars.length,covered,text:Math.round(card.querySelector('[data-media-text]')!.getBoundingClientRect().width),card:Math.round(card.getBoundingClientRect().width)};
   };
   const grid=await page.locator('#grid [data-media-grid] > [data-media-card]').first().evaluate(measure);
   assert.equal(grid.shown,5);
   assert.equal(grid.covered,0,`有首字母被左邻盖住：${JSON.stringify(grid)}`);
   const portrait=await short.evaluate(measure);
   assert.equal(portrait.shown,2);
   assert.equal(portrait.covered,0,JSON.stringify(portrait));
   assert.ok(portrait.text>=portrait.card*0.6,`竖屏卡的标题列被头像挤窄：${JSON.stringify(portrait)}`);
  }finally{await context.close()}
 });
 it('Shift 连选几张卡不把中间的文字刷成选区',async()=>{
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  try{
   const page=await context.newPage();
   await openHome(page,'big');
   const titles=page.locator('#grid [data-media-grid] > [data-media-card] [data-media-title]');
   await titles.nth(0).click({modifiers:['Control']});
   assert.equal(await titles.nth(0).evaluate(node=>getComputedStyle(node.closest('[data-media-card]')!).userSelect),'none');
   /* 多选态的 `user-select: none` 之外，Shift 按下那一下本身也不起选区：撤掉前者再点，两道各验一次。 */
   await page.addStyleTag({content:'.peach-react [data-media-card]{user-select:text !important}'});
   await titles.nth(3).click({modifiers:['Shift']});
   await page.locator('#grid [data-media-card][data-selected]').nth(3).waitFor();
   assert.equal(await page.evaluate(()=>String(getSelection())),'');
  }finally{await context.close()}
 });
});
