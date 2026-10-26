import { moveGlidePane } from '@peach/legacy/ui';
import { initBoardTooltip } from './board-tooltip';

/** Board 控件的共享展示层；范围输入保留浏览器原生键盘语义。 */
export function syncBoardRange(input: HTMLInputElement) {
  const min=Number(input.min)||0,max=Number(input.max)||100,value=Number(input.value);
  const percent=max>min?Math.max(0,Math.min(100,(value-min)/(max-min)*100)):0;
  input.style.setProperty('--board-range-value',`${percent}%`);
}

const boardDocuments = new WeakSet<Document>();
export function initBoardControls() {
  if(boardDocuments.has(document))return;
  boardDocuments.add(document);
  const scan=(root:ParentNode)=>{root.querySelectorAll<HTMLInputElement>('input[type=range]').forEach(syncBoardRange);wireBoardSegments(root);wireBoardTabs(root)};
  scan(document);
  const observer=new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node instanceof Element){if(node.matches('input[type=range]'))syncBoardRange(node as HTMLInputElement);scan(node)}});
  observer.observe(document.body,{subtree:true,childList:true});
  document.addEventListener('input',event=>{if(event.target instanceof HTMLInputElement&&event.target.type==='range')syncBoardRange(event.target)});
  initBoardTooltip();
}

/** 页面导航只同步可见位置，选中态由共享样式绘制。 */
export function wireBoardTabs(root:ParentNode){
  const selector='.ui-board-local-nav:not([data-section-nav])';
  const groups=[...root.querySelectorAll<HTMLElement>(selector)];
  if(root instanceof HTMLElement&&root.matches(selector))groups.push(root);
  groups.forEach(group=>{
    if(group.hasAttribute('data-board-tabs'))return;group.dataset.boardTabs='true';
    const measure=()=>{const selected=group.querySelector<HTMLElement>('button[aria-selected=true],button[aria-pressed=true]');if(!selected||!selected.offsetWidth)return;const left=selected.offsetLeft,right=left+selected.offsetWidth;if(left<group.scrollLeft)group.scrollLeft=left;else if(right>group.scrollLeft+group.clientWidth)group.scrollLeft=right-group.clientWidth};
    measure();
    const mutation=new MutationObserver(measure);mutation.observe(group,{subtree:true,attributes:true,attributeFilter:['aria-selected','aria-pressed']});
    const resize=new ResizeObserver(()=>{if(!group.isConnected){resize.disconnect();mutation.disconnect();return}measure()});resize.observe(group);
  });
}

const segmentPositions=new Map<string,{x:number;y:number;w:number;h:number}>();
/** 原生 radio 保留方向键语义，选中底板按实际尺寸滑动。
    面板切换那几组是 `role=tablist`：滑块换成按 `aria-selected` 找当前项，ARIA 仍然是 Tabs
    控制 tabpanel，不为了长得像分段控件就把它写成 radiogroup。 */
export function wireBoardSegments(root:ParentNode) {
  const selector='.iconswitch,.insightswitch,.follow-workspace-switch';
  const groups=[...root.querySelectorAll<HTMLElement>(selector)];
  if(root instanceof HTMLElement&&root.matches(selector))groups.push(root);
  groups.forEach(group=>{
    if(group.hasAttribute('data-board-segments')||group.closest('[data-skeleton]'))return;
    group.dataset.boardSegments='true';
    const thumb=document.createElement('span');thumb.className='board-segment-thumb';thumb.setAttribute('aria-hidden','true');group.prepend(thumb);
    /* 底板换位跟筛选条上那块玻璃是同一件事——「当前是这一个」从一处挪到另一处——所以
       走同一段动作：滑过去、冲过落点、荡回来。
       上一次停在哪儿按这一组的身份记，不按节点记：点一下往往把整块重画一遍，拿节点
       记等于每次都是一块新出现的底板，只落位不动画，一路点下来它一次都没动过。
       真正头一次出现时没有上一次，那一下不动画——新出现的底板不该从别处飞进来。 */
    const key=group.className+(group.getAttribute('aria-label')??'');
    let from=segmentPositions.get(key)??null;
    const measure=()=>{
      const selected=group.querySelector<HTMLElement>('label:has(input:checked),button[aria-selected=true]');if(!selected||!selected.offsetWidth)return;
      const box={x:selected.offsetLeft,y:selected.offsetTop,w:selected.offsetWidth,h:selected.offsetHeight};
      moveGlidePane(thumb,from,box,'x');
      from=box;segmentPositions.set(key,box);
    };
    group.addEventListener('change',measure);
    const mutation=new MutationObserver(measure);mutation.observe(group,{subtree:true,attributes:true,attributeFilter:['aria-selected']});
    const resize=new ResizeObserver(()=>{if(!group.isConnected){resize.disconnect();mutation.disconnect();return}measure()});resize.observe(group);
    measure();requestAnimationFrame(()=>group.classList.add('board-segments-ready'));
  });
}
