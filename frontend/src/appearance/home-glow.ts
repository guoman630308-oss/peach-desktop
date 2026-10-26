/* 外观配色的纯数据层：光晕参数、预设、命名色板、强调色清单与存量规范化。
 * 当前设置与目标元素由 glow.ts、settings.ts 递进来，paintHomeGlow 只写目标元素。
 * @peach/appearance 直接解析到共享源码，主界面只发布一份预设表和规范化实现。
 * glowNumber 与 number-setting.ts 的 boundedPreference 都要求整数落在区间内，否则取默认值。
 * 光晕写在局部元素，避免拖动时触发整棵文档的样式重算；实测记录见 web/css/01-base.css。 */

export interface GlowSpot { color: string; alpha: number }
/** 一份光晕设置。 */
export interface HomeGlow {
  on: boolean; preset: string; strength: number; noise: number; speed: number; soften: number; size: number;
  spot1: GlowSpot; spot2: GlowSpot; spot3: GlowSpot;
}
type SpotKey = 'spot1' | 'spot2' | 'spot3';
type GlowPalette = Pick<HomeGlow, SpotKey>;
type GlowField = 'strength' | 'noise' | 'speed' | 'soften' | 'size';

/* 一档配色就是三枚光晕的颜色与不透明度；开关、强度和颗粒不属于配色，换档时不动。
   椭圆半轴和收边位置不在这里，只有 web/css/01-base.css 一份，用户不调、也不存；圆心
   根本不存在——三枚光晕由 glowdrift 那两条动画一直推着走，那是侧栏玻璃自带的漂移。
   这个形状借自 feralui.dev/gradients 的 JSON 导出（登记在 docs/HANDOFF.md），只借参数
   模型，渲染仍是那三层纯 CSS 径向渐变，不引入 Canvas。
   三枚光晕各自向 transparent 收边，落在侧栏那块玻璃上，所以每一枚都得自己亮得起来：
   压暗的那种颜色在深色侧栏上等于没有，在浅色侧栏上是一团脏。
   `ash` 是默认那一档，和 :root 上的默认值一字不差。
   每一档还带一枚搭配的强调色：换配色是换整套外观，按钮、焦点环和链接跟着走才是一副面。
   单点强调色可以把它覆盖掉，那一下只改强调色、不动光晕。 */
const HOME_GLOW_SPOTS:readonly SpotKey[]=['spot1','spot2','spot3'];
const GLOW_SPOT_LABELS:readonly string[]=['光晕一','光晕二','光晕三'];
/* 「玻璃原色」是每块玻璃自带的那两团反光，颜色由明暗主题给：深色近白、浅色一蓝一灰。
   它不走光晕那一层——两团、各自的尺寸与轨迹全在 board.css 的 `--glass-drift-a/b` 里，
   照着三枚光晕重画一遍只会得到一份形状不同的仿制品。选中这一档时光晕整层的强度算成 0，
   同时不往根上写 `--glass-tint-*`，每块玻璃于是退回自己那一档主题色。
   留着的这三枚颜色是模型里的填充，让「三枚光晕」在任何一档下都取得到合法值；这一档的
   界面不显示它们。 */
const GLASS_NATIVE_PRESET='native';
/* 默认那一档排在最前：灰雾配蓝。灰是最不挑主题的光晕，蓝是 BoardUI 自己的强调色，
   两者一起就是这个壳没被改过时的样子；暖色那几档留给人自己挑。 */
