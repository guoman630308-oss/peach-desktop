/* 头像的人脸放大：倍数夹在「够看清」「摆得正」「不切头」「不上采样」之间。
 *
 * 期望值照 `faceZoom` 的算式逐步写，不写小数字面量：手抄一个四舍五入过的数只会把断言变成
 * 「和上次跑出来的一样」，看不出哪一条上限在说话。 */
import { describe, expect, it } from 'vitest';

import { faceFrame, faceZoom, hasFaceBox } from '../../src/card-art/face-frame';

/** 全库最小的那张脸：`performer-8711` 是 640×960 的全身站姿照，脸框只有 67 px 宽。
 *  拿它当基准，因为「放大多少」只在这一档上才会同时撞到三条上限。 */
const SMALL_FACE = { cx: 0.439, cy: 0.224, faceW: 67, imgW: 640, imgH: 960 };
/** 539 张里四分之三长这样：脸框已占框宽四成以上，一放大就切头顶。 */
const CLOSE_UP = { cx: 0.5, cy: 0.42, faceW: 280, imgW: 640, imgH: 896 };
/** 关注页题材圆标的代表图，`/work-icon` 落盘那一份：512 px 见方，脸框 170 px。
 *  `Final Fantasy` 那枚，脸在画面上方 0.211 处——半张图都在脸下面。 */
const WORK_ICON = { cx: 0.508, cy: 0.211, faceW: 170, imgW: 512, imgH: 512 };
/** `Tekken` 那枚：脸挤在右上角，两根轴都离边缘很近。 */
const WORK_ICON_CORNER = { cx: 0.889, cy: 0.134, faceW: 110, imgW: 512, imgH: 512 };

const ring = (side: number) => ({ w: side, h: side });

describe('faceZoom', () => {
  it('28 px 的圆标按像素下限要更大的占比，48 px 的作者头像仍走比例那一档', () => {
    // 两张脸都在画面正中，「摆正」那一档算出来正好是 1 倍：只剩下限和比例在竞争。
    const icon = { ...WORK_ICON, cx: 0.5, cy: 0.5 };
    const mid = { cx: 0.5, cy: 0.5, faceW: 187, imgW: 640, imgH: 960 };
    expect(faceZoom(icon, ring(28), 2)).toBeCloseTo((13 / 28) * 28 / (170 * Math.max(28 / 512, 28 / 512)), 10);
    expect(faceZoom(mid, ring(48), 2)).toBeCloseTo(0.32 * 48 / (187 * Math.max(48 / 640, 48 / 960)), 10);
  });

  it('脸偏在一侧时放大到够得着框心，再由构图上限刹住', () => {
    const ceiling = (face: typeof WORK_ICON) => 0.6 * 28 / (face.faceW * Math.max(28 / 512, 28 / 512));
    expect(faceZoom(WORK_ICON, ring(28), 2)).toBeCloseTo(ceiling(WORK_ICON), 10);
    // 两根轴都靠边时取更紧的那根。这一枚要 4.5 倍才摆得正，构图上限先到。
    expect(faceZoom(WORK_ICON_CORNER, ring(28), 2)).toBeCloseTo(ceiling(WORK_ICON_CORNER), 10);
    // 已经在正中的特写一个像素都不动。
    expect(faceZoom(CLOSE_UP, ring(160), 2)).toBe(1);
  });

  it('先撞到哪条上限就由哪条说话', () => {
    const wanted = (side: number) => 0.32 * side / (67 * Math.max(side / 640, side / 960));
    // 资料页 160 px 圆框、2 倍屏：无损上限先到，脸 67 px 正好还得起 2 倍。
    expect(faceZoom(SMALL_FACE, ring(160), 2)).toBeCloseTo(2, 10);
    // 同一张图换到顶栏 64 px 圆框：无损上限升到 5 倍，改由目标占比说话。
    expect(faceZoom(SMALL_FACE, ring(64), 2)).toBeCloseTo(wanted(64), 10);
    // 1 倍屏要的设备像素少一半，无损上限翻倍到 4，仍由目标占比压住。
    expect(faceZoom(SMALL_FACE, ring(160), 1)).toBeCloseTo(wanted(160), 10);
    expect(faceZoom(CLOSE_UP, ring(160), 2)).toBe(1);
    expect(faceFrame(CLOSE_UP, ring(160), 2)).toBeNull();
  });

  it('远景全身图放到五倍开外，压住它的只有无损上限', () => {
    // `Clair Obscur: Expedition 33` 那张 16:9 封面，脸占画面宽的 4.5%。
    const far = { cx: 0.532, cy: 0.196, faceW: 0.045 * 512, imgW: 512, imgH: 288 };
    const lossless = far.faceW / (far.faceW * Math.max(28 / 512, 28 / 288) * 2);
    expect(faceZoom(far, ring(28), 2)).toBeCloseTo(lossless, 10);
  });
});

describe('faceFrame', () => {
  it('放大后的图盖满框，脸心拉向框心', () => {
    // 脸在画面上方 0.181 处：放大到脸心刚好够得着框心，`top` 正正好是 0。
    expect(faceFrame({ cx: 0.736, cy: 0.181, faceW: 234, imgW: 2184, imgH: 1468 }, ring(160), 2))
      .toEqual({ zoom: 2.762, width: 410.98, height: 276.24, left: -252.48, top: 0 });
    expect(faceFrame(SMALL_FACE, ring(160), 2))
      .toEqual({ zoom: 2, width: 200, height: 300, left: -37.8, top: -17.2 });
    // 方图放大之后比框大，纵向终于挪得动。
    expect(faceFrame({ cx: 0.5, cy: 0.3, faceW: 150, imgW: 1000, imgH: 1000 }, ring(160), 2))
      .toEqual({ zoom: 2.133, width: 213.33, height: 213.33, left: -56.67, top: -14 });
    // 3:4 的框：cover 的基础缩放按更紧的那一边算，两个轴各自夹持。
    expect(faceFrame(SMALL_FACE, { w: 150, h: 200 }, 2))
      .toEqual({ zoom: 2.133, width: 213.33, height: 240, left: -43.65, top: -3.76 });
  });
});

describe('缺数据时退回只挪不放大', () => {
  const noPixels = { cx: 0.4, cy: 0.2 };
  it('缺脸框像素、宽为 0 或整个缺席都不算有脸框；脸心为 0 是真实构图', () => {
    expect(hasFaceBox(SMALL_FACE)).toBe(true);
    expect(hasFaceBox(noPixels)).toBe(false);
    expect(hasFaceBox({ cx: 0.4, cy: 0.2, faceW: 0, imgW: 640, imgH: 960 })).toBe(false);
    expect(hasFaceBox(null)).toBe(false);
    expect(hasFaceBox({ cx: 0, cy: 0, faceW: 67, imgW: 640, imgH: 960 })).toBe(true);
  });

  it('算不出倍数时是 1 倍、没有取景结果', () => {
    expect(faceZoom(noPixels, ring(160), 2)).toBe(1);
    expect(faceFrame(noPixels, ring(160), 2)).toBeNull();
    // 框还没布局（骨架屏、display:none）时宽高是 0。
    expect(faceZoom(SMALL_FACE, { w: 0, h: 0 }, 2)).toBe(1);
  });
});
