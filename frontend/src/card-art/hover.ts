/* 卡片悬停预览：只有本地文件拉真视频。115 / PikPak 等远端源只扫本地接触印相，避免页面移除后
 * 继续下载或填满缓存。
 *
 * 卡片有两种：壳拼的 `.card`，和 React 网格里的 `[data-media-card]`／`[data-mix-card]`。
 * 悬停态写在卡上的 `data-previewing`／`data-longhover`：卡的类名归 React 管，壳不往上加。
 *
 * 要不要起预览，判据在壳里（多选、遮挡、悬停延时设置），由 `configureHoverPreview` 以取值函数
 * 交进来：延时在计时器里还要再读一次，设置中途改了，下一拍读到的就是新值，所以不能抄一份
 * 快照。没配置时按「不拦、不延时」处理。 */

import { loadLocalPlayerPolicy, prefersExternalPlayer } from '../local-player';

/** 起预览前要问壳的三件事。都是取值函数，每次用时现读。 */
export interface HoverPreviewConfig {
  /** 多选模式开着：这时不起预览。 */
  selecting(): boolean;
  /** 遮挡开着：这时不起预览，已经排上的那一段到点也不放。 */
  censored(): boolean;
  /** 停够多少秒进入「长悬停」（倒计时走完、三颗快退快进出现）；0 是关掉。 */
  delaySeconds(): number;
}

/** 卡片上那几个要用到的字段。 */
export interface HoverItem { id: number; location?: string; has_thumb?: boolean }

type HoverCard = HTMLElement & { _stopHover?: () => void };
type HoverVideo = HTMLVideoElement & { _hop?: ReturnType<typeof setInterval> };

const HOVER_CARDS = '.card,[data-media-card],[data-mix-card]';

let config: HoverPreviewConfig = { selecting: () => false, censored: () => false, delaySeconds: () => 0 };

export function configureHoverPreview(next: HoverPreviewConfig): void {
  config = next;
}

const scrolling = () => !!(window as Window & { __scrolling?: unknown }).__scrolling;

/** 收掉 `root` 下所有悬停预览，`except` 那张卡留着。 */
export function releaseHoverPreviews(root: ParentNode | null = document, except: Element | null = null): void {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll<HoverCard>(HOVER_CARDS).forEach(card => {
    if (card !== except && card._stopHover) card._stopHover();
  });
  root.querySelectorAll<HoverVideo>('video.hv').forEach(v => {
    if (v.closest(HOVER_CARDS) === except) return;
    if (v._hop) clearInterval(v._hop); v.pause(); v.removeAttribute('src'); v.load(); v.remove();
  });
  // 远端源那一层和视频同样要兜一遍：卡片被重画过的话，旧元素上的 `_stopHover`
  // 已经跟着旧 DOM 走了，只靠上面那轮回调收不到它留在画面上的扫视图。
  root.querySelectorAll('img.ui-hvframes').forEach(im => {
    if (im.closest(HOVER_CARDS) === except) return;
    im.removeAttribute('src'); im.remove();
  });
}

/** 一张卡要拆下来之前：它自己正在放的那一段和它里面所有预览一起收掉。 */
export function releaseHover(card: HTMLElement): void {
  (card as HoverCard)._stopHover?.();
  releaseHoverPreviews(card);
}

export function setHoverState(el: HTMLElement, name: 'previewing' | 'longhover', on: boolean): void {
  if (on) el.dataset[name] = ''; else delete el.dataset[name];
}