/** `[键, 名称, 三枚光晕, 搭配的强调色]`。 */
const HOME_GLOW_PRESETS:readonly (readonly [key:string,label:string,palette:GlowPalette,accent:string])[]=[
  ['ash','灰雾',{
    spot1:{color:'#8f98a4',alpha:52},spot2:{color:'#b6bcc4',alpha:38},spot3:{color:'#6f7783',alpha:50}},'blue'],
  ['amber','钨丝暖阁',{
    spot1:{color:'#e08a2f',alpha:62},spot2:{color:'#c4544a',alpha:52},spot3:{color:'#a86a52',alpha:52}},'amber'],
  ['plum','夜樱',{
    spot1:{color:'#b455a0',alpha:64},spot2:{color:'#6a4fd0',alpha:54},spot3:{color:'#7a4f96',alpha:56}},'violet'],
  ['pine','深林',{
    spot1:{color:'#3f9e57',alpha:66},spot2:{color:'#7fae4a',alpha:48},spot3:{color:'#4f9463',alpha:56}},'emerald'],
  /* 下面十二档照 feralui.dev/gradients 预设区那十二枚 chip 取色，逐档记在
     docs/reference-snapshots/feralui-gradients-measured.md。它每档四枚色标，第一枚一律是
     铺满画布的底色（十一档near-white、Midnight bloom 那档是近黑），Peach 没有底色层——
     三枚光晕各自向 transparent 收边、身后就是页面自己的面，把那一枚搬过来在深色侧栏上
     等于没有、在浅色侧栏上是一团脏。所以取的是第二、三、四枚，也就是它真正当光斑用的
     那三枚。不透明度不跟着走：那是这块玻璃自己的浓淡，统一用默认档那一组。
     档名中文自拟，按颜色取，不照抄英文名。 */
  ['iris','琉璃霞',{
    spot1:{color:'#1e50a2',alpha:62},spot2:{color:'#f09199',alpha:52},spot3:{color:'#895b8a',alpha:52}},'indigo'],
  ['opal','蛋白石',{
    spot1:{color:'#9be0e8',alpha:62},spot2:{color:'#c4b5f7',alpha:52},spot3:{color:'#f8b8d9',alpha:52}},'sky'],
  ['lagoon','潟湖',{
    spot1:{color:'#5ce3e6',alpha:62},spot2:{color:'#0f9cc2',alpha:52},spot3:{color:'#274a78',alpha:52}},'teal'],
  ['jade','翡翠',{
    spot1:{color:'#8fe3b0',alpha:62},spot2:{color:'#22c79a',alpha:52},spot3:{color:'#0b5f51',alpha:52}},'emerald'],
  ['flare','耀斑',{
    spot1:{color:'#ffc24b',alpha:62},spot2:{color:'#f4664d',alpha:52},spot3:{color:'#8a2e5e',alpha:52}},'orange'],
  ['orchid','兰紫',{
    spot1:{color:'#e794c9',alpha:62},spot2:{color:'#9678ce',alpha:52},spot3:{color:'#4a3894',alpha:52}},'violet'],
  ['peach','桃晕',{
    spot1:{color:'#ffc9a3',alpha:62},spot2:{color:'#f08a8c',alpha:52},spot3:{color:'#b65e8c',alpha:52}},'rose'],
  ['tide','电蓝',{
    spot1:{color:'#6fd8f2',alpha:62},spot2:{color:'#4c5be0',alpha:52},spot3:{color:'#2a2450',alpha:52}},'blue'],
  ['sunset','落日',{
    spot1:{color:'#ffae3f',alpha:62},spot2:{color:'#f0574d',alpha:52},spot3:{color:'#5d2a66',alpha:52}},'amber'],
  ['mint','薄荷冰',{
    spot1:{color:'#a8f0dc',alpha:62},spot2:{color:'#52cbb0',alpha:52},spot3:{color:'#147a5f',alpha:52}},'emerald'],
  ['bloom','夜昙',{
    spot1:{color:'#4c3894',alpha:62},spot2:{color:'#b387e8',alpha:52},spot3:{color:'#f6c6e2',alpha:52}},'fuchsia'],
  ['rosegold','玫瑰金',{
    spot1:{color:'#fbc9ac',alpha:62},spot2:{color:'#e79ba7',alpha:52},spot3:{color:'#8e5a74',alpha:52}},'rose'],
  [GLASS_NATIVE_PRESET,'玻璃原色',{
    spot1:{color:'#6686b8',alpha:52},spot2:{color:'#8f98a4',alpha:44},spot3:{color:'#6686b8',alpha:52}},'blue'],
];
/** 「玻璃原色」那一档：没有三枚光晕，面上漂的是每块玻璃自带的两团反光。 */
const isNativeGlass=(key:string):boolean=>key===GLASS_NATIVE_PRESET;
/* 手调过颜色之后当前档就不再是任何一个预设，侧栏那一格和面板顶上的标识要如实说这件事，
   不能继续顶着上一档的名字。 */
