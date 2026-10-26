/* 使用项目采样弹簧构造玻璃轮廓，限制在视口内；内容不做横向缩放。 */
export function searchMorphFrames(from,to,base,paddingFrom,paddingTo,easing,viewportWidth){
  const values=easing.startsWith('linear(')?easing.slice(7,-1).split(',').map(Number):[0,1];
  return values.map((progress,index)=>{
    const t=index/(values.length-1),mix=(a,b)=>a+(b-a)*progress;
    const left=Math.max(8,Math.min(viewportWidth-44,mix(from.left,to.left)));
    const width=Math.max(36,Math.min(viewportWidth-8-left,mix(from.width,to.width)));
    const height=mix(from.height,to.height)+Math.sin(Math.PI*t)*to.height*.12;
    const center=mix(from.top+from.height/2,to.top+to.height/2);
    return {offset:t,width:`${width}px`,height:`${height}px`,
      translate:`${left-base.left}px ${center-height/2-base.top}px`,
      paddingLeft:`${mix(paddingFrom,paddingTo)}px`,paddingRight:`${mix(paddingFrom,paddingTo)}px`};
  });
}
