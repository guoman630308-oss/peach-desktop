import assert from 'node:assert/strict';
import {after,before,describe,it} from 'node:test';
import type {Browser} from 'playwright-core';
import {launch,visit,VIEWPORTS} from './harness.ts';

describe('Toast 操作强调色',()=>{
 let browser:Browser;
 before(async()=>{browser=await launch()});
 after(async()=>{await browser.close()});
 for(const viewport of VIEWPORTS)for(const theme of ['light','dark'])for(const accent of ['blue','emerald','rose']){
  it(`${viewport.name} ${theme} ${accent} 撤销操作`,{timeout:60000},async()=>{
   const opened=await visit(browser,'/follow',viewport);
   try{
    const page=opened.page;
    await page.route(url=>url.pathname==='/api/library-processing',route=>route.fulfill({json:{status:'idle'}}));
    await page.goto(new URL('/',page.url()).href,{waitUntil:'load'});
    await page.evaluate(async({theme,accent})=>{
     document.documentElement.dataset.theme=theme;
     document.documentElement.classList.toggle('dark',theme==='dark');
     document.documentElement.dataset.accent=accent;
     (window as any).__undoClicked=0;
     const entry='/dist/peach-app.js',ui=await import(entry);
     ui.showToast(document.getElementById('toasts'),{success:'',error:''},'accent-undo',
      {html:'已加入稍后看',alert:false,timeout:0,action:{label:'撤销',run:()=>{(window as any).__undoClicked++}}});
    },{theme,accent});
    const button=page.locator('#toasts [data-action]').last();await button.waitFor();
    const colours=async(variable:string)=>button.evaluate((e,variable)=>{
     const probe=document.createElement('button');probe.style.background=`var(${variable})`;probe.style.color='var(--color-text-white)';document.body.append(probe);
     const expected=getComputedStyle(probe),actual=getComputedStyle(e);
     const result={expectedBackground:expected.backgroundImage,background:actual.backgroundImage,expectedColor:expected.color,color:actual.color};probe.remove();return result;
    },variable);
    const resting=await colours('--board-blue');assert.equal(resting.background,resting.expectedBackground);assert.equal(resting.color,resting.expectedColor);
    if(!viewport.mobile){await button.hover();const hovering=await colours('--board-blue-hover');assert.equal(hovering.background,hovering.expectedBackground)}
    await button.click();assert.equal(await page.evaluate(()=>(window as any).__undoClicked),1);
   }finally{await opened.close()}
  });
 }
});
