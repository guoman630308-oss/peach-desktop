/**
 * Geist Collapse：原生 `<details>` 不过渡高度，所以把 summary 以外的内容包进
 * `.ui-fcollapse`，开合时量 `scrollHeight` 写 inline `height` 让它过渡。
 * （试过 `::details-content`，那条路会吞掉内容，已弃。）
 *
 * 同一个 `details` 只接一次，重绘后原样再调用是安全的。
 */
export function wireCollapse(
  root: ParentNode | null | undefined, selector: string, idPrefix: string, triggerSelector = 'summary',
): void {
  root?.querySelectorAll<HTMLDetailsElement>(selector).forEach((details,index)=>{
    if(details.querySelector(':scope > .ui-fcollapse'))return;
    const body=document.createElement('div');body.className='ui-fcollapse';
    /* 内边距放在内层 .fcollapsebody：.ui-fcollapse 自身不带 padding，height 才能真正
       过渡到 0，否则 border-box 会卡在内边距上、收起末尾跳一下。 */
    const inner=document.createElement('div');inner.className='fcollapsebody';
    [...details.children].forEach(child=>{
      if(child.tagName==='SUMMARY')return;
      inner.appendChild(child);
    });
    body.appendChild(inner);details.appendChild(body);
    const summary=details.querySelector(triggerSelector)!;
    if(triggerSelector!=='summary')details.querySelector('summary')!.addEventListener('click',event=>event.preventDefault());
    let expanded=details.open;
    body.id=`${idPrefix}-${index}`;
    body.inert=!expanded;
    /* 高度过渡要 `overflow:hidden`，可展开着不动时它还在裁——里面最后那一行卡片的落影
       正好落在下沿外，被切掉半条，读出来是这一列没排完。过渡跑完（或一开始就是展开的）
       就摘掉那道裁边；收起那一下先装回去，否则内容会在高度收到 0 的过程中一直露在外面。 */
    if(expanded)body.classList.add('ui-fcollapse-settled');
    summary.setAttribute('aria-controls',body.id);
    summary.setAttribute('aria-expanded',String(expanded));
    summary.addEventListener('click',event=>{
      event.preventDefault();
      expanded=!expanded;
      summary.setAttribute('aria-expanded',String(expanded));
      setCollapseOpen(details,body,expanded);
    });
  });
}

/**
 * 把一个 Collapse 开到或收到 `expanded`，`body` 是 summary 后面那层容器，`.ui-fcollapse` 由这里挂上。
 * `wireCollapse`、React 设置页的 `Disclosure` 与侧栏分组共用这一份；过渡途中又被反向点按时，前一次的收尾不再做。
 */
const collapseRuns=new WeakMap<HTMLElement,number>();
export function setCollapseOpen(details: HTMLDetailsElement, body: HTMLElement, expanded: boolean): void {
  body.classList.add('ui-fcollapse');
  const run=(collapseRuns.get(body)||0)+1;collapseRuns.set(body,run);
  const isCurrent=()=>collapseRuns.get(body)===run;
  if(expanded){
    body.inert=false;
    const start=details.open?body.getBoundingClientRect().height:0;
    details.open=true;
    growCollapse(body,start,isCurrent);
  }else{
    body.inert=true;body.classList.remove('ui-fcollapse-settled');
    body.style.height=body.getBoundingClientRect().height+'px';body.getBoundingClientRect();
    body.style.height='0px';
    settleHeight(body,()=>{if(isCurrent()){details.open=false;body.style.height=''}});
  }
}

/* 高度过渡跑完再收尾；减少动效时没有 transitionend，260ms 兜底。 */
function settleHeight(body: HTMLElement, fn: () => void): void {
  let done=false,timer:ReturnType<typeof setTimeout>|undefined;
  const finish=(e?:TransitionEvent)=>{
    if(e&&e.propertyName!=='height')return;
    if(done)return;done=true;
    body.removeEventListener('transitionend',finish);clearTimeout(timer);
    fn();
  };
  body.addEventListener('transitionend',finish);
  timer=setTimeout(finish,260);
}

/**
 * `.ui-fcollapse` 从 `start` 长到内容此刻的高度，跑完交回 `auto` 并摘掉裁边。Collapse 展开与
 * 侧栏名单摊开共用这一份。`isCurrent` 返回 false 说明中途又被收起，收尾就不做。
 */
export function growCollapse(body: HTMLElement, start: number, isCurrent: () => boolean = ()=>true): void {
  body.classList.remove('ui-fcollapse-settled');
  body.style.height=start+'px';body.getBoundingClientRect();
  body.style.height=body.scrollHeight+'px';
  settleHeight(body,()=>{if(isCurrent()){body.style.height='auto';body.classList.add('ui-fcollapse-settled')}});
}
