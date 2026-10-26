import assert from 'node:assert/strict';
import {after,before,describe,it} from 'node:test';
import type {Browser} from 'playwright-core';
import {launch,layout,requiredEnv,VIEWPORTS} from './harness.ts';

describe('新作封面显示时机',()=>{
 let browser:Browser;
 before(async()=>{browser=await launch()});
 after(async()=>{await browser?.close()});
 for(const viewport of VIEWPORTS){
  it(`${viewport.name} 新作正封完整居中并补底，整幅大封不使用模糊`,{timeout:60000},async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height},reducedMotion:'reduce'});
   try{
    await context.addInitScript(()=>localStorage.setItem('peach.settings.v1',JSON.stringify({detailAutoplay:false,feedAutoScroll:false})));
    const page=await context.newPage();
    const cases=[
     {width:632,height:900,front:632},
     {width:1500,height:1000,front:704},
     {width:1500,height:1000,front:667,poster_box:{x0:833,y0:0,x1:1500,y1:1000,px:[1500,1000],method:'manual'}},
     {width:1800,height:1000,front:850,wide:true},
     {width:2184,height:1638,front:2184,layout:'still'},
    ];
    await page.route('https://covers.example.test/fit/*',route=>{
     const one=cases[Number(route.request().url().split('/').pop())]!;
     return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${one.width}" height="${one.height}"><rect width="${one.width}" height="${one.height}" fill="#f00"/><rect x="${one.width-one.front}" width="${one.front}" height="${one.height}" fill="#0f0"/></svg>`});
    });
    await page.route(/\/api\/entity\/shapes/,route=>route.fulfill({json:{home:{feed:true},entities:[]}}));
    await page.route(/\/api\/feeds\/discoveries\?/,route=>route.fulfill({json:{ok:true,items:cases.map((one,index)=>({
     id:index+1,code:`EXM-00${index+1}`,title:one.wide?'Blu-ray 示例新作':'示例新作',cover_url:`https://covers.example.test/fit/${index}`,poster_box:one.poster_box,cover_frame:{layout:one.layout},
    }))}}));
    await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/');
    for(let index=0;index<cases.length;index++){
     const card=page.locator(`#feedNew [data-feed-id="${index+1}"]`);
     await card.scrollIntoViewIfNeeded();
     await card.locator('img.cover[data-frame]').waitFor({state:'visible'});
     const shape=await card.evaluate(el=>{
      const pic=el.querySelector('.pic')!,img=pic.querySelector('img')!;
      const box=pic.getBoundingClientRect(),image=img.getBoundingClientRect();
      const clip=getComputedStyle(img).clipPath.match(/[\d.]+/g)?.map(Number)||[0,0,0,0];
      const left=image.left+image.width*clip[3]!/100,right=image.right-image.width*clip[1]!/100;
      return {width:box.width,height:box.height,left:left-box.left,right:box.right-right,background:getComputedStyle(pic,'::before').backgroundImage};
     });
     assert.ok(Math.abs(shape.width/shape.height-.75)<.001,'新作卡片保持固定比例');
     assert.ok(Math.abs(shape.height-224)<1,'同排封面等高');
     if(cases[index]!.layout==='still')assert.equal(shape.background,'none','整幅大封不使用模糊');
     else{
      assert.notEqual(shape.background,'none','普通正封的留白由封面模糊补底');
      assert.ok(shape.left>=-1&&shape.right>=-1&&Math.abs(shape.left-shape.right)<1,'正封完整居中');
     }
    }
    assert.ok((await layout(page)).scrollWidth<=viewport.width+1,'横滚限制在新作行');
    await page.screenshot({path:`../build/feed-cover-fit-${viewport.name}.png`});
   }finally{await context.close()}
  });
  it(`${viewport.name} 新作头像显示人物图与首字并提供独立资料入口`,{timeout:60000},async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height},reducedMotion:'reduce'});
   try{
    await context.addInitScript(()=>localStorage.setItem('peach.settings.v1',JSON.stringify({detailAutoplay:false,feedAutoScroll:false})));
    const page=await context.newPage();
    const name='有很长名字的规范人物';
    await page.route(/\/entity-image\?kind=performer&id=42/,route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" fill="#777"/></svg>'}));
    await page.route(/\/api\/entity\/shapes/,route=>route.fulfill({json:{home:{feed:true},entities:[]}}));
    await page.route(/\/api\/feeds\/discoveries\?/,route=>route.fulfill({json:{ok:true,items:[
     {id:1,code:'EXM-001',title:'示例新作',link:'https://javdb.com/v/example',performers:name+'、乙、丙、丁',release_date:'2026-10-30',
      performer_refs:[{id:42,name,has_image:true,image_version:'7'},...['乙','丙','丁'].map((name,index)=>({id:43+index,name,has_image:false}))]},
     {id:2,code:'EXM-002',title:'未建档新作',performers:'未建档人物'}]}}));
    await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/stats');
    await page.evaluate(()=>{history.pushState({},'','/');window.dispatchEvent(new PopStateEvent('popstate'))});
    const card=page.locator('#feedNew [data-feed-id="1"]');
    await card.waitFor({state:'visible'});
    const avatar=card.getByRole('link',{name:'打开女优页：'+name,exact:true});
    await avatar.locator('img').waitFor({state:'visible'});
    assert.equal(await card.locator('.mav').count(),3);
    assert.equal(await card.locator('.mav img').count(),1);
    assert.equal(await avatar.getAttribute('href'),'/performers/42');
    assert.equal(await avatar.evaluate(el=>getComputedStyle(el).pointerEvents),'auto');
    const names=await card.locator('.feednewperformers').boundingBox();
    const date=await card.locator('.meta .s>span').last().boundingBox();
    assert.ok(names&&date&&names.width>=30&&date.y>=names.y+names.height-1,'姓名和完整日期各有可见的一行');
    assert.equal(await page.locator('#feedNew [data-feed-id="2"] .mav .ini').textContent(),'未');
    const geometry=await layout(page);
    assert.ok(geometry.scrollWidth<=geometry.viewportWidth+1);
    await page.screenshot({path:`../build/feed-avatars-${viewport.name}.png`});
    await avatar.click();
    await page.waitForURL('**/performers/42');
   }finally{await context.close()}
  });
 }
 for(const viewport of VIEWPORTS)for(const delay of [0,1800]){
  it(`${viewport.name} ${delay?'慢图':'预加载'}完成取景才显示`,{timeout:60000},async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height}});
   try{
    await context.addInitScript(()=>{
     localStorage.setItem('peach.settings.v1',JSON.stringify({detailAutoplay:false}));
     (window as any).__unframed=[];
     const check=(img:HTMLImageElement)=>{
      if(!img.matches('.feednew img.cover')||img.dataset.frame)return;
      if(getComputedStyle(img).visibility==='visible'&&img.naturalWidth)
       (window as any).__unframed.push({src:img.src,complete:img.complete});
     };
     // 捕获监听先于应用执行，记录加载图尚未取景的那一刻。
     document.addEventListener('load',event=>{
      if(event.target instanceof HTMLImageElement)check(event.target);
     },true);
     const sample=()=>{document.querySelectorAll<HTMLImageElement>('.feednew img.cover').forEach(check);requestAnimationFrame(sample)};
     requestAnimationFrame(sample);
    });
    const page=await context.newPage();
    const remote='https://covers.example.test/framing.svg';
    const audits=await context.newCDPSession(page), lazyIssues:string[]=[];
    audits.on('Audits.issueAdded',({issue})=>{
     if(issue.code==='LazyLoadImageIssue'&&issue.details.lazyLoadImageIssueDetails?.url===remote)
      lazyIssues.push(issue.details.lazyLoadImageIssueDetails.url);
    });
    await audits.send('Audits.enable');
    await page.route(remote,async route=>{
     if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
     await route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="1000"><rect width="796" height="1000" fill="#f00"/><rect x="796" width="704" height="1000" fill="#0f0"/></svg>'});
    });
    await page.route(/\/api\/feeds\/discoveries\?/,route=>route.fulfill({json:{ok:true,items:[{id:1,code:'EXM-001',title:'示例新作',has_cover:false,cover_url:remote}]}}));
    await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/stats');
    for(let round=0;round<2;round++){
     await page.evaluate(()=>{history.pushState({},'','/');window.dispatchEvent(new PopStateEvent('popstate'))});
     const image=page.locator('#feedNew img.cover.panel');
     await image.waitFor({state:'visible'});
     assert.equal(await image.evaluate(img=>img.style.getPropertyValue('--panel-clip')),'0% 0% 0% 53.07%');
     const positions=await image.evaluate(async img=>{
      const samples:string[]=[];
      for(let n=0;n<12;n++){
       await new Promise(requestAnimationFrame);
       const css=getComputedStyle(img);
       samples.push([css.left,css.top,css.width,css.height,css.clipPath].join('|'));
      }
      return samples;
     });
     assert.equal(new Set(positions).size,1,'可见封面的取景保持稳定');
     assert.deepEqual(lazyIssues,[],'正封裁切图在 DevTools 中有明确尺寸');
     const ratio=await image.evaluate((img:HTMLImageElement)=>{
      const box=img.getBoundingClientRect();return box.width/box.height;
     });
     assert.ok(Math.abs(ratio-1.5)<.01,'裁切使用源图宽高比');
     assert.deepEqual(await page.evaluate(()=>(window as any).__unframed),[],'已加载封面在取景完成前保持隐藏');
     await page.evaluate(()=>{history.pushState({},'','/stats');window.dispatchEvent(new PopStateEvent('popstate'))});
     await page.locator('#feedNew').waitFor({state:'hidden'});
    }
   }finally{await context.close()}
  });
 }
 for(const viewport of VIEWPORTS){
  it(`${viewport.name} 设置里的视频大小同步首页和 JAV`,{timeout:60000},async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height},reducedMotion:'reduce'});
   try{
    await context.addInitScript(()=>localStorage.setItem('peach.settings.v1',JSON.stringify({detailAutoplay:false,homeLayout:'big',javLayout:'big'})));
    const page=await context.newPage();
    await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/');
    await page.locator('#grid [data-media-card]').first().waitFor({state:'visible'});
    if(viewport.name==='mobile')await page.locator('#filterBtn').click();
    await page.locator('#settingsBtn').click();
    await page.locator('#settingsPanel').getByRole('tab',{name:'浏览与播放',exact:true}).click();
    await page.locator('#javSizeSetting label:has(input[value="small"])').click();
    const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('peach.settings.v1')||'{}'));
    assert.equal(saved.homeLayout,'small');assert.equal(saved.javLayout,'small');
    await page.locator('#settingsClose').click();
    const ratio=await page.locator('#grid [data-media-pic]').first().evaluate(node=>{
     const rect=node.getBoundingClientRect();return rect.width/rect.height;
    });
    assert.ok(Math.abs(ratio-16/9)<.01);
   }finally{await context.close()}
  });
  it(`${viewport.name} 竖版正封完整放入卡片`,{timeout:60000},async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height}});
   try{
    const page=await context.newPage(),remote='https://covers.example.test/front.svg';
    await page.route(remote,route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="632" height="900"><rect width="632" height="900" fill="#0f0"/></svg>'}));
    await page.route(/\/api\/feeds\/discoveries\?/,route=>route.fulfill({json:{ok:true,items:[{id:1,code:'EXM-001',title:'示例新作',has_cover:false,cover_url:remote}]}}));
    await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/');
    const image=page.locator('#feedNew img.cover[data-frame="front"]');await image.waitFor({state:'visible'});
    assert.equal(await image.evaluate(img=>getComputedStyle(img).objectFit),'contain');
   }finally{await context.close()}
  });
 }
});
