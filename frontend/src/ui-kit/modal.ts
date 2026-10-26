/* Geist Modal 的两种正文：一句待确认的话（`confirmModal`）与要填的一份表单（`formModal`）。
   由共享源码直接引用，随 `peach-app.js` 发出；壳与 React 子树打开的是同一份，
   标题 id 的序号因此全页连续。 */
import { setActionBusy } from './controls';
import { noteHtml } from './markup';
import { playUiSound } from './sounds';
import { attachOverlayScrollbar } from './overlay-scrollbar';

/* Geist Modal 的另一种正文：要填的一份表单，而不是一句待确认的话。

   壳、遮罩、焦点陷阱、Escape 和关掉后把焦点还给触发钮全部来自 confirmModal 用的那身
   `.geist-modal`，两者的差别只有正文和主按钮做什么。所以弹层的几何只有一处：标题
   20px/26px 的 h3、正文 20px 内边距、操作条粘在底两端对齐、主按钮在右下角。

   返回值里的 `dialog` 交给调用方接自己的行事件，`done` 在弹层关掉时兑现。 */
let formModalSeq=0;
export function formModal({title,description='',body='',confirmLabel,cancelLabel='取消',
                           onConfirm=null,confirmDisabled=false}:{
  title?: string; description?: string; body?: string; confirmLabel?: string; cancelLabel?: string;
  onConfirm?: (()=>unknown)|null; confirmDisabled?: boolean;
}={}):{dialog:HTMLDialogElement;confirmButton:any;done:Promise<{confirmed:boolean;result?:unknown}>;close:()=>void}{
  const trigger=document.activeElement;
  const dialog=document.createElement('dialog');
  dialog.className='ui-geist-modal';
  const titleId=`geist-form-title-${++formModalSeq}`;
  dialog.setAttribute('aria-labelledby',titleId);
  dialog.innerHTML=`<form class="ui-geist-modal-form" novalidate>
      <div class="ui-geist-modal-body"><h3 id="${titleId}"></h3>${description?'<p></p>':''}
        <div class="ui-geist-modal-fields">${body}</div><div data-modal-error></div></div>
      <footer class="geist-modal-footer">
        <div><button type="button" class="geist-button" data-modal-cancel></button></div>
        <div><button type="submit" class="geist-button primary" data-modal-confirm></button></div>
      </footer></form>`;
  dialog.querySelector('h3')!.textContent=title as string;
  if(description)dialog.querySelector('.ui-geist-modal-body p')!.textContent=description;
  const cancel:any=dialog.querySelector('[data-modal-cancel]');
  const accept:any=dialog.querySelector('[data-modal-confirm]');
  const failure:any=dialog.querySelector('[data-modal-error]');
  cancel.textContent=cancelLabel;
  accept.textContent=confirmLabel;
  accept.disabled=!!confirmDisabled;
  document.body.append(dialog);
  let settled:{confirmed:boolean;result?:unknown}|null=null,busy=false;
  const done=new Promise<{confirmed:boolean;result?:unknown}>(resolve=>dialog.addEventListener('close',()=>{
    dialog.remove();
    if(trigger instanceof HTMLElement&&trigger.isConnected)trigger.focus();
    resolve(settled||{confirmed:false});
  },{once:true}));
  cancel.onclick=()=>{if(!busy)dialog.close()};
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault()});
  // 遮罩上的点击落在 <dialog> 自己身上；这里没有不可逆的动作，允许点外面关掉。
  dialog.addEventListener('click',event=>{if(event.target===dialog&&!busy)dialog.close()});
  dialog.querySelector('form')!.onsubmit=async event=>{
    event.preventDefault();
    if(busy||accept.disabled)return;
    if(!onConfirm){settled={confirmed:true};dialog.close();return}
    failure.innerHTML='';busy=true;setActionBusy(accept);
    try{
      settled={confirmed:true,result:await onConfirm()};
      dialog.close();
    }catch(error:any){
      failure.innerHTML=noteHtml(error.message||'操作未完成',{variant:'error'});
      setActionBusy(accept,false);
    }finally{busy=false}
  };
  dialog.showModal();
  playUiSound('pop');
  (dialog.querySelector<HTMLElement>('.ui-geist-modal-fields input:not([type="checkbox"])')||accept).focus();
  return {dialog,confirmButton:accept,done,close:()=>dialog.close()};
}

