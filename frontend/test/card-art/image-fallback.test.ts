/* 图片回退链：取不到就换下一张，都取不到就按 `data-drop` 收场。 */
import { describe, expect, it } from 'vitest';

import { advanceImageFallback, imageFallbackAttrs, parseFallbacks, wireImageFallbacks } from '../../src/card-art/image-fallback';

function image(attrs: string): HTMLImageElement {
  const box = document.createElement('div');
  box.className = 'ring';
  box.innerHTML = `<img src="/first" ${attrs}>`;
  document.body.append(box);
  return box.querySelector('img')!;
}

describe('声明', () => {
  it('空候选丢掉，地址转义进属性', () => {
    expect(imageFallbackAttrs({ fallbacks: ['', '/a?x="1"', '/b'] }))
      .toBe('data-drop="self" data-fallbacks="/a?x=&quot;1&quot;|/b"');
    expect(imageFallbackAttrs({ fallbacks: '' })).toBe('data-drop="self"');
    expect(imageFallbackAttrs({ drop: 'initial', initial: 'A', dropClass: 'ini', dropStyle: true }))
      .toBe('data-drop="initial" data-initial="A" data-drop-class="ini" data-drop-style');
  });

  it('解析时去掉空段和空白', () => {
    expect(parseFallbacks(' /a | |/b ')).toEqual(['/a', '/b']);
    expect(parseFallbacks(undefined)).toEqual([]);
  });
});

describe('推进', () => {
  it('没登记 data-drop 的图一概不动', () => {
    const img = image('');
    expect(advanceImageFallback(img)).toBe('');
    expect(img.isConnected).toBe(true);
  });

  it('按序换完候选再收场，最后一环把图摘掉', () => {
    const img = image('data-drop="self" data-fallbacks="/second|/third"');
    expect(advanceImageFallback(img)).toBe('retry');
    expect(img.getAttribute('src')).toBe('/second');
    expect(img.dataset.fallbacks).toBe('/third');
    expect(advanceImageFallback(img)).toBe('retry');
    expect(img.getAttribute('src')).toBe('/third');
    expect(img.dataset.fallbacks).toBeUndefined();
    expect(advanceImageFallback(img)).toBe('drop');
    expect(img.isConnected).toBe(false);
  });

  it('换下一环时撤掉按上一张算的取景；脸框不论开关都撤', () => {
    const framed = image('style="object-position:50% 12%" data-facebox="1 2 3 4 5" data-drop="self" data-drop-style data-fallbacks="/b"');
    advanceImageFallback(framed);
    expect(framed.hasAttribute('style')).toBe(false);
    expect(framed.dataset.facebox).toBeUndefined();
    const kept = image('style="--face:1" data-facebox="1 2 3 4 5" data-drop="self" data-fallbacks="/b"');
    advanceImageFallback(kept);
    expect(kept.getAttribute('style')).toBe('--face:1');
    expect(kept.dataset.facebox).toBeUndefined();
  });

  it('closest 摘掉最近的容器，initial 换成首字母', () => {
    const inBox = image('data-drop="closest:.ring"');
    const ring = inBox.parentElement!;
    advanceImageFallback(inBox);
    expect(ring.isConnected).toBe(false);
    const initial = image('data-drop="initial" data-initial="A" data-drop-class="ini"');
    const parent = initial.parentElement!;
    advanceImageFallback(initial);
    expect(parent.innerHTML).toBe('<span class="ini">A</span>');
  });
});

describe('委托监听', () => {
  it('error 不冒泡，捕获阶段接住后代 <img>', () => {
    const root = document.createElement('section');
    document.body.append(root);
    wireImageFallbacks(root);
    root.innerHTML = '<div><img src="/x" data-drop="self"></div>';
    const img = root.querySelector('img')!;
    img.dispatchEvent(new Event('error'));
    expect(img.isConnected).toBe(false);
  });
});