const HOME_GLOW_CHOICES:readonly (readonly [key:string,label:string])[]=[...HOME_GLOW_PRESETS.map(([key,label])=>[key,label] as const),['custom','自定义']];
const glowPresetName=(key:string):string=>(HOME_GLOW_CHOICES.find(([name])=>name===key)||HOME_GLOW_CHOICES[0]!)[1];
/** 一档预设的三枚光晕（深拷贝）；认不出的键给默认那一档。 */
const glowPalette=(key:string):GlowPalette=>structuredClone((HOME_GLOW_PRESETS.find(([name])=>name===key)||HOME_GLOW_PRESETS[0]!)[2]);
/* 强调色是按钮、焦点环、链接和数据那一档色，走 BoardUI 的 accent 机制：十一级色阶整组
   换掉，组件本身一个字不改（`frontend/src/react/boardui/styles/theme.css` 的 accent 段）。
   这里只存档名，十一级的实际值在 `web/board.css` 里按 `:root[data-accent=…]` 一档一条；
   把色阶抄进 JS 会得到两份必然走偏的色板，而圆球本身正是拿同一组变量画的。
   十二档是 Tailwind v4 色板里绕色轮一圈取的十二个色相，六列两行正好铺满；中性色不收，
   灰的链接和灰的焦点环在这套界面上读不出是「可点的东西」。 */
/** `[键, 名称]`，十二档强调色。 */
const ACCENTS:readonly (readonly [key:string,label:string])[]=[['red','红'],['orange','橙'],['amber','琥珀'],['lime','柠绿'],['emerald','翠绿'],['teal','青绿'],
  ['sky','天蓝'],['blue','蓝'],['indigo','靛蓝'],['violet','紫罗兰'],['fuchsia','品红'],['rose','玫红']];
const DEFAULT_ACCENT:string='blue';
const normalizeAccent=(value:unknown):string=>ACCENTS.some(([key])=>key===value)?value as string:DEFAULT_ACCENT;
/** 一档预设搭配的强调色。 */
const glowAccent=(key:string):string=>(HOME_GLOW_PRESETS.find(([name])=>name===key)||[])[3]||DEFAULT_ACCENT;
/* 挑颜色的那张色板。名字按颜色本身取，不按它被用在哪儿——同一枚颜色换到另一枚光晕上
   还是同一个名字。每一档预设用到的颜色全部落在这张表里，所以从侧栏选完预设再打开颜色
   弹层，选中环指得出当前那一格；表里缺哪一枚，那一枚就永远是「没选中」。
   前七个色系每系六档明度打底，其余是各档预设带进来的颜色，按色相归进对应色系，所以
   各系不再一样长。青绿自成一系：feralui 那几档水色预设的主色都落在这一段，它在深色玻璃
   上确实偏冷，但那是一种可以挑的冷，不是不能出现的颜色。 */
/** `[色系, 名称]`，第一项是「全部」。 */
const GLOW_SWATCH_FAMILIES:readonly (readonly [key:string,label:string])[]=[['all','全部'],['gray','灰'],['red','红'],['yellow','黄'],
  ['green','绿'],['cyan','青'],['blue','蓝'],['purple','紫'],['brown','棕']];
