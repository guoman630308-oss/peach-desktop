import { describe, expect, it } from 'vitest';
import { markClamp, MARK_MIN_SHORT, nativeImageFit, faceSourceScale } from '../../src/card-art/native-image';

it('544×724 的旧人像不能使用 2184×1468 封面的焦点', () => {
  expect(faceSourceScale(544, 724, 2184, 1468)).toBe(0);
  expect(faceSourceScale(544, 724, 544, 724)).toBe(1);
  expect(faceSourceScale(0, 0, 0, 0)).toBe(0);
});

describe('派生件相对边车源图的比例', () => {
  it('原件本身是 1', () => {
    expect(faceSourceScale(1200, 1600, 1200, 1600)).toBe(1);
  });
  it('缩到长边 640 的那一份给出缩放比', () => {
    expect(faceSourceScale(480, 640, 1200, 1600)).toBeCloseTo(0.4, 6);
  });
  it('取整带来的半像素误差仍算同一张图', () => {
    expect(faceSourceScale(481, 640, 1203, 1600)).toBeGreaterThan(0);
  });
  it('换了比例就是另一张图', () => {
    expect(faceSourceScale(544, 724, 2184, 1468)).toBe(0);
  });
  it('比源图还大的必然不是它缩出来的', () => {
    expect(faceSourceScale(2400, 3200, 1200, 1600)).toBe(0);
  });
  it('尺寸缺失时不取景', () => {
    expect(faceSourceScale(0, 0, 1200, 1600)).toBe(0);
    expect(faceSourceScale(480, 640, 0, 0)).toBe(0);
  });
});

describe('头像和标识的小图补底', () => {
  it('大框中的低分辨率图片按原尺寸居中', () => {
    expect(nativeImageFit(64, 64, 180, 240)).toEqual({ small: true, width: 64, height: 64 });
  });
  it('70 px 紧凑头像适用，32 px 图标不补底', () => {
    expect(nativeImageFit(16, 16, 70, 70).small).toBe(true);
    expect(nativeImageFit(16, 16, 32, 32).small).toBe(false);
  });
  it('宽字标保持比例且不超出容器', () => {
    expect(nativeImageFit(400, 40, 180, 180)).toEqual({ small: true, width: 180, height: 18 });
  });
  it('高像素密度屏幕上的 64 px 方标允许在紧凑模式放大', () => {
    expect(nativeImageFit(64, 64, 70, 70, 2)).toEqual({ small: false, width: 32, height: 32 });
    expect(nativeImageFit(64, 64, 32, 32, 2).small).toBe(false);
  });
  it('清晰图片和只需轻微放大的图片铺满', () => {
    expect(nativeImageFit(300, 400, 180, 240).small).toBe(false);
    expect(nativeImageFit(350, 350, 369, 369, 2).small).toBe(false);
    expect(nativeImageFit(150, 200, 180, 240).small).toBe(false);
    expect(nativeImageFit(0, 0, 180, 240).small).toBe(false);
  });
});

describe('超扁、超高的标识撑出最小短边', () => {
  it('1378×42 的横条短边撑到框短边的下限，长边仍按框', () => {
    expect(markClamp(1378, 42, 180, 180)).toEqual({ width: 180, height: 180 * MARK_MIN_SHORT });
  });
  it('42×600 的竖条同理撑宽', () => {
    expect(markClamp(42, 600, 180, 180)).toEqual({ width: 180 * MARK_MIN_SHORT, height: 180 });
  });
  it('10:1 的字标和方形标识照常 contain', () => {
    expect(markClamp(400, 40, 180, 180)).toBeNull();
    expect(markClamp(120, 120, 180, 180)).toBeNull();
  });
  it('尺寸没量到时不动', () => {
    expect(markClamp(0, 0, 180, 180)).toBeNull();
    expect(markClamp(1378, 42, 0, 0)).toBeNull();
  });
});
