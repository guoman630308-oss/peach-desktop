/* 图片灯箱：按需加载 Swiper、从点中那一张开始、翻页与缩略图条跟随、缩放条、信息面板与关闭。
 *
 * Swiper 换成一个记账的替身：这里看的是灯箱交给它什么、听它什么，轮播本身的位移、键盘与滚轮
 * 由 e2e 在真浏览器里用真 Swiper 走一遍（`e2e/photo-lightbox.test.ts`）。 */
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { LightboxHost, LightboxSlide } from '../../src/react/photo-lightbox/photo-lightbox';
import { photoMeta, photoZoom, type SwiperInstance } from '../../src/react/photo-lightbox/photo-lightbox';
import { closePhotoLightbox, openPhotoLightbox } from '../../src/react/photo-lightbox/photo-lightbox-dialog';
import { click, settle, type } from './render';

type Handler = (swiper: SwiperInstance, ...args: number[]) => void;
class FakeSwiper {
  static made: FakeSwiper[] = [];
  activeIndex: number;
  destroyed = false;
  handlers: Record<string, Handler[]> = {};
  zoom = { in: vi.fn(), out: vi.fn() };
  slideTo = vi.fn();
  update = vi.fn();
  destroy = vi.fn(() => { this.destroyed = true });
  constructor(public el: HTMLElement, public options: Record<string, any>) {
    this.activeIndex = options.initialSlide ?? 0;
    FakeSwiper.made.push(this);
  }
  get slides() { return [...this.el.querySelector('.swiper-wrapper')!.children] as HTMLElement[] }
  on(event: string, handler: Handler) { (this.handlers[event] ??= []).push(handler) }
  emit(event: string, ...args: number[]) {
    if (event === 'slideChange') this.options.on?.slideChange?.call(this);
    for (const handler of this.handlers[event] ?? []) handler(this as unknown as SwiperInstance, ...args);
  }
  /** 翻到第 `at` 张，照 Swiper 的顺序先改 `activeIndex` 再发 `slideChange`。 */
  go(at: number) { return act(async () => { this.activeIndex = at; this.emit('slideChange') }) }
}
const strip = () => FakeSwiper.made.at(-2)!;
const main = () => FakeSwiper.made.at(-1)!;

let observed: (() => void)[] = [];
const disconnected = vi.fn();
beforeEach(() => {
  FakeSwiper.made = [];
  observed = [];
  (window as { Swiper?: unknown }).Swiper = FakeSwiper;
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { observed.push(callback) }
    observe() {}
    disconnect() { disconnected() }
  });
  /* happy-dom 没有原生 popover：顶层开合记在一个属性上，`:popover-open` 读它。 */
  const proto = HTMLElement.prototype as HTMLElement & Record<string, unknown>;
  const matches = Element.prototype.matches;
  vi.spyOn(Element.prototype, 'matches').mockImplementation(function (this: Element, selector: string) {
    return selector === ':popover-open' ? this.hasAttribute('data-test-popover-open') : matches.call(this, selector);
  });
  proto.showPopover = function (this: HTMLElement) { this.setAttribute('data-test-popover-open', '') };
  proto.hidePopover = function (this: HTMLElement) { this.removeAttribute('data-test-popover-open') };
});
afterEach(async () => {
  await act(async () => closePhotoLightbox());
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
  delete proto.showPopover;
  delete proto.hidePopover;
  disconnected.mockClear();
});

const frame = () => act(() => new Promise<void>((done) => requestAnimationFrame(() => done())));
/** 加载器还没取过样式表时，插进来的 link 不进文档（进了 happy-dom 会真去取 `/vendor/`），
 *  当场放行 load；取过就直接打开。 */
async function open(index: number, slides: LightboxSlide[], host?: LightboxHost) {
  const append = vi.spyOn(document.head, 'append').mockImplementation((...nodes) => {
    for (const node of nodes) if (node instanceof HTMLLinkElement) queueMicrotask(() => node.dispatchEvent(new Event('load')));
  });
  try {
    await act(async () => { await openPhotoLightbox(index, slides, host) });
  } finally {
    append.mockRestore();
  }
  await frame();
  return document.querySelector<HTMLDialogElement>('dialog[data-photo-lightbox]')!;
}

