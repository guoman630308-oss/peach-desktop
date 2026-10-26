interface Lane { axis: 'x' | 'y'; track: HTMLDivElement; thumb: HTMLDivElement; range: number; travel: number }

/**
 * 覆盖式滚动条：滑块浮在内容上，一列宽度都不占。
 *
 * 已有的 `.geist-scroller` 解决的是另一半问题——它用两端渐隐说明「还能往下」，
 * 但没有滑块，读不出这一列有多长、自己停在哪儿，而且要求内容套进它自己的包装层。
 * 这里只往既有滚动容器的父元素上挂轨道，不动内容结构，两者可以叠加使用。
 *
 * 轨道必须是容器的兄弟：跟着内容一起滚的轨道等于没有轨道。宿主因此得是定位祖先，
 * 而且不一定和容器一样大（设置面板的卡片还含着标题栏），所以轨道的位置每次都按
 * 两个 rect 量出来，不假设它们同框。两条轴各一条轨道，谁溢出谁显示——同一个容器
 * 可能在不同版式下换轴（队列列表 `[data-mix-list]` 在带队列的详情格里就是横滚）。
 * 整页那一条是唯一的例外：`html` 没有元素父级，轨道挂进 body 并由 `.page` 改成
 * fixed，位置只认视口，不用量。
 * 几何按 2026-09-05 实测 vercel.com 侧栏：
 * 滑块长 = 可视长 / 内容长 × 轨道长，位移 = 滚动进度 × (轨道长 − 滑块长)。
 * 返回一个手动重算函数，给那些既不改容器尺寸也不改子树的情形（例如换字体后重排）。
 */
