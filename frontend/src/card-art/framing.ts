/* 图片落地之后的取景：头像按人脸放大，封面按人脸与正封框取景，过小的图原尺寸摆，加载中的
 * 框铺微光。
 *
 * 这些图都是模板字符串拼出来的（`./markup.ts`），没法逐张挂监听，所以由 `installCardArt`
 * 在 document 上挂一组捕获监听和一个 MutationObserver 统一接管。监听只能有一份：这个模块
 * 由 Application 与 React 页面共享源码，启动时只安装一次。 */
import { SKELETON_REVEAL_DELAY } from '@peach/legacy/ui';

import { PANEL_ASPECT, WIDE_PANEL_ASPECT, WIDE_SLEEVE_RATIO_MIN, WIDE_SLEEVE_RATIO_MAX, panelFrame, relayoutJavImages, type JavLayout } from '../jav-artwork';
import { faceFrame } from './face-frame';
import { faceSourceScale, markClamp, nativeImageFit } from './native-image';

const devicePixels = () => window.devicePixelRatio || 1;

/* 圆框里那张图按人脸取景。放大靠改 img 自己的尺寸和偏移，不用 transform：
   `object-position` 只能在 cover 裁掉的那部分里挪，方图根本没得挪，而 transform
   缩放会连圆框的描边一起放大。元素撑到「图按 cover 缩放再乘倍数」那么大，再用负偏移
   把脸心拉到框心，圆框的 `overflow:hidden` 负责裁——和不放大时是同一套几何。 */
export function avatarFrame(img: HTMLImageElement): void {
  const ring = img.parentElement;
  if (!ring) return;
  ['position', 'right', 'bottom', 'left', 'top', 'width', 'height', 'max-width', 'max-height']
    .forEach(name => img.style.removeProperty(name));
  // 取景完才露面（`09-skeleton.css`）：本函数同步写完样式，先标上；只有等框布局那条路撤掉。
  img.dataset.faceFramed = '';
  if (ring.dataset.nativeSmall === 'true') return;
  const rect = ring.getBoundingClientRect();
  /* 图加载完时框还没布局，是真会发生的一整类情况：面板隐藏、`display:none` 的页签、
     缓存直出。那一刻框是 0×0，算出来的倍数只能是 1，而 `load` 不会再来第二次——
     放大于是静默地永不生效，页面上看不出和「这张图不需要放大」有任何区别。
     实测在资料页复现过：框已经 160×160、图也 complete，style 里却只有平移。
     等到框拿到尺寸再算一次，等不到就维持不放大。 */
  if (!(rect.width > 0 && rect.height > 0)) {
    if (typeof ResizeObserver !== 'function') return;
    // 框是 0×0 时本来也看不见；等它量到尺寸、取完景再露面。
    delete img.dataset.faceFramed;
    const watch = new ResizeObserver(() => {
      const now = ring.getBoundingClientRect();
      if (!(now.width > 0 && now.height > 0)) return;
      watch.disconnect();
      avatarFrame(img);
    });
    watch.observe(ring);
    return;
  }
  const [cx = NaN, cy = NaN, faceW = NaN, imgW = NaN, imgH = NaN] = String(img.dataset.facebox).split(' ').map(Number);
  /* 索引页取的是实体图的派生件，边车记的是原件像素：等比缩过的仍是同一张图，按比例
     换算就对得上。脸心是归一化的，不跟着缩；脸框和图的像素一起乘，`faceZoom` 里那条
     无损上限才问得到手上这张真有多少像素。比例为 0 是换成了别的图，那时退回几何居中。 */
  const scale = faceSourceScale(img.naturalWidth, img.naturalHeight, imgW, imgH);
  if (!scale) {
    img.style.objectPosition = '50% 50%';
    return;
  }
  const frame = faceFrame({ cx, cy, faceW: faceW * scale, imgW: imgW * scale, imgH: imgH * scale },
    { w: rect.width, h: rect.height }, devicePixels());
  // 放不大就一个字都不写：留下的是 CSS 里那份几何，`object-position` 照旧生效。
  if (!frame) return;
  const s = img.style;
  // `inset:0` 定了 right/bottom，和这里的 left+width 过约束；显式撤掉，不靠浏览器取舍。
  s.position = 'absolute'; s.right = 'auto'; s.bottom = 'auto';
  s.left = `${frame.left}%`; s.top = `${frame.top}%`;
  s.width = `${frame.width}%`; s.height = `${frame.height}%`;
  /* 宽常常超过框宽。React 岛的 Tailwind 预检给每张 img `max-width:100%`，会把宽夹回框宽、
     高照样放大，脸偏到左边、右侧露出底色；内联撤掉，图落在哪个容器里都不用再各补一条。 */
  s.maxWidth = 'none'; s.maxHeight = 'none';
}

