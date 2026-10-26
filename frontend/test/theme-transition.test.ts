import { expect, it, vi } from 'vitest';
import { transitionTheme } from '../src/theme-transition';
it('theme snapshots restore live glass after completion or rejection and apply once',async()=>{
  document.body.replaceChildren();
  vi.stubGlobal('matchMedia',()=>({matches:false}));
  const button=document.createElement('button');document.body.append(button);
  for(const rejects of [false,true]){
    const apply=vi.fn();
    let finish!:()=>void;
    const finished=new Promise<void>((resolve,reject)=>{finish=()=>rejects?reject(new Error('skip')):resolve()});
    const start=vi.fn((update:()=>void)=>{
      expect(document.documentElement.dataset.themeSnapshot).toBe('true');
      update();return{finished};
    });
    Object.defineProperty(document,'startViewTransition',{configurable:true,value:start});
    const pending=transitionTheme(button,apply);
    expect(apply).toHaveBeenCalledTimes(1);
    finish();await pending;
    expect(apply).toHaveBeenCalledTimes(1);
    expect(document.documentElement.hasAttribute('data-theme-snapshot')).toBe(false);
    expect([...document.head.querySelectorAll('style')].some(style=>style.textContent?.includes('peach-theme-reveal'))).toBe(false);
  }
  Reflect.deleteProperty(document,'startViewTransition');vi.unstubAllGlobals();
});
