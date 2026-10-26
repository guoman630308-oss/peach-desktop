/* 图片落地之后的取景：头像按人脸放大、封面按人脸与正封框取景、过小的图原尺寸摆、加载中的微光。
 *
 * 测试环境没有布局也不解码图片：框的尺寸和图的像素直接写在元素上。 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  avatarFrame, coverAnchor, fitNativeImage, installCardArt, refitNativeImages, settleImage, upgradeCover,
  watchPendingImages,
} from '../../src/card-art/framing';

function sized(img: HTMLImageElement, width: number, height: number): HTMLImageElement {
  Object.defineProperty(img, 'naturalWidth', { configurable: true, value: width });
  Object.defineProperty(img, 'naturalHeight', { configurable: true, value: height });
  Object.defineProperty(img, 'complete', { configurable: true, value: true });
  return img;
}

function boxOf(el: Element, width: number, height: number): void {
  el.getBoundingClientRect = () => ({ width, height, x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, toJSON: () => ({}) });
}

function mount(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  document.body.append(host);
  return host;
}

afterEach(() => {
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe('avatarFrame：圆框按人脸放大', () => {
  it('图撑到 cover 缩放乘倍数那么大，负偏移把脸拉向框心，并撤掉预检的宽度上限', () => {
    const host = mount('<span class="ring"><img data-facebox="0.439 0.224 67 640 960"></span>');
    const img = sized(host.querySelector('img')!, 640, 960);
    boxOf(img.parentElement!, 160, 160);
    vi.stubGlobal('devicePixelRatio', 2);
    avatarFrame(img);
    vi.unstubAllGlobals();
    expect(img.dataset.faceFramed).toBe('');
    expect(img.style.position).toBe('absolute');
    expect(img.style.width).toBe('200%');
    expect(img.style.height).toBe('300%');
    expect(img.style.left).toBe('-37.8%');
    expect(img.style.top).toBe('-17.2%');
    expect(img.style.maxWidth).toBe('none');
  });

  it('派生件按比例换算脸框，换成别的图就退回几何居中', () => {
    const host = mount('<span class="ring"><img data-facebox="0.439 0.224 67 640 960"></span>');
    const derived = sized(host.querySelector('img')!, 320, 480);
    boxOf(derived.parentElement!, 160, 160);
    avatarFrame(derived);
    expect(derived.style.width).not.toBe('');
    const other = mount('<span class="ring"><img data-facebox="0.439 0.224 67 640 960"></span>').querySelector('img')!;
    sized(other, 500, 500);
    boxOf(other.parentElement!, 160, 160);
    avatarFrame(other);
    expect(other.style.objectPosition).toBe('50% 50%');
    expect(other.style.width).toBe('');
  });

  it('原尺寸摆放的小图不放大', () => {
    const host = mount('<span class="ring" data-native-small="true"><img data-facebox="0.439 0.224 67 640 960"></span>');
    const img = sized(host.querySelector('img')!, 640, 960);
    boxOf(img.parentElement!, 160, 160);
    avatarFrame(img);
    expect(img.dataset.faceFramed).toBe('');
    expect(img.style.width).toBe('');
  });

  it('框还没布局时先藏着，等量到尺寸再取景', () => {
    const observers: { fire(): void }[] = [];
    vi.stubGlobal('ResizeObserver', class {
      constructor(private readonly callback: () => void) { observers.push({ fire: () => this.callback() }); }
      observe() {}
      disconnect() {}
    });
    const host = mount('<span class="ring"><img data-facebox="0.439 0.224 67 640 960"></span>');
    const img = sized(host.querySelector('img')!, 640, 960);
    boxOf(img.parentElement!, 0, 0);
    avatarFrame(img);
    expect(img.dataset.faceFramed).toBeUndefined();
    boxOf(img.parentElement!, 160, 160);
    observers[0]!.fire();
    vi.unstubAllGlobals();
    expect(img.dataset.faceFramed).toBe('');
    expect(img.style.width).not.toBe('');
  });
});

describe('coverAnchor：封面按自己的宽高比分流', () => {
  const cover = (src: string, width: number, height: number, attrs = '') => {
    const host = mount(`<div class="pic"><img class="poster cover front" src="${src}" style="--card-ratio:0.75" ${attrs}></div>`);
    return sized(host.querySelector('img')!, width, height);
  };

  it('封套、正封、剧照三种形态；FC2 一律当整幅画面', () => {
    const sleeve = cover('/cover?code=SSIS-001', 800, 538);
    coverAnchor(sleeve);
    expect(sleeve.dataset.frame).toBe('sleeve');
    const front = cover('/cover?code=SSIS-002', 350, 500);
    coverAnchor(front);
    expect(front.dataset.frame).toBe('front');
    const still = cover('/cover?code=SSIS-003', 1600, 900);
    coverAnchor(still);
    expect(still.dataset.frame).toBe('still');
    const fc2 = cover('/cover?code=FC2-PPV-1', 800, 538);
    coverAnchor(fc2);
    expect(fc2.dataset.frame).toBe('still');
  });

  it('人脸落到可见窗口正中：锚点是 (脸 − 窗口/2) / (1 − 窗口)，夹在 0–100%', () => {
    const img = cover('/cover?code=SSIS-003', 1600, 900, 'data-cx="0.6"');
    coverAnchor(img);
    const visible = 0.75 / (1600 / 900);
    expect(img.style.getPropertyValue('--cover-x')).toBe(`${Math.round((0.6 - visible / 2) / (1 - visible) * 100)}%`);
    const edge = cover('/cover?code=SSIS-004', 1600, 900, 'data-cx="0.95"');
    coverAnchor(edge);
    expect(edge.style.getPropertyValue('--cover-x')).toBe('100%');
    // 横向整幅可见的那根轴不裁，锚点在那里是死值，不写。
    expect(edge.style.getPropertyValue('--cover-y')).toBe('');
  });

  it('没有框的封套按正封比例从右缘量回去，正封边缘使用封面模糊补底', () => {
    const img = cover('/cover?code=SSIS-001&thumb=1', 800, 538);
    img.parentElement!.style.setProperty('--cover-blur', 'url("/cover?code=SSIS-001&thumb=1")');
    coverAnchor(img);
    expect(img.classList.contains('panel')).toBe(true);
    expect(img.style.getPropertyValue('--panel-clip')).toMatch(/^0% 0% 0% \d/);
    expect(img.style.getPropertyValue('--panel-height')).toBe('100%');
    expect(img.parentElement!.style.getPropertyValue('--cover-blur')).toContain('/cover?code=SSIS-001&thumb=1');
  });

  it('整幅大封按主体人脸居中，重取景会清掉正封框样式', () => {
    const img = cover('/cover?code=SPONE-001', 2184, 1638, 'data-cx=".555" data-cy=".331"');
    coverAnchor(img);
    expect(img.classList.contains('panel')).toBe(true);
    img.dataset.coverLayout = 'still';
    coverAnchor(img);
    expect(img.dataset.frame).toBe('still');
    expect(img.classList.contains('panel')).toBe(false);
    expect(img.style.getPropertyValue('--panel-clip')).toBe('');
    expect(img.style.getPropertyValue('--cover-x')).toBe('63%');
    expect(img.parentElement!.style.getPropertyValue('--cover-blur')).toBe('');
  });

  it('Blu-ray 与已知宽封套按宽正封取景，FC2 保留整幅画面', () => {
    const wide = cover('https://covers.example/MIDA.jpg', 800, 438, 'data-cover-code="MIDA-122" data-wide-sleeve="1"');
    coverAnchor(wide);
    expect(wide.dataset.frame).toBe('sleeve');
    expect(wide.style.getPropertyValue('--panel-clip')).toBe('0% 0% 0% 53.5%');
    expect(parseFloat(wide.style.getPropertyValue('--panel-height'))).toBeLessThan(100);
    const fc2 = cover('https://covers.example/fc2.jpg', 800, 438, 'data-cover-code="FC2-PPV-1" data-wide-sleeve="1"');
    coverAnchor(fc2);
    expect(fc2.dataset.frame).toBe('still');
    expect(fc2.classList.contains('panel')).toBe(false);
    const dvd = cover('/cover?code=MIDA-159', 800, 538, 'data-wide-sleeve="1"');
    coverAnchor(dvd);
    expect(dvd.style.getPropertyValue('--panel-height')).toBe('100%');
  });

  it('框描述的是另一张图时一个字都不写；等比缩小的派生档照用', () => {
    const derived = cover('/cover?code=SSIS-001&thumb=1', 800, 538, 'data-posterbox="842 1600 1076 0 1600 1076"');
    coverAnchor(derived);
    expect(derived.classList.contains('panel')).toBe(true);
    const img = cover('/cover?code=SSIS-001', 800, 538, 'data-posterbox="210 400 269 0 400 269"');
    coverAnchor(img);
    expect(img.classList.contains('panel')).toBe(false);
  });
});

describe('宽正封框', () => {
  it('VR 画面在 4:3 源图上按左侧人脸取景', () => {
    const host = mount('<div class="pic"><img class="poster cover front" src="/cover?code=MDVR-276" style="--card-ratio:.75" data-cover-layout="still" data-cx=".22" data-posterbox="380 800 600 0 800 600"></div>');
    const img = sized(host.querySelector('img')!, 800, 600);
    coverAnchor(img);
    expect(img.dataset.frame).toBe('still');
    expect(img.classList.contains('panel')).toBe(false);
    expect(img.style.getPropertyValue('--cover-x')).toBe('0%');
  });
  it('来源框独立于标题保留完整宽正封', () => {
    const host = mount('<div class="pic"><img class="poster cover front" src="/cover?code=MIDA-239" style="--card-ratio:.75" data-posterbox="428 800 438 0 800 438"></div>');
    const img = sized(host.querySelector('img')!, 800, 438);
    coverAnchor(img);
    expect(img.classList.contains('panel')).toBe(true);
    expect(parseFloat(img.style.getPropertyValue('--panel-height'))).toBeLessThan(100);
    expect(parseFloat(img.style.getPropertyValue('--panel-top'))).toBeGreaterThan(0);
  });
});

describe('upgradeCover：派生档不够清楚就换回原件', () => {
  it('一个源像素要占不止一个设备像素才换，模糊底仍取派生档', () => {
    const host = mount('<div class="pic"><img class="poster cover" src="/cover?code=A&thumb=1"></div>');
    const img = sized(host.querySelector('img')!, 300, 200);
    boxOf(img, 600, 400);
    upgradeCover(img);
    expect(new URL(img.src).search).toBe('?code=A');
    expect(img.dataset.thumbSrc).toContain('/cover?code=A&thumb=1');
    const sharp = sized(mount('<img class="poster cover" src="/cover?code=B&thumb=1">').querySelector('img')!, 1200, 800);
    boxOf(sharp, 600, 400);
    upgradeCover(sharp);
    expect(sharp.getAttribute('src')).toBe('/cover?code=B&thumb=1');
  });
});

describe('fitNativeImage：比框还小的图原尺寸摆', () => {
  it('小图按源尺寸摆并拿自己垫模糊底，够大的铺满', () => {
    const host = mount('<span data-fit-native><img src="/logo?studio=a"></span>');
    const box = host.querySelector<HTMLElement>('[data-fit-native]')!;
    Object.defineProperty(box, 'clientWidth', { value: 180 });
    Object.defineProperty(box, 'clientHeight', { value: 180 });
    const img = sized(box.querySelector('img')!, 42, 42);
    fitNativeImage(img);
    expect(box.dataset.nativeSmall).toBe('true');
    expect(box.style.getPropertyValue('--markw')).toBe('42px');
    expect(box.style.getPropertyValue('--markbg')).toContain('/logo?studio=a');
    sized(img, 1378, 1378);
    refitNativeImages(host);
    expect(box.dataset.nativeSmall).toBe('false');
    expect(box.style.getPropertyValue('--markw')).toBe('100%');
    expect(box.style.getPropertyValue('--markbg')).toBe('none');
  });

  it('超扁的标识短边撑到下限、不补模糊底；人像框不走这一条', () => {
    const host = mount('<span data-fit-native="mark"><img src="/logo?studio=b"></span>'
      + '<span data-fit-native="portrait"><img src="/avatar?p=c"></span>');
    const [mark, portrait] = [...host.querySelectorAll<HTMLElement>('[data-fit-native]')];
    for (const box of [mark!, portrait!]) {
      Object.defineProperty(box, 'clientWidth', { value: 180 });
      Object.defineProperty(box, 'clientHeight', { value: 180 });
    }
    fitNativeImage(sized(mark!.querySelector('img')!, 1378, 42));
    expect(mark!.dataset.nativeClamp).toBe('true');
    expect(mark!.style.getPropertyValue('--markw')).toBe('180px');
    expect(mark!.style.getPropertyValue('--markh')).toBe('18px');
    expect(mark!.style.getPropertyValue('--markbg')).toBe('none');
    fitNativeImage(sized(portrait!.querySelector('img')!, 1378, 42));
    expect(portrait!.dataset.nativeClamp).toBe('false');
  });
});

describe('加载微光', () => {
  it('插进页面时还没到手的图在框上标 imgwait，等得不久就直接摘', () => {
    const host = mount('<span class="ring"><img src="/a"></span>');
    const img = host.querySelector('img')!;
    Object.defineProperty(img, 'complete', { configurable: true, value: false });
    watchPendingImages(host);
    expect(img.parentElement!.classList.contains('imgwait')).toBe(true);
    settleImage(img);
    expect(img.parentElement!.classList.contains('imgwait')).toBe(false);
    expect(img.parentElement!.classList.contains('ui-imgdone')).toBe(false);
  });

  it('等过门槛的淡出完再摘；动效不来时一秒兜底', () => {
    vi.useFakeTimers();
    const host = mount('<span class="ring"><img src="/a"></span>');
    const img = host.querySelector('img')!;
    Object.defineProperty(img, 'complete', { configurable: true, value: false });
    watchPendingImages(host);
    vi.advanceTimersByTime(500);
    settleImage(img);
    expect(img.parentElement!.classList.contains('ui-imgdone')).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(img.parentElement!.classList.contains('ui-imgdone')).toBe(false);
  });
});

describe('installCardArt：全站一组捕获监听', () => {
  it('头像加载完按脸框取景；封面加载完取景；取不到图收掉微光', () => {
    installCardArt();
    installCardArt();
    const host = mount('<span class="ring"><img data-facebox="0.439 0.224 67 640 960"></span><div class="pic"><img class="poster cover" src="/cover?code=X" style="--card-ratio:0.75"></div><span class="ring imgwait"><img src="/gone"></span>');
    const face = sized(host.querySelector<HTMLImageElement>('.ring img')!, 640, 960);
    boxOf(face.parentElement!, 160, 160);
    face.dispatchEvent(new Event('load'));
    expect(face.dataset.faceFramed).toBe('');
    expect(face.style.width).not.toBe('');
    const cover = sized(host.querySelector<HTMLImageElement>('img.cover')!, 1600, 900);
    cover.dispatchEvent(new Event('load'));
    expect(cover.dataset.frame).toBe('still');
    const gone = host.querySelector<HTMLImageElement>('img[src="/gone"]')!;
    gone.dispatchEvent(new Event('error'));
    expect(gone.parentElement!.classList.contains('imgwait')).toBe(false);
  });
});
