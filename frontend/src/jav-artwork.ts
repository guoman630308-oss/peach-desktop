export type JavImagePreference = 'cover' | 'thumbnail';
export type JavLayout = 'big' | 'small';

export function normalizeJavLayout(value: unknown): JavLayout {
  return ['small', 'sleeve', 'preview'].includes(String(value)) ? 'small' : 'big';
}

export function normalizeJavPreferences(settings: { javLayout?: unknown; javImage?: unknown }) {
  return {
    javLayout: normalizeJavLayout(settings.javLayout),
    javImage: normalizeJavImage(settings.javLayout === 'preview' ? 'thumbnail' : settings.javImage),
  };
}

export function normalizeJavImage(value: unknown): JavImagePreference {
  return value === 'thumbnail' ? 'thumbnail' : 'cover';
}

export function javImageKind(item: { is_jav?: boolean; code?: string; has_cover?: boolean; has_thumb?: boolean }, preference: unknown): 'cover' | 'thumbnail' | '' {
  const cover = Boolean(item.is_jav && item.code && item.has_cover);
  if (cover && (normalizeJavImage(preference) === 'cover' || !item.has_thumb)) return 'cover';
  return item.has_thumb ? 'thumbnail' : '';
}

/** 正封宽高比与 `jav_poster_crop.PANEL_ASPECT` 一致。封面框选按它锁比例；没有边车的双页封套按它从右缘量回正封
 *  宽度，贴右缘时 0.75 的卡片比正封宽，不量就会带进一条书脊。 */
export const PANEL_ASPECT = 0.704;
// 与 jav_poster_crop 的宽封套模板共用尺寸；明确的 Blu-ray 版次才启用。
export const WIDE_PANEL_ASPECT = 0.85;
export const WIDE_SLEEVE_RATIO_MIN = 1.75;
export const WIDE_SLEEVE_RATIO_MAX = 1.85;

/** 接口 `poster_box` 的形状：`x0/y0/x1/y1` 是源图像素坐标，`px` 是源图尺寸，
 *  `method` 记着这个框是沿折痕（`fold`）、按正封宽高比的先验（`ratio`），还是人在
 *  详情页自己框的（`manual`）。前两档的框永远满高贴右缘，只有 `x0` 是活的；手工框
 *  四边都可能动。不该裁、没算过、算法版本落后的封面拿到的是 null。 */
export interface PosterBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  method?: string;
  px: number[];
}

/** 正封在卡片里的摆法。四个数都是百分数：`clip` 是从图片四边各切掉多少（喂
 *  `clip-path: inset()`），`left`／`top` 是图片左上角相对卡片的偏移，`height` 是图片
 *  高度相对卡片高度。没有框时返回 null，调用方原样退回 CSS 里那份贴右缘的回退。
 *
 *  缩放由框的高度定：框那一块正好铺满卡片高度，所以图片被放到卡片高的
 *  `源图高 / 框高` 倍。算出来的那两档框本来就是满高，这一档于是恒为 100%，一个像素
 *  都不放大；只有手工框横着切一刀时才会大于 100%。
 *
 *  横向：框占卡片宽的 `框宽 / 框高 / 容器比例`。装得下就居中，两侧各留一条交给模糊
 *  背景；装不下就让框的右缘贴住卡片右缘再从左边切，因为标题、女优名和角标都压在
 *  正封右侧。 */