/* 官方封面有三种形态，实测过：整张封套约 1.48（左侧是剧照拼贴，右侧才是正封），
   竖版正封约 0.70（本身就是正封，没有左半边可裁），16:9 官方剧照约 1.78（整幅
   都是画面，没有「正封那一块」可推）。所以取景不能写死「取右边」，得等图片加载后
   按它自己的宽高比分流——服务端没存这个比例，也不该为此再存一份。
   剧照必须自成一档：把 1.78 归进 front 就会按写死的 50% 取横向中段，人偏在一侧
   就整个被切掉，而大图容器比所有封面都竖、纵向锚点在那里根本不生效。 */
export function coverAnchor(img: HTMLImageElement): void {
  img.closest<HTMLElement>('.pic,[data-media-pic]')?.style.removeProperty('--cover-blur');
  const r = img.naturalWidth / img.naturalHeight;
  if (!r) return;
  img.classList.remove('panel');
  ['--panel-aspect', '--panel-clip', '--panel-left', '--panel-top', '--panel-height', '--cover-x', '--cover-y']
    .forEach(name => img.style.removeProperty(name));
  const code = img.dataset.coverCode || new URL(img.currentSrc || img.src, location.href).searchParams.get('code') || '';
  // FC2 封面是整幅画面，横向取景跟随人脸；宽高比不代表有 DVD 正封。
  img.dataset.frame = img.dataset.coverLayout === 'still' || /^FC2(?:-PPV)?-/i.test(code) ? 'still' : wideSleeve(img) ? 'sleeve' : r >= 1.65 ? 'still' : r > 1.2 ? 'sleeve' : 'front';
  /* `object-position` 的百分比说的是「图片上这个点对齐可见窗口的同一个百分比位置」，
     不是「这个点落到窗口正中」。所以人脸中心原样当锚点只能保证脸还在画面里：0.81
     那种偏右的脸会贴着窗口右缘，图片右边还剩一截永远露不出来。可见窗口占图片 w 时，
     让人脸落到正中的锚点是 (face - w/2) / (1 - w)。夹回 0–1 是因为脸离图片边缘不足
     半个窗口时窗口已经顶到边，再往外推只会把图片外面推进来。 */
  const car = coverRatio(img);
  const center = (name: string, face: number | null, visible: number) => {
    // 只给被裁的那个轴算。`object-fit:cover` 一次只裁一个轴，另一个轴整幅可见
    // （visible>=1），那里的 object-position 是死值，算了也不生效。
    if (face == null || !(visible > 0 && visible < 1)) return;
    const pct = Math.min(1, Math.max(0, (face - visible / 2) / (1 - visible)));
    img.style.setProperty(name, `${Math.round(pct * 100)}%`);
  };
  center('--cover-x', coverFace(img, 'cx'), car / r);
  center('--cover-y', coverFace(img, 'cy'), r / car);
  posterPanel(img, car);
  // 传统封面的整图与竖版正封保持完整，比例不同处用同图补底；整幅大封保持纯色底。
  if (img.dataset.frame !== 'still' && (img.classList.contains('whole') || img.dataset.frame === 'front')
    && Math.abs(r / car - 1) > .02) coverBackdrop(img);
}

/* 只把折痕右边那块正封摆进卡片，封底一个像素都不露。折痕位置在 `data-posterbox` 里，
   换算要的容器比例只有页面知道，两边在这里才凑齐。 */
