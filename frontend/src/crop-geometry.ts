/* 框选那一块在源图里的坐标。纯算术，不碰 DOM，也不认识 React。
 *
 * 一切都用**源图像素**表示，右下开区间——落盘的框、发给后端的框、`poster_box`
 * 那个字段都是这一套（`src/peach/jav_poster_crop.py` 的 `projection` 文档串）。
 * 屏幕上量到的是显示像素，换算只在进出这一层时各做一次：两边各按各的坐标算下去，
 * 框会在缩放比不是整数时差出一两个像素，而那一两个像素在屏幕上看不出来，只在
 * 事后对不上账。
 */

export interface CropSize {
  width: number;
  height: number;
}

export interface CropBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** 框的最小边长，源图像素。再小就不是在框一块内容，而是在框噪点。 */
export const MIN_CROP_EDGE = 16;

function whole(size: CropSize): CropBox {
  return { x0: 0, y0: 0, x1: size.width, y1: size.height };
}

function round(box: CropBox): CropBox {
  return {
    x0: Math.round(box.x0), y0: Math.round(box.y0),
    x1: Math.round(box.x1), y1: Math.round(box.y1),
  };
}

export function isUsableSize(size: CropSize | null | undefined): size is CropSize {
  return Boolean(size) && Number.isFinite(size!.width) && Number.isFinite(size!.height)
    && size!.width > 0 && size!.height > 0;
}

/** 装得下的最大的那个框，按 `aspect`（宽/高）居中；`aspect` 为 null 时就是整张图。
 *
 *  正封那一路的默认框另有讲究，见 `defaultPanelBox`。 */
export function centeredBox(size: CropSize, aspect: number | null): CropBox {
  if (!isUsableSize(size)) return { x0: 0, y0: 0, x1: 0, y1: 0 };
  if (aspect == null || !(aspect > 0)) return whole(size);
  let width = size.width;
  let height = width / aspect;
  if (height > size.height) {
    height = size.height;
    width = height * aspect;
  }
  const left = (size.width - width) / 2;
  const top = (size.height - height) / 2;
  return round({ x0: left, y0: top, x1: left + width, y1: top + height });
}

/** 正封的默认框：满高、按比例取宽、贴右缘。
 *
 *  横版封套是「背面 | 书脊 | 正面」，正面在右侧，所以默认框就是「从右缘量回去
 *  一个正封那么宽」。人要改的多半只是折痕落在哪一列，默认框先把另外三条边摆对，
 *  少三次拖拽。装不下就退成整张图。 */
export function defaultPanelBox(size: CropSize, aspect: number): CropBox {
  if (!isUsableSize(size) || !(aspect > 0)) return whole(size);
  const width = Math.min(size.width, size.height * aspect);
  return round({ x0: size.width - width, y0: 0, x1: size.width, y1: size.height });
}

/** `focus` 里按 `aspect` 装得下的最大的框，中心对齐 `focus` 的中心。
 *
 *  `focus` 是后端给的「该取景的那一块」（脸周围的方图或正封），形状不一定是
 *  `aspect`。取装得下的而不是围得住的：围住竖长的正封要带进书脊，围住脸那块方图
 *  的 3:4 格子会带进一截胸口，而这一块本来就是按「装进头像刚好」量出来的。 */
export function frameWithin(focus: CropBox, size: CropSize, aspect: number): CropBox {
  if (!isUsableSize(size) || !(aspect > 0)) return whole(size);
  const inside = clampBox(focus, size);
  const box = centeredBox({ width: inside.x1 - inside.x0, height: inside.y1 - inside.y0 }, aspect);
  return {
    x0: inside.x0 + box.x0, y0: inside.y0 + box.y0,
    x1: inside.x0 + box.x1, y1: inside.y0 + box.y1,
  };
}

/** 把框收进源图里，并保证它还算一个框。
 *
 *  `aspect` 给了就锁比例：先按 `anchor` 那一边定尺寸，装不下再换另一边定，仍然
 *  装不下就贴着图的边缘缩。四个角都夹过之后，宽高都至少 `MIN_CROP_EDGE`。 */
export function clampBox(box: CropBox, size: CropSize, aspect: number | null = null,
                         anchor: 'width' | 'height' = 'width'): CropBox {
  if (!isUsableSize(size)) return { x0: 0, y0: 0, x1: 0, y1: 0 };
  const floor = Math.min(MIN_CROP_EDGE, size.width, size.height);
  let width = Math.max(floor, Math.min(box.x1 - box.x0, size.width));
  let height = Math.max(floor, Math.min(box.y1 - box.y0, size.height));
  if (aspect != null && aspect > 0) {
    if (anchor === 'width') {
      height = width / aspect;
      if (height > size.height) { height = size.height; width = height * aspect; }
    } else {
      width = height * aspect;
      if (width > size.width) { width = size.width; height = width / aspect; }
    }
    if (width > size.width) { width = size.width; height = width / aspect; }
  }
  const left = Math.max(0, Math.min(box.x0, size.width - width));
  const top = Math.max(0, Math.min(box.y0, size.height - height));
  return round({ x0: left, y0: top, x1: left + width, y1: top + height });
}