/* Geist Modal：一次写操作落库前的确认。

   实测 https://vercel.com/geist/modal（2026-09-04）：卡片 540px 宽、12px 圆角、窄屏两侧
   各留 10px，正文 20px 内边距、14px/20px，标题是 20px/26px 的 600 字重 h3，底部操作条
   12px 内边距、粘在底、两端对齐，按钮 32px 高、6px 圆角、14px/500，遮罩纯黑不带模糊。
   标题写成陈述句而不是问句；主按钮是与标题同一个动词的「动词+名词」，取消键就写「取消」；
   成功后的 Toast 与主按钮共用那个动词。

   用原生 <dialog> 承载：焦点陷阱、Escape、背景 inert 和关掉后把焦点还给触发钮都由它给，
   自己搭一遍只会少掉其中一两样。onConfirm 失败时弹层不关，原因留在原位等重试。 */
let modalSeq=0;
export function confirmModal(options:{
  title: string; body: string; confirmLabel: string; cancelLabel?: string; danger?: boolean;
  items?: readonly string[];
  onConfirm?: () => Promise<unknown>;
}):Promise<{confirmed:boolean;result?:unknown}>;
export function confirmModal({title,body,items=[],confirmLabel,cancelLabel='取消',onConfirm=null,danger=false}:{
  title?: string; body?: string; confirmLabel?: string; cancelLabel?: string; danger?: boolean;
  items?: readonly string[];
  onConfirm?: (()=>unknown)|null;
}={}):Promise<{confirmed:boolean;result?:unknown}>{
  const trigger=document.activeElement;
  const dialog=document.createElement('dialog');
  dialog.className='ui-geist-modal';
  const titleId=`geist-modal-title-${++modalSeq}`;
  dialog.setAttribute('aria-labelledby',titleId);
  dialog.innerHTML=`<div class="ui-geist-modal-body">
      <h3 id="${titleId}"></h3><p></p><div data-modal-error></div></div>
    <footer class="geist-modal-footer">
      <div><button type="button" class="geist-button" data-modal-cancel></button></div>
      <div><button type="button" class="geist-button primary" data-modal-confirm></button></div>
    </footer>`;
  dialog.querySelector('h3')!.textContent=title as string;
  dialog.querySelector('.ui-geist-modal-body p')!.textContent=body as string;
  let list: HTMLUListElement | null = null;
  if(items.length){
    const host=document.createElement('div');
    host.className='ui-geist-modal-list';
    list=document.createElement('ul');
    list.setAttribute('aria-label','所选记录');
    list.tabIndex=0;
    for(const text of items){
      const item=document.createElement('li');
      item.textContent=text;
      list.append(item);
    }
    host.append(list);
    dialog.querySelector('[data-modal-error]')!.before(host);
  }
  const cancel:any=dialog.querySelector('[data-modal-cancel]');
  const accept:any=dialog.querySelector('[data-modal-confirm]');
  if(danger){accept.classList.remove('primary');accept.classList.add('danger')}
  const failure:any=dialog.querySelector('[data-modal-error]');
  cancel.textContent=cancelLabel;
  accept.textContent=confirmLabel;
  document.body.append(dialog);
  return new Promise(resolve=>{
    let settled:{confirmed:boolean;result?:unknown}|null=null;
    let busy=false;
    dialog.addEventListener('close',()=>{
      dialog.remove();
      if(trigger instanceof HTMLElement&&trigger.isConnected)trigger.focus();
      resolve(settled||{confirmed:false});
    },{once:true});
    cancel.onclick=()=>{if(!busy)dialog.close()};
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault()});
    /* 遮罩上的点击落在 <dialog> 自己身上，卡片里的落在子元素上。这个动作可撤销，
       按 Geist 的判据允许点外面关掉。 */
    dialog.addEventListener('click',event=>{if(event.target===dialog&&!busy&&!danger)dialog.close()});
    accept.onclick=async()=>{
      if(busy)return;
      if(!onConfirm){settled={confirmed:true};dialog.close();return}
      failure.innerHTML='';
      busy=true;
      setActionBusy(accept);
      try{
        settled={confirmed:true,result:await onConfirm()};
        dialog.close();
      }catch(error:any){
        failure.innerHTML=noteHtml(error.message||'操作未完成',{variant:'error'});
        setActionBusy(accept,false);
      }finally{busy=false}
    };
    dialog.showModal();
    attachOverlayScrollbar(list);
    playUiSound('pop');
    (danger?cancel:accept).focus();
  });
}
