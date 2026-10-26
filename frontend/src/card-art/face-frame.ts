/* 圆头像的人脸取景：按源图里那张脸有多少像素，算出能放大多少倍。

   只挪不放大的时候，「脸太小」这件事是没法解决的：`object-fit:cover` 的缩放由框和
   图的比例定死，脸在图里占多少，在框里就占多少。实测 539 张实体图，脸框宽占框宽的
   中位数是 44%——四分之三的头像本来就是特写，放大只会把头顶和下巴切出去；真正偏小的
   只有 29 张，它们几乎都是全身站姿照，脸落在画面上半截的一小块里。

   所以放大倍数不能写死，也不能只由服务端算一份：约束是「源图里那张脸有多少像素」比
   「这个框要显示多少像素」，而后者取决于框的实际尺寸和设备像素比。同一张 640×960、
   脸只有 67 px 宽的图（`performer-8711`），在资料页 160 px 圆框上放大 2 倍就到了
   源图 1:1，再放大就是上采样；在顶栏 64 px 圆框上放到 3 倍仍有余量。判据一样，
   结论差一倍多，只有在页面这一侧、拿到框的真实尺寸时才算得出来。

   夹持顺序固定：先算「要多大才看得清、才摆得正」，用构图上限刹住，最后由「放到多大
   就开始虚」压死。`zoom` 为 1 时只挪不放大，已经够大的那 395 张走的就是这条路。

   没有一条上限是倍数：倍数答不了任何一个问题。同样 3 倍，特写被放成五官大头，远景
   全身图的脸却还只有 6 px。问的是脸放完有多大（`FACE_CEILING`）和源图还剩多少像素
   （无损上限），两条都用得着框的真实尺寸和设备像素比，所以只能在页面这一侧算。 */

/** 脸框宽占框短边这个比例就算看得清是谁，不再往上放。
 *
 *  检出给的框只覆盖眼鼻嘴，不含头发和下巴外沿；32% 时整颗头大约占七成，构图还松。
 *  往上调会让本来就偏紧的特写被这一档"救"进放大，那些图不需要。 */
export const FACE_TARGET = 0.32;

/** 脸框在框里至少要有这么多 CSS 像素宽。
 *
 *  比例这一档回答不了小圆标的问题：32% 在 120 px 的资料页框里是 38 px 的脸，在
 *  28 px 的题材圆标里只有 9 px，而 9 px 宽的眼鼻嘴认不出是谁——这排圆标存在的理由
 *  正是让人一眼认出题材。像素下限只在框小到这个比例不够用时才接管（28 px 框要 46%、
 *  48 px 框算出来低于 32% 就仍走比例那一档），大框上一个数都不动。 */
export const MIN_FACE_PX = 13;

/** 构图上限：脸框最多占框短边这么多。
 *
 *  脸框只覆盖眼鼻嘴：60% 时整颗头刚好填满框，再往上就开始切头顶和下巴。
 *
 *  这条问的是「放完之后脸有多大」，不问放大了几倍：倍数答不了构图的问题——同样
 *  3 倍，特写被放成五官大头，远景全身图的脸却还只有 6 px。
 *  实测那张 16:9 的 `Clair Obscur` 封面，脸占画面宽的 4.5%，cover 进 28 px 的圆里
 *  只剩 2.2 px——它要 5.8 倍才够看，而无损上限本来就还有 5.1 倍的余量。 */
export const FACE_CEILING = 0.6;

/** 人脸数据齐不齐。缺一样就没法算，调用方只挪不放大。
 *
 *  `faceW` 是脸框宽的**源图像素**，不是归一化值：无损上限问的就是「有多少像素可用」，
 *  归一化值除得出比例却除不出像素，换算要在拿得到原图尺寸的那一侧做。 */
export interface FaceBox { cx: number; cy: number; faceW: number; imgW: number; imgH: number }

/** 框的 CSS 像素尺寸。 */
export interface FrameSize { w: number; h: number }