export function posterPanel(img: HTMLImageElement, ratio: number): void {
  if (img.dataset.frame === 'front' || img.dataset.coverLayout === 'still') return;
  let [x0 = NaN, imgW = NaN, imgH = NaN, y0 = NaN, x1 = NaN, y1 = NaN] = String(img.dataset.posterbox || '').split(' ').map(Number);
  /* 没有框的双页封套按先验从右缘量回去，与服务端折痕找不到时的比例框一致。 */
  if (!img.dataset.posterbox && img.dataset.frame === 'sleeve') {
    imgW = img.naturalWidth; imgH = img.naturalHeight;
    x0 = Math.round(imgW - (wideSleeve(img) ? WIDE_PANEL_ASPECT : PANEL_ASPECT) * imgH); y0 = 0; x1 = imgW; y1 = imgH;
  }
  /* 框是按那一版源图的像素算的，而封面会被更大的那张原子替换。尺寸对不上就说明
     框描述的是另一张图，落在这张上是一块错位的区域——而错位在页面上和「本来就该
     这么取景」看不出区别，所以宁可退回回退值。 */
  // 卡片先取的是等比缩小的派生档，框的百分比在等比缩放下不变，所以认缩小，不认别的图。
  if (!faceSourceScale(img.naturalWidth, img.naturalHeight, imgW, imgH)) return;
  const panelAspect = (x1 - x0) / (y1 - y0);
  const sourceAspect = imgW / imgH;
  const widePanel = sourceAspect >= WIDE_SLEEVE_RATIO_MIN && sourceAspect < WIDE_SLEEVE_RATIO_MAX
    && Math.abs(panelAspect - WIDE_PANEL_ASPECT) <= .02;
  const frame = panelFrame({ x0, y0, x1, y1, px: [imgW, imgH] }, ratio, wideSleeve(img) || widePanel);
  if (!frame) return;
  img.style.setProperty('--panel-aspect', `${img.naturalWidth} / ${img.naturalHeight}`);
  img.classList.add('panel');
  img.style.setProperty('--panel-clip',
    `${frame.clip.top}% ${frame.clip.right}% ${frame.clip.bottom}% ${frame.clip.left}%`);
  img.style.setProperty('--panel-left', `${frame.left}%`);
  img.style.setProperty('--panel-top', `${frame.top}%`);
  img.style.setProperty('--panel-height', `${frame.height}%`);
  if (img.dataset.frame !== 'still' && img.classList.contains('front')
    && Math.abs(panelAspect / ratio - 1) > .02) coverBackdrop(img);
}