const local = (id: number, extra: Record<string, unknown> = {}): LightboxSlide => ({
  src: `/photo?id=${id}`, thumb: `/photo-thumb?id=${id}`, name: `${id}.jpg`,
  asset: { id, name: `${id}.jpg`, location: 'local', size: 2 * 1024 * 1024, ...extra },
});
const sample = (n: number): LightboxSlide => ({
  src: `/sample-image?code=SSIS-057&n=${n}`, thumb: `/sample-thumb?code=SSIS-057&n=${n}`,
  name: `SSIS-057 样张 ${n}`, asset: null, source: 'DMM',
});
const WALL = [sample(1), sample(2), local(100), local(101), local(102)];

const q = <T extends Element = HTMLElement>(box: Element, selector: string) => box.querySelector<T>(selector)!;
const labelled = (box: Element, label: string) => q<HTMLButtonElement>(box, `[aria-label="${label}"]`);
const escape = () => act(async () => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
});

describe('打开', () => {
  it('Swiper 按固定版本按需取，样式与脚本都到了才建轮播；取不到就在新标签页开那张大图', async () => {
    delete (window as { Swiper?: unknown }).Swiper;
    /* 元素记下来但不进文档：进了文档 happy-dom 会真去取 `/vendor/`，load 与 error 由这里派发。 */
    const appended: Element[] = [];
    const append = vi.spyOn(document.head, 'append').mockImplementation((...nodes) => { appended.push(...nodes as Element[]) });
    const link = () => appended.filter((node) => node instanceof HTMLLinkElement).at(-1) as HTMLLinkElement;
    const script = () => appended.filter((node) => node instanceof HTMLScriptElement).at(-1) as HTMLScriptElement;
    const opener = vi.spyOn(window, 'open').mockImplementation(() => null);
    await act(async () => {
      const opened = openPhotoLightbox(1, WALL);
      link().dispatchEvent(new Event('load'));
      script().onerror!(new Event('error'));
      await opened;
    });
    expect(opener).toHaveBeenCalledWith('/sample-image?code=SSIS-057&n=2', '_blank', 'noopener');
    expect(document.querySelector('dialog')).toBeNull();

    await act(async () => {
      const opened = openPhotoLightbox(0, WALL);
      expect(link().getAttribute('href')).toBe('/vendor/swiper/14.3.0/swiper-bundle.min.css');
      expect(script().getAttribute('src')).toBe('/vendor/swiper/14.3.0/swiper-bundle.min.js');
      (window as { Swiper?: unknown }).Swiper = FakeSwiper;
      script().onload!(new Event('load'));
      await Promise.resolve();
      expect(FakeSwiper.made).toHaveLength(0);
      link().dispatchEvent(new Event('load'));
      await opened;
    });
    append.mockRestore();
    opener.mockRestore();
    expect(document.querySelector('dialog[data-photo-lightbox]')).not.toBeNull();
    expect(FakeSwiper.made).toHaveLength(2);
  });

  it('从点中那一张开始：模态打开、页码写「n / N」并读给读屏、缩略图条当场居中到这一张', async () => {
    const box = await open(2, WALL);
    expect(box.open).toBe(true);
    expect(box.getAttribute('aria-label')).toBe('图片浏览');
    expect(box.hasAttribute('data-has-strip')).toBe(true);
    expect(document.body.classList.contains('photolight-open')).toBe(true);
    expect(box.closest('.peach-react')?.parentElement).toBe(document.body);
    const count = q(box, '[data-photo-count]');
    expect(count.textContent).toBe('3 / 5');
    expect(count.getAttribute('aria-live')).toBe('polite');
    expect(main().options.initialSlide).toBe(2);
    expect(strip().slideTo).toHaveBeenCalledWith(2, 0);
    const images = [...q(box, '[data-photo-main]').querySelectorAll('img')];
    expect(images.map((img) => img.getAttribute('src'))).toEqual(WALL.map((slide) => slide.src));
    expect(images.every((img) => img.getAttribute('loading') === 'lazy'
      && img.getAttribute('referrerpolicy') === 'no-referrer')).toBe(true);
    expect([...q(box, '[data-photo-strip]').querySelectorAll('img')].map((img) => img.getAttribute('src')))
      .toEqual(WALL.map((slide) => slide.thumb));
  });

  it('主画布带缩放、键盘、滚轮翻页、缩略图联动、动态页码点与左右键；缩略图条自由拖、点哪张居中哪张', async () => {
    const box = await open(0, WALL);
    const options = main().options;
    expect(options).toMatchObject({
      zoom: { minRatio: .01, maxRatio: 100 }, keyboard: { enabled: true }, lazyPreloadPrevNext: 1,
      mousewheel: { enabled: true, forceToAxis: false },
      pagination: { clickable: true, dynamicBullets: true, dynamicMainBullets: 3 },
    });
    expect(options.thumbs.swiper).toBe(strip());
    expect(options.pagination.el).toBe(q(box, '[data-photo-pagination]'));
    expect(options.pagination.el.getAttribute('aria-label')).toBe('选择图片');
    expect(options.pagination.renderBullet(1, 'swiper-pagination-bullet'))
      .toBe('<button type="button" class="swiper-pagination-bullet" aria-label="查看第 2 张图片"></button>');
    expect(options.navigation).toEqual({ prevEl: labelled(box, '上一张'), nextEl: labelled(box, '下一张') });
    expect(strip().options).toEqual({
      slidesPerView: 'auto', spaceBetween: 8, freeMode: true, watchSlidesProgress: true,
      centeredSlides: true, slideToClickedSlide: true,
    });
  });

  it('只有一张时不出缩略图条与页码点', async () => {
    const box = await open(0, [local(7)]);
    expect(box.hasAttribute('data-has-strip')).toBe(false);
    expect(box.querySelector('[data-photo-pagination]')).toBeNull();
    expect(q(box, '[data-photo-count]').textContent).toBe('1 / 1');
  });

  it('序号越界或一张都没有就不开', async () => {
    await act(async () => { await openPhotoLightbox(5, WALL) });
    await act(async () => { await openPhotoLightbox(0, []) });
    expect(document.querySelector('dialog')).toBeNull();
  });

  it('同一时刻只有一份：再开一次，上一份的两条轮播先拆掉', async () => {
    await open(0, WALL);
    const [firstStrip, firstMain] = FakeSwiper.made;
    await open(1, [local(9), local(10)]);
    expect(document.querySelectorAll('dialog')).toHaveLength(1);
    expect(firstMain.destroy).toHaveBeenCalledWith(true, true);
    expect(firstStrip.destroy).toHaveBeenCalledWith(true, true);
    expect(q(document.body, '[data-photo-count]').textContent).toBe('2 / 2');
  });
});