/** `[色系, 名称, #rrggbb]`。 */
const GLOW_SWATCHES:readonly (readonly [family:string,name:string,hex:string])[]=[
  ['gray','云灰','#d8dade'],['gray','雾灰','#b6bcc4'],['gray','石灰','#8f98a4'],
  ['gray','铁灰','#6f7783'],['gray','墨灰','#4a4e56'],['gray','深灰','#2e3138'],
  ['red','樱红','#f08a8a'],['red','珊瑚','#e26a62'],['red','砖红','#c4544a'],
  ['red','朱红','#b5322f'],['red','酒红','#8e2a2c'],['red','暗红','#6b2224'],
  ['yellow','麦黄','#f2d48a'],['yellow','琥珀','#e8b451'],['yellow','钨丝','#e08a2f'],
  ['yellow','金黄','#cf8a20'],['yellow','姜黄','#a9701c'],['yellow','栗黄','#7d5216'],
  ['green','嫩芽','#a9cf7e'],['green','叶绿','#7fae4a'],['green','草绿','#5da34f'],
  ['green','森绿','#3f9e57'],['green','苔绿','#4f9463'],['green','墨绿','#27563a'],
  ['blue','天蓝','#9fc2e8'],['blue','湖蓝','#6a9fd8'],['blue','靛蓝','#4478c0'],
  ['blue','宝蓝','#2f5ba3'],['blue','深蓝','#27467c'],['blue','夜蓝','#1c3358'],
  ['purple','丁香','#c3a7e0'],['purple','品红','#b455a0'],['purple','薰衣草','#a67fd2'],
  ['purple','葡萄','#6a4fd0'],['purple','茄紫','#7a4f96'],['purple','深紫','#432c6d'],
  ['brown','沙棕','#d6b492'],['brown','陶棕','#bd8f68'],['brown','赭棕','#a86a52'],
  ['brown','栗棕','#8c5340'],['brown','褐棕','#6d3f31'],['brown','深褐','#4e2d23'],
  ['red','霞红','#f09199'],['red','藕粉','#f8b8d9'],['red','火红','#f4664d'],
  ['red','桃红','#f08a8c'],['red','夕红','#f0574d'],['red','粉樱','#f6c6e2'],
  ['red','玫粉','#e79ba7'],
  ['yellow','阳黄','#ffc24b'],['yellow','橘黄','#ffae3f'],
  ['green','玉绿','#8fe3b0'],['green','翠绿','#22c79a'],['green','深翠','#0b5f51'],
  ['green','松绿','#147a5f'],
  ['cyan','浅青','#9be0e8'],['cyan','碧波','#5ce3e6'],['cyan','孔雀','#0f9cc2'],
  ['cyan','冰青','#6fd8f2'],['cyan','薄荷','#a8f0dc'],['cyan','碧绿','#52cbb0'],
  ['blue','琉璃','#1e50a2'],['blue','藏蓝','#274a78'],['blue','电蓝','#4c5be0'],
  ['purple','古紫','#895b8a'],['purple','紫藤','#c4b5f7'],['purple','梅紫','#8a2e5e'],
  ['purple','兰粉','#e794c9'],['purple','兰紫','#9678ce'],['purple','靛紫','#4a3894'],
  ['purple','莓紫','#b65e8c'],['purple','墨紫','#2a2450'],['purple','暮紫','#5d2a66'],
  ['purple','夜紫','#4c3894'],['purple','淡紫','#b387e8'],['purple','玫紫','#8e5a74'],
  ['brown','杏橙','#ffc9a3'],['brown','浅杏','#fbc9ac'],
  /* 这一枚是玻璃自带那团蓝色反光在浅色主题下的颜色，另一团是上面灰系的石灰。「玻璃原色」
     那一档的界面不显示颜色，但它在模型里仍有三枚合法色，同一条「预设用到的颜色都在表里」
     因此也要对它成立。 */
  ['blue','霁蓝','#6686b8'],
];
const glowNumber=(value:number,min:number,max:number,fallback:number):number=>Number.isInteger(value)&&value>=min&&value<=max?value:fallback;
/** 合法的 `#rrggbb` 转成小写原样返回，否则给 `fallback`。 */
const glowColor=(value:unknown,fallback:string):string=>/^#[0-9a-f]{6}$/i.test(String(value))?String(value).toLowerCase():fallback;
const glowRgba=(hex:string,alpha:number)=>`rgba(${[1,3,5].map(at=>parseInt(hex.slice(at,at+2),16)).join(',')},${(alpha/100).toFixed(2)})`;
/* 速度、柔化和大小都存成 0–100 那一档的整数，换算成 CSS 要的倍率在 paintHomeGlow 里
   一次算完：存倍率的话，「默认」在数据里就是 1、在界面上却要显示成中间那一格，两边迟早
   会各按各的理解走。速度是个例外，它的中位是 100，上限 300——那一档拉满是三倍快，
   而 0 就是停住。 */
