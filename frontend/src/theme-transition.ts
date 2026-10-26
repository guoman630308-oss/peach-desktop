/* 明暗主题切换的过渡：侧栏底栏那对明暗键按下时走这一份。 */
let themeChanging=false;
/** Board 的主题切换从触发器中心扩散，减少动态效果时直接应用。 */
export async function transitionTheme(button:HTMLElement,apply:()=>void):Promise<void>{
  if(themeChanging)return;
  if(!document.startViewTransition||matchMedia('(prefers-reduced-motion: reduce)').matches){apply();return}
  const rect=button.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2;
  const size=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y))*2.5;
  /* 柔边那一圈由一段 CSS 渐变画，不套 SVG：遮罩的尺寸每一帧都在变，每一帧就要照新
     尺寸把它重新光栅化一遍，而一张带高斯模糊滤镜的 SVG 每次都要连滤镜一起重跑。
     渐变没有这一层，边缘的过渡带靠色标位置给。 */
  const mask='radial-gradient(circle closest-side,#000 78%,#0006 88%,transparent)';
  const style=document.createElement('style');style.textContent=`::view-transition-old(root){animation:none}::view-transition-new(root){mix-blend-mode:normal;mask-image:${mask};mask-repeat:no-repeat;will-change:mask-position,mask-size;animation:peach-theme-reveal 560ms cubic-bezier(.16,1,.3,1) both}@keyframes peach-theme-reveal{from{mask-position:${x}px ${y}px;mask-size:0px 0px}to{mask-position:${x-size/2}px ${y-size/2}px;mask-size:${size}px ${size}px}}`;
  themeChanging=true;document.head.append(style);
  const root=document.documentElement;
  root.dataset.themeSnapshot='true';
  let applied=false;
  const applyOnce=()=>{if(!applied){applied=true;apply()}};
  try{await document.startViewTransition(applyOnce).finished}catch{applyOnce()}
  finally{delete root.dataset.themeSnapshot;style.remove();themeChanging=false}
}
