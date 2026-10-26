/* 界面音效：按钮、开关、菜单、弹层和操作回执各配一声轻响。

   声音全部由 Web Audio 当场合成，不带音频文件，也不多一个网络请求。配方取自
   dannyjpwilliams/ui-sound-design-skill 的 `references/sound-recipes.md`（MIT）：
   点击是带通滤过的噪声、开关是一段正弦扫频、成功是升一个大三度、出错是压暗的下行
   锯齿波、警告是中频的两下短脉冲、菜单是滤波噪声扫过、弹层是一记音高骤降的正弦。
   音量按这个库的口径调到
   配方给的下限附近：它们一天里要响几百次，只能是陪衬。

   全站只开一个 AudioContext，而且要等到第一声真要响时才建：浏览器在用户手势之前
   不让出声，页面一进来就建会被挂起，还多占一路系统音频。开关状态由 Application 的
   `appSettings.uiSounds` 决定，通过 `setUiSoundsEnabled` 灌进来；关着时这里连
   AudioContext 都不碰。

   随主包发出，由共享源码直接引用：开关状态只能有一份，设置面板打开的
   和菜单开合时响的是同一个。 */

type Recipe = (ctx: AudioContext, now: number) => void;

let audioContext:AudioContext|null=null;
let enabled=false;

/* 单例：`exponentialRampToValueAtTime` 不能落到 0，收尾一律 0.001，听不见但合法。
   每个配方开头读一次 `currentTime`，之后全从它推——中途再读，节点就排到了过去。 */
function context(){
  const Context=globalThis.AudioContext||(globalThis as typeof globalThis & {webkitAudioContext?: typeof AudioContext}).webkitAudioContext;
  if(!Context)return null;
  if(!audioContext)audioContext=new Context();
  /* 没有用户手势时 resume 会被拒：那一声本来就不该响，吞掉即可，别在控制台留一条未处理的拒绝。 */
  if(audioContext.state==='suspended')audioContext.resume()?.catch?.(()=>{});
  return audioContext;
}

interface ToneOptions {
  type?: OscillatorType; from: number; to?: number; duration: number; volume: number; attack?: number; start?: number;
  filter?: {type: BiquadFilterType; frequency: number; Q?: number} | null;
}
function tone(ctx:AudioContext,now:number,{type='sine',from,to=from,duration,volume,attack=0,start=0,filter=null}:ToneOptions){
  const at=now+start;
  const osc=ctx.createOscillator();
  const gain=ctx.createGain();
  osc.type=type;
  osc.frequency.setValueAtTime(from,at);
  if(to!==from)osc.frequency.exponentialRampToValueAtTime(to,at+duration);
  if(attack){
    gain.gain.setValueAtTime(0.001,at);
    gain.gain.exponentialRampToValueAtTime(volume,at+attack);
  }else gain.gain.setValueAtTime(volume,at);
  gain.gain.exponentialRampToValueAtTime(0.001,at+duration);
  let tail:AudioNode=osc;
  if(filter){
    const shape=ctx.createBiquadFilter();
    shape.type=filter.type;
    shape.frequency.value=filter.frequency;
    shape.Q.value=filter.Q??1;
    osc.connect(shape);tail=shape;
  }
  tail.connect(gain);
  gain.connect(ctx.destination);
  osc.start(at);
  osc.stop(at+duration+0.01);
}

interface NoiseOptions {
  duration: number; volume: number; filter: {frequency: number; Q: number}; sweepTo?: number | null; attack?: number;
}
/* 噪声源是一次性的：每响一声造一段新缓冲，放完把整条链拆掉。振荡器 stop 之后会
   自己断开，BufferSource 不会，滤波器和增益节点就一直挂在图上。 */
function noise(ctx:AudioContext,now:number,{duration,volume,filter,sweepTo=null,attack=0}:NoiseOptions){
  const length=Math.max(1,Math.round(ctx.sampleRate*duration));
  const buffer=ctx.createBuffer(1,length,ctx.sampleRate);
  const data=buffer.getChannelData(0);
  for(let i=0;i<length;i++)data[i]=Math.random()*2-1;
  const source=ctx.createBufferSource();
  source.buffer=buffer;
  const shape=ctx.createBiquadFilter();
  shape.type='bandpass';
  shape.Q.value=filter.Q;
  shape.frequency.setValueAtTime(filter.frequency,now);
  if(sweepTo)shape.frequency.exponentialRampToValueAtTime(sweepTo,now+duration);
  const gain=ctx.createGain();
  if(attack){
    gain.gain.setValueAtTime(0.001,now);
    gain.gain.exponentialRampToValueAtTime(volume,now+attack);
  }else gain.gain.setValueAtTime(volume,now);
  gain.gain.exponentialRampToValueAtTime(0.001,now+duration);
  source.connect(shape);
  shape.connect(gain);
  gain.connect(ctx.destination);
  source.onended=()=>{source.disconnect();shape.disconnect();gain.disconnect()};
  source.start(now);
}

