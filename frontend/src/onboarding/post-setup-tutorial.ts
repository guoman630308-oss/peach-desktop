/* 安装后教程的状态。由共享源码直接引用，随 `peach-app.js` 发出；请求代际是模块级状态，
   壳与设置面板读写的是同一份。

   教程 DOM 由 application/tutorial.js 装配；完成状态与渲染分开。
   清单完成跟着数据库走（`/api/settings` 的 `postSetupTutorialDone`），
   换台设备打开不会又被教一遍；折叠和逐项跳过是当下这块屏幕的摆法，留在本地。
   三个键、签名和请求代际都收在这里，装配那一侧只管把它们接到 DOM 上。 */
export const POST_SETUP_TUTORIAL_KEY='peach.post-setup-tutorial.v1';
export const POST_SETUP_TUTORIAL_COLLAPSED_KEY='peach.post-setup-tutorial-collapsed.v1';
export const POST_SETUP_TUTORIAL_SKIPPED_KEY='peach.post-setup-tutorial-skipped.v1';

const readStored=(key:string)=>{try{return localStorage.getItem(key)}catch{return null}};
const writeStored=(key:string,value:string)=>{try{localStorage.setItem(key,value)}catch{}};

export const postSetupTutorialMarker=():string=>readStored(POST_SETUP_TUTORIAL_KEY)||'';
export const setPostSetupTutorialMarker=(value:string):void=>writeStored(POST_SETUP_TUTORIAL_KEY,value);
export const postSetupTutorialCollapsed=():boolean=>readStored(POST_SETUP_TUTORIAL_COLLAPSED_KEY)==='1';
export const setPostSetupTutorialCollapsed=(value:unknown):void=>
  writeStored(POST_SETUP_TUTORIAL_COLLAPSED_KEY,value?'1':'0');
export const postSetupTutorialSkipped=():Set<string>=>{
  try{
    const parsed=JSON.parse(readStored(POST_SETUP_TUTORIAL_SKIPPED_KEY)||'[]');
    return new Set(Array.isArray(parsed)?parsed.filter(key=>typeof key==='string'):[]);
  }catch{return new Set()}
};
export const setPostSetupTutorialSkipped=(values:Iterable<string>):void=>
  writeStored(POST_SETUP_TUTORIAL_SKIPPED_KEY,JSON.stringify([...values]));

/* 重绘的判据：清单里每一项的文字、目标和完成与否。只要这一串没变，页面上那张卡就
   还是对的，重画一次只会打断正在读它的人和刚点开的折叠。 */
export const postSetupTutorialSignature=(tasks:any[]):string=>JSON.stringify(
  tasks.map(task=>[task.key,task.done,task.label,task.description,task.href]));

/* 请求代际：教程跟着每次路由切换重新取数，慢的那一发回来时页面可能已经换了。 */
let postSetupTutorialRequest=0;
export const nextPostSetupTutorialRequest=():number=>++postSetupTutorialRequest;
export const isCurrentPostSetupTutorialRequest=(request:number):boolean=>request===postSetupTutorialRequest;

/** 重新打开教程：清掉跳过与折叠，本地标记回到未完成，由调用方负责服务端那一半。 */
export function resetPostSetupTutorialState():void{
  setPostSetupTutorialSkipped(new Set());
  setPostSetupTutorialCollapsed(false);
  setPostSetupTutorialMarker('pending');
}