/** 给一张卡挂上悬停预览。画面格是卡里的 `[data-media-pic]`，没有就不挂。 */
export function wireHover(el: HTMLElement, it: HoverItem): void {
  const card = el as HoverCard;
  const pic = el.querySelector('[data-media-pic]'); if (!pic) return;
  el.dataset.hoverMode = it.location === 'local' ? 'video' : 'frames';
  let longTimer: ReturnType<typeof setTimeout> | undefined;
  const armLong = () => {
    clearTimeout(longTimer); if (!config.delaySeconds()) return; setHoverState(el, 'previewing', true);
    longTimer = setTimeout(() => { if (config.delaySeconds()) setHoverState(el, 'longhover', true); }, config.delaySeconds() * 1000);
  };
  const clearLong = () => { clearTimeout(longTimer); setHoverState(el, 'previewing', false); setHoverState(el, 'longhover', false); };
  if (it.location !== 'local') {        // 远端源：只在接触印相的格子间扫视，零网络流量
    /* 扫视图是叠在画面之上新建的一层，不改任何已有 `<img>` 的 src。JAV 大图和小图
       版式里画面就是封面本身（`.poster.cover`），改它的 src 等于把封面当场换掉；
       按类名把封面排掉又等于这两种版式整个没有悬停预览，连 `data-longhover` 都不进，
       快退快进那三颗也跟着永远不出现。叠一层对三种版式是同一条路。
       这一层用 contain 加黑底：大图版式的容器是 0.75 的竖比例，16:9 的接触印相格子
       在里面居中、上下留黑，和本地视频的 `.hv` 同一个口径。 */
    if (!it.has_thumb) return;        // 没有接触印相就没有可扫的格子
    let t: ReturnType<typeof setInterval> | undefined, i = 4, layer: HTMLImageElement | null = null, loading = false;
    el.addEventListener('mouseenter', () => {
      if (config.selecting() || config.censored()) return; armLong();
      if (!layer) {
        layer = document.createElement('img');
        layer.className = 'ui-hvframes'; layer.alt = '';
        layer.src = `/poster?id=${it.id}&c=${i}`;
        pic.appendChild(layer);
      }
      clearInterval(t);
      t = setInterval(() => {
        if (!layer || loading) return;
        const next = (i + 1) % 9, pre = new Image(); loading = true;
        pre.onload = () => { if (layer) { layer.src = pre.src; i = next; } loading = false; };
        pre.onerror = () => { loading = false; };
        pre.src = `/poster?id=${it.id}&c=${next}`;
      }, 430);
    });
    const stop = () => {
      clearLong(); clearInterval(t); t = undefined;
      if (layer) { layer.remove(); layer = null; } i = 4;
    };
    card._stopHover = stop; el.addEventListener('mouseleave', stop);
    return;
  }
  let timer: ReturnType<typeof setTimeout> | undefined, v: HoverVideo | null = null, hoverEpoch = 0;
  el.addEventListener('mouseenter', () => {
    if (config.selecting() || config.censored() || scrolling() || prefersExternalPlayer()) return;
    const epoch = ++hoverEpoch;
    timer = setTimeout(async () => {
      // 首次悬停也先读播放器设置，避免尚未打开详情时触发整片转码。
      try { await loadLocalPlayerPolicy() } catch { return }
      if (epoch !== hoverEpoch || !el.isConnected || scrolling() || config.selecting()
        || config.censored() || prefersExternalPlayer()) return;
      releaseHoverPreviews(document, el);   // 同一时间只保留一个本地视频预览
      const video = document.createElement('video') as HoverVideo;
      v = video;
      video.className = 'hv'; video.muted = true; video.playsInline = true; video.loop = true; video.preload = 'metadata';
      video.src = '/stream?id=' + it.id;
      // 分段跳跃：每段放 1.4 秒就跳到下一段，扫完全片，而不是从一个点连续播
      const SEG = [0.08, 0.22, 0.36, 0.50, 0.64, 0.78, 0.90]; let si = 0;
      const seek = () => { try { video.currentTime = (video.duration || 0) * (SEG[si] ?? 0); } catch { /* 元数据还没到 */ } };
      video.addEventListener('loadedmetadata', () => {
        seek(); video.classList.add('on'); video.dataset.playing = ''; armLong();
        video._hop = setInterval(() => { si = (si + 1) % SEG.length; seek(); }, 1400);
      }, { once: true });
      pic.appendChild(video); video.play().catch(() => {});
    }, 340);                         // 340ms 防抖，鼠标划过不触发
  });
  const stop = () => {
    hoverEpoch += 1;
    clearLong();
    clearTimeout(timer); timer = undefined;
    if (v) { if (v._hop) clearInterval(v._hop); v.pause(); v.removeAttribute('src'); v.load(); v.remove(); v = null; }
  };
  card._stopHover = stop; el.addEventListener('mouseleave', stop);
}
