import {describe,expect,it} from 'vitest';
import {requestErrorMessage} from '../src/core';
import {noteHtml,projectBannerHtml,gaugeHtml,progressHtml,configurationSkeletonHtml} from '../src/ui-kit';

describe('中文错误反馈',()=>{
  it.each(['Failed to fetch','NetworkError when attempting to fetch resource.','Load failed'])('网络异常 %s 给出连接检查方法',message=>{
    expect(requestErrorMessage(new TypeError(message))).toBe('无法连接到 Peach 服务，请确认网络连接和托盘服务已启动。');
  });
  it.each([[401,'登录已失效'],[403,'没有执行此操作的权限'],[404,'已不存在'],[429,'过于频繁'],[503,'暂时不可用'],[504,'超时']])('HTTP %s 使用明确原因', (code,reason)=>{
    expect(requestErrorMessage('request failed',code)).toContain(reason);
  });
  it('正文里的 timeout 字样不把 500 读成超时',()=>{
    expect(requestErrorMessage('Traceback: httpx.ReadTimeout raised in handler (timeout=30)',500))
      .toBe('Peach 服务处理失败，请重试；持续失败时查看托盘日志。');
    expect(requestErrorMessage('read timed out')).toContain('超时');
  });
  it('本机接口的 502 说的是 Peach 服务，不提代理',()=>{
    const text=requestErrorMessage('Bad Gateway',502);
    expect(text).toContain('Peach 服务');
    expect(text).not.toContain('代理');
  });
  it('保留服务端明确的中文原因',()=>{
    expect(requestErrorMessage('媒体盘没有挂载，请先连接媒体盘。',409)).toBe('媒体盘没有挂载，请先连接媒体盘。');
  });
});
describe('持久提示控件',()=>{
  it('配置骨架标题和卡片共用真实页面网格与正文底栏',()=>{
    const host=document.createElement('div');host.innerHTML=configurationSkeletonHtml();
    const page=host.querySelector('.ui-configpage')!;
    expect(page.querySelectorAll(':scope > .ui-configgroup')).toHaveLength(5);
    expect(page.querySelectorAll(':scope > .configfieldset')).toHaveLength(12);
    for(const card of page.querySelectorAll('.configfieldset')){
      expect(card.querySelector(':scope > .geist-fieldset-content')).not.toBeNull();
      expect(card.querySelector(':scope > .geist-fieldset-footer')).not.toBeNull();
    }
    expect(host.children).toHaveLength(1);
  });
  it.each([[79,'normal'],[80,'warning'],[94,'warning'],[95,'error']])('容量 %s 使用一致阈值', (value,level)=>{
    const host=document.createElement('div');host.innerHTML=gaugeHtml('空间使用率',value,100,{usage:true});
    expect(host.querySelector('.ui-geist-gauge')?.getAttribute('data-level')).toBe(level);
    expect(gaugeHtml('完成率',value,100)).toContain('data-level="normal"');
  });
  it('失败提示包含一个内部操作按钮',()=>{
    const host=document.createElement('div');host.innerHTML=noteHtml('来源离线',{variant:'error',filled:true,actionLabel:'重试'});
    expect(host.querySelectorAll('[role=alert] button')).toHaveLength(1);
  });
  it('容量比例和任务计数使用真实上限，未知容量不显示零',()=>{
    const host=document.createElement('div');host.innerHTML=gaugeHtml('使用率',25,50);
    expect(host.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe('50');
    expect(gaugeHtml('使用率',0,0)).toContain('未取得');
    host.innerHTML=progressHtml('分析记录',3,10,{stops:[{value:5,label:'分析结束'}],variant:'warning'});
    expect(host.querySelector('[role=progressbar]')?.getAttribute('aria-valuemax')).toBe('10');
    expect(host.querySelector('[aria-label="分析结束"]')).not.toBeNull();
  });
  it('Note 错误不会直接输出英文网络异常',()=>{
    const host=document.createElement('div');host.innerHTML=noteHtml('Failed to fetch',{variant:'error',size:'small',filled:true});
    expect(host.querySelector('[role=alert]')?.textContent).toContain('无法连接到 Peach');
    expect(host.querySelector('.geist-note-small.geist-note-filled')).not.toBeNull();
    expect(host.querySelector('button')).toBeNull();
  });
  it('Project Banner 包含处理入口且没有关闭键',()=>{
    const host=document.createElement('div');host.innerHTML=projectBannerHtml('扫描未完成',{variant:'warning',href:'/data-cleanup',label:'查看并处理'});
    expect(host.querySelector('.ui-project-banner-warning')).not.toBeNull();
    expect(host.querySelector('a')?.getAttribute('href')).toBe('/data-cleanup');
    expect(host.querySelector('button')).toBeNull();
  });
});