describe('翻页', () => {
  it('换一张：页码跟上，缩略图条滑到这一张，信息面板换成这一张', async () => {
    const box = await open(2, WALL);
    await main().go(3);
    expect(q(box, '[data-photo-count]').textContent).toBe('4 / 5');
    expect(strip().slideTo).toHaveBeenLastCalledWith(3, 200);
    expect(q(box, '[data-photo-detail] h2').textContent).toBe('101.jpg');
  });

  it('原图取不到换上这张的缩略图，只换一次；缩略图也取不到就换成占位', async () => {
    const box = await open(0, WALL);
    const slide = q(box, '[data-photo-main] .swiper-slide');
    const img = q<HTMLImageElement>(slide, 'img');
    await act(async () => { img.dispatchEvent(new Event('error')) });
    expect(img.getAttribute('src')).toBe('/sample-thumb?code=SSIS-057&n=1');
    await act(async () => { img.dispatchEvent(new Event('error')) });
    expect(slide.querySelector('img')).toBeNull();
    expect(slide.querySelector('[data-photo-missing]')?.textContent).toBe('图片取不到');
  });

  it('灯箱量到新尺寸就让两条轮播重量，缩放按上一次的目标重算', async () => {
    await open(0, WALL);
    main().zoom.out.mockClear();
    observed.forEach((callback) => callback());
    expect(main().update).toHaveBeenCalled();
    expect(strip().update).toHaveBeenCalled();
    expect(main().zoom.out).toHaveBeenCalled();
  });
});