const GLOW_RANGES:Record<GlowField,[min:number,max:number,fallback:number]>={strength:[0,100,100],noise:[0,100,0],speed:[0,300,100],soften:[0,100,50],size:[0,100,50]};
const DEFAULT_HOME_GLOW:HomeGlow={on:true,preset:'ash',strength:100,noise:0,speed:100,soften:50,size:50,
  ...glowPalette('ash')};
/* 光晕参数整份来自 localStorage，形态和范围都不可信：颜色写成任意字符串会让那一层
   渐变整条失效，百分比越界会把光晕糊成一整片或者缩没。逐项夹回区间、认不出就退回
   默认那一档，而不是整份丢掉——一个坏掉的数不该把用户调好的其余几档一起清空。
   只有一种情形要连颜色一起换掉：存着的档名已经不在清单里。那说明这一档被清退了，
   留在旁边的三枚颜色正是被清退的那一版，按坏值逐项夹回去只会把它原样留在页面上。
   自定义不在此列——那三枚颜色是用户自己挑的，档名认得出来，照样留着。
   已经不存在的键不必单独清理：这里只按当前模型逐项取值，重建出来的对象里没有它们。
   旧版本那套横躺在首页顶部的圆心与半轴就是这么掉的——几何现在只由 CSS 给，存过的那份
   要是跟着夹回来，老用户看到的还是半截光。 */
/** 读回存量时的规范化；给 null 得到出厂那一套。 */
function normalizeHomeGlow(raw:unknown):HomeGlow{
  const stored:any=raw&&typeof raw==='object'?raw:{};
  const known=HOME_GLOW_CHOICES.some(([key])=>key===stored.preset);
  const preset:string=known?stored.preset:'ash';
  const seed=glowPalette(preset==='custom'?'ash':preset);
  const glow={on:stored.on!==false,preset} as HomeGlow;
  for(const [field,[min,max,fallback]] of Object.entries(GLOW_RANGES) as [GlowField,[number,number,number]][])
    glow[field]=glowNumber(+stored[field],min,max,fallback);
  for(const key of HOME_GLOW_SPOTS){
    const spot=known&&stored[key]&&typeof stored[key]==='object'?stored[key]:{},fallback=seed[key];
    glow[key]={color:glowColor(spot.color,fallback.color),
      alpha:glowNumber(+spot.alpha,0,100,fallback.alpha)};
  }
  return glow;
}
/* 预设圆球那一圈：几枚颜色就等分成几段。写成函数是因为自定义档的颜色数和预设一样多，
   却要在弹层里现算一遍。 */
const glowChipFill=(colors:readonly string[]):string=>`conic-gradient(from -90deg,${colors.map((color,index)=>
  `${color} ${(index*100/colors.length).toFixed(3)}% ${((index+1)*100/colors.length).toFixed(3)}%`).join(',')})`;
/* 只写值，光晕怎么画留在 web/css/01-base.css 一份。关掉和「玻璃原色」那一档都让强度与
   颗粒归零，`.glowlayer::before` 的不透明度按那句乘法算成 0，不另设一个「关」的分支——
   否则「关着」「玻璃原色」和「强度 0」会是三条各自演化的路径。

   写之前先和上一次写进这枚元素的值比一遍。拖任意一条拉条时变的只有一个变量；不比就是
   每一步把九个都重设一遍，中间那八次全是白写。
   记账按元素分开存，多个壳各画各的那一层时不会互相把对方的值判成「已经写过」。

   三条几何参数在这里换算成倍率：柔化 0–100 落在 0.7–1.3，收边位置按它缩放；大小同理
   落在 0.5–1.5，椭圆两根半轴同乘。速度是时长的倒数——拉条上的 100 是原速，所以倍率写成
   `100/speed`；拉到 0 没有倒数可言，那一档直接把漂移那两条动画暂停，淡入那一条不跟着停，
   否则整层会停在 opacity 0 上，看起来像光晕被关掉了。 */