export function attachOverlayScrollbar(
  container: HTMLElement | null, { variant = '' }: { variant?: string } = {},
): (() => void) | null {
  if(!container||container.dataset.overlayScrollbar)return null;
  const root=container===document.documentElement;
  const host=root?document.body:container.parentElement;
  if(!host)return null;
  container.dataset.overlayScrollbar='true';
  // 轨道按宿主的内边距框定位，static 的宿主会把它甩到更外层某个祖先上去。
  if(!root&&getComputedStyle(host).position==='static')host.style.position='relative';
  // 局部纵向滚动统一显示边缘提示，整页使用粘性导航层次。
  const edges=root?null:document.createElement('div');
  if(edges){
    edges.className='ui-ov-edges';
    edges.setAttribute('aria-hidden','true');
    edges.innerHTML='<span class="ui-ov-edge-top"></span><span class="ui-ov-edge-bottom"></span>';
    host.append(edges);
  }
  const axes:Lane['axis'][]=root?['y']:['y','x'];
  const lanes=axes.map((axis):Lane=>{
    const track=document.createElement('div');
    // 两个类名写全，别拼 `ov-${axis}`：样式表的选择器要能在源码里查到消费者。
    track.className=`ovtrack ${axis==='y'?'ov-y':'ov-x'}${variant?` ${variant}`:''}`;
    const thumb=document.createElement('div');
    thumb.className='ovthumb';
    track.append(thumb);
    host.append(track);
    return {axis,track,thumb,range:0,travel:0};
  });
  /* 容器在宿主里的偏移，只能按布局盒子量。`getBoundingClientRect()` 给的是变换后的
     几何，而下面要拿它跟 `clientWidth` 这类布局值相减——弹层开合动画正把卡片按
     `scale(.85)` 缩着的那 300ms 里，两者不在同一个坐标系，算出来的轨道位置会落进
     容器内部。实测设置弹层首帧右边距 41px（应为 0），直到滚一下触发重算才跳回右
     边缘，看着像「滚动条从左边跑到右边」。`offsetLeft`/`offsetTop` 不受变换影响，
     沿 offsetParent 链累加到宿主为止；走不到宿主（宿主不是定位祖先）才退回量 rect。 */
  const offsetWithin=()=>{
    let left=0,top=0,node:Element|null=container;
    while(node&&node!==host){
      const box=node as HTMLElement;left+=box.offsetLeft;top+=box.offsetTop;node=box.offsetParent}
    if(node===host)return {left,top};
    const hostRect=host.getBoundingClientRect(),rect=container.getBoundingClientRect();
    return {left:rect.left-hostRect.left-host.clientLeft,
      top:rect.top-hostRect.top-host.clientTop};
  };
  const place=({axis,track}:Lane)=>{
    if(root)return;
    const {left,top}=offsetWithin();
    if(axis==='y'){
      track.style.top=`${top+8}px`;
      track.style.height=`${Math.max(0,container.clientHeight-16)}px`;
      track.style.right=`${host.clientWidth-left-container.clientWidth}px`;
    }else{
      track.style.left=`${left+8}px`;
      track.style.width=`${Math.max(0,container.clientWidth-16)}px`;
      track.style.bottom=`${host.clientHeight-top-container.clientHeight}px`;
    }
  };
  let verticalRange=0,edgeState='';
  // 滚动只读取位置；容器与轨道尺寸由尺寸、内容变化和手动重算刷新。
  const paintPosition=()=>{
    const top=container.scrollTop,left=container.scrollLeft;
    if(edges){
      const above=verticalRange>1&&top>1;
      const below=verticalRange>1&&top<verticalRange-1;
      const state=`${above}:${below}`;
      if(state!==edgeState){
        edgeState=state;
        edges.hidden=!above&&!below;
        edges.classList.toggle('ui-can-scroll-top',above);
        edges.classList.toggle('ui-can-scroll-bottom',below);
        if(above||below){
          container.style.setProperty('--scroll-edge-top',above?'16px':'0px');
          container.style.setProperty('--scroll-edge-bottom',below?'16px':'0px');
        }else{
          container.style.removeProperty('--scroll-edge-top');
          container.style.removeProperty('--scroll-edge-bottom');
        }
        container.toggleAttribute('data-scroll-edges',above||below);
      }
    }
    lanes.forEach(({axis,thumb,range,travel})=>{
      if(range<=1)return;
      const offset=travel>0?Math.max(0,Math.min(1,(axis==='y'?top:left)/range))*travel:0;
      thumb.style.transform=`translate${axis==='y'?'Y':'X'}(${offset}px)`;
    });
  };
  let pendingFrame:number|null=null;
  const schedulePosition=()=>{
    if(pendingFrame!==null)return;
    pendingFrame=requestAnimationFrame(()=>{pendingFrame=null;paintPosition()});
  };
  const sync=()=>{
    verticalRange=container.scrollHeight-container.clientHeight;
    if(edges){
      const {left,top}=offsetWithin();
      edges.style.left=`${left}px`;
      edges.style.top=`${top}px`;
      edges.style.width=`${container.clientWidth}px`;
      edges.style.height=`${container.clientHeight}px`;
    }
    lanes.forEach(lane=>{
      const {axis,track,thumb}=lane,vertical=axis==='y';
      const size=vertical?container.clientHeight:container.clientWidth;
      const content=vertical?container.scrollHeight:container.scrollWidth;
      const range=content-size;
      lane.range=range;
      lane.travel=0;
      if(range<=1){track.hidden=true;return}
      // 先显再量：藏起来的轨道长度是 0，拿它当「量不到」会把自己永久锁在隐藏态。
      track.hidden=false;
      place(lane);
      const trackSize=vertical?track.clientHeight:track.clientWidth;
      if(!trackSize)return;
      // 短到抓不住的滑块等于没有滑块：内容特别长时给它一个下限，代价是滑块位置与
      // 滚动进度不再严格线性，但可拖动比可换算重要。
      const thumbSize=Math.max(24,Math.min(trackSize,size/content*trackSize));
      lane.travel=trackSize-thumbSize;
      thumb.style[vertical?'height':'width']=`${thumbSize}px`;
    });
    paintPosition();
  };
  const scroller:EventTarget=root?document:container;
  scroller.addEventListener('scroll',schedulePosition,{passive:true});
  const resize=new ResizeObserver(sync);
  resize.observe(container);
  if(!root)container.addEventListener('load',sync,true);
  // 内容长短变了但容器盒子没变（抽屉重建、分区展开），容器自己的 ResizeObserver 一声不响。
  // 整页那一条改看 body：它的高度就是内容高度，而在 documentElement 上挂 subtree 的
  // MutationObserver 等于每渲染一张卡都强制一次重排。
  if(root)resize.observe(document.body);
  else{
    const observed=new Set<Element>();
    const contentChanged=()=>{
      const children=new Set(container.children);
      observed.forEach(child=>{if(!children.has(child)){resize.unobserve(child);observed.delete(child)}});
      children.forEach(child=>{if(!observed.has(child)){resize.observe(child);observed.add(child)}});
      sync();
    };
    // 内容自身的高度变化也刷新几何，折叠过渡与字体重排无需等待滚动。
    contentChanged();
    container.addEventListener('transitionend',sync);
    new MutationObserver(contentChanged).observe(container,{childList:true});
  }
  lanes.forEach(({axis,track,thumb})=>track.addEventListener('pointerdown',event=>{
    const vertical=axis==='y';
    const trackRect=track.getBoundingClientRect(),thumbRect=thumb.getBoundingClientRect();
    const travel=(vertical?trackRect.height-thumbRect.height:trackRect.width-thumbRect.width);
    const range=vertical?container.scrollHeight-container.clientHeight
      :container.scrollWidth-container.clientWidth;
    if(travel<=0||range<=0)return;
    const point=(moved:PointerEvent)=>vertical?moved.clientY:moved.clientX;
    const head=vertical?thumbRect.top:thumbRect.left,tail=vertical?thumbRect.bottom:thumbRect.right;
    // 按在滑块上就保持按住的那一点，按在轨道空白处则把滑块中心挪过来。
    const grab=point(event)>=head&&point(event)<=tail?point(event)-head
      :(vertical?thumbRect.height:thumbRect.width)/2;
    const origin=vertical?trackRect.top:trackRect.left;
    const to=(moved:PointerEvent)=>{const at=Math.max(0,Math.min(range,(point(moved)-origin-grab)/travel*range));
      if(vertical)container.scrollTop=at;else container.scrollLeft=at};
    const stop=()=>{track.classList.remove('dragging');
      track.removeEventListener('pointermove',to);track.removeEventListener('pointerup',stop);
      track.removeEventListener('pointercancel',stop)};
    track.classList.add('dragging');
    track.setPointerCapture(event.pointerId);
    track.addEventListener('pointermove',to);
    track.addEventListener('pointerup',stop);
    track.addEventListener('pointercancel',stop);
    to(event);
    event.preventDefault();
  }));
  sync();
  return sync;
}
