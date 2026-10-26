import assert from 'node:assert/strict';
import {after,before,describe,it} from 'node:test';
import type {Browser} from 'playwright-core';
import {expectBody,launch,requiredEnv,VIEWPORTS} from './harness.ts';

describe('首页加载结构',()=>{
 let browser:Browser;
 before(async()=>{browser=await launch()});
 after(async()=>{await browser?.close()});
 for(const viewport of VIEWPORTS)for(const hasFeed of [true,false]){
  it(`${viewport.name} 新作区 ${hasFeed?'有内容':'为空'}`,{timeout:60000},async()=>{
   const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height}});
   let release:()=>void=()=>{};
   try{
    await context.addInitScript(({hasFeed})=>{
     localStorage.setItem('peach.settings.v1',JSON.stringify({detailAutoplay:false,homeLayout:hasFeed?'big':'small'}));
     const seen=new WeakSet<Element>();
     (window as any).__catalogSkeletons=0;
     (window as any).__catalogRatios=[];
     new MutationObserver(()=>{document.querySelectorAll('#grid .catalog-skeleton').forEach(node=>{
      if(!seen.has(node)){seen.add(node);(window as any).__catalogSkeletons++;
       (window as any).__catalogRatios.push(Number((node.querySelector('[style]') as HTMLElement)?.style.getPropertyValue('--skeleton-card-ratio')))}
     });
      if(!(window as any).__firstCard){
       const card=document.querySelector('#grid [data-media-card]');
       if(card){(window as any).__firstCard=card;(window as any).__firstTier=document.querySelector('[data-catalog-tier]')}
      }
     }).observe(document,{childList:true,subtree:true});
    },{hasFeed});
    const page=await context.newPage();
    const problems:string[]=[];
    page.on('pageerror',error=>problems.push(error.message));
    page.on('console',message=>{if(message.type()==='error')problems.push(message.text())});
    page.setDefaultTimeout(8000);
    const scopes:string[]=[];
    await page.route('**/api/sources',route=>route.fulfill({json:{ok:true,sources:[{location:'local',online:true},{location:'115',online:false}]}}));
    await page.route(/\/api\/facets\?/,async route=>{
     scopes.push(new URL(route.request().url()).searchParams.get('loc')||'');
     await route.continue();
    });
    await page.route(/\/api\/tops\?/,async route=>{
     const first=!(Number(new URL(route.request().url()).searchParams.get('page'))>0);
     await route.fulfill({json:{performers:first?[{id:90001,k:'示例演员',n:1,has_image:false,has_avatar:false}]:[],studios:[]}});
    });
    await page.route('**/api/entity/shapes',async route=>{
     const response=await route.fetch(),data=await response.json();
     await route.fulfill({json:{...data,home:{feed:hasFeed}}});
    });
    await page.route('**/api/settings',async route=>{
     if(route.request().method()!=='GET'){await route.continue();return}
     await new Promise(resolve=>setTimeout(resolve,650));
     await route.fulfill({json:{sidebarOrder:['','jav','follow','performers','tags','studios','flagged','manage']}});
    });
    const held=new Promise<void>(resolve=>{release=resolve});
    await page.route(/\/api\/feeds\/discoveries\?/,async route=>{
     await held;
     await route.fulfill({json:{ok:true,items:hasFeed?[{id:1,code:'EXM-001',title:'示例新作',has_cover:false}]:[]}});
    });
    await page.goto(requiredEnv('PEACH_E2E_ORIGIN')+'/',{waitUntil:'load'});
    const card=page.locator('#grid [data-media-card]').first();
    await expectBody(page,'首页目录',[card],problems);
    await page.waitForTimeout(1100);
    const before=(await card.boundingBox())!;
    const first=await card.elementHandle();
    const tiers=await page.locator('[data-catalog-tier]').first().elementHandle();
    assert.equal(await page.locator('#feedNew .feednewskeleton').count()>0,hasFeed);
    if(hasFeed){
     const colors=await page.locator('#feedNew .feednewskeleton .pic').first().evaluate(node=>{
      const probe=document.createElement('i');probe.style.background='var(--hover)';node.append(probe);
      const expected=getComputedStyle(probe).backgroundColor,actual=getComputedStyle(node).backgroundColor;
      probe.remove();return {expected,actual};
     });
     assert.equal(colors.actual,colors.expected,'骨架圆角底色与占位层一致');
    }
    assert.equal(await page.evaluate(()=>(window as any).__catalogSkeletons),1,'目录骨架只能创建一次');
    assert.deepEqual(await page.evaluate(()=>(window as any).__catalogRatios),[hasFeed?3/4:16/9]);
    release();
    if(hasFeed)await page.locator('#feedNew [data-feed-id]').first().waitFor({state:'visible'});
    await page.waitForTimeout(1400);
    const after=(await card.boundingBox())!;
    assert.ok(Math.abs(after.y-before.y)<=1,`新作加载影响网格位置：${before.y} → ${after.y}`);
    assert.equal(await first!.evaluate(node=>node.isConnected),true);
    assert.equal(await tiers!.evaluate(node=>node.isConnected),true);
    assert.equal(await page.evaluate(()=>(window as any).__firstCard?.isConnected&&(window as any).__firstTier?.isConnected),true);
    assert.equal(await page.locator('#feedNew [data-skeleton]').count(),0);
    assert.equal(await page.locator('#feedNew').getAttribute('aria-label'),'未入库的新作');
    assert.ok(scopes.length>0);
    assert.deepEqual([...new Set(scopes)],['local'],'默认筛选始终排除离线来源');
   }finally{release();await context.close()}
  });
 }
});
