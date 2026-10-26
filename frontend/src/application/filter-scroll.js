/** 窄屏滚动方向累计到 8px 才切换吸顶，顶端与控件交互保持显示。 */
export function filterScrollState(previous,y,narrow,hold=false){
  y=Math.max(0,y);
  if(!previous||!narrow||hold||y<=8)return {y,origin:y,direction:0,free:false};
  const delta=y-previous.y;
  if(!delta)return previous;
  const direction=Math.sign(delta),origin=direction===previous.direction?previous.origin:previous.y;
  const free=Math.abs(y-origin)>=8?direction>0:previous.free;
  return {y,origin,direction,free};
}