export function panelFrame(box: Partial<PosterBox> | null | undefined, ratio: number, contain = false): {
  clip: { top: number; right: number; bottom: number; left: number };
  left: number; top: number; height: number;
} | null {
  const width = Number(box?.px?.[0]);
  const height = Number(box?.px?.[1]);
  const x0 = Number(box?.x0);
  if (!(width > 0 && height > 0 && ratio > 0) || !Number.isFinite(x0)) return null;
  // 夹回图片里面：框落在图片外面是数据坏了，按整幅可见处理比按负宽度算下去安全。
  // 缺 `y0/y1/x1` 的按「满高到右缘」补——算出来的那两档框就是这个形状。
  const span = (value: unknown, fallback: number, limit: number) =>
    Math.min(limit, Math.max(0, Number.isFinite(Number(value)) ? Number(value) : fallback));
  const left0 = span(x0, 0, width);
  const right = Math.max(left0, span(box?.x1, width, width));
  const top0 = span(box?.y0, 0, height);
  const bottom = Math.max(top0, span(box?.y1, height, height));
  const boxWidth = right - left0;
  const boxHeight = bottom - top0;
  if (!(boxWidth > 0 && boxHeight > 0)) return null;
  const visible = boxWidth / boxHeight / ratio;
  if (!(visible > 0)) return null;
  const scale = contain ? Math.min(1, 1 / visible) : 1;
  const offset = contain ? (1 - visible * scale) / 2 - left0 / boxHeight / ratio * scale : visible <= 1
    ? (1 - visible) / 2 - left0 / boxHeight / ratio
    : 1 - right / boxHeight / ratio;
  // `|| 0` 是为了把 -0 收成 0：写进 CSS 两者一样，读出来比对时不一样。
  const percent = (value: number) => (Math.round(value * 10000) || 0) / 100;
  return {
    clip: {
      top: percent(top0 / height), right: percent(1 - right / width),
      bottom: percent(1 - bottom / height), left: percent(left0 / width),
    },
    left: percent(offset),
    top: percent((1 - scale) / 2 - top0 / boxHeight * scale),
    height: percent(height / boxHeight * scale),
  };
}

/** 原地换图，保留列表顺序、滚动位置和正在播放的媒体。 */
export function syncJavImages(root: ParentNode, preference: unknown): void {
  root.querySelectorAll<HTMLImageElement>('img[data-jav-image]').forEach(img => {
    const cover = img.dataset.javCover || '';
    const thumb = img.dataset.javThumb || '';
    const useCover = Boolean(cover && (normalizeJavImage(preference) === 'cover' || !thumb));
    const src = useCover ? cover : thumb;
    img.classList.toggle('cover', useCover);
    img.classList.toggle('whole', useCover && img.dataset.javImageLayout !== 'big');
    img.classList.toggle('front', useCover && img.dataset.javImageLayout === 'big');
    /* 取景是按上一张图算的，换图之后一律作废，由 `coverAnchor` 在新图加载完重算。
       模糊背景挂在卡片上而不是图片上，`removeAttribute('style')` 够不着它。 */
    img.classList.remove('panel');
    img.removeAttribute('style');
    (img.closest('.pic,[data-media-pic]') as HTMLElement | null)?.style.removeProperty('--cover-blur');
    if (src && img.getAttribute('src') !== src) img.src = src;
  });
}

/** 大图／小图原地换：同一张图只换取景类名，来源和已换回的原件都留着，不重新解码。
 *  上一种版式算出的取景一律作废；返回已经加载完、要按新卡片比例立刻重算取景的那几张，
 *  还没到手的等 `load` 自己算。 */
export function relayoutJavImages(root: ParentNode, layout: JavLayout): HTMLImageElement[] {
  const ready: HTMLImageElement[] = [];
  root.querySelectorAll<HTMLImageElement>('img[data-jav-image]').forEach(img => {
    if (img.dataset.javImageLayout === layout) return;
    img.dataset.javImageLayout = layout;
    if (!img.classList.contains('cover')) return;
    img.classList.toggle('whole', layout !== 'big');
    img.classList.toggle('front', layout === 'big');
    img.classList.remove('panel');
    img.removeAttribute('style');
    (img.closest('.pic,[data-media-pic]') as HTMLElement | null)?.style.removeProperty('--cover-blur');
    if (img.complete && img.naturalWidth) ready.push(img);
  });
  return ready;
}