/** 传统正封的留白使用同图模糊补底，优先复用已加载的派生档。 */
function coverBackdrop(img: HTMLImageElement): void {
  const src = (img.dataset.thumbSrc || img.currentSrc || img.src).replace(/"/g, '%22');
  img.closest<HTMLElement>('.pic,[data-media-pic]')?.style.setProperty('--cover-blur', `url("${src}")`);
}

function wideSleeve(img: HTMLImageElement): boolean {
  const code = img.dataset.coverCode || new URL(img.currentSrc || img.src, location.href).searchParams.get('code') || '';
  const ratio = img.naturalWidth / img.naturalHeight;
  return !/^FC2(?:-PPV)?-/i.test(code)
    && (img.dataset.wideSleeve === '1' || /^(CWPBD|SMBD)-/i.test(code))
    && ratio >= WIDE_SLEEVE_RATIO_MIN && ratio < WIDE_SLEEVE_RATIO_MAX;
}

/* 卡片先取封面的派生档（`/cover?thumb=1`）：高清原件一张解码 38 MB，一页几十张挤爆
   解码缓存，来回滚动时滚走的被清掉、滚回来现解，那一段是空白。取景落定之后量这张图在
   屏幕上铺开多大：一个源像素要占不止一个设备像素，就是派生档不够清楚，换回原件。
   单列、大图这些真用得上像素的地方照旧是原件，多列时屏幕本来就放不下那么多像素。 */
export function upgradeCover(img: HTMLImageElement): void {
  if (!/[?&]thumb=1(&|$)/.test(img.src) || !img.naturalWidth) return;
  const { width, height } = img.getBoundingClientRect();
  const pick = getComputedStyle(img).objectFit === 'contain' ? Math.min : Math.max;
  const scale = pick(width / img.naturalWidth, height / img.naturalHeight) * devicePixels();
  if (!(scale > 1.01)) return;
  img.dataset.thumbSrc = img.src;
  img.src = img.src.replace(/[?&]thumb=1(?=&|$)/, '');
}

/* 容器比例只有 `.pic` 的 `--card-ratio` 知道：竖屏开关、JAV 大图和普通卡片各写一个
   值，在这里按 layout 重算迟早会和它分叉。自定义属性会继承，直接从图片上读；
   `aspect-ratio` 允许 `16/9` 这种写法，所以两种形式都得认。 */
export function coverRatio(img: HTMLImageElement): number {
  const parts = getComputedStyle(img).getPropertyValue('--card-ratio').trim().split('/').map(Number);
  const r = parts.length === 2 ? Number(parts[0]) / Number(parts[1]) : Number(parts[0]);
  return Number.isFinite(r) && r > 0 ? r : 16 / 9;
}

export function coverFace(img: HTMLImageElement, axis: 'cx' | 'cy'): number | null {
  const face = parseFloat(img.dataset[axis] ?? '');
  return Number.isFinite(face) ? face : null;
}

/* 封面与头像的加载态，和换头像那一格同一形态：图还在路上时框上铺一层微光，到手后
   微光淡出并糊掉（`09-skeleton.css` 的 `.imgwait`）。图是模板字符串拼进来的，逐张挂
   监听做不到，所以在插进页面时看一眼：已经 `complete` 的（缓存里直接解码的那种）
   什么都不标，页面每次重绘都不会闪一下微光。标上之后 `load` 一定会来——图已经挂在
   文档上，捕获阶段的监听收得到；取不到图的 `error` 同样收尾，不让微光盖住首字母。
   收尾后类名一并摘掉：没到门槛就到手的直接摘，淡出过的等淡出完再摘，封面上平时不留
   那层 `::after`。React 索引页的头像框（`[data-person-ring]`）与资料卡的大位、同台艺人
   （`[data-entity-portrait]`、`[data-hero-ring]`）里那张图同样由这一层拼，插进页面时照样
   被这里看见。 */
const PENDING_IMAGES = '.pic>img.poster,[data-media-art]>img,.ring>img,[data-tier-ring]>img,[data-person-ring]>img,[data-entity-portrait]>img,[data-hero-ring]>img';
const pendingSince = new WeakMap<Element, number>();

/** 插进页面时还没到手的图，在框上标 `imgwait`。 */
export function watchPendingImages(node: Element): void {
  const found = node.matches(PENDING_IMAGES) ? [node] : node.querySelectorAll(PENDING_IMAGES);
  for (const img of found) {
    if (!(img as HTMLImageElement).complete && img.parentElement) {
      img.parentElement.classList.add('imgwait'); pendingSince.set(img.parentElement, performance.now());
    }
  }
}

/** 图到手或取不到：收掉框上那层微光。等得不够久的直接摘，否则淡出完再摘。 */
export function settleImage(img: HTMLImageElement): void {
  const box = img.parentElement;
  if (!box?.classList.contains('imgwait')) return;
  if (performance.now() - (pendingSince.get(box) ?? NaN) < SKELETON_REVEAL_DELAY) { box.classList.remove('imgwait'); return; }
  box.classList.replace('imgwait', 'ui-imgdone');
  let timer: ReturnType<typeof setTimeout> | undefined;
  const drop = (event?: Event) => {
    if (event && (event.target !== box || (event as TransitionEvent).pseudoElement !== '::after')) return;
    box.removeEventListener('transitionend', drop); clearTimeout(timer); box.classList.remove('ui-imgdone');
  };
  box.addEventListener('transitionend', drop);
  // 兜底：面板藏在后台或动效归零时 `transitionend` 不会来。
  timer = setTimeout(drop, 1000);
}

/* 缓存图插进页面的第一帧就画出来了，`load` 却排在下一个任务里：不在这里先取景，那一帧
   要么藏着（`09-skeleton.css`），要么是几何居中。观察回调是微任务，赶在绘制之前。
   不限于 `PENDING_IMAGES` 那几种框：带脸框的头像散在各页，哪里的都一样要先取景。 */
export function frameCachedImages(node: Element): void {
  const framed = 'img.cover,img[data-facebox]';
  const found = node.matches(framed) ? [node] : node.querySelectorAll(framed);
  for (const el of found) {
    const img = el as HTMLImageElement;
    if (!(img.complete && img.naturalWidth)) continue;
    if (img.classList.contains('cover')) coverAnchor(img);
    else { fitNativeImage(img); avatarFrame(img); }
  }
}

/* 图比框还小时不再拉伸：原尺寸居中摆，空出来的一圈拿同一张图放大模糊补底。

   厂牌标识实测从 42 px 到 1378 px 都有。`/logo?variant=large` 已经先挑过这个厂牌
   最清晰的一份，剩下的是本来就没有大图的厂牌——把 112 px 的那张拉满 180 px 的格子
   只是把糊放大给人看，而摆在原尺寸上，它至少是清楚的。

   度量只能在 `load` 之后做：图没加载完时 `naturalWidth` 读到的是 0。换过回落图后
   `load` 会再来一次，这里读的 `currentSrc` 也就跟着是当前真正显示的那张。
   允许适度放大；明显过小的图片才按源尺寸补底。 */
export function fitNativeImage(img: HTMLImageElement): void {
  const box = img.closest<HTMLElement>('[data-fit-native]');
  if (!box || !img.naturalWidth) return;
  /* 超扁、超高的标识另走一条：短边撑到下限、长边两头裁掉（`[data-native-clamp]` 换成 cover），
     不补底——那一圈模糊放大的是一道线，只会糊成一大块。 */
  const clamp = box.dataset.fitNative === 'mark'
    ? markClamp(img.naturalWidth, img.naturalHeight, box.clientWidth, box.clientHeight) : null;
  box.dataset.nativeClamp = String(!!clamp);
  if (clamp) {
    box.dataset.nativeSmall = 'false';
    box.style.setProperty('--markw', clamp.width + 'px');
    box.style.setProperty('--markh', clamp.height + 'px');
    box.style.setProperty('--markbg', 'none');
    return;
  }
  // 版式切换会改变框的大小，每次按屏幕像素密度重新判断。
  const { small, width, height } = nativeImageFit(img.naturalWidth, img.naturalHeight, box.clientWidth, box.clientHeight, devicePixels());
  box.dataset.nativeSmall = String(small);
  box.style.setProperty('--markw', small ? width + 'px' : '100%');
  box.style.setProperty('--markh', small ? height + 'px' : '100%');
  const src = (img.currentSrc || img.src).replace(/"/g, '%22');
  box.style.setProperty('--markbg', small ? `url("${src}")` : 'none');
}

/** 已经加载完的图不会再发 `load`，容器换了尺寸就得自己重量一遍。 */
export function refitNativeImages(root?: ParentNode | null): void {
  (root || document).querySelectorAll<HTMLImageElement>('[data-fit-native] img').forEach(img => {
    fitNativeImage(img);
    if (img.dataset.facebox) avatarFrame(img);
  });
}

/** 封面格换版式：格里的番号封面原地换取景类名，已经到手的按新卡片比例重新取景，派生档
 *  不够清楚的换回原件。 */
export function relayoutCovers(root: ParentNode, layout: JavLayout): void {
  for (const img of relayoutJavImages(root, layout)) { coverAnchor(img); upgradeCover(img); }
}

let installed = false;

/** 取景与微光的那组全站监听。壳启动时调一次；重复调用不会再挂第二份。 */
export function installCardArt(): void {
  if (installed) return;
  installed = true;
  /* 封面是模板字符串拼出来的，没法逐张挂监听；内联 `onload` 属性只能调全局函数，而
     模块里的取景函数在那里取不到——页面会每张图报一次 ReferenceError，封面全部按回落
     取景。`load` 不冒泡，但捕获阶段照样收得到。 */
  document.addEventListener('load', event => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement)) return;
    settleImage(img);
    fitNativeImage(img);
    if (img.classList.contains('cover')) { coverAnchor(img); upgradeCover(img); }
    // 头像走同一条路，理由也同一个：倍数要等图和框都落地才算得出来。
    else if (img.dataset.facebox) avatarFrame(img);
  }, true);
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes)
      if (node.nodeType === Node.ELEMENT_NODE) { watchPendingImages(node as Element); frameCachedImages(node as Element); }
  }).observe(document.body, { childList: true, subtree: true });
  // 挂在 document 上，比 body 上那条兜底链先收到：图被摘掉之前框还找得到。兜底链里还有
  // 下一张时微光留着，换上的那张到手才收。
  document.addEventListener('error', event => {
    const img = event.target;
    if (img instanceof HTMLImageElement && !img.dataset.fallbacks) settleImage(img);
  }, true);
}