describe('缩放条', () => {
  /* 测试环境没有布局，量不出适应窗口的比例：图没有自然尺寸时按 100% 算，这里的百分比就是倍数。 */
  it('步进键一次十个百分点，1:1 回到原图像素，滑杆直接给目标；读数与滑杆跟着走', async () => {
    const box = await open(0, WALL);
    const readout = q(box, '[data-photo-zoom-readout]');
    const range = q<HTMLInputElement>(box, '[data-photo-zoom] input[type=range]');
    expect([range.min, range.max, range.getAttribute('aria-label')]).toEqual(['10', '400', '缩放']);
    expect(readout.textContent).toBe('100%');
    await click(labelled(box, '放大'));
    expect(main().zoom.in).toHaveBeenLastCalledWith(1.1);
    expect([readout.textContent, range.value]).toEqual(['110%', '110']);
    await click(labelled(box, '原大小'));
    expect(readout.textContent).toBe('100%');
    expect(main().zoom.out).toHaveBeenCalled();
    await click(labelled(box, '缩小'));
    expect(main().zoom.in).toHaveBeenLastCalledWith(.9);
    await type(range, '250');
    expect(main().zoom.in).toHaveBeenLastCalledWith(2.5);
    expect(readout.textContent).toBe('250%');
    main().zoom.out.mockClear();
    await click(labelled(box, '适应窗口'));
    expect(main().zoom.out).toHaveBeenCalledTimes(1);
    expect(readout.textContent).toBe('100%');
  });

  it('两端步进键画的是放大镜加减号，不是文本字形', async () => {
    const box = await open(0, WALL);
    expect(labelled(box, '缩小').innerHTML).toContain('href="#i-zoom-out"');
    expect(labelled(box, '放大').innerHTML).toContain('href="#i-zoom-in"');
    expect(labelled(box, '适应窗口').innerHTML).toContain('href="#i-maximize"');
    expect(labelled(box, '原大小').textContent).toBe('1:1');
  });

  it('捏合或双击改了倍数，读数按原图像素折算回来', async () => {
    const box = await open(0, WALL);
    await act(async () => main().emit('zoomChange', 3));
    expect(q(box, '[data-photo-zoom-readout]').textContent).toBe('300%');
  });
});