export function hasFaceBox(face: Partial<FaceBox> | null | undefined): face is FaceBox {
  if (!face) return false;
  // 脸心可以是 0：脸贴着左边缘或顶边的图确实存在，那不是缺数据。
  return Number.isFinite(face.cx) && Number.isFinite(face.cy)
    && Number(face.faceW) > 0 && Number(face.imgW) > 0 && Number(face.imgH) > 0;
}

/** 放大多少倍。`frame` 是框的 CSS 像素尺寸，`dpr` 是设备像素比。
 *
 *  推着它往上的有两样：脸要够大（`wanted`），脸还要摆得正（`centred`）；
 *  压着它的有两样：脸放完不许超过构图上限，源图剩下的像素不许被上采样。 */
export function faceZoom(face: Partial<FaceBox> | null | undefined, frame: FrameSize | null | undefined, dpr = 1,
                         target = FACE_TARGET, ceiling = FACE_CEILING): number {
  if (!hasFaceBox(face) || !(frame && frame.w > 0 && frame.h > 0)) return 1;
  const ratio = dpr > 0 ? dpr : 1;
  // cover 的基础缩放：图缩到刚好盖住框，紧的那一边说话。
  const base = Math.max(frame.w / face.imgW, frame.h / face.imgH);
  const shown = face.faceW * base;
  if (!(shown > 0)) return 1;
  const side = Math.min(frame.w, frame.h);
  // 两档取宽的那一档：比例管大框，像素下限管小到比例不够用的框。
  const wanted = Math.max(target, MIN_FACE_PX / side) * side / shown;
  /* 摆正也要放大。脸心拉到框心的前提是图比框大得够多——不许露白，图不够大时脸只能
     被顶在框的边上，量出来是够大的，看着却是「小而偏」。脸离画面边缘越近，把它拉到
     框心要的放大越多，所以判据是「更近的那一边到中线的距离」，两根轴各算一次取紧的。 */
  const centred = Math.max(
    frame.w / (2 * Math.min(face.cx, 1 - face.cx) * face.imgW * base),
    frame.h / (2 * Math.min(face.cy, 1 - face.cy) * face.imgH * base));
  // 脸的源像素 ÷ 现在这个框要的设备像素。等于 1 就是已经 1:1，再放大就是上采样。
  const lossless = face.faceW / (shown * ratio);
  return Math.max(1, Math.min(
    Math.max(wanted, Math.min(centred, ceiling * side / shown)),
    lossless));
}

/** 取景结果，四个值都是相对框的百分比。
 *
 *  给百分比而不是像素：索引页大图版式的框宽跟着列宽走，视口一变就得跟着变，
 *  百分比让 CSS 自己跟随。`zoom` 按加载时的框尺寸算一次就够——框变大只会让
 *  放大倍数偏保守，不会突然越过无损上限。 */
export function faceFrame(face: Partial<FaceBox> | null | undefined, frame: FrameSize | null | undefined, dpr = 1,
                          target = FACE_TARGET, ceiling = FACE_CEILING):
  { zoom: number; width: number; height: number; left: number; top: number } | null {
  const zoom = faceZoom(face, frame, dpr, target, ceiling);
  // 倍数大于 1 就说明框量得到尺寸；这里再判一次只是给类型收窄。
  if (zoom <= 1 || !hasFaceBox(face) || !frame) return null;
  const scale = Math.max(frame.w / face.imgW, frame.h / face.imgH) * zoom;
  const width = face.imgW * scale;
  const height = face.imgH * scale;
  // 脸心对准框心，再夹回来不许露白。`zoom >= 1` 且底子是 cover，所以图一定比框大，
  // 这个区间不会是空的。
  const left = Math.min(0, Math.max(frame.w - width, frame.w / 2 - face.cx * width));
  const top = Math.min(0, Math.max(frame.h - height, frame.h / 2 - face.cy * height));
  const pct = (value: number, span: number) => Math.round(value / span * 1e4) / 100;
  return {
    zoom: Math.round(zoom * 1000) / 1000,
    width: pct(width, frame.w), height: pct(height, frame.h),
    left: pct(left, frame.w), top: pct(top, frame.h),
  };
}