/** 整个框平移 `(dx, dy)` 源图像素，尺寸不变，碰到边就停。 */
export function moveBox(box: CropBox, dx: number, dy: number, size: CropSize): CropBox {
  const width = box.x1 - box.x0;
  const height = box.y1 - box.y0;
  const left = Math.max(0, Math.min(box.x0 + dx, size.width - width));
  const top = Math.max(0, Math.min(box.y0 + dy, size.height - height));
  return round({ x0: left, y0: top, x1: left + width, y1: top + height });
}

/** 以框心为中心缩放。`factor` 大于 1 是放大框（看到的内容更多）。 */
export function scaleBox(box: CropBox, factor: number, size: CropSize,
                         aspect: number | null = null): CropBox {
  if (!isUsableSize(size) || !(factor > 0)) return box;
  const cx = (box.x0 + box.x1) / 2;
  const cy = (box.y0 + box.y1) / 2;
  const width = (box.x1 - box.x0) * factor;
  const height = (box.y1 - box.y0) * factor;
  return clampBox({
    x0: cx - width / 2, y0: cy - height / 2,
    x1: cx + width / 2, y1: cy + height / 2,
  }, size, aspect);
}

/** 拖右下角：左上角钉住不动，右下角跟着指针走。 */
export function resizeFromCorner(box: CropBox, x: number, y: number, size: CropSize,
                                 aspect: number | null = null): CropBox {
  const next = { x0: box.x0, y0: box.y0, x1: Math.max(box.x0 + 1, x), y1: Math.max(box.y0 + 1, y) };
  const anchor: 'width' | 'height' = (next.x1 - next.x0) / Math.max(1, next.y1 - next.y0)
    > (aspect ?? 1) ? 'height' : 'width';
  return clampBox(next, size, aspect, anchor);
}

/** 源图像素框 → 覆盖层的百分比位置。显示尺寸不参与：百分比是相对图片本身的。 */
export function boxPercent(box: CropBox, size: CropSize): {
  left: number; top: number; width: number; height: number;
} {
  if (!isUsableSize(size)) return { left: 0, top: 0, width: 0, height: 0 };
  return {
    left: (box.x0 / size.width) * 100,
    top: (box.y0 / size.height) * 100,
    width: ((box.x1 - box.x0) / size.width) * 100,
    height: ((box.y1 - box.y0) / size.height) * 100,
  };
}

/** 框里那一块单独摆满一个容器时的 `background-size` / `background-position`。
 *
 *  预览不重新取一次图：同一个地址换成背景图，浏览器用的是同一份缓存字节。
 *  `background-position` 的百分比量的是「图比容器多出来的那部分」，所以框正好等于
 *  整张图时分母是 0，那时位置取 0 —— 图和容器一样大，摆哪里都一样。 */
export function previewStyle(box: CropBox, size: CropSize): {
  size: string; position: string;
} {
  if (!isUsableSize(size)) return { size: 'cover', position: '50% 50%' };
  const width = Math.max(1, box.x1 - box.x0);
  const height = Math.max(1, box.y1 - box.y0);
  const spare = (offset: number, whole: number, part: number) =>
    whole - part > 0 ? (offset / (whole - part)) * 100 : 0;
  return {
    size: `${(size.width / width) * 100}% ${(size.height / height) * 100}%`,
    position: `${spare(box.x0, size.width, width)}% ${spare(box.y0, size.height, height)}%`,
  };
}

/** 框里那一块摆满一个同比例容器时，整张 `<img>` 按绝对定位该放在哪、放多大。
 *
 *  和 `previewStyle` 是同一件事，给的是图片元素而不是背景：图片元素才有 `load`
 *  事件可接，淡入要等它。`left`／`width` 的百分比量的是容器宽，`top`／`height`
 *  量的是容器高，容器与框同比例，所以两个轴各按各的边长换算。 */
export function windowStyle(box: CropBox, size: CropSize): {
  left: string; top: string; width: string; height: string;
} {
  const width = Math.max(1, box.x1 - box.x0);
  const height = Math.max(1, box.y1 - box.y0);
  return {
    left: `${(-box.x0 / width) * 100}%`,
    top: `${(-box.y0 / height) * 100}%`,
    width: `${(size.width / width) * 100}%`,
    height: `${(size.height / height) * 100}%`,
  };
}

/** 显示像素 → 源图像素。`rendered` 是 `<img>` 当前占的 CSS 像素尺寸。 */
export function toNatural(value: number, rendered: number, natural: number): number {
  if (!(rendered > 0)) return 0;
  return (value * natural) / rendered;
}

/** 框在屏幕上的短边小于这么多 CSS 像素时，改大小那枚角柄挪到框外。角柄 12px，框只比它
 *  大一点时整块都被角柄盖住，按下去只能改大小、拖不动框。 */
export const HANDLE_ROOM = 36;

/** 角柄该不该放到框外：`rect` 是 `boxPercent` 的百分比，`shown` 是图当前的显示尺寸。
 *  显示尺寸还没量到时不挪。 */
export function handleOutside(
  rect: { width: number; height: number }, shown: CropSize | null,
): boolean {
  if (!shown || !(shown.width > 0) || !(shown.height > 0)) return false;
  return Math.min((rect.width / 100) * shown.width, (rect.height / 100) * shown.height) < HANDLE_ROOM;
}