describe('缩放换算', () => {
  const fixture = (natural: [number, number], shown: [number, number]) => {
    const box = document.createElement('div');
    box.innerHTML = '<div class="swiper-slide"><img></div>';
    document.body.append(box);
    const img = box.querySelector('img')!;
    Object.defineProperties(img, {
      naturalWidth: { value: natural[0] }, naturalHeight: { value: natural[1] },
      offsetWidth: { value: shown[0] }, offsetHeight: { value: shown[1] },
    });
    const handlers: Record<string, Handler> = {};
    const swiper = {
      activeIndex: 0, destroyed: false, slides: [box.firstElementChild as HTMLElement],
      zoom: { in: vi.fn(), out: vi.fn() }, on: (event: string, handler: Handler) => { handlers[event] = handler },
      slideTo: vi.fn(), update: vi.fn(), destroy: vi.fn(),
    };
    const show = vi.fn();
    return { box, img, swiper, show, handlers, zoom: photoZoom(box, swiper, show) };
  };

  it('百分比相对原图像素：适应窗口的那一档就是显示宽高与原图之比，封顶 100%', () => {
    const { swiper, show, zoom } = fixture([2000, 1000], [500, 250]);
    zoom.apply('fit');
    expect(show).toHaveBeenLastCalledWith(25);
    expect(swiper.zoom.out).toHaveBeenCalled();
    zoom.apply(100);
    expect(swiper.zoom.in).toHaveBeenLastCalledWith(4);
    zoom.apply(1000);
    expect(show).toHaveBeenLastCalledWith(400);
    zoom.apply(1);
    expect(show).toHaveBeenLastCalledWith(10);
    expect(swiper.zoom.in).toHaveBeenLastCalledWith(.4);
  });

  it('量尺寸与大图到了都按上一次的目标重算；不是当前这张的图到了不算', () => {
    const { img, swiper, zoom } = fixture([2000, 1000], [500, 250]);
    zoom.apply(200);
    swiper.zoom.in.mockClear();
    zoom.resize();
    expect(swiper.zoom.in).toHaveBeenLastCalledWith(8);
    zoom.loaded(document.createElement('img'));
    expect(swiper.zoom.in).toHaveBeenCalledTimes(1);
    zoom.loaded(img);
    expect(swiper.zoom.in).toHaveBeenCalledTimes(2);
  });

  it('灯箱已经关掉或 Swiper 已经拆掉：迟到的尺寸与加载回调什么都不做，不抛错', () => {
    const { box, img, swiper, zoom } = fixture([2000, 1000], [500, 250]);
    swiper.destroyed = true;
    expect(() => { zoom.resize(); zoom.loaded(img) }).not.toThrow();
    swiper.destroyed = false;
    box.remove();
    expect(() => { zoom.resize(); zoom.loaded(img) }).not.toThrow();
    expect(swiper.zoom.out).not.toHaveBeenCalled();
    expect(swiper.zoom.in).not.toHaveBeenCalled();
  });

  it('捏合的倍数按适应窗口那一档折回原图像素', () => {
    const { show, handlers } = fixture([2000, 1000], [500, 250]);
    handlers.zoomChange({} as SwiperInstance, 2);
    expect(show).toHaveBeenLastCalledWith(50);
  });
});

describe('信息面板', () => {
  it('本地图写来源与大小，定位只把 asset id 交给壳；路径不进页面', async () => {
    const revealSource = vi.fn(async () => '');
    const slides = [local(100, { path: 'R:\\media\\secret\\100.jpg' })];
    const box = await open(0, slides, { revealSource });
    const panel = q(box, '[data-photo-detail]');
    expect(q(panel, 'h2').textContent).toBe('100.jpg');
    expect(q(panel, 'h2').getAttribute('tabindex')).toBe('-1');
    expect(q(panel, '[data-photo-detail-meta]').textContent).toBe('本地 · 2\u00a0MB');
    const reveal = q<HTMLButtonElement>(panel, '[data-photo-reveal]');
    expect(reveal.hidden).toBe(false);
    expect(reveal.textContent).toBe('在资源管理器中显示');
    await click(reveal);
    expect(revealSource).toHaveBeenCalledWith(100);
    expect(document.body.innerHTML).not.toContain('secret');
  });

  it('定位等回话时按钮忙着、再点不重发；失败的原因写在按钮下，换一张就清掉', async () => {
    let release!: (text: string) => void;
    const revealSource = vi.fn(() => new Promise<string>((done) => { release = done }));
    const box = await open(0, [local(100), local(101)], { revealSource });
    const reveal = q<HTMLButtonElement>(box, '[data-photo-reveal]');
    await click(reveal);
    expect(reveal.getAttribute('aria-busy')).toBe('true');
    expect(reveal.querySelector('[role=status]')?.getAttribute('aria-label')).toBe('正在定位');
    await click(reveal);
    expect(revealSource).toHaveBeenCalledTimes(1);
    await act(async () => release('源文件不在这台机器上'));
    await settle();
    expect(reveal.hasAttribute('aria-busy')).toBe(false);
    expect(reveal.textContent).toBe('在资源管理器中显示');
    const status = q(box, '[data-photo-status]');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toBe('源文件不在这台机器上');
    await main().go(1);
    expect(status.textContent).toBe('');
  });

  it('样张与在线图不出定位键：写来源，大图到了补上分辨率，第几张只看底栏；没名字写「未命名图片」', async () => {
    const box = await open(0, [sample(1), { src: '/follow-stream?id=5&media=0', thumb: '/t.jpg', size: 3000 }]);
    const meta = q(box, '[data-photo-detail-meta]');
    expect(q<HTMLButtonElement>(box, '[data-photo-reveal]').hidden).toBe(true);
    expect(meta.textContent).toBe('DMM');
    expect(q(box, '[data-photo-count]').textContent).toBe('1 / 2');
    const img = q<HTMLImageElement>(box, '[data-photo-main] img');
    Object.defineProperties(img, { naturalWidth: { value: 1600 }, naturalHeight: { value: 1200 } });
    await act(async () => { img.dispatchEvent(new Event('load')) });
    expect(meta.textContent).toBe('DMM · 1600 × 1200');
    await main().go(1);
    expect(q(box, '[data-photo-detail] h2').textContent).toBe('未命名图片');
    expect(meta.textContent).toBe('在线图片 · 3\u00a0KB');
  });

  it('本地图没给定位的壳也不出定位键', async () => {
    const box = await open(0, [local(100)]);
    expect(q<HTMLButtonElement>(box, '[data-photo-reveal]').hidden).toBe(true);
  });

  it('圆圈 i 点开面板、点面板外收起而不关灯箱；Escape 先收面板再关灯箱', async () => {
    const box = await open(0, WALL);
    const toggle = labelled(box, '图片详情');
    const panel = q(box, '[data-photo-detail]');
    expect(toggle.getAttribute('aria-haspopup')).toBe('dialog');
    expect(toggle.getAttribute('aria-controls')).toBe(panel.id);
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.hidden).toBe(true);
    await click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(panel.hidden).toBe(false);
    await click(box);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(box.isConnected).toBe(true);
    await click(toggle);
    await escape();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(box.isConnected).toBe(true);
    await escape();
    expect(box.isConnected).toBe(false);
  });
});