/** 名字对应界面上的一类动作，不对应某个控件；同一类动作在哪一页都是同一声。 */
const RECIPES:Record<string,Recipe>={
  /* 软点击：配方的 Soft click 变体，再往低压一档音量。 */
  click:(ctx,now)=>noise(ctx,now,{duration:0.06,volume:0.12,filter:{frequency:1200,Q:1}}),
  /* 开关：升是开、降是关，配方的 Minimal toggle 变体，两头音量一致。 */
  'toggle-on':(ctx,now)=>tone(ctx,now,{from:550,to:650,duration:0.08,volume:0.15}),
  'toggle-off':(ctx,now)=>tone(ctx,now,{from:650,to:550,duration:0.08,volume:0.15}),
  /* 成功：C5 起升一个大三度，两个音之间留 80ms。 */
  success:(ctx,now)=>{
    tone(ctx,now,{from:523,duration:0.12,volume:0.18});
    tone(ctx,now,{from:523*1.25,duration:0.12,volume:0.18,start:0.2});
  },
  /* 警告：中频三角波两下短脉冲，说的是「留意一下」，不带出错那种负面。 */
  warning:(ctx,now)=>{
    tone(ctx,now,{type:'triangle',from:600,duration:0.08,volume:0.15});
    tone(ctx,now,{type:'triangle',from:600,duration:0.08,volume:0.15,start:0.16});
  },
  /* 出错：锯齿波经低通压暗后下行，配方默认参数按音量下限取。 */
  error:(ctx,now)=>tone(ctx,now,{type:'sawtooth',from:400,to:200,duration:0.25,volume:0.15,
    filter:{type:'lowpass',frequency:1500,Q:1}}),
  /* 菜单与面板进场：配方的 Quick swipe 变体。 */
  whoosh:(ctx,now)=>noise(ctx,now,{duration:0.12,volume:0.08,attack:0.036,
    filter:{frequency:1000,Q:1},sweepTo:6000}),
  /* 弹层进场：配方的 Light tap 变体。 */
  pop:(ctx,now)=>tone(ctx,now,{from:1500,to:500,duration:0.04,volume:0.15}),
};

export const UI_SOUNDS=Object.freeze(Object.keys(RECIPES));

export function setUiSoundsEnabled(on:unknown):void{enabled=on===true}
export function uiSoundsEnabled():boolean{return enabled}

/** 响一声；关着、浏览器没有 Web Audio 时什么都不做并返回 false。没有这种音效就抛错。 */
export function playUiSound(name:string):boolean{
  const recipe=RECIPES[name];
  if(!recipe)throw new Error(`没有「${name}」这种界面音效`);
  if(!enabled)return false;
  const ctx=context();
  if(!ctx)return false;
  recipe(ctx,ctx.currentTime);
  return true;
}

/* 会响的控件按角色认，不按类名：React 子树的按钮和旧壳的按钮长得不一样，但都是
   `button`。开关走 change 而不是 click：一次点击只该响一声，而且响的是开还是关要看
   点完的状态。禁用中的（`disabled`、`aria-disabled`）不响——它没有发生任何事。 */
const CLICKABLE='button,[role="button"],[role="tab"],[role="menuitem"],[role="menuitemradio"],'
  +'[role="menuitemcheckbox"],[role="option"],summary,a[href]';
const SWITCH='input[type="checkbox"][role="switch"]';

function inert(control:Element){
  return (control as HTMLButtonElement).disabled===true||control.getAttribute('aria-disabled')==='true';
}

/** 挂到 document 上：用捕获阶段，哪个页面在自己那层 stopPropagation 都拦不住这一声。 */
const soundRoots=new WeakSet<Document|Element>();
export function wireUiSounds(root:Document|Element=document):void{
  if(soundRoots.has(root))return;
  soundRoots.add(root);
  root.addEventListener('click',event=>{
    const control=(event.target as Element|null)?.closest?.(CLICKABLE);
    if(!control||inert(control))return;
    playUiSound('click');
  },{capture:true});
  root.addEventListener('change',event=>{
    const control=event.target as HTMLInputElement|null;
    if(!control?.matches?.(SWITCH)||inert(control))return;
    playUiSound(control.checked?'toggle-on':'toggle-off');
  },{capture:true});
}
