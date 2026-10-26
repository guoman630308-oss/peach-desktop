/* 图片灯箱（ADR-0031 第 10f 步）：数据形状、Swiper 的按需加载与缩放条的换算。
 *
 * 灯箱服务资料页的照片墙（本地图片与番号样张）和关注页的在线图集。每个调用方把自己的
 * 形状换成 `LightboxSlide` 再交进来；三边共用信息面板，动作不同：本地图可按 asset id
 * 定位源文件，样张和在线图只展示来源、序号、尺寸和可取得的大小。 */
import { fmtSize, LOC } from '@peach/legacy/core';

/** 本地图片在数据库里的那几样。定位只把 `id` 交给服务端，路径绝不进浏览器。 */
export interface LightboxAsset {
  id: number;
  name?: string;
  location?: string;
  size?: number;
  [field: string]: unknown;
}

/** 灯箱里的一张。`src` 是大图，`thumb` 是缩略图条那一格，大图取不到时也退到它。 */
export interface LightboxSlide {
  src: string;
  thumb: string;
  name?: string;
  /** 本地图片才有；有它才出「在资源管理器中显示」。 */
  asset?: LightboxAsset | null;
  /** 没有 asset 时信息面板第一段：站名或「官方样张」，缺省是「在线图片」。 */
  source?: string;
  size?: number;
}

/** 灯箱要回壳办的事。定位成功由壳发回执，失败回一句原因，写在按钮下面。 */
export interface LightboxHost {
  revealSource(id: number): Promise<string>;
}

/* ── Swiper ──
 * 大图轮播、底部缩略图条和键盘左右键都是 Swiper 自带的模块，没必要自己写一遍；但它只有
 * 看照片时才用得上，不该进首屏。只用它的核心 API，不用它的 React 封装：那一层自带一套
 * 渲染，和这里「DOM 由 React 画、Swiper 只接管位移与状态类」的分工对不上。 */

/** 用到的那一截 Swiper 实例接口。 */
export interface SwiperInstance {
  activeIndex: number;
  destroyed: boolean;
  slides: HTMLElement[];
  zoom: { in(ratio?: number): void; out(): void };
  on(event: string, handler: (swiper: SwiperInstance, ...args: number[]) => void): void;
  slideTo(index: number, speed?: number): void;
  update(): void;
  destroy(deleteInstance?: boolean, cleanStyles?: boolean): void;
}
export type SwiperConstructor = new (el: HTMLElement, options: Record<string, unknown>) => SwiperInstance;

const SWIPER = '/vendor/swiper/14.3.0/';
let loader: Promise<SwiperConstructor> | null = null;

/** 样式与脚本一起等：样式晚到一拍，所有 slide 会先当普通块元素同时画出来。 */
export const loadSwiper = (): Promise<SwiperConstructor> => loader ??= Promise.all([
  new Promise<void>((resolve, reject) => {
    const href = `${SWIPER}swiper-bundle.min.css`;
    const existing = document.querySelector<HTMLLinkElement>(`link[href="${href}"]`);
    if (existing?.sheet) { resolve(); return }
    const style = existing ?? document.createElement('link');
    style.rel = 'stylesheet';
    style.href = href;
    style.addEventListener('load', () => resolve(), { once: true });
    style.addEventListener('error', () => reject(new Error('swiper styles unavailable')), { once: true });
    if (!existing) document.head.append(style);
  }),
  new Promise<SwiperConstructor>((resolve, reject) => {
    const loaded = (window as { Swiper?: SwiperConstructor }).Swiper;
    if (loaded) { resolve(loaded); return }
    const script = document.createElement('script');
    script.src = `${SWIPER}swiper-bundle.min.js`;
    script.onload = () => resolve((window as { Swiper?: SwiperConstructor }).Swiper!);
    script.onerror = () => reject(new Error('swiper unavailable'));
    document.head.append(script);
  }),
]).then(([, Swiper]) => Swiper).catch((error: unknown) => { loader = null; throw error });

/* ── 缩放条 ──
 * 显示相对原图像素的百分比，而不是相对「适应窗口」的变换倍数。因此大图初始可能是 34%，
 * 原大小才是 100%；Swiper 14 的 zoom.in(number) 能直接接收目标倍数，既可低于 1 也可高于 1。 */
export const PHOTO_ZOOM_MIN = 10;
export const PHOTO_ZOOM_MAX = 400;
export const PHOTO_ZOOM_STEP = 10;

export interface PhotoZoom {
  /** `'fit'` 是适应窗口；数字是目标百分比，夹在上下限之间。 */
  apply(raw: number | 'fit'): void;
  /** 灯箱量过一次尺寸：按上一次的目标重算。 */
  resize(): void;
  /** 一张大图到了：是当前这张就按目标重算，适应窗口的百分比要等它的自然尺寸。 */
  loaded(img: HTMLImageElement): void;
}

/** `box` 是灯箱本身，它不在文档里了（已经关掉）就什么都不做；`show` 把百分比交给读数与滑杆。 */
export function photoZoom(box: Element, main: SwiperInstance, show: (percent: number) => void): PhotoZoom {
  let target: number | 'fit' = 'fit';
  const image = () => main.slides?.[main.activeIndex]?.querySelector('img');
  const fitPercent = () => {
    const img = image();
    if (!img?.naturalWidth || !img.naturalHeight) return 100;
    return Math.min(100, img.offsetWidth / img.naturalWidth * 100, img.offsetHeight / img.naturalHeight * 100);
  };
  const apply = (raw: number | 'fit') => {
    if (main.destroyed || !box.isConnected) return;
    const fit = fitPercent();
    const percent = raw === 'fit' ? fit : Math.min(PHOTO_ZOOM_MAX, Math.max(PHOTO_ZOOM_MIN, Number(raw) || fit));
    target = raw === 'fit' ? 'fit' : percent;
    show(Math.round(percent));
    const ratio = percent / fit;
    if (Math.abs(ratio - 1) < .001) main.zoom.out();
    else main.zoom.in(ratio);
  };
  const reset = () => requestAnimationFrame(() => apply('fit'));
  main.on('slideChange', reset);
  main.on('zoomChange', (_swiper, scale) => { if (scale) show(Math.round(fitPercent() * scale)) });
  reset();
  return {
    apply,
    resize: () => apply(target),
    loaded: (img) => { if (img === image()) apply(target) },
  };
}

/* ── 信息面板 ── */

/** 一千字节以下也写 1 KB；数和单位之间是不换行空格，窄面板里不拆成两行。 */
export const fmtPhotoSize = (raw: unknown): string => {
  const size = Number(raw) || 0;
  if (!size) return '大小未知';
  return (size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : fmtSize(size)).replace(' ', ' ');
};

export const photoTitle = (slide: LightboxSlide): string => (slide.asset?.name || slide.name) || '未命名图片';

/** 面板第二行。本地图是来源与大小；别的是来源、分辨率（大图到了才有）和大小。第几张只有底栏那一个编号。 */
export function photoMeta(slide: LightboxSlide, natural: readonly [number, number] | undefined): string {
  const asset = slide.asset;
  if (asset) return [LOC[asset.location ?? ''] || asset.location || '来源未知', fmtPhotoSize(asset.size)].join(' · ');
  const resolution = natural?.[0] && natural[1] ? `${natural[0]} × ${natural[1]}` : '';
  return [slide.source || '在线图片', resolution, slide.size ? fmtPhotoSize(slide.size) : '']
    .filter(Boolean).join(' · ');
}