describe('关闭', () => {
  it('关闭键：拆掉两条轮播、摘掉页面锁滚、停止量尺寸，Escape 不再归它管', async () => {
    const box = await open(0, WALL);
    const [s, m] = [strip(), main()];
    await click(labelled(box, '关闭'));
    expect(box.isConnected).toBe(false);
    expect(m.destroy).toHaveBeenCalledWith(true, true);
    expect(s.destroy).toHaveBeenCalledWith(true, true);
    expect(disconnected).toHaveBeenCalled();
    expect(document.body.classList.contains('photolight-open')).toBe(false);
    const later = vi.fn();
    document.addEventListener('keydown', later);
    await escape();
    document.removeEventListener('keydown', later);
    expect(later).toHaveBeenCalled();
  });

  it('点黑底或图四周的留白关掉，点图、工具条和缩略图不关', async () => {
    let box = await open(0, WALL);
    await click(q(box, '[data-photo-main] img'));
    await click(q(box, '[data-photo-bar]'));
    await click(q(box, '[data-photo-strip] img'));
    expect(box.isConnected).toBe(true);
    await click(q(box, '.swiper-zoom-container'));
    expect(box.isConnected).toBe(false);
    box = await open(0, WALL);
    await click(box);
    expect(box.isConnected).toBe(false);
  });

  it('浏览器自己的取消（Esc 关模态）先拦下，再走同一条关闭', async () => {
    const box = await open(0, WALL);
    const cancel = new Event('cancel', { cancelable: true });
    await act(async () => { box.dispatchEvent(cancel) });
    expect(cancel.defaultPrevented).toBe(true);
    expect(box.isConnected).toBe(false);
  });
});

it('面板第二行的口径：本地图缺来源写「来源未知」、缺大小写「大小未知」，一千字节以下也写 1 KB', () => {
  expect(photoMeta({ src: '', thumb: '', asset: { id: 1 } }, undefined)).toBe('来源未知 · 大小未知');
  expect(photoMeta({ src: '', thumb: '', asset: { id: 1, location: 'pikpak', size: 10 } }, undefined)).toBe('PikPak · 1\u00a0KB');
});
