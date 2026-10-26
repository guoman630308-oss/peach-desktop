import { describe, expect, it } from 'vitest';

import {
  boxPercent, centeredBox, clampBox, defaultPanelBox, frameWithin, handleOutside, HANDLE_ROOM, MIN_CROP_EDGE, moveBox,
  previewStyle, resizeFromCorner, scaleBox, toNatural, windowStyle,
} from '../src/crop-geometry';

/** 本机封套最常见的尺寸。 */
const SLEEVE = { width: 800, height: 538 };
/** 正封的宽高比，与 `src/peach/jav_poster_crop.py` 的 `PANEL_ASPECT` 同一个数。 */
const PANEL_ASPECT = 0.704;

describe('默认框', () => {
  it('方框按短边居中，头像框的两条边一样长', () => {
    expect(centeredBox(SLEEVE, 1)).toEqual({ x0: 131, y0: 0, x1: 669, y1: 538 });
  });

  it('不锁比例时默认框就是整张图', () => {
    expect(centeredBox(SLEEVE, null)).toEqual({ x0: 0, y0: 0, x1: 800, y1: 538 });
  });

  it('正封的默认框满高贴右缘，人只要挪折痕那一条边', () => {
    // 538×0.704≈379，从右缘 800 量回去就是 421——和算法在没找到折痕时给的数一致。
    expect(defaultPanelBox(SLEEVE, PANEL_ASPECT)).toEqual({ x0: 421, y0: 0, x1: 800, y1: 538 });
  });

  it('按比例取的宽超过图宽时退成整张图', () => {
    expect(defaultPanelBox({ width: 300, height: 538 }, PANEL_ASPECT))
      .toEqual({ x0: 0, y0: 0, x1: 300, y1: 538 });
  });

  it('尺寸还没量出来时不硬算', () => {
    expect(centeredBox({ width: 0, height: 0 }, 1)).toEqual({ x0: 0, y0: 0, x1: 0, y1: 0 });
  });
});

describe('围着取景区取景', () => {
  it('脸那块方图里取方框就是它自己，取 3:4 是削掉两侧、上下不动', () => {
    const face = { x0: 592, y0: 114, x1: 688, y1: 210 };
    expect(frameWithin(face, SLEEVE, 1)).toEqual(face);
    expect(frameWithin(face, SLEEVE, 3 / 4)).toEqual({ x0: 604, y0: 114, x1: 676, y1: 210 });
  });

  it('竖长的正封里取方框按它的宽，居中不带进书脊', () => {
    const panel = { x0: 421, y0: 0, x1: 800, y1: 538 };
    expect(frameWithin(panel, SLEEVE, 1)).toEqual({ x0: 421, y0: 80, x1: 800, y1: 459 });
  });

  it('越出图外的取景区先收回图里', () => {
    expect(frameWithin({ x0: 700, y0: -40, x1: 900, y1: 160 }, SLEEVE, 1))
      .toEqual({ x0: 600, y0: 0, x1: 800, y1: 200 });
  });

  it('整张图按绝对定位摆，让框里那一块正好摆满同比例的格子', () => {
    expect(windowStyle({ x0: 200, y0: 100, x1: 400, y1: 300 }, { width: 800, height: 600 })).toEqual({
      left: '-100%', top: '-50%', width: '400%', height: '300%',
    });
  });
});

describe('框始终收在图里', () => {
  it('越界的框贴着边缘停下，尺寸不缩水', () => {
    expect(clampBox({ x0: 700, y0: 400, x1: 900, y1: 600 }, SLEEVE))
      .toEqual({ x0: 600, y0: 338, x1: 800, y1: 538 });
  });

  it('比最小边长还小的框被撑回最小边长', () => {
    const box = clampBox({ x0: 10, y0: 10, x1: 12, y1: 12 }, SLEEVE);
    expect(box.x1 - box.x0).toBe(MIN_CROP_EDGE);
    expect(box.y1 - box.y0).toBe(MIN_CROP_EDGE);
  });

  it('锁了比例就按比例补另一条边，装不下时换一条边定尺寸', () => {
    expect(clampBox({ x0: 0, y0: 0, x1: 400, y1: 100 }, SLEEVE, 1))
      .toEqual({ x0: 0, y0: 0, x1: 400, y1: 400 });
    // 宽 700 的方框在 538 高的图里放不下，改由高定尺寸。
    expect(clampBox({ x0: 0, y0: 0, x1: 700, y1: 100 }, SLEEVE, 1))
      .toEqual({ x0: 0, y0: 0, x1: 538, y1: 538 });
  });
});

