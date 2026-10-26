/* 图片灯箱：一枚原生模态 dialog，主画布与缩略图条各是一个 Swiper。
 *
 * 宿主是 body 末尾一个带 `.peach-react` 的容器，一棵根常驻：打开时换上新的一份灯箱
 * （`key` 每次加一，Swiper 从头建），关掉时只把内容渲染成空，根和容器留着给下一次。不进
 * 路由树：那一套是给壳的页面容器用的，容器归壳、换页就被整块重写；灯箱浮在所有页面
 * 之上，哪一页都能打开，跟哪个页面容器都不同生共死。 */
import { useId, useLayoutEffect, useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { flushSync } from 'react-dom';

import { icon } from '@peach/legacy/core';
import { setActionBusy, spinnerHtml, wireContextCard } from '@peach/legacy/ui';

import { syncBoardRange } from '../../board-controls';
import {
  loadSwiper, photoMeta, photoTitle, photoZoom, PHOTO_ZOOM_MAX, PHOTO_ZOOM_MIN, PHOTO_ZOOM_STEP,
  type LightboxHost, type LightboxSlide, type PhotoZoom, type SwiperConstructor, type SwiperInstance,
} from './photo-lightbox';

let host: HTMLElement | null = null;
let root: Root | null = null;
let generation = 0;

function lightboxRoot(): Root {
  if (!root || !host?.isConnected) {
    root?.unmount();
    host = document.createElement('div');
    host.className = 'peach-react';
    host.dataset.photoLightboxHost = '';
    document.body.append(host);
    root = createRoot(host);
  }
  return root;
}

/** 从第 `index` 张开始看 `slides`。Swiper 取不到时退到新标签页里打开那一张大图。 */
export async function openPhotoLightbox(index: number, slides: LightboxSlide[], actions?: LightboxHost): Promise<void> {
  if (!slides.length || index < 0 || index >= slides.length) return;
  let Swiper: SwiperConstructor;
  try {
    Swiper = await loadSwiper();
  } catch {
    window.open(slides[index].src, '_blank', 'noopener');
    return;
  }
  const at = lightboxRoot();
  flushSync(() => at.render(
    <PhotoLightbox key={++generation} slides={slides} index={index} Swiper={Swiper} host={actions ?? null} />,
  ));
}

export function closePhotoLightbox(): void {
  if (root) flushSync(() => root!.render(null));
}

interface Detail {
  dismiss(returnFocus?: boolean): void;
  /** 面板开着、这一下点在面板和触发键之外：收起面板，这一下不再算别的。 */
  dismissOutside(target: EventTarget | null): boolean;
  isOpen(): boolean;
}

function PhotoLightbox({ slides, index, Swiper, host: actions }: {
  slides: LightboxSlide[]; index: number; Swiper: SwiperConstructor; host: LightboxHost | null;
}) {
  const many = slides.length > 1;
  const detailId = useId();
  const box = useRef<HTMLDialogElement>(null);
  const mainEl = useRef<HTMLDivElement>(null);
  const stripEl = useRef<HTMLDivElement>(null);
  const pagination = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const fwd = useRef<HTMLButtonElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const range = useRef<HTMLInputElement>(null);
  const zoom = useRef<PhotoZoom | null>(null);
  const detail = useRef<Detail | null>(null);
  const [active, setActive] = useState(index);
  const [percent, setPercent] = useState(100);
  /* 大图的自然尺寸，到了才知道：面板上的分辨率读它。取不到原图的那几张换成缩略图。 */
  const [natural, setNatural] = useState<Record<number, [number, number]>>({});
  const [broken, setBroken] = useState<ReadonlySet<number>>(() => new Set());
  /** 原图和缩略图都取不到的那几张：放一块占位，不露浏览器的破图标。 */
  const [lost, setLost] = useState<ReadonlySet<number>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ at: number; text: string }>({ at: index, text: '' });

  useLayoutEffect(() => {
    const dialog = box.current!;
    dialog.showModal();
    document.body.classList.add('photolight-open');
    const strip = new Swiper(stripEl.current!, {
      slidesPerView: 'auto', spaceBetween: 8, freeMode: true, watchSlidesProgress: true,
      centeredSlides: true, slideToClickedSlide: true,
    });
    const centerThumb = (at: number, speed = 200) => strip.slideTo(at, speed);
    const main = new Swiper(mainEl.current!, {
      initialSlide: index, zoom: { minRatio: .01, maxRatio: 100 }, keyboard: { enabled: true }, lazyPreloadPrevNext: 1,
      // 上下滚也翻页：看图时手在滚轮上，没人愿意为了换一张去够左右键或按钮。
      mousewheel: { enabled: true, forceToAxis: false },
      thumbs: { swiper: strip },
      pagination: {
        el: pagination.current, clickable: true, dynamicBullets: true, dynamicMainBullets: 3,
        renderBullet: (at: number, className: string) =>
          `<button type="button" class="${className}" aria-label="查看第 ${at + 1} 张图片"></button>`,
      },
      navigation: { prevEl: back.current, nextEl: fwd.current },
      on: {
        slideChange(this: SwiperInstance) {
          setActive(this.activeIndex);
          centerThumb(this.activeIndex);
        },
      },
    });
    centerThumb(index, 0);
    zoom.current = photoZoom(dialog, main, setPercent);
    const context = wireContextCard(dialog, toggle.current!, panel.current!);
    const dismiss = (returnFocus = false) => {
      context.hide();
      toggle.current?.setAttribute('aria-expanded', 'false');
      if (returnFocus && toggle.current && document.contains(toggle.current)) toggle.current.focus();
    };
    /* 开着没开着问信息卡自己：退场动画那一段面板还没 hidden，按 hidden 判会把「正在收」
       当成「开着」，连按两下 Escape 第二下就只是又收了一遍。 */
    detail.current = {
      dismiss,
      dismissOutside: (target) => {
        const node = target instanceof Node ? target : null;
        if (!context.isOpen() || toggle.current!.contains(node) || panel.current!.contains(node)) return false;
        dismiss();
        return true;
      },
      isOpen: context.isOpen,
    };
    /* Swiper 只在自己构造的那一刻量一次容器。窗口一改大小（或首屏字体、滚动条落定得比构造晚）
       slide 就停在旧宽度上，大图按错误的框缩放，看起来就是「显示不全」。 */
    const resize = new ResizeObserver(() => { main.update(); strip.update(); zoom.current?.resize() });
    resize.observe(dialog);
    const keys = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (detail.current?.isOpen()) detail.current.dismiss(true);
      else closePhotoLightbox();
    };
    document.addEventListener('keydown', keys, true);
    return () => {
      document.removeEventListener('keydown', keys, true);
      dismiss();
      resize.disconnect();
      main.destroy(true, true);
      strip.destroy(true, true);
      zoom.current = null;
      detail.current = null;
      dialog.close();
      document.body.classList.remove('photolight-open');
    };
    // 每次打开换一份新的（`key`），这一段只在挂上与卸下时各跑一次。
  }, []);

  useLayoutEffect(() => { if (range.current) syncBoardRange(range.current) }, [percent]);

  const slide = slides[active];
  const asset = slide.asset ?? null;
  const reveal = async (button: HTMLButtonElement) => {
    if (!asset || !actions || button.getAttribute('aria-busy') === 'true') return;
    const at = active;
    setActionBusy(button);
    setBusy(true);
    setStatus({ at, text: '' });
    try {
      setStatus({ at, text: await actions.revealSource(asset.id) });
    } finally {
      setActionBusy(button, false);
      setBusy(false);
    }
  };
  const loaded = (at: number, img: HTMLImageElement) => {
    if (img.naturalWidth && img.naturalHeight && !natural[at]) {
      setNatural((prev) => ({ ...prev, [at]: [img.naturalWidth, img.naturalHeight] }));
    }
    zoom.current?.loaded(img);
  };
  // 原图取不到时换上这张的缩略图：归档站的原文件主机会拦下代理（pawchive 的 file.
  // 子域挂着 ddos-guard），缩略图由浏览器直接读公开主机。只换一次，缩略图也取不到就放占位。
  const failed = (at: number) => {
    if (broken.has(at) || !slides[at].thumb || slides[at].thumb === slides[at].src) {
      setLost((prev) => new Set(prev).add(at));
      return;
    }
    setBroken((prev) => new Set(prev).add(at));
  };
  const apply = (raw: number | 'fit') => zoom.current?.apply(raw);
  const revealLabel = '<span>在资源管理器中显示</span>';

  return (
    <dialog ref={box} aria-label="图片浏览" data-photo-lightbox="" data-has-strip={many ? '' : undefined}
      onCancel={(event) => { event.preventDefault(); closePhotoLightbox() }}
      onClick={(event) => {
        // 主画布铺满视口后，黑色留白属于 zoom 容器而不是最外层；两者都视为背景。
        // 点图片、缩略图条、工具栏和翻页按钮仍不退出。
        const target = event.target as Element;
        if (detail.current?.dismissOutside(target)) return;
        if (target === box.current || target.classList.contains('swiper-zoom-container')) closePhotoLightbox();
      }}>
      <button type="button" data-photo-close="" aria-label="关闭" onClick={closePhotoLightbox}
        dangerouslySetInnerHTML={{ __html: icon('x') }} />
      <div ref={mainEl} className="swiper" data-photo-main="" role="region" aria-roledescription="轮播"
        aria-label="图片浏览">
        <div className="swiper-wrapper">
          {slides.map((item, at) => (
            <div key={at} className="swiper-slide">
              <div className="swiper-zoom-container">
                {lost.has(at)
                  ? <span data-photo-missing="">图片取不到</span>
                  : <img src={broken.has(at) ? item.thumb : item.src} alt={item.name ?? ''} loading="lazy"
                    referrerPolicy="no-referrer" onLoad={(event) => loaded(at, event.currentTarget)}
                    onError={() => failed(at)} />}
              </div>
            </div>
          ))}
        </div>
        {/* 方向只翻图标，不转整个按钮。两枚都画朝左那一枚，朝右的在样式里转半圈。 */}
        <button ref={back} type="button" data-photo-nav="back" aria-label="上一张"
          dangerouslySetInnerHTML={{ __html: icon('chevron-left') }} />
        <button ref={fwd} type="button" data-photo-nav="fwd" aria-label="下一张"
          dangerouslySetInnerHTML={{ __html: icon('chevron-left') }} />
      </div>
      <div data-photo-bar="">
        <button ref={toggle} type="button" data-photo-detail-toggle="" aria-expanded="false" aria-controls={detailId}
          aria-haspopup="dialog" aria-label="图片详情" title="图片详情" dangerouslySetInnerHTML={{ __html: icon('info') }} />
        <div>
          <div data-photo-count="" aria-live="polite">{`${active + 1} / ${slides.length}`}</div>
          {many ? <div ref={pagination} data-photo-pagination="" aria-label="选择图片" /> : null}
        </div>
        <div data-photo-zoom="">
          <button type="button" data-zoom-step="-1" aria-label="缩小"
            onClick={() => apply(percent - PHOTO_ZOOM_STEP)} dangerouslySetInnerHTML={{ __html: icon('zoom-out') }} />
          <input ref={range} type="range" min={PHOTO_ZOOM_MIN} max={PHOTO_ZOOM_MAX} step="1" value={percent}
            aria-label="缩放" onChange={(event) => apply(Number(event.currentTarget.value))} />
          <button type="button" data-zoom-step="1" aria-label="放大"
            onClick={() => apply(percent + PHOTO_ZOOM_STEP)} dangerouslySetInnerHTML={{ __html: icon('zoom-in') }} />
          <b data-photo-zoom-readout="">{`${percent}%`}</b>
          <button type="button" data-photo-scale="fit" aria-label="适应窗口" title="适应窗口"
            onClick={() => apply('fit')} dangerouslySetInnerHTML={{ __html: icon('maximize') }} />
          <button type="button" data-photo-scale="original" aria-label="原大小" title="原大小"
            onClick={() => apply(100)}>1:1</button>
        </div>
      </div>
      {/* 图片详情只展示安全元数据，定位只把 asset id 交给服务端：绝不能为了显示路径把 ledger
          的本机绝对路径送进浏览器。标题按这一张换一个元素：中段省略（`src/ui-kit/middle-truncate.ts`）
          会改写它的文字，同一个元素上 React 再改字就对不上了。 */}
      <section ref={panel} id={detailId} data-photo-detail="" role="dialog" aria-modal="false"
        aria-labelledby={`${detailId}-title`} hidden>
        <div data-photo-detail-copy="">
          <h2 key={active} id={`${detailId}-title`} data-middle-truncate="" tabIndex={-1}>{photoTitle(slide)}</h2>
          <span data-photo-detail-meta="">{photoMeta(slide, natural[active])}</span>
        </div>
        <button type="button" data-photo-reveal="" hidden={!asset || !actions}
          onClick={(event) => void reveal(event.currentTarget)}
          dangerouslySetInnerHTML={{ __html: (busy ? spinnerHtml('正在定位') : icon('folder-open')) + revealLabel }} />
        <span data-photo-status="" aria-live="polite">{status.at === active ? status.text : ''}</span>
      </section>
      <div ref={stripEl} className="swiper" data-photo-strip="">
        <div className="swiper-wrapper">
          {slides.map((item, at) => (
            <div key={at} className="swiper-slide">
              <img src={item.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" />
            </div>
          ))}
        </div>
      </div>
    </dialog>
  );
}