const glowWritten=new WeakMap<HTMLElement,Map<string,string>>();
/** 光晕那一层的变量写到 `el` 上（同值不重写）。 */
function paintHomeGlow(el:HTMLElement|null,glow:HomeGlow):void{
  if(!el)return;
  let written=glowWritten.get(el);
  if(!written){written=new Map();glowWritten.set(el,written)}
  const live=glow.on&&!isNativeGlass(glow.preset);
  const write=(name:string,value:string)=>{
    if(written.get(name)===value)return;
    written.set(name,value);el.style.setProperty(name,value);
  };
  write('--glow-strength',String(live?glow.strength/100:0));
  write('--glow-noise',String(live?glow.noise/100:0));
  write('--glow-soften',(0.7+glow.soften/100*0.6).toFixed(3));
  write('--glow-size',(0.5+glow.size/100).toFixed(3));
  write('--glow-drift-scale',glow.speed?(100/glow.speed).toFixed(3):'1');
  write('--glow-drift-play',glow.speed?'running':'paused');
  HOME_GLOW_SPOTS.forEach((key,index)=>{
    const spot=glow[key];
    write(`--glow-spot-${index+1}-color`,glowRgba(spot.color,spot.alpha));
  });
}

/* 玻璃面上那两团慢漂反光的色相。侧栏那块玻璃交给光晕层，其余每一块（搜索框、顶栏图标
   钮、筛选浮层、设置卡的分区导航、媒体库弹层、配色弹层、窄栏、选择工具条）仍旧走
   `--glass-drift-a/b`，只是颜色不再写死：两团分别取第一枚和第二枚光晕的颜色，尺寸、
   alpha 档位和那条李萨如轨迹一概不动，换的只有色相。
   写的是根元素，这一点和光晕那一层相反——那几块玻璃分散在整棵树上，没有共同的宿主。
   代价也因此照付：写一次根就是整棵树重算样式。所以这里只在点选配色、换档、开关和松开
   速度那条拉条时被调用，拖动过程中一次都不写——拖动期间只有侧栏那一层跟着动。
   跟着走的只有漂移速度。柔化和大小不跟：那两团的半轴和收边是按各个控件自己的尺寸量过的，
   一个搜索框和一整条侧栏共用一个倍率，小的那个会先糊成一整片。
   「玻璃原色」和整项关掉走同一条路：把两枚色相变量摘掉，每块玻璃退回样式表里自己那一档
   主题色。摘掉而不是写回主题色，是因为主题色有两套、跟着明暗切换，写回去的那一套会在
   切主题时僵在原地。速度不摘——原色那两团也在漂，停不停由用户那一条拉条说了算。 */
/** 其余玻璃面那两团反光的色相写到 `el`（根元素）上。 */
function paintGlassFaces(el:HTMLElement|null,glow:HomeGlow):void{
  if(!el)return;
  const native=!glow.on||isNativeGlass(glow.preset);
  el.toggleAttribute('data-glow-native',native);
  el.style.setProperty('--glow-drift-scale',glow.speed?(100/glow.speed).toFixed(3):'1');
  el.style.setProperty('--glow-drift-play',glow.speed?'running':'paused');
  if(native){el.style.removeProperty('--glass-tint-a');el.style.removeProperty('--glass-tint-b');return}
  el.style.setProperty('--glass-tint-a',glow.spot1.color);
  el.style.setProperty('--glass-tint-b',glow.spot2.color);
}

export {ACCENTS, DEFAULT_ACCENT, DEFAULT_HOME_GLOW, GLASS_NATIVE_PRESET, GLOW_SPOT_LABELS,
  GLOW_SWATCHES, GLOW_SWATCH_FAMILIES, HOME_GLOW_CHOICES, HOME_GLOW_PRESETS, HOME_GLOW_SPOTS,
  glowAccent, glowChipFill, glowColor, glowPalette, glowPresetName, isNativeGlass,
  normalizeAccent, normalizeHomeGlow, paintGlassFaces, paintHomeGlow};