describe('拖动、缩放与拖角', () => {
  it('平移不改尺寸，碰到边就停', () => {
    const box = { x0: 100, y0: 100, x1: 300, y1: 300 };
    expect(moveBox(box, 50, -40, SLEEVE)).toEqual({ x0: 150, y0: 60, x1: 350, y1: 260 });
    expect(moveBox(box, 9999, 9999, SLEEVE)).toEqual({ x0: 600, y0: 338, x1: 800, y1: 538 });
  });

  it('滚轮以框心为中心缩放', () => {
    expect(scaleBox({ x0: 300, y0: 200, x1: 500, y1: 400 }, 1.5, SLEEVE))
      .toEqual({ x0: 250, y0: 150, x1: 550, y1: 450 });
  });

  it('拖右下角时左上角钉住不动', () => {
    expect(resizeFromCorner({ x0: 100, y0: 100, x1: 200, y1: 200 }, 460, 380, SLEEVE))
      .toEqual({ x0: 100, y0: 100, x1: 460, y1: 380 });
    // 锁了比例的方框：往右拖得比往下多，就由高来定尺寸，免得框冲出图底。
    expect(resizeFromCorner({ x0: 100, y0: 100, x1: 200, y1: 200 }, 700, 300, SLEEVE, 1))
      .toEqual({ x0: 100, y0: 100, x1: 300, y1: 300 });
  });
});

describe('框换算成样式', () => {
  it('覆盖层的百分比是相对图片本身的，与显示尺寸无关', () => {
    expect(boxPercent({ x0: 200, y0: 0, x1: 600, y1: 538 }, SLEEVE))
      .toEqual({ left: 25, top: 0, width: 50, height: 100 });
  });

  it('圆形预览把框里那一块摆满容器', () => {
    expect(previewStyle({ x0: 200, y0: 138, x1: 600, y1: 538 }, SLEEVE))
      .toEqual({ size: '200% 134.5%', position: '50% 100%' });
  });

  it('框等于整张图时位置取 0：图和容器一样大，摆哪里都一样', () => {
    expect(previewStyle({ x0: 0, y0: 0, x1: 800, y1: 538 }, SLEEVE))
      .toEqual({ size: '100% 100%', position: '0% 0%' });
  });

  it('屏幕上量到的像素换回源图像素', () => {
    expect(toNatural(100, 400, 800)).toBe(200);
    // 图还没布局出来，除数是 0：给 0 而不是 Infinity。
    expect(toNatural(100, 0, 800)).toBe(0);
  });
});

describe('角柄放哪儿', () => {
  it('框在屏幕上够大时角柄压在框角上', () => {
    expect(handleOutside({ width: 50, height: 50 }, { width: 400, height: 400 })).toBe(false);
  });
  it('4000×200 的图只剩 20px 高，框比角柄大不了多少，角柄挪到框外', () => {
    // 方框占满图高：屏幕上 20×20。
    expect(handleOutside({ width: 5, height: 100 }, { width: 400, height: 20 })).toBe(true);
  });
  it('只看短边：长条框哪怕很长也要挪', () => {
    expect(handleOutside({ width: 100, height: 100 }, { width: 600, height: HANDLE_ROOM - 1 })).toBe(true);
  });
  it('图的显示尺寸还没量到时不挪', () => {
    expect(handleOutside({ width: 5, height: 5 }, null)).toBe(false);
    expect(handleOutside({ width: 5, height: 5 }, { width: 0, height: 0 })).toBe(false);
  });
});
